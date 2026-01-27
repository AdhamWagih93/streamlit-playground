from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Event
from typing import Any, Dict, List, Optional

import requests
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.ai.mcp_langchain_tools import invoke_tool
from src.scheduler.config import SchedulerConfig
from src.scheduler.mcp_targets import build_langchain_conn, build_target_specs
from src.scheduler.repo import (
    claim_due_jobs,
    record_run,
    set_next_run,
)


@dataclass
class SchedulerRuntimeState:
    started_at_utc: str
    last_tick_at_utc: Optional[str] = None
    last_tick_summary: Optional[Dict[str, Any]] = None


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _parse_args(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _normalise_mcp_http_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return raw
    base = raw.rstrip("/")
    if base.endswith("/mcp"):
        return base
    return base + "/mcp"


def _extract_sse_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    data_lines = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if not data_lines:
        return {}
    payload = "\n".join(data_lines).strip()
    try:
        return json.loads(payload)
    except Exception:
        return {}


def _call_mcp_http_tool(url: str, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    endpoint = _normalise_mcp_http_url(url)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "bsw-scheduler", "version": "1.0"},
        },
    }
    init_resp = requests.post(endpoint, json=init_payload, headers=headers, timeout=15)
    if init_resp.status_code >= 400:
        return {"ok": False, "error": f"HTTP {init_resp.status_code}", "details": init_resp.text[:500]}
    session_id = init_resp.headers.get("mcp-session-id")
    if not session_id:
        return {"ok": False, "error": "Missing mcp-session-id", "details": init_resp.text[:500]}

    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }
    resp = requests.post(
        endpoint,
        json=payload,
        headers={**headers, "mcp-session-id": session_id},
        timeout=15,
    )
    if resp.status_code >= 400:
        return {"ok": False, "error": f"HTTP {resp.status_code}", "details": resp.text[:500]}

    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        data = _extract_sse_json(resp.text) or {}
    else:
        try:
            data = resp.json()
        except Exception:
            return {"ok": False, "error": "Invalid JSON response", "details": resp.text[:500]}

    if isinstance(data, dict) and data.get("error"):
        return {"ok": False, "error": str(data.get("error"))}

    return data.get("result", {"ok": True, "result": data})


def run_scheduler_forever(cfg: SchedulerConfig, stop_event: Event, state: SchedulerRuntimeState) -> None:
    """Blocking loop that executes due jobs on a wall-clock timer."""

    specs = build_target_specs()

    while not stop_event.is_set():
        tick_started = datetime.utcnow()
        state.last_tick_at_utc = _utc_now_iso()

        executed = 0
        ok = 0
        failed = 0

        due = claim_due_jobs(cfg.database_url, now=tick_started, limit=int(cfg.max_jobs_per_tick))
        for job in due:
            if stop_event.is_set():
                break

            run_started = datetime.utcnow()
            err_text: Optional[str] = None
            res_dict: Optional[Dict[str, Any]] = None
            ok_val: Optional[bool] = None

            try:
                spec = specs.get(job.server)
                if spec is None:
                    raise ValueError(f"Unknown server: {job.server}")

                transport = (spec.transport or "stdio").lower().strip()
                if transport in {"http", "streamable-http"}:
                    tools = None
                else:
                    conn = build_langchain_conn(spec)
                    client = MultiServerMCPClient(connections={job.server: conn})
                    tools = asyncio.run(client.get_tools())

                args = _parse_args(job.args_json)
                token = getattr(spec, "client_token", None)
                if token and "_client_token" not in args:
                    args["_client_token"] = token

                if transport in {"http", "streamable-http"}:
                    result = _call_mcp_http_tool(str(spec.url or ""), job.tool, args)
                else:
                    result = invoke_tool(list(tools or []), job.tool, args)
                if isinstance(result, dict):
                    res_dict = result
                    ok_val = bool(result.get("ok")) if "ok" in result else True
                else:
                    res_dict = {"ok": True, "result": str(result)}
                    ok_val = True

                if ok_val:
                    ok += 1
                else:
                    failed += 1

            except Exception as exc:  # noqa: BLE001
                err_text = str(exc)
                res_dict = {"ok": False, "error": err_text}
                ok_val = False
                failed += 1
            finally:
                run_finished = datetime.utcnow()
                record_run(
                    cfg.database_url,
                    job_id=job.id,
                    started_at=run_started,
                    finished_at=run_finished,
                    ok=ok_val,
                    result=res_dict,
                    error=err_text,
                )

                # Schedule next run from completion time to reduce drift when
                # tool execution takes longer than the tick interval.
                next_run = datetime.utcnow() + timedelta(seconds=int(job.interval_seconds))
                set_next_run(cfg.database_url, job.id, next_run_at=next_run)

                executed += 1

        state.last_tick_summary = {
            "executed": executed,
            "ok": ok,
            "failed": failed,
            "jobs_due": len(due),
        }

        # Sleep for tick interval (minus time spent), but wake quickly on stop.
        elapsed = (datetime.utcnow() - tick_started).total_seconds()
        sleep_s = max(0.2, float(cfg.tick_seconds) - float(elapsed))
        stop_event.wait(timeout=sleep_s)
