"""Fake Elasticsearch client for local/CI testing.

The dashboard imports a prebuilt singleton ``es_prd`` and only ever calls
``search`` / ``open_point_in_time`` / ``close_point_in_time`` on it. This fake
implements exactly that surface and returns STRUCTURALLY-VALID responses so
every consuming code path executes without KeyError/TypeError — the whole page
renders even with no real cluster.

Data fidelity is optional and additive: drop a JSON file at
``localdev/fixtures/<index>.json`` containing a list of ``_source`` documents
and the fake will return them as hits (best-effort term/terms filtering).
Aggregations return valid EMPTY buckets by default; add
``localdev/fixtures/<index>.aggs.json`` ({agg_name: agg_result}) to surface
canned aggregation results for higher-fidelity tiles.
"""

from __future__ import annotations

import json
import os
from typing import Any

_FIX_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "fixtures")


def _load_fixture(name: str) -> Any:
    path = os.path.join(_FIX_DIR, name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _flatten_filters(node: Any, out: list) -> None:
    """Collect simple term/terms clauses from a bool query tree (best effort)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("term", "terms") and isinstance(v, dict):
                for field, val in v.items():
                    field = field[:-8] if field.endswith(".keyword") else field
                    if k == "term":
                        val = val.get("value") if isinstance(val, dict) else val
                        out.append((field, [val]))
                    else:
                        out.append((field, list(val) if isinstance(val, (list, tuple)) else [val]))
            elif k == "must_not":
                continue  # don't apply exclusions in the fake — keep it permissive
            else:
                _flatten_filters(v, out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _flatten_filters(item, out)


def _doc_matches(doc: dict, clauses: list) -> bool:
    for field, wanted in clauses:
        val = doc.get(field)
        wl = {str(w).strip().lower() for w in wanted}
        if isinstance(val, (list, tuple, set)):
            if not any(str(v).strip().lower() in wl for v in val):
                return False
        else:
            if str(val if val is not None else "").strip().lower() not in wl:
                return False
    return True


def _empty_agg(defn: dict) -> dict:
    """Build a valid EMPTY result for one aggregation definition (recursive)."""
    sub = {k: _empty_agg(v) for k, v in (defn.get("aggs") or {}).items()
           if isinstance(v, dict)}
    if "top_hits" in defn:
        return {"hits": {"total": {"value": 0}, "hits": []}}
    if any(m in defn for m in ("sum", "max", "min", "avg",
                               "value_count", "cardinality")):
        return {"value": 0}
    if "filter" in defn:
        return {"doc_count": 0, **sub}
    if "filters" in defn:
        names = ((defn["filters"] or {}).get("filters") or {})
        return {"buckets": {n: {"doc_count": 0, **sub} for n in names}}
    if any(b in defn for b in ("terms", "composite", "date_histogram",
                               "histogram", "significant_terms")):
        res: dict = {"buckets": []}
        if "composite" in defn:
            res["after_key"] = None
        return res
    # Unknown agg shape → empty object is safest.
    return {}


class _FakeES:
    """Minimal fake of the elasticsearch-py client surface the dashboard uses."""

    def search(self, index: str = "", body: dict | None = None,
               size: int = 0, request_timeout: int = 0, **kwargs):
        body = body or {}
        aggs_def = body.get("aggs") or body.get("aggregations") or {}
        aggregations = {name: _empty_agg(defn)
                        for name, defn in aggs_def.items()
                        if isinstance(defn, dict)}
        # Canned aggregation fixtures override the empty defaults.
        canned = _load_fixture(f"{index}.aggs.json")
        if isinstance(canned, dict):
            aggregations.update(canned)

        hits: list = []
        total = 0
        if size and size > 0:
            docs = _load_fixture(f"{index}.json")
            if isinstance(docs, list):
                clauses: list = []
                _flatten_filters(body.get("query") or {}, clauses)
                matched = [d for d in docs
                           if isinstance(d, dict) and _doc_matches(d, clauses)]
                total = len(matched)
                hits = [{"_index": index, "_id": d.get("id") or str(i),
                         "_source": d, "sort": [i]}
                        for i, d in enumerate(matched[:size])]
        return {
            "took": 0, "timed_out": False,
            "hits": {"total": {"value": total, "relation": "eq"},
                     "max_score": None, "hits": hits},
            "aggregations": aggregations,
        }

    # Point-in-time API (history → PG migration path). The fake never migrates;
    # returning a token + empty searches keeps that surface inert but valid.
    def open_point_in_time(self, index: str = "", keep_alive: str = "1m", **kw):
        return {"id": "fake-pit"}

    def close_point_in_time(self, body: dict | None = None, **kw):
        return {"succeeded": True, "num_freed": 1}

    def count(self, index: str = "", body: dict | None = None, **kw):
        return {"count": 0}


# The singleton the dashboard imports: `from utils.elasticsearch import es_prd`
es_prd = _FakeES()
