"""Fake Elasticsearch client for local/CI testing.

The dashboard imports a prebuilt singleton ``es_prd`` and only calls ``search`` /
``open_point_in_time`` / ``close_point_in_time``. This fake implements that
surface AND a pragmatic subset of the ES query/aggregation DSL, computed over
per-index fixture documents — so tiles, charts and tables populate with
realistic data from one dataset (``localdev/fixtures/<index>.json``) instead of
being empty. Anything it can't translate falls back to a valid empty result, so
the page never crashes.

Supported filters: bool(must/filter/should/must_not + minimum_should_match),
term, terms, range (numeric + ISO date), exists, match_phrase/match.
Supported aggs: terms, composite{terms…}, filter, filters, top_hits,
sum/max/min/avg/value_count/cardinality, date_histogram (by day). Unknown shapes
→ empty.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

_FIX_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "fixtures")


def _load_fixture(name: str):
    path = os.path.join(_FIX_DIR, name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


# ── value access ──────────────────────────────────────────────────────────
def _bare(field: str) -> str:
    return field[:-8] if field.endswith(".keyword") else field


def _get(doc: dict, field: str):
    """Read a field that may be flat ('a.b' stored literally) or nested."""
    f = _bare(field)
    if f in doc:
        return doc[f]
    cur = doc
    for part in f.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _as_list(v):
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple, set)) else [v]


def _to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_epoch(v):
    """Parse a numeric epoch or ISO-ish date string to epoch seconds."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        # ES often stores epoch millis
        return float(v) / 1000.0 if v > 1e11 else float(v)
    s = str(v).strip()
    if not s:
        return None
    if s.isdigit():
        n = float(s)
        return n / 1000.0 if n > 1e11 else n
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s.replace("Z", "+0000"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return None


# ── filter matching ───────────────────────────────────────────────────────
def _match_clause(doc: dict, clause: dict) -> bool:
    if not isinstance(clause, dict):
        return True
    if "bool" in clause:
        b = clause["bool"]
        for sub in _as_list(b.get("must")) + _as_list(b.get("filter")):
            if not _match_clause(doc, sub):
                return False
        for sub in _as_list(b.get("must_not")):
            if _match_clause(doc, sub):
                return False
        shoulds = _as_list(b.get("should"))
        if shoulds:
            msm = b.get("minimum_should_match", 1)
            hits = sum(1 for s in shoulds if _match_clause(doc, s))
            if hits < (msm if isinstance(msm, int) else 1):
                return False
        return True
    if "term" in clause:
        for f, v in clause["term"].items():
            v = v.get("value") if isinstance(v, dict) else v
            vals = {str(x).strip().lower() for x in _as_list(_get(doc, f))}
            if str(v).strip().lower() not in vals:
                return False
        return True
    if "terms" in clause:
        for f, vs in clause["terms"].items():
            want = {str(x).strip().lower() for x in _as_list(vs)}
            have = {str(x).strip().lower() for x in _as_list(_get(doc, f))}
            if not (want & have):
                return False
        return True
    if "match_phrase" in clause or "match" in clause:
        m = clause.get("match_phrase") or clause.get("match")
        for f, v in m.items():
            v = v.get("query") if isinstance(v, dict) else v
            have = " ".join(str(x) for x in _as_list(_get(doc, f))).lower()
            if str(v).strip().lower() not in have:
                return False
        return True
    if "exists" in clause:
        return _get(doc, clause["exists"].get("field", "")) not in (None, "", [])
    if "range" in clause:
        for f, cond in clause["range"].items():
            raw = _get(doc, f)
            is_date = any(isinstance(b, str) and not str(b).lstrip("-").isdigit()
                          for b in cond.values()) or "date" in f.lower() or f.endswith("date")
            val = _to_epoch(raw) if is_date else _to_num(raw)
            if val is None:
                return False
            for op, bound in cond.items():
                if op in ("format", "time_zone", "boost"):
                    continue
                b = _to_epoch(bound) if is_date else _to_num(bound)
                if b is None:
                    continue
                if op == "gte" and not val >= b:
                    return False
                if op == "gt" and not val > b:
                    return False
                if op == "lte" and not val <= b:
                    return False
                if op == "lt" and not val < b:
                    return False
        return True
    if "match_all" in clause:
        return True
    # Unknown clause → don't exclude (permissive keeps data flowing).
    return True


def _query_docs(query: dict, docs: list) -> list:
    if not query:
        return list(docs)
    return [d for d in docs if _match_clause(d, query)]


# ── aggregations ──────────────────────────────────────────────────────────
def _run_aggs(aggs_def: dict, docs: list) -> dict:
    return {name: _run_agg(defn, docs)
            for name, defn in (aggs_def or {}).items()
            if isinstance(defn, dict)}


def _run_agg(defn: dict, docs: list) -> dict:
    sub = defn.get("aggs") or {}
    if "terms" in defn:
        t = defn["terms"]
        field = t["field"]
        size = int(t.get("size", 10))
        missing = t.get("missing")
        groups: dict = {}
        for d in docs:
            for k in (_as_list(_get(d, field)) or [None]):
                if k in (None, ""):
                    if missing is None:
                        continue
                    k = missing
                groups.setdefault(str(k), []).append(d)
        buckets = [{"key": k, "doc_count": len(g), **_run_aggs(sub, g)}
                   for k, g in groups.items()]
        buckets.sort(key=lambda b: -b["doc_count"])
        return {"buckets": buckets[:size], "sum_other_doc_count": 0,
                "doc_count_error_upper_bound": 0}
    if "composite" in defn:
        c = defn["composite"]
        sources = c.get("sources", [])
        size = int(c.get("size", 10))
        groups: dict = {}
        for d in docs:
            key = {}
            skip = False
            for src in sources:
                (nm, body), = src.items()
                spec = body.get("terms") or body.get("histogram") \
                    or body.get("date_histogram") or {}
                vals = _as_list(_get(d, spec.get("field", "")))
                v = vals[0] if vals else None
                if v in (None, "") and not spec.get("missing_bucket"):
                    skip = True
                    break
                key[nm] = v if v not in (None, "") else None
            if skip:
                continue
            gk = json.dumps(key, sort_keys=True, default=str)
            groups.setdefault(gk, [key, []])[1].append(d)
        buckets = [{"key": k, "doc_count": len(g), **_run_aggs(sub, g)}
                   for k, g in groups.values()]
        buckets.sort(key=lambda b: -b["doc_count"])
        return {"after_key": None, "buckets": buckets[:size]}
    if "filter" in defn:
        fdocs = _query_docs(defn["filter"], docs)
        return {"doc_count": len(fdocs), **_run_aggs(sub, fdocs)}
    if "filters" in defn:
        named = (defn["filters"] or {}).get("filters") or {}
        out = {}
        for n, q in named.items():
            fdocs = _query_docs(q, docs)
            out[n] = {"doc_count": len(fdocs), **_run_aggs(sub, fdocs)}
        return {"buckets": out}
    if "top_hits" in defn:
        th = defn["top_hits"]
        n = int(th.get("size", 3))
        src = th.get("_source")
        sort = th.get("sort")
        sel = list(docs)
        if isinstance(sort, list) and sort:
            (sf, so), = list(sort[0].items())[:1] or [(None, {})]
            if sf:
                rev = isinstance(so, dict) and so.get("order") == "desc"
                sel = sorted(sel, key=lambda d: (_to_epoch(_get(d, sf))
                                                 or _to_num(_get(d, sf)) or 0),
                             reverse=rev)
        hits = []
        for i, d in enumerate(sel[:n]):
            if isinstance(src, list):
                s = {f: _get(d, f) for f in src if _get(d, f) is not None}
            else:
                s = d
            hits.append({"_index": "", "_id": str(d.get("id") or i),
                         "_source": s, "sort": [i]})
        return {"hits": {"total": {"value": len(docs)}, "hits": hits}}
    for m in ("sum", "max", "min", "avg", "value_count", "cardinality"):
        if m in defn:
            field = defn[m]["field"]
            vals = [_to_num(_get(d, field)) for d in docs]
            vals = [v for v in vals if v is not None]
            if m == "value_count":
                val = len(vals)
            elif m == "cardinality":
                val = len({_get(d, field) for d in docs if _get(d, field) is not None})
            elif not vals:
                val = 0
            elif m == "sum":
                val = sum(vals)
            elif m == "max":
                val = max(vals)
            elif m == "min":
                val = min(vals)
            else:
                val = sum(vals) / len(vals)
            return {"value": val}
    if "date_histogram" in defn:
        dh = defn["date_histogram"]
        field = dh.get("field", "")
        groups: dict = {}
        for d in docs:
            ep = _to_epoch(_get(d, field))
            if ep is None:
                continue
            day = datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m-%d")
            groups.setdefault(day, []).append(d)
        buckets = [{"key_as_string": day, "key": int(_to_epoch(day) * 1000),
                    "doc_count": len(g), **_run_aggs(sub, g)}
                   for day, g in sorted(groups.items())]
        return {"buckets": buckets}
    # Unknown → valid empty.
    if "top_hits" in defn:
        return {"hits": {"total": {"value": 0}, "hits": []}}
    return {"buckets": []}


class _FakeES:
    def search(self, index: str = "", body: dict | None = None,
               size: int = 0, request_timeout: int = 0, **kwargs):
        body = body or {}
        docs = _load_fixture(f"{index}.json")
        docs = docs if isinstance(docs, list) else []
        matched = _query_docs(body.get("query") or {}, docs)

        aggs_def = body.get("aggs") or body.get("aggregations") or {}
        aggregations = _run_aggs(aggs_def, matched)
        canned = _load_fixture(f"{index}.aggs.json")
        if isinstance(canned, dict):
            aggregations.update(canned)

        hits = []
        if size and size > 0:
            for i, d in enumerate(matched[:size]):
                hits.append({"_index": index, "_id": str(d.get("id") or i),
                             "_source": d, "sort": [i]})
        return {
            "took": 0, "timed_out": False,
            "hits": {"total": {"value": len(matched), "relation": "eq"},
                     "max_score": None, "hits": hits},
            "aggregations": aggregations,
        }

    def open_point_in_time(self, index: str = "", keep_alive: str = "1m", **kw):
        return {"id": "fake-pit"}

    def close_point_in_time(self, body: dict | None = None, **kw):
        return {"succeeded": True, "num_freed": 1}

    def count(self, index: str = "", body: dict | None = None, **kw):
        docs = _load_fixture(f"{index}.json")
        docs = docs if isinstance(docs, list) else []
        return {"count": len(_query_docs((body or {}).get("query") or {}, docs))}


# `from utils.elasticsearch import es_prd`
es_prd = _FakeES()
