"""AI slice — demo provider (incident analysis + knowledge assistant).

Streams are canned-but-parameterized: the staging/timing is scripted for demo
effect, but every number woven into the console (event counts in the failure
window, release history, connection targets) is measured from the demo world.
"""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from ...auth.rbac import User
from ...config import get_settings
from .scope import visible_app_names
from .world import DOC_CATEGORIES, get_world

DOC_TOTAL = 1384

# In-memory audit log of assistant exchanges (demo: process lifetime).
_CHAT_LOG: list[dict] = []


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _line(text: str, tone: str = "dim") -> dict:
    return {"text": text, "tone": tone}


# ---------------------------------------------------------------- incidents
def _is_story(inc: dict) -> bool:
    """The baked-in payments-gateway config-parity incident."""
    return inc["app"] == "payments-gateway" and inc["version"] == "2.14.3" and inc["env"] == "prd"


def _template(inc: dict) -> str:
    """Deterministic verdict template per incident (seeded by id)."""
    return random.Random(inc["id"]).choice(["config", "registry", "pool"])


def _headline(inc: dict) -> str:
    if _is_story(inc):
        return "Readiness probe 503 after rollout — retried ×3, auto-rollback to 2.14.1"
    env = inc["env"].upper()
    return {
        "config": f"Startup validation failed in {env} — rollout aborted, auto-rollback",
        "registry": f"ImagePullBackOff during {env} rollout — image never pulled, auto-rollback",
        "pool": f"Readiness probes timed out in {env} — rollout unhealthy, auto-rollback",
    }[_template(inc)]


def incidents(user: User) -> list[dict]:
    names = visible_app_names(user)
    return [{**i, "headline": _headline(i)} for i in get_world().incidents if i["app"] in names]


def _evidence(w, inc: dict) -> dict:
    """Real measurements from the world woven into the console + verdict."""
    app, env = inc["app"], inc["env"]
    t_fail = datetime.fromisoformat(inc["when"])
    win = timedelta(minutes=30)
    ev_count = sum(1 for e in w.events if e["app"] == app and abs(e["when"] - t_fail) <= win)
    rel_count = sum(1 for e in w.events if e["app"] == app and e["type"] == "release")
    a = next((x for x in w.apps if x.application == app), None)
    prev = ""
    if a:
        st = a.stages.get(env) or a.stages.get("prd") or {}
        prev = st.get("version", "")
    # A real connection target from the architecture model (pool template).
    target, endpoint = "primary datastore", "db.corp:5432"
    for e2 in (env, "prd", "uat", "qc", "dev"):
        entry = (w.architecture.get(e2) or {}).get(app)
        if entry and entry["connections"]:
            conns = [c for c in entry["connections"] if c["kind"] in ("db", "service")] \
                or entry["connections"]
            target, endpoint = conns[0]["target"], conns[0]["endpoint"]
            break
    image = (a.build_image_name if a and a.build_image_name
             else f"registry.corp/{inc['project'].lower()}/{app}")
    return dict(ev_count=ev_count, rel_count=rel_count, prev=prev,
                target=target, endpoint=endpoint, image=image)


def analyze_incident(user: User, incident_id: int):
    w = get_world()
    inc = next((i for i in w.incidents if i["id"] == incident_id), None)
    if not inc or inc["app"] not in visible_app_names(user):
        raise HTTPException(status_code=404, detail="Incident not found or out of scope")
    return _analysis_stream(w, inc)


def _analysis_stream(w, inc: dict):
    rng = random.Random(inc["id"] * 31 + 7)
    ev = _evidence(w, inc)
    story = _is_story(inc)
    tpl = "story" if story else _template(inc)
    app, ver, envu = inc["app"], inc["version"], inc["env"].upper()
    model = get_settings().docchat_model
    confidence = 0.93 if story else round(rng.uniform(0.87, 0.95), 2)

    # ---- step 1: rollout logs -----------------------------------------------
    s1_run = [_line(f"$ evidence pull --incident {inc['id']} --source orchestrator --app {app}")]
    if tpl == "story":
        s1_done = [
            _line(f"rollout {app}:{ver} → PRD: 2 replicas started, strict config validation FAILED", "err"),
            _line("readiness probe /actuator/health → 503 (3 attempts) · orchestrator engaged auto-rollback", "err"),
        ]
    elif tpl == "config":
        s1_done = [
            _line(f"rollout {app}:{ver} → {envu}: startup validation FAILED — missing required key", "err"),
            _line("readiness gate never passed · retried ×3 · auto-rollback engaged", "err"),
        ]
    elif tpl == "registry":
        s1_done = [
            _line(f"pods for {app}:{ver} stuck in ImagePullBackOff on 3/3 nodes", "err"),
            _line(f"pull {ev['image']}:{ver} → 401 Unauthorized from registry.corp", "err"),
        ]
    else:  # pool
        s1_done = [
            _line(f"rollout {app}:{ver} → {envu}: containers healthy, readiness probes timing out", "err"),
            _line("probe timeout 5s ×3 · rollout marked unhealthy · auto-rollback engaged", "err"),
        ]

    # ---- step 2: correlate platform events (real count) ----------------------
    s2_run = [_line(f"$ search events --app {app} --window ±30m @ {inc['when'][:16]}Z")]
    s2_done = [
        _line(f"{ev['ev_count']} platform event(s) for {app} in the ±30 min window around the failure", "ok"),
        _line("failure correlates with the deploy request chain · no unrelated change events in window"),
    ]

    # ---- step 3: config diff (per-team config repos) -------------------------
    s3_run = [_line(f"$ config diff --app {app} --envs uat,prd --repo config/{inc['project'].lower()}")]
    if tpl == "story":
        s3_done = [
            _line(f"uat/{app}.yaml ⇄ prd/{app}.yaml — 41 keys compared"),
            _line("payment.provider.fallback_url → present in UAT · MISSING in PROD", "err"),
            _line("1 config-parity gap flagged", "gold"),
        ]
    elif tpl == "config":
        key = rng.choice(["FEATURE_FLAGS_URL", "OTEL_EXPORTER_ENDPOINT", "provider.timeout_ms",
                          "queue.dlq.name", "cache.redis.url"])
        inc["_key"] = key  # reuse in verdict
        s3_done = [
            _line(f"upstream config ⇄ {inc['env']}/{app}.yaml — {rng.randint(24, 52)} keys compared"),
            _line(f"{key} → present upstream · MISSING in {envu}", "err"),
            _line("1 config-parity gap flagged", "gold"),
        ]
    elif tpl == "registry":
        s3_done = [_line(f"configs identical across environments — no drift for {app}", "ok")]
    else:
        s3_done = [_line(f"configs identical — pool ceiling {rng.choice([20, 32, 40])} conns in both environments", "ok")]

    # ---- step 4: release history (real count) --------------------------------
    s4_run = [_line(f"$ history releases --app {app}")]
    if tpl == "story":
        s4_done = [
            _line(f"{ev['rel_count']} release(s) on record for {app} · PRD held at last-good 2.14.1"),
            _line("no failed PRD rollouts across the previous 5 releases", "ok"),
        ]
    else:
        s4_done = [
            _line(f"{ev['rel_count']} release(s) on record for {app} · last good {envu} version {ev['prev'] or 'n/a'}"),
        ]

    # ---- step 5: reasoning ----------------------------------------------------
    s5_run = [_line(f"⟡ {model} · reasoning over 4 evidence sources — on-prem, zero data egress", "gold")]
    s5_done = [_line(f"verdict ready · confidence {confidence:.2f}", "gold")]

    steps = [
        ("Pulling rollout logs — pipeline orchestrator", s1_run, s1_done),
        ("Correlating platform events — search store (±30 min window)", s2_run, s2_done),
        ("Diffing environment configs — per-team config repos (UAT vs PROD)", s3_run, s3_done),
        ("Checking release history", s4_run, s4_done),
        ("Reasoning over evidence — on-prem model", s5_run, s5_done),
    ]

    verdict = _verdict(inc, tpl, ev, confidence, rng)

    def gen():
        t0 = time.monotonic()
        for i, (label, run_lines, done_lines) in enumerate(steps, start=1):
            yield _sse("step", {"index": i, "label": label, "status": "running",
                                "console_lines": run_lines})
            time.sleep(rng.uniform(0.35, 0.8))
            yield _sse("step", {"index": i, "label": label, "status": "done",
                                "console_lines": done_lines})
            time.sleep(rng.uniform(0.35, 0.55))
        verdict["duration_s"] = round(time.monotonic() - t0, 1)
        yield _sse("verdict", verdict)
        time.sleep(0.4)
        yield _sse("done", {"audit": "analysis attached to incident record · audited"})
        inc["status"] = "analyzed"

    return gen()


def _verdict(inc: dict, tpl: str, ev: dict, confidence: float, rng: random.Random) -> dict:
    app, ver, envu = inc["app"], inc["version"], inc["env"].upper()
    if tpl == "story":
        return dict(
            root_cause=(
                "Release 2.14.3 added strict startup validation for the payment-provider failover "
                "path, which requires the config key `payment.provider.fallback_url`. That key was "
                "introduced in the UAT config repo during testing but never promoted to the PROD "
                "repo, so the service failed validation at boot and its readiness probe returned "
                "503 until the platform auto-rolled back after three retries. This is a "
                "configuration-parity gap between environments, not a code defect — the identical "
                "artifact passed cleanly in UAT."
            ),
            confidence=confidence, evidence_sources=4,
            actions=[
                "Promote `payment.provider.fallback_url` to the PROD config repo (mirror the UAT "
                "entry with the PROD provider endpoint) and redeploy 2.14.3.",
                "Add a config-parity promotion gate: block PROD deploy requests while UAT-only "
                "keys exist for the app.",
                "Backfill a config lint step in the build pipeline so newly required keys fail "
                "fast at build time, not at rollout.",
            ],
            impact="No customer impact — auto-rollback restored 2.14.1 in under two minutes; "
                   "PROD stayed on the last-good version.",
            mttr_estimate="≈15 min — config promote + redeploy",
        )
    if tpl == "config":
        key = inc.get("_key", "FEATURE_FLAGS_URL")
        return dict(
            root_cause=(
                f"The rollout of {app} {ver} to {envu} aborted at startup: required configuration "
                f"key `{key}` exists in the lower-environment config repo but was never promoted "
                f"to {envu}. Strict validation stopped the boot sequence and the platform rolled "
                f"the deployment back automatically. Evidence points to a config promotion gap "
                f"rather than a code regression — the artifact is identical to the one that "
                f"passed downstream."
            ),
            confidence=confidence, evidence_sources=4,
            actions=[
                f"Promote `{key}` to the {envu} config repo and redeploy {ver}.",
                "Add a config-parity promotion gate that blocks requests while upstream-only keys exist.",
                "Backfill a config lint step in the pipeline so required keys fail fast at build time.",
            ],
            impact=f"Bounded — {envu} remained on {ev['prev'] or 'the previous version'} throughout; "
                   "no traffic was served by the failed replicas.",
            mttr_estimate="≈20 min — promote key + redeploy",
        )
    if tpl == "registry":
        return dict(
            root_cause=(
                f"Nodes in {envu} could not pull `{ev['image']}:{ver}` — the registry pull-secret "
                f"for the namespace had expired, so kubelet received 401 Unauthorized and backed "
                f"off into ImagePullBackOff until the rollout was rolled back. The build artifact "
                f"itself is intact and pulled successfully in lower environments; this is an "
                f"environment credential-rotation failure, not an application fault."
            ),
            confidence=confidence, evidence_sources=4,
            actions=[
                f"Re-issue the registry pull-secret for the {envu} namespace from Vault and re-run "
                f"the deployment of {ver}.",
                "Automate pull-secret rotation ahead of expiry via the platform credentials job.",
                "Alert on registry-credential age so expiring secrets surface before a rollout "
                "depends on them.",
            ],
            impact=f"Bounded — no failed pods served traffic; {envu} continued on "
                   f"{ev['prev'] or 'the previous version'}.",
            mttr_estimate="≈10 min — rotate secret + redeploy",
        )
    return dict(  # pool
        root_cause=(
            f"During the {envu} rollout of {app} {ver}, old and new replica sets ran side by side "
            f"and together exhausted the connection pool toward {ev['target']} "
            f"({ev['endpoint']}). Readiness probes could not obtain a connection, timed out, and "
            f"the rollout was declared unhealthy and rolled back. The ceiling on concurrent "
            f"connections — not the release itself — is the limiting factor."
        ),
        confidence=confidence, evidence_sources=4,
        actions=[
            f"Raise the connection-pool ceiling toward {ev['target']} (or enable probe connection "
            f"reuse) and redeploy {ver}.",
            "Set rollout surge to one replica at a time so peak concurrent connections stay under "
            "the pool limit.",
            f"Add a saturation alert on the {ev['target']} pool so exhaustion is visible before "
            f"rollouts fail.",
        ],
        impact="Degraded headroom during the rollout window only — existing replicas kept "
               "serving; auto-rollback restored steady state.",
        mttr_estimate="≈25 min — tune pool + staged redeploy",
    )


# ---------------------------------------------------------------- assistant
def assistant_sources(user: User) -> dict:
    return {
        "categories": [{"name": n, "count": c} for n, c in DOC_CATEGORIES],
        "total": DOC_TOTAL,
        "grounded": True,
        "model": get_settings().docchat_model,
    }


def assistant_stats(user: User) -> dict:
    return {"questions_this_month": 1240 + len(_CHAT_LOG), "teams": 18}


_DEV_ANSWER = (
    "payments-gateway authenticates to ledger-service with mutual TLS brokered by the identity "
    "broker. At startup the gateway presents its workload certificate to the broker and receives "
    "a short-lived, audience-scoped service token (TTL 15 minutes); both the certificate and the "
    "token rotate automatically, so no static credentials live in config or code.\n\n"
    "Per engineering standard ENG-SEC-04, direct database access to the ledger schema is "
    "prohibited — every read and write must go through ledger-service's API. The only sanctioned "
    "exception is the reconciliation batch, which uses a read-only replica under a separate "
    "grant.\n\n"
    "If you are wiring a new consumer: request identity-broker onboarding from the Platform team, "
    "then reuse the svc-auth Helm library — it mounts the client certificate and handles token "
    "refresh for you."
)
_DEV_CITES = ["ENG-SEC-04 Service-to-service auth", "payments-gateway/architecture.md",
              "identity-broker runbook"]


def _analyst_answer(w) -> tuple[str, list[str]]:
    loans = next((a.application for a in w.apps if a.application.startswith("loans")), "loans-engine")
    notify = next((a.application for a in w.apps if a.application.startswith("notify")), "notify-hub")
    text = (
        "Draft scope — Instalment payments (BRD skeleton, grounded in the current payments and "
        "lending stack):\n\n"
        "In scope\n"
        "• Instalment plan creation at checkout via payments-gateway (3 / 6 / 12-month tenors)\n"
        f"• Credit decisioning through the existing {loans} scoring flow\n"
        "• Repayment scheduling and collection with retries and grace periods\n"
        f"• Customer notifications on plan events (created, due, missed) via {notify}\n\n"
        "Out of scope\n"
        "• Merchant-financed instalments and revenue-share settlement (phase 2)\n"
        "• Early-settlement fee waivers — pending Compliance ruling\n"
        "• Back-office restructuring tools for delinquent plans\n\n"
        "Dependencies\n"
        "• ledger-service double-entry postings for plan principal and fees\n"
        "• Risk team sign-off on the revised exposure model\n"
        "• Regulatory disclosure templates from Compliance\n\n"
        "Flagged gaps\n"
        "• Current BRDs do not define behaviour when a card expires mid-plan — needs a decision\n"
        "• No agreed SLA between decisioning latency and the checkout timeout budget "
        "(gateway end-to-end budget is 8s)"
    )
    cites = ["BRD-2214 Instalment payments (draft)", f"{loans} functional spec",
             "payments-gateway/architecture.md", "Compliance — consumer lending checklist"]
    return text, cites


_TESTER_ANSWER = (
    "Regression suite — payments-gateway release (core paths + new-in-release):\n\n"
    "1. Authorization happy path — standard card purchase settles end-to-end; ledger posting "
    "matches amount and currency.\n"
    "2. Idempotency — replay the same payment request with the same idempotency key; exactly one "
    "charge is created.\n"
    "3. Provider failover (new in release) — primary provider returns 5xx; traffic shifts to the "
    "fallback URL (`payment.provider.fallback_url`) with no customer-visible error.\n"
    "4. Timeout budget — provider latency at 7.9s completes; at 8.1s the gateway aborts cleanly "
    "and no orphan authorization remains.\n"
    "5. Refund flow — full and partial refunds post compensating ledger entries and update the "
    "settlement report.\n"
    "6. Currency edge cases — zero-decimal (JPY) and 3-decimal (KWD) currencies round per "
    "ISO 4217.\n"
    "7. Auth token rotation — force identity-broker token expiry mid-session; in-flight requests "
    "retry transparently after refresh.\n"
    "8. Rollback safety — deploy the release, roll back to the previous version, confirm config "
    "and schema stay backward-compatible and traffic is clean.\n\n"
    "Suggested split: 1–5 in the API regression pack; 6–8 staged in the UAT pipeline gate."
)
_TESTER_CITES = ["payments-gateway test plan", "Release notes — payments-gateway 2.14.x",
                 "QA regression playbook"]


def _generic_answer(w, question: str) -> tuple[str, list[str]]:
    rng = random.Random(len(question) * 131 + sum(map(ord, question[:32])))
    pool = [a for a in w.apps if not a.is_legacy]
    a1, a2 = rng.sample(pool, 2)
    team = (a1.teams.get("dev_team") or ["Platform"])[0]
    text = (
        f"Grounded summary from the internal corpus: the closest indexed material sits in the "
        f"{a1.project} project space, around {a1.application} and {a2.application}. "
        f"{a1.application} is a {a1.app_type} ({a1.build_technology or 'maven'} build, deployed "
        f"with {a1.deploy_technology} on {a1.deploy_platform or 'ocp'}), owned by the {team} "
        f"team; its architecture notes and runbook cover interfaces, health endpoints and the "
        f"rollback procedure. {a2.application} is documented in the same collection alongside "
        f"its BRD and functional spec.\n\n"
        f"If you narrow the question to a specific service, standard or environment I can quote "
        f"the exact section — the citations below are the highest-relevance grounded sources "
        f"for this query."
    )
    cites = [f"{a1.application}/architecture.md", f"{a1.application} runbook",
             f"BRD index — {a1.project}", "Engineering standards index"]
    return text, cites


def _route_answer(persona: str, question: str) -> tuple[str, list[str]]:
    w = get_world()
    ql = question.lower()
    if persona == "developer" and any(k in ql for k in
                                      ("auth", "ledger", "mtls", "token", "credential", "connect")):
        return _DEV_ANSWER, _DEV_CITES
    if persona == "analyst" and any(k in ql for k in
                                    ("instalment", "installment", "brd", "scope", "requirement")):
        return _analyst_answer(w)
    if persona == "tester" and any(k in ql for k in
                                   ("regression", "test", "suite", "release", "case")):
        return _TESTER_ANSWER, _TESTER_CITES
    return _generic_answer(w, question)


def assistant_chat(user: User, messages: list[dict], persona: str):
    question = next((m.get("content", "") for m in reversed(messages)
                     if m.get("role") == "user"), "")
    text, cites = _route_answer(persona, question)
    _CHAT_LOG.append({
        "username": user.username,
        "persona": persona,
        "when": datetime.now(timezone.utc).isoformat(),
        "question": question[:300],
    })

    def gen():
        rng = random.Random()
        for chunk in re.findall(r"\S+\s*", text):
            yield _sse("token", {"text": chunk})
            time.sleep(rng.uniform(0.02, 0.05))
        yield _sse("citations", {"documents": cites})
        yield _sse("done", {"audit": "answer grounded in internal corpus · exchange audited"})

    return gen()
