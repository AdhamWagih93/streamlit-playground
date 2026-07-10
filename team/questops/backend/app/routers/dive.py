"""Failure Dive: drill into a Jenkins failure — console log first, then the
pipeline definition resolved from SCM (the Engine repo), and ONLY after the
user confirms, an AI-guided root-cause analysis."""

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..db import User
from ..integrations import elastic, jenkins, ollama, repos
from ..integrations.repos import RepoError

router = APIRouter(prefix="/api/dive", tags=["dive"])


def _norm_url(u: str) -> str:
    return (u or "").rstrip("/").removesuffix(".git").lower()


def _resolve_pipeline(job: str) -> dict:
    """Jenkins job definition -> scriptPath + SCM url -> the matching DEFINED
    repository (by SCM url, falling back to the one named 'Engine') -> the
    actual groovy script content from its local workspace."""
    try:
        d = jenkins.job_definition(job)
    except Exception as exc:  # noqa: BLE001 — best-effort panel
        return {"script_path": "", "note": f"job definition unavailable: {str(exc)[:150]}"}
    out = {"script_path": d.get("script_path", ""), "scm_url": d.get("scm_url", ""),
           "defined_on": d.get("defined_on", ""), "note": d.get("note", ""),
           "repo": None, "script": None}
    if not out["script_path"]:
        return out

    defined = repos.configured()
    target = next((r for r in defined
                   if out["scm_url"] and _norm_url(r["url"]) == _norm_url(out["scm_url"])),
                  None)
    if target is None:
        target = next((r for r in defined if r["name"].lower() == "engine"), None)
    if target is None:
        out["note"] = ("the pipeline's SCM repo is not defined — add your Engine "
                       "repository on the Repositories page to continue the "
                       "investigation in its groovy source")
        return out

    row = next((r for r in repos.list_repos() if r["slot"] == target["slot"]), {})
    out["repo"] = {"slot": target["slot"], "name": target["name"],
                   "cloned": bool(row.get("cloned"))}
    if not row.get("cloned"):
        out["note"] = f"clone '{target['name']}' on the Repositories page to view the pipeline source"
        return out
    try:
        out["script"] = repos.read_file(target["slot"], out["script_path"])["content"]
    except RepoError as exc:
        out["note"] = f"couldn't read {out['script_path']} in '{target['name']}': {exc}"
    return out


def _error_context(job: str) -> list[dict]:
    """Known categorized failures for this job from the error-analysis index."""
    base = job.split("/")[0].lower()
    try:
        docs = elastic.error_analysis(None)
    except Exception:  # noqa: BLE001
        return []
    hits = [d for d in docs
            if base in str(d.get("jobname", "")).lower()
            or base in str(d.get("jobpath", "")).lower()]
    return [{"TicketFlag": d.get("TicketFlag"), "ErrorCode": d.get("ErrorCode"),
             "ErrorType": d.get("ErrorType"), "ErrorAction": d.get("ErrorAction"),
             "AIErrorAction": d.get("AIErrorAction"), "Date": d.get("Date")}
            for d in hits[:3]]


_ERR_LINE = re.compile(r"(?i)\b(error|exception|failed|failure|fatal|caused by)\b")


def _heuristic(log: str, pipe: dict) -> str:
    """Deterministic fallback when Ollama is offline: extract the evidence."""
    lines = log.splitlines()
    errs = [l for l in lines if _ERR_LINE.search(l)][-12:]
    stages = [l for l in lines if "(Build)" in l or "stage" in l.lower()][-6:]
    out = ["**AI is offline — heuristic extraction** (configure OLLAMA_URL for guided analysis)"]
    if errs:
        out.append("Error lines from the log tail:\n```\n" + "\n".join(errs) + "\n```")
    if stages:
        out.append("Last stage markers:\n```\n" + "\n".join(stages) + "\n```")
    if pipe.get("script_path"):
        out.append(f"Pipeline source: `{pipe['script_path']}`"
                   + (f" in repo **{pipe['repo']['name']}**" if pipe.get("repo") else ""))
    return "\n\n".join(out)


@router.get("/log")
def dive_log(job: str, number: int, user: User = Depends(current_user)):
    try:
        return {"job": job, "number": number, "log": jenkins.console_log(job, number)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"couldn't fetch the console log: {str(exc)[:200]}")


@router.get("/pipeline")
def dive_pipeline(job: str, user: User = Depends(current_user)):
    return _resolve_pipeline(job)


class AnalyzeBody(BaseModel):
    job: str
    number: int


@router.post("/analyze")
def analyze(body: AnalyzeBody, user: User = Depends(current_user)):
    """AI root-cause guidance — only ever called after the user confirms."""
    try:
        log = jenkins.console_log(body.job, body.number)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"couldn't fetch the console log: {str(exc)[:200]}")
    pipe = _resolve_pipeline(body.job)
    errctx = _error_context(body.job)

    parts = [f"Jenkins job: {body.job} — build #{body.number} FAILED.",
             f"Console log tail:\n```\n{log[-6000:]}\n```"]
    if pipe.get("script"):
        parts.append(f"The pipeline is defined in `{pipe['script_path']}` "
                     f"(repo '{pipe['repo']['name']}'):\n```groovy\n{pipe['script'][:4000]}\n```")
    if errctx:
        parts.append("Known categorized failures for this job (error-analysis index): "
                     + "; ".join(f"{e['TicketFlag']}/{e['ErrorCode']}: {e['ErrorAction'] or e['AIErrorAction']}"
                                 for e in errctx))
    system = ("You are a CI failure analyst for a DevOps platform team. Work ONLY "
              "from the evidence provided. Structure your answer in markdown as: "
              "**Symptom** (one line) · **Evidence** (quote the decisive log lines) · "
              "**Root cause** (most probable, say how confident) · "
              "**Where in the pipeline** (stage/step, reference the groovy if given) · "
              "**Fix steps** (numbered, concrete) · **Prevent recurrence** (one idea). "
              "Be concise; no generic advice.")
    analysis = ollama.safe_chat([{"role": "user", "content": "\n\n".join(parts)}],
                                system=system, fallback=_heuristic(log, pipe))
    return {"analysis": analysis, "used_pipeline": bool(pipe.get("script")),
            "error_context": errctx,
            "engine": "ollama" if ollama.available() else "heuristic fallback"}
