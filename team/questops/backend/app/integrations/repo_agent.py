"""LangChain agent over a repository workspace (Ollama-backed) with a HARD
human-approval gate: the agent only ever PROPOSES tool calls; nothing executes
until the user approves each one, and every proposal/decision/output is logged
to the agent_commands table.

Flow: start() runs the model until it proposes tool calls -> rows are created
as 'pending' and returned to the UI -> decide() executes (or denies) one call
-> when the round is fully decided the loop resumes with the results.

Read-only shell commands are additionally whitelisted (see run_readonly);
write tools exist only when the user flips 'enable write actions' on the page,
and even then edits stay in the local workspace — never committed or pushed."""

import json
import shlex
import subprocess
import time
import uuid

from sqlalchemy.orm import Session

from ..config import settings
from ..db import AgentCommand, utcnow
from . import ollama as ollama_client
from . import repo_scan
from .repos import (RepoError, _repo_by_slot,
                    read_file as repos_read_file, tree as repos_tree,
                    write_file as repos_write_file)

CMD_TIMEOUT = 15
MAX_TOOL_OUTPUT = 8000
MAX_ROUNDS = 8
SESSION_TTL = 3600  # seconds; commands stay logged forever, sessions don't

READ_CMDS = {"ls", "cat", "head", "tail", "grep", "find", "wc", "file", "du",
             "sort", "uniq", "cut", "tr", "basename", "dirname", "stat"}
GIT_READ_SUBCMDS = {"status", "log", "show", "diff", "branch", "blame",
                    "ls-files", "shortlog", "rev-parse", "grep", "describe"}
FIND_BANNED_FLAGS = {"-delete", "-exec", "-execdir", "-ok", "-okdir",
                     "-fprint", "-fprintf", "-fls"}

WRITE_TOOLS = {"write_file"}

# session id -> loop state (single-process, like jenkins.CLAIMS); the DB rows
# are the durable record — a lost session never loses the audit trail
_SESSIONS: dict[str, dict] = {}


def _repo_dir(slot: int, username: str | None = None):
    """username -> that member's own worktree; None -> the server copy."""
    from .repos import _workspace
    repo = _repo_by_slot(slot)
    return repo, _workspace(repo, username)


# ------------------------------------------------------------- the sandbox
def run_readonly(slot: int, command: str, username: str | None = None) -> str:
    """Run one whitelisted read-only command inside the repo workspace.
    shell=False (no interpolation); paths must stay inside the repo."""
    _, repo_dir = _repo_dir(slot, username)
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"error: {exc}"
    if not argv:
        return "error: empty command"
    prog = argv[0]
    if prog not in READ_CMDS and prog != "git":
        return (f"error: '{prog}' is not allowed. Read-only commands only: "
                f"{', '.join(sorted(READ_CMDS))}, git <{'/'.join(sorted(GIT_READ_SUBCMDS))}>")
    if prog == "git" and (len(argv) < 2 or argv[1] not in GIT_READ_SUBCMDS):
        return f"error: only read-only git subcommands are allowed: {', '.join(sorted(GIT_READ_SUBCMDS))}"
    if prog == "find" and set(argv) & FIND_BANNED_FLAGS:
        return "error: find's write/exec flags are not allowed"
    for tok in argv[1:]:
        if tok.startswith(("/", "~")) or ".." in tok:
            return "error: paths must be relative to the repo root (no '/', '~' or '..')"
    try:
        p = subprocess.run(argv, cwd=repo_dir, capture_output=True, text=True,
                           timeout=CMD_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"error: command timed out after {CMD_TIMEOUT}s"
    except FileNotFoundError:
        return f"error: '{prog}' is not installed on the server"
    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    out = out.strip()
    if len(out) > MAX_TOOL_OUTPUT:
        out = out[:MAX_TOOL_OUTPUT] + f"\n… (truncated at {MAX_TOOL_OUTPUT} chars)"
    return out or f"(exit {p.returncode}, no output)"


def _tech_scan_text(slot: int) -> str:
    r = repo_scan.scan(slot)
    out = []
    for t in r["technologies"]:
        out.append(f"{t['name']} (evidence: {', '.join(t['evidence'])})")
        out += [f"  - {rec}" for rec in t["recommendations"]]
    out += [f"general: {g}" for g in r["general"]]
    return "\n".join(out) or "nothing detected"


def _execute(slot: int, allow_write: bool, tool: str, args: dict,
             username: str | None = None) -> str:
    """The ONLY place agent tool calls are executed — always post-approval,
    always inside the requesting member's own worktree."""
    try:
        if tool == "run_command":
            return run_readonly(slot, str(args.get("command", "")), username)
        if tool == "list_directory":
            t = repos_tree(slot, str(args.get("path", "")), username)
            return "\n".join(f"{'dir ' if e['type'] == 'dir' else 'file'} {e['path']}"
                             for e in t["entries"]) or "(empty directory)"
        if tool == "read_file":
            return repos_read_file(slot, str(args.get("path", "")), username)["content"]
        if tool == "tech_scan":
            return _tech_scan_text(slot)
        if tool == "write_file":
            if not allow_write:
                return "error: write actions are disabled for this session"
            content = str(args.get("content", ""))
            repos_write_file(slot, str(args.get("path", "")), content, username)
            return (f"wrote {args.get('path')} ({len(content.encode())} bytes) — "
                    "local to your workspace, visible as a diff on the Repositories page")
        return f"error: unknown tool '{tool}'"
    except RepoError as exc:
        return f"error: {exc}"


# ------------------------------------------------------------- session core
def _prune_sessions() -> None:
    cutoff = time.time() - SESSION_TTL
    for sid in [s for s, v in _SESSIONS.items() if v["at"] < cutoff]:
        _SESSIONS.pop(sid, None)


def _steps(db: Session, sid: str) -> list[dict]:
    rows = (db.query(AgentCommand).filter(AgentCommand.session_id == sid)
            .order_by(AgentCommand.id).all())
    return [{"id": r.id, "tool": r.tool, "input": r.input[:300],
             "output": (r.output or "")[:1200], "status": r.status,
             "decided_by": r.decided_by} for r in rows]


def _pending(db: Session, sid: str) -> list[dict]:
    rows = (db.query(AgentCommand)
            .filter(AgentCommand.session_id == sid,
                    AgentCommand.status == "pending")
            .order_by(AgentCommand.id).all())
    return [{"id": r.id, "tool": r.tool, "input": r.input, "write": r.write}
            for r in rows]


def _propose(db: Session, sid: str, calls: list[tuple[str, dict, str]]) -> dict:
    """Log proposed tool calls as pending rows; nothing runs yet."""
    sess = _SESSIONS[sid]
    sess["round"] = {}
    for tool, args, call_id in calls:
        row = AgentCommand(session_id=sid, repo_slot=sess["slot"],
                           repo_name=sess["repo_name"], username=sess["username"],
                           tool=tool, input=json.dumps(args),
                           write=tool in WRITE_TOOLS, status="pending")
        db.add(row)
        db.flush()
        sess["round"][row.id] = {"tool": tool, "args": args, "call_id": call_id}
    db.commit()
    return {"status": "pending", "session": sid, "reply": None,
            "engine": sess["engine"], "pending": _pending(db, sid),
            "steps": _steps(db, sid)}


def _final(db: Session, sid: str, reply: str) -> dict:
    sess = _SESSIONS.pop(sid, {"engine": "?"})
    return {"status": "final", "session": sid, "reply": reply,
            "engine": sess["engine"], "pending": [], "steps": _steps(db, sid)}


# ------------------------------------------------------------- engines
def _advance_lc(db: Session, sid: str) -> dict:
    """One model turn: either a final answer, or a batch of PROPOSED calls.
    Resumption after the human decides happens via decide() -> _resume_lc."""
    sess = _SESSIONS[sid]
    if sess["rounds"] >= MAX_ROUNDS:
        return _final(db, sid, "(stopped: too many tool rounds — narrow the question)")
    resp = sess["llm"].invoke(sess["messages"])
    sess["messages"].append(resp)
    sess["rounds"] += 1
    calls = getattr(resp, "tool_calls", None) or []
    if not calls:
        return _final(db, sid, resp.content or "(no answer)")
    return _propose(db, sid, [(c["name"], c.get("args") or {}, c["id"]) for c in calls])


def _resume_lc(db: Session, sid: str, results: dict[str, str]) -> dict:
    from langchain_core.messages import ToolMessage

    sess = _SESSIONS[sid]
    for info in sess["round"].values():
        sess["messages"].append(ToolMessage(
            content=results[info["call_id"]], tool_call_id=info["call_id"]))
    sess["round"] = {}
    return _advance_lc(db, sid)


def _advance_demo(db: Session, sid: str) -> dict:
    """Offline engine (no LangChain/Ollama): a tiny scripted agent so the
    approval flow works end-to-end in demo mode too."""
    sess = _SESSIONS[sid]
    stage = sess["stage"]
    if stage == 0:
        sess["stage"] = 1
        return _propose(db, sid, [("run_command", {"command": "ls"}, "demo-ls")])
    executed = {r["call_id"]: r for r in sess["results"]}
    if stage == 1 and "README.md" in (executed.get("demo-ls", {}).get("output") or ""):
        sess["stage"] = 2
        return _propose(db, sid, [("read_file", {"path": "README.md"}, "demo-readme")])
    # final: build a reply from whatever the human allowed
    parts = [f"**{sess['repo_name']}** — offline agent (configure Ollama + install "
             "langchain for real exploration). You asked: “{q}”".format(q=sess["query"])]
    for r in sess["results"]:
        label = {"demo-ls": "Repository root (`ls`)",
                 "demo-readme": "README.md"}.get(r["call_id"], r["tool"])
        if r["status"] == "executed":
            parts.append(f"{label}:\n```\n{r['output'][:800]}\n```")
        else:
            parts.append(f"{label}: you denied this command, skipped.")
    try:
        parts.append("Tech scan summary:\n" + _tech_scan_text(sess["slot"])[:800])
    except RepoError:
        pass
    return _final(db, sid, "\n\n".join(parts))


# ------------------------------------------------------------- public API
def start(db: Session, slot: int, username: str, message: str,
          history: list[dict] | None, allow_write: bool) -> dict:
    repo, _ = _repo_dir(slot)
    _prune_sessions()
    sid = uuid.uuid4().hex[:24]
    sess = {"slot": slot, "repo_name": repo["name"], "username": username,
            "allow_write": allow_write, "at": time.time(), "rounds": 0,
            "round": {}, "results": [], "query": message, "stage": 0}
    _SESSIONS[sid] = sess

    lc_ok = True
    try:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
        from langchain_core.tools import tool as lc_tool
        from langchain_ollama import ChatOllama
    except ImportError:
        lc_ok = False

    if not lc_ok or not ollama_client.available():
        sess["engine"] = "demo" if settings.demo_mode else "fallback"
        if sess["engine"] == "fallback":
            reason = ("LangChain is not installed" if not lc_ok
                      else f"Ollama is not reachable at {settings.ollama_url}")
            try:
                scan_note = _tech_scan_text(slot)[:900]
            except RepoError:
                scan_note = ""
            return _final(db, sid, f"(exploration agent offline — {reason})\n\n"
                          f"Deterministic tech scan:\n{scan_note}\n\n"
                          "Install `langchain` + `langchain-ollama` and configure "
                          "OLLAMA_URL to chat with the repository interactively.")
        return _advance_demo(db, sid)

    # ---- real engine: schema-only tool defs; execution NEVER happens here ----
    @lc_tool
    def run_command(command: str) -> str:
        """Run ONE read-only exploration command inside the repository, e.g.
        'ls src', 'grep -rn "TODO" src', 'git log -5 --oneline'.
        Paths are relative to the repo root. A human must approve each call."""

    @lc_tool
    def list_directory(path: str = "") -> str:
        """List one directory of the repository ('' = repo root)."""

    @lc_tool
    def read_file(path: str) -> str:
        """Read a text file from the repository (max 512KB)."""

    @lc_tool
    def tech_scan() -> str:
        """QuestOps' deterministic technology scan with recommendations."""

    tools = [run_command, list_directory, read_file, tech_scan]
    if allow_write:
        @lc_tool
        def write_file(path: str, content: str) -> str:
            """Create or overwrite ONE file in the LOCAL workspace (never
            committed/pushed). A human must approve. Read before rewriting."""
        tools.append(write_file)

    system = (
        f"You are the QuestOps repository agent for the git repository "
        f"'{repo['name']}' ({repo['url']}), working in a local server-side clone. "
        "EVERY tool call you make is shown to a human who must approve it before "
        "it runs — keep calls few and purposeful; a denied call means don't retry "
        "it. Always inspect real files before answering; cite file paths. "
        + ("Write access is ENABLED via the write_file tool (local workspace "
           "only, reviewed as diffs)." if allow_write else
           "Write access is DISABLED: propose changes as snippets instead.")
    )
    sess["engine"] = "langchain+ollama"
    sess["llm"] = ChatOllama(base_url=settings.ollama_url,
                             model=settings.ollama_model,
                             temperature=0.2).bind_tools(tools)
    msgs = [SystemMessage(content=system)]
    for m in (history or [])[-8:]:
        cls = HumanMessage if m.get("role") == "user" else AIMessage
        msgs.append(cls(content=str(m.get("content", ""))))
    msgs.append(HumanMessage(content=message))
    sess["messages"] = msgs
    try:
        return _advance_lc(db, sid)
    except Exception as exc:  # noqa: BLE001
        return _final(db, sid, f"(agent error: {str(exc)[:200]})")


def decide(db: Session, command_id: int, approve: bool, username: str) -> dict:
    row = db.get(AgentCommand, command_id)
    if row is None:
        raise RepoError("command not found")
    if row.status != "pending":
        raise RepoError(f"command already {row.status}")

    sess = _SESSIONS.get(row.session_id)
    if approve:
        allow_write = sess["allow_write"] if sess else False
        # execute in the CHAT USER's worktree — never in a teammate's
        row.output = _execute(row.repo_slot, allow_write, row.tool,
                              json.loads(row.input or "{}"), row.username)
        row.status = "error" if row.output.startswith("error:") else "executed"
    else:
        row.status = "denied"
        row.output = "denied by the user"
    row.decided_by = username
    row.decided_at = utcnow()
    db.commit()

    if sess is None:
        return {"status": "final", "session": row.session_id, "engine": "?",
                "reply": "(agent session expired — the decision was logged, "
                         "but the conversation can't resume; ask again)",
                "pending": [], "steps": _steps(db, row.session_id)}

    info = sess["round"].pop(row.id, None)
    sess["at"] = time.time()
    sess["results"].append({"tool": row.tool, "call_id": (info or {}).get("call_id"),
                            "output": row.output, "status": row.status})
    if sess["round"]:  # more calls in this round still await a decision
        return {"status": "pending", "session": row.session_id,
                "engine": sess["engine"], "reply": None,
                "pending": _pending(db, row.session_id),
                "steps": _steps(db, row.session_id)}

    sid = row.session_id
    try:
        if sess["engine"] == "langchain+ollama":
            recent = {r["call_id"]: (r["output"] if r["status"] == "executed"
                                     else "The user DENIED this call. Do not retry it; "
                                          "continue without it.")
                      for r in sess["results"] if r["call_id"]}
            return _resume_lc(db, sid, recent)
        return _advance_demo(db, sid)
    except Exception as exc:  # noqa: BLE001
        return _final(db, sid, f"(agent error: {str(exc)[:200]})")


def audit_log(db: Session, slot: int, limit: int = 30) -> list[dict]:
    rows = (db.query(AgentCommand).filter(AgentCommand.repo_slot == slot)
            .order_by(AgentCommand.id.desc()).limit(limit).all())
    return [{"id": r.id, "at": r.requested_at.isoformat(), "username": r.username,
             "tool": r.tool, "input": r.input[:200], "status": r.status,
             "write": r.write, "decided_by": r.decided_by,
             "output": (r.output or "")[:400]} for r in rows]
