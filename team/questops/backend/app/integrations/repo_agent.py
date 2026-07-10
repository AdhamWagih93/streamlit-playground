"""LangChain agent over a repository workspace (Ollama-backed).

The agent explores with WHITELISTED read-only shell commands and file tools;
write tools exist only when the user flips 'enable write actions' on the page,
and even then edits stay in the local workspace — this app never pushes from
the Repositories page. Every tool call is returned to the UI as a step, so
the human sees exactly what the agent ran."""

import shlex
import subprocess

from ..config import settings
from . import ollama as ollama_client
from . import repo_scan
from .repos import (RepoError, _dir_for, _repo_by_slot,
                    read_file as repos_read_file, tree as repos_tree,
                    write_file as repos_write_file)

CMD_TIMEOUT = 15
MAX_TOOL_OUTPUT = 8000
MAX_ITERATIONS = 8

READ_CMDS = {"ls", "cat", "head", "tail", "grep", "find", "wc", "file", "du",
             "sort", "uniq", "cut", "tr", "basename", "dirname", "stat"}
GIT_READ_SUBCMDS = {"status", "log", "show", "diff", "branch", "blame",
                    "ls-files", "shortlog", "rev-parse", "grep", "describe"}
FIND_BANNED_FLAGS = {"-delete", "-exec", "-execdir", "-ok", "-okdir",
                     "-fprint", "-fprintf", "-fls"}


def _repo_dir(slot: int):
    repo = _repo_by_slot(slot)
    d = _dir_for(repo)
    if not d.exists():
        raise RepoError("not cloned yet")
    return repo, d


def run_readonly(slot: int, command: str) -> str:
    """Run one whitelisted read-only command inside the repo workspace.
    shell=False (no interpolation); paths must stay inside the repo."""
    _, repo_dir = _repo_dir(slot)
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


def _fallback(slot: int, repo: dict, allow_write: bool, reason: str) -> dict:
    """No LangChain / no Ollama: still useful — surface the deterministic scan."""
    lines = [f"(exploration agent offline — {reason})"]
    try:
        result = repo_scan.scan(slot)
        techs = ", ".join(f"{t['icon']} {t['name']}" for t in result["technologies"])
        lines.append(f"Detected in **{repo['name']}**: {techs or 'no known technologies'}.")
        recs = [r for t in result["technologies"] for r in t["recommendations"]]
        recs += result["general"]
        if recs:
            lines.append("Top recommendations:\n" + "\n".join(f"- {r}" for r in recs[:6]))
        lines.append("Repository root:\n" + run_readonly(slot, "ls"))
    except (RepoError, Exception):  # noqa: BLE001
        pass
    lines.append("Install `langchain` + `langchain-ollama` and configure OLLAMA_URL "
                 "to chat with the repository interactively.")
    return {"reply": "\n\n".join(lines), "steps": [], "engine": "fallback",
            "write_enabled": allow_write}


def run_agent(slot: int, message: str, history: list[dict] | None,
              allow_write: bool) -> dict:
    repo, _ = _repo_dir(slot)

    try:
        from langchain.agents import AgentExecutor, create_tool_calling_agent
        from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
        from langchain_core.tools import tool
        from langchain_ollama import ChatOllama
    except ImportError:
        return _fallback(slot, repo, allow_write,
                         "LangChain is not installed (pip install langchain langchain-ollama)")
    if not ollama_client.available():
        return _fallback(slot, repo, allow_write,
                         f"Ollama is not reachable at {settings.ollama_url}")

    @tool
    def run_command(command: str) -> str:
        """Run ONE read-only exploration command inside the repository, e.g.
        'ls src', 'grep -rn "TODO" src', 'find . -name "*.yaml"',
        'git log -5 --oneline'. Paths are relative to the repo root."""
        return run_readonly(slot, command)

    @tool
    def list_directory(path: str = "") -> str:
        """List one directory of the repository ('' = repo root)."""
        try:
            t = repos_tree(slot, path)
            return "\n".join(f"{'dir ' if e['type'] == 'dir' else 'file'} {e['path']}"
                             for e in t["entries"]) or "(empty directory)"
        except RepoError as exc:
            return f"error: {exc}"

    @tool
    def read_file(path: str) -> str:
        """Read a text file from the repository (max 512KB)."""
        try:
            return repos_read_file(slot, path)["content"]
        except RepoError as exc:
            return f"error: {exc}"

    @tool
    def tech_scan() -> str:
        """Run QuestOps' deterministic technology scan: detected stacks
        plus per-technology and general recommendations."""
        try:
            r = repo_scan.scan(slot)
            out = []
            for t in r["technologies"]:
                out.append(f"{t['name']} (evidence: {', '.join(t['evidence'])})")
                out += [f"  - {rec}" for rec in t["recommendations"]]
            out += [f"general: {g}" for g in r["general"]]
            return "\n".join(out) or "nothing detected"
        except RepoError as exc:
            return f"error: {exc}"

    tools = [run_command, list_directory, read_file, tech_scan]

    if allow_write:
        @tool
        def write_file(path: str, content: str) -> str:
            """Create or overwrite ONE file in the LOCAL repository workspace.
            Nothing is committed or pushed; the team reviews the change as a
            diff on the Repositories page. Always read a file before rewriting it."""
            try:
                repos_write_file(slot, path, content)
                return f"wrote {path} ({len(content.encode())} bytes) — local only, visible as a diff"
            except RepoError as exc:
                return f"error: {exc}"
        tools.append(write_file)

    system = (
        f"You are the QuestOps repository agent for the git repository "
        f"'{repo['name']}' ({repo['url']}), working in a local server-side clone. "
        "ALWAYS inspect real files with your tools before answering; cite file "
        "paths in your answer. Be concise and concrete. "
        + ("Write access is ENABLED: you may create/overwrite files with the "
           "write_file tool. Changes stay in the local workspace — never "
           "committed or pushed — and the user reviews them as diffs."
           if allow_write else
           "Write access is DISABLED: you cannot modify anything. Propose "
           "changes as concrete snippets the user can apply.")
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    llm = ChatOllama(base_url=settings.ollama_url, model=settings.ollama_model,
                     temperature=0.2)
    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools,
                             max_iterations=MAX_ITERATIONS,
                             return_intermediate_steps=True,
                             handle_parsing_errors=True)
    chat_history = [("human" if m.get("role") == "user" else "ai",
                     str(m.get("content", ""))) for m in (history or [])[-8:]]
    try:
        result = executor.invoke({"input": message, "chat_history": chat_history})
    except Exception as exc:  # noqa: BLE001 — model/agent runtime problems
        return _fallback(slot, repo, allow_write, f"agent error: {str(exc)[:200]}")

    steps = [{"tool": action.tool, "input": str(action.tool_input)[:300],
              "output": str(observation)[:1200]}
             for action, observation in result.get("intermediate_steps", [])]
    return {"reply": result.get("output", ""), "steps": steps,
            "engine": "langchain+ollama", "write_enabled": allow_write}
