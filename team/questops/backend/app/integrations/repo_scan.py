"""Deterministic technology detection + recommendations for repo workspaces.

Pure marker-file / content checks — no AI, so results are instant and
repeatable. The repo agent builds on top of this for the chatty part."""

from pathlib import Path

from .repos import RepoError, _dir_for, _repo_by_slot

MAX_FILES = 4000
MAX_PROBE_BYTES = 64 * 1024  # content probes only read small config-ish files
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
             "build", "target", ".idea", ".vscode", ".terraform", "vendor"}


def _walk(root: Path) -> list[Path]:
    files: list[Path] = []
    stack = [root]
    while stack and len(files) < MAX_FILES:
        d = stack.pop()
        try:
            children = sorted(d.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for p in children:
            if p.is_dir():
                if p.name not in SKIP_DIRS:
                    stack.append(p)
            elif len(files) < MAX_FILES:
                files.append(p)
    return files


def _read(p: Path) -> str:
    try:
        if p.stat().st_size > MAX_PROBE_BYTES:
            return ""
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _rel(root: Path, paths: list[Path], cap: int = 6) -> list[str]:
    return [str(p.relative_to(root)) for p in paths[:cap]]


def scan(slot: int, username: str | None = None) -> dict:
    """Detect technologies and return per-tech + general recommendations.
    Scans the member's worktree when they have one (their edits count),
    else the server copy."""
    repo = _repo_by_slot(slot)
    root = _dir_for(repo)
    if username:
        from .repos import _safe_user, _worktree_root
        wt = _worktree_root(repo) / _safe_user(username)
        if wt.exists():
            root = wt
    if not root.exists():
        raise RepoError("not cloned yet")

    files = _walk(root)
    names = {p.name for p in files}
    rels = {str(p.relative_to(root)) for p in files}
    by_name = lambda *ns: [p for p in files if p.name in ns]  # noqa: E731
    by_suffix = lambda *sx: [p for p in files if p.suffix in sx]  # noqa: E731
    techs: list[dict] = []

    def tech(key: str, name: str, icon: str, evidence: list[Path], recs: list[str]):
        if evidence:
            techs.append({"key": key, "name": name, "icon": icon,
                          "evidence": _rel(root, evidence),
                          "recommendations": [r for r in recs if r]})

    # ---- languages / package managers ----
    py_markers = by_name("pyproject.toml", "requirements.txt", "setup.py", "Pipfile")
    py_files = by_suffix(".py")
    if py_markers or len(py_files) >= 2:
        recs = []
        if "requirements.txt" in names and "pyproject.toml" not in names:
            recs.append("adopt pyproject.toml (PEP 621) as the single source of project metadata")
        if not ({"requirements.txt", "poetry.lock", "Pipfile.lock", "uv.lock",
                 "requirements.lock"} & names) and "pyproject.toml" in names:
            recs.append("no lockfile found — pin dependencies (uv.lock / poetry.lock) for reproducible builds")
        if not ({"ruff.toml", ".ruff.toml", ".flake8", "setup.cfg", "tox.ini"} & names) \
                and not any("[tool.ruff]" in _read(p) for p in by_name("pyproject.toml")):
            recs.append("add a linter config (ruff) so style is enforced in CI, not in review comments")
        tech("python", "Python", "🐍", py_markers or py_files[:3], recs)

    node = by_name("package.json")
    if node:
        recs = []
        if not ({"package-lock.json", "yarn.lock", "pnpm-lock.yaml"} & names):
            recs.append("commit a lockfile (package-lock.json / pnpm-lock.yaml) for reproducible installs")
        if not any('"engines"' in _read(p) for p in node):
            recs.append('pin the Node version with an "engines" field (and .nvmrc) to avoid works-on-my-machine drift')
        tech("node", "Node.js", "🟩", node, recs)

    java = by_name("pom.xml", "build.gradle", "build.gradle.kts")
    if java:
        recs = []
        if not ({"gradlew", "mvnw"} & names):
            recs.append("commit the gradle/maven wrapper (gradlew/mvnw) so builds don't depend on host tooling")
        tech("java", "Java (Maven/Gradle)", "☕", java, recs)

    go = by_name("go.mod")
    if go:
        tech("go", "Go", "🐹", go,
             ["run govulncheck in CI to catch vulnerable module versions"]
             if "go.sum" in names else
             ["go.sum is missing — commit it for verifiable dependencies"])

    # ---- containers ----
    dockerfiles = [p for p in files if p.name == "Dockerfile"
                   or p.name.startswith("Dockerfile.")]
    if dockerfiles:
        recs = []
        content = "\n".join(_read(p) for p in dockerfiles)
        froms = [l.strip() for l in content.splitlines()
                 if l.strip().upper().startswith("FROM ")]
        if any(":latest" in f or (":" not in f.split()[1] if len(f.split()) > 1 else True)
               for f in froms):
            recs.append("pin base image tags (FROM image:1.2.3) — ':latest'/untagged images make builds unrepeatable")
        if "USER " not in content.upper():
            recs.append("containers run as root — add a USER instruction (least privilege)")
        if "HEALTHCHECK" not in content.upper():
            recs.append("add a HEALTHCHECK so orchestrators can detect a wedged container")
        if ".dockerignore" not in names:
            recs.append("add a .dockerignore — build context is shipping everything (incl. .git) to the daemon")
        tech("docker", "Docker", "🐳", dockerfiles, recs)

    compose = by_name("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml")
    if compose:
        tech("compose", "Docker Compose", "🧩", compose,
             ["pin image tags in compose services for reproducible environments"]
             if any(":latest" in _read(p) for p in compose) else [])

    # ---- kubernetes / helm ----
    charts = by_name("Chart.yaml")
    if charts:
        recs = []
        values = by_name("values.yaml", "values.yml")
        vcontent = "\n".join(_read(p) for p in values)
        if values and "resources" not in vcontent:
            recs.append("values.yaml sets no resources — add requests/limits so the scheduler can protect the node")
        elif values and "limits" not in vcontent:
            recs.append("resource requests without limits — add limits to prevent noisy-neighbour blowups")
        if not any(p.name == "NOTES.txt" for p in files):
            recs.append("add templates/NOTES.txt so installers get post-deploy instructions")
        tech("helm", "Helm", "⎈", charts, recs)
    else:
        k8s = [p for p in by_suffix(".yaml", ".yml")
               if "apiVersion" in (c := _read(p)) and "kind:" in c
               and "helm" not in str(p).lower()][:5]
        if k8s:
            tech("kubernetes", "Kubernetes manifests", "☸", k8s,
                 ["consider packaging raw manifests as a helm chart or kustomize base for per-env overrides"])

    # ---- CI / IaC / config mgmt ----
    jenkinsfiles = [p for p in files if p.name == "Jenkinsfile"
                    or p.name.startswith("Jenkinsfile.")]
    if jenkinsfiles:
        recs = []
        jcontent = "\n".join(_read(p) for p in jenkinsfiles)
        if "agent any" in jcontent:
            recs.append("Jenkinsfile uses 'agent any' — pin a label so builds land on suitable nodes")
        if "timeout(" not in jcontent and "timeout {" not in jcontent:
            recs.append("no pipeline timeout — add options { timeout(...) } so hung builds self-terminate")
        tech("jenkins", "Jenkins pipeline", "⚙", jenkinsfiles, recs)

    gha = [p for p in files if ".github/workflows" in str(p.relative_to(root))]
    if gha:
        tech("gha", "GitHub Actions", "🐙", gha,
             ["pin third-party actions to a commit SHA, not a mutable tag"])
    gitlab = by_name(".gitlab-ci.yml")
    if gitlab:
        tech("gitlab", "GitLab CI", "🦊", gitlab, [])

    tf = by_suffix(".tf")
    if tf:
        recs = []
        tfcontent = "\n".join(_read(p) for p in tf[:20])
        if 'backend "' not in tfcontent:
            recs.append("no remote backend configured — local state files don't survive laptops; use a shared backend with locking")
        if ".terraform.lock.hcl" not in names:
            recs.append("commit .terraform.lock.hcl to pin provider versions")
        tech("terraform", "Terraform", "🏗", tf, recs)

    ansible = by_name("ansible.cfg") or [
        p for p in by_suffix(".yml", ".yaml")
        if "hosts:" in _read(p) and ("tasks:" in _read(p) or "roles:" in _read(p))][:3]
    if ansible:
        tech("ansible", "Ansible", "📜", ansible,
             ["run ansible-lint in CI; keep secrets in vault files, never in plain vars"])

    sh = by_suffix(".sh")
    if sh:
        tech("shell", "Shell scripts", "🐚", sh[:4],
             ["run shellcheck in CI — shell bugs are silent until 3am"])

    # ---- general hygiene ----
    general: list[str] = []
    if not any(n.lower().startswith("readme") for n in names):
        general.append("no README — add one: what this repo is, how to run it, who owns it")
    if ".gitignore" not in names:
        general.append("no .gitignore — build artifacts and editor files will creep into history")
    has_tests = any("test" in part.lower()
                    for p in files for part in p.relative_to(root).parts) \
        or any(n.startswith("test_") or n.endswith(("_test.py", ".test.js", "_test.go"))
               for n in names)
    if not has_tests:
        general.append("no tests detected — even a smoke test stops the worst regressions")
    if not (jenkinsfiles or gha or gitlab):
        general.append("no CI pipeline found (Jenkinsfile / workflows) — nothing guards main")
    if "CODEOWNERS" not in names and ".github/CODEOWNERS" not in rels:
        general.append("no CODEOWNERS — reviews depend on tribal knowledge")

    return {"repo": repo["name"], "files_scanned": len(files),
            "truncated": len(files) >= MAX_FILES,
            "technologies": techs, "general": general}
