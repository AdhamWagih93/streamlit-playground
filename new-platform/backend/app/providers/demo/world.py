"""The seeded demo world — one deterministic, internally consistent dataset.

Everything the demo providers serve derives from this module. It is generated once
per process from DEMO_SEED, so restarting the server reproduces the same fleet and
the UI stays stable while developing.

Deliberate stories baked in (so every panel has something to show):
  * payments-gateway v2.14.3 → failed PRD deploy (config-parity incident, #4211-style)
  * legacy-batch-core → legacy app: EOL runtime, direct-DB coupling, no pipeline hooks
  * a handful of git↔ES drift rows, inventory↔Postgres team mismatches, LDAP deltas
  * unauthorized ADO/JIRA grants for the tool-access audit
  * repos without pipelines / missing service hooks for ADO coverage
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ...config import get_settings

ENVS = ["dev", "qc", "uat", "prd"]
STAGES = ["build", "release", "dev", "qc", "uat", "prd"]

TEAMS = [
    "Platform", "Payments", "Cards", "Lending", "Channels", "Onboarding",
    "Treasury", "Risk", "Compliance", "Data", "Integration", "Mobile",
    "Web", "CoreBanking", "Notifications", "Identity", "Reporting", "QA-Central",
]

COMPANIES = ["EFB", "EFH", "Valu", "Tanmeyah", "PayNas", "Bedaya"]

FIRST = ["Adham", "Sara", "Omar", "Nour", "Youssef", "Laila", "Karim", "Dina", "Hassan",
         "Mona", "Tarek", "Aya", "Mostafa", "Rania", "Sherif", "Heba", "Ali", "Nadine",
         "Mahmoud", "Salma", "Amr", "Yasmin", "Khaled", "Farida", "Ziad"]
LAST = ["Meshhal", "Hassan", "Ibrahim", "Fahmy", "ElSayed", "Mansour", "Kamel", "Adel",
        "Ghanem", "Sami", "Fawzy", "Nassar", "Refaat", "Sharaf", "Zaki", "Amin",
        "Lotfy", "Badr", "Selim", "Hegazy"]

BUILD_TECH = ["maven", "gradle", "npm", "dotnet", "python-wheel", "go"]
DEPLOY_TECH = ["helm", "kubectl", "ansible", "docker-compose"]
PLATFORMS = ["ocp", "k8s", "vm", "iis"]
APP_TYPES = ["microservice", "web", "api", "batch", "gateway", "worker"]

APP_WORDS_A = ["payments", "cards", "loans", "kyc", "ledger", "billing", "notify", "auth",
               "customer", "account", "transfer", "fx", "collect", "score", "docs", "report",
               "audit", "limits", "fees", "wallet", "merchant", "settle", "recon", "onboard",
               "statement", "dispute", "fraud", "token", "session", "consent"]
APP_WORDS_B = ["gateway", "service", "api", "engine", "core", "hub", "manager", "processor",
               "orchestrator", "adapter", "portal", "worker", "sync", "bridge", "store"]

SCHEMES = ["http", "https", "grpc", "postgresql", "oracle", "kafka", "amqp", "ldap", "sftp", "smtp"]

DOC_CATEGORIES = [
    ("Architecture & design", 412), ("Runbooks & operations", 231),
    ("Engineering standards", 96), ("BRDs & functional specs", 518),
    ("Onboarding guides", 127),
]

NOW = datetime.now(timezone.utc)


@dataclass
class App:
    application: str
    project: str
    company: str
    app_type: str
    build_technology: str
    deploy_technology: str
    deploy_platform: str
    repository_name: str
    build_image_name: str = ""
    build_image_tag: str = ""
    deploy_image_name: str = ""
    deploy_image_tag: str = ""
    teams: dict = field(default_factory=dict)          # *_team -> [names]
    namespaces: dict = field(default_factory=dict)     # env -> ns
    is_legacy: bool = False
    # stage -> {"version","status","when","by"}
    stages: dict = field(default_factory=dict)
    next_versions: dict = field(default_factory=dict)  # branch -> next version


@dataclass
class Person:
    username: str
    display_name: str
    email: str
    title: str
    department: str
    company: str
    manager: str
    teams: list = field(default_factory=list)
    when_created: datetime = NOW
    when_changed: datetime = NOW


class World:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.apps: list[App] = []
        self.people: list[Person] = []
        self.events: list[dict] = []          # unified event log rows
        self.scans: dict = {}                 # (scanner, app, version) -> counts dict
        self.incidents: list[dict] = []
        self.ado: dict = {}                   # coverage dataset
        self.drift: dict = {}                 # sync-check datasets
        self.tool_access: list[dict] = []
        self.architecture: dict = {}          # env -> {app -> [connections]}
        self.history_jobs: dict = {}          # index_key -> migration job state (mutable)
        self._build()

    # ------------------------------------------------------------------ helpers
    def _ver_chain(self, n: int) -> list[str]:
        r = self.rng
        maj, mi, pa = r.randint(1, 3), r.randint(0, 9), r.randint(0, 9)
        out = []
        for _ in range(n):
            out.append(f"{maj}.{mi}.{pa}")
            bump = r.random()
            if bump < 0.72:
                pa += 1
            elif bump < 0.94:
                mi, pa = mi + 1, 0
            else:
                maj, mi, pa = maj + 1, 0, 0
        return out

    def _person_name(self, i: int) -> tuple[str, str]:
        r = self.rng
        f, l = FIRST[i % len(FIRST)], LAST[(i * 7 + i // len(FIRST)) % len(LAST)]
        return f"{f} {l}", f"{f.lower()}.{l.lower()}{'' if i < len(FIRST) * len(LAST) else i}"

    def _when(self, days_back_max: int = 365) -> datetime:
        # Recent-biased timestamps so "last 7d" windows are lively.
        r = self.rng
        days = min(r.expovariate(1 / 30.0), days_back_max)
        return NOW - timedelta(days=days, hours=r.uniform(0, 24), minutes=r.uniform(0, 60))

    # ------------------------------------------------------------------ build
    def _build(self):
        r = self.rng
        self._build_people()
        self._build_apps()
        self._build_events_and_stages()
        self._build_scans()
        self._build_incidents()
        self._build_architecture()
        self._build_ado()
        self._build_drift()
        self._build_tool_access()
        self._build_history_jobs()
        self.events.sort(key=lambda e: e["when"], reverse=True)

    def _build_people(self):
        r = self.rng
        titles = ["Software Engineer", "Senior Software Engineer", "Staff Engineer", "QC Engineer",
                  "Senior QC Engineer", "DevOps Engineer", "SRE", "Team Lead", "Engineering Manager",
                  "Business Analyst", "Product Owner"]
        depts = ["Engineering", "Quality", "Operations", "Platform", "Product"]
        for i in range(152):
            name, uname = self._person_name(i)
            n_teams = 1 if r.random() < 0.8 else 2
            teams = r.sample(TEAMS, n_teams)
            self.people.append(Person(
                username=uname,
                display_name=name,
                email=f"{uname}@corp.example.com",
                title=r.choice(titles),
                department=r.choice(depts),
                company=r.choice(COMPANIES),
                manager=self._person_name(r.randrange(0, 40))[0] if i > 40 else "",
                teams=teams,
                when_created=NOW - timedelta(days=r.randint(30, 2200)),
                when_changed=NOW - timedelta(days=r.randint(0, 400)),
            ))

    def _build_apps(self):
        r = self.rng
        n_projects = 20
        used_names: set[str] = set()
        projects = []
        proj_words = ["Atlas", "Nile", "Horus", "Sphinx", "Luxor", "Delta", "Ivory", "Cairo",
                      "Giza", "Aswan", "Sinai", "Karnak", "Memphis", "Rosetta", "Thebes",
                      "Alex", "Fayoum", "Dahab", "Siwa", "Nubia"]
        for i in range(n_projects):
            projects.append({
                "project": proj_words[i],
                "company": r.choice(COMPANIES),
                "dev_team": r.choice(TEAMS),
                "qc_team": r.choice(["QA-Central", r.choice(TEAMS)]),
                "ops_team": r.choice(TEAMS),
            })
        for p in projects:
            for _ in range(r.randint(3, 9)):
                for _try in range(30):
                    name = f"{r.choice(APP_WORDS_A)}-{r.choice(APP_WORDS_B)}"
                    if name not in used_names:
                        used_names.add(name)
                        break
                platform = r.choice(PLATFORMS)
                app = App(
                    application=name,
                    project=p["project"],
                    company=p["company"],
                    app_type=r.choice(APP_TYPES),
                    build_technology=r.choice(BUILD_TECH),
                    deploy_technology=r.choice(DEPLOY_TECH) if platform in ("ocp", "k8s") else "ansible",
                    deploy_platform=platform,
                    repository_name=name,
                    build_image_name=f"registry.corp/{p['project'].lower()}/{name}",
                    build_image_tag="{version}",
                    deploy_image_name=f"registry.corp/{p['project'].lower()}/{name}",
                    deploy_image_tag="{version}",
                    teams={
                        "dev_team": [p["dev_team"]],
                        "qc_team": [p["qc_team"]],
                        "uat_team": [p["ops_team"]],
                        "prd_team": [p["ops_team"]],
                    },
                    namespaces={e: f"{p['project'].lower()}-{e}" for e in ENVS}
                    if platform in ("ocp", "k8s") else {},
                )
                # ~7% of fields intentionally unset → "fully specified %" isn't 100
                if r.random() < 0.07:
                    app.build_technology = ""
                if r.random() < 0.05:
                    app.deploy_platform = ""
                self.apps.append(app)

        # The two story apps, pinned into the Payments project sphere.
        pay = next((x for x in projects if x["dev_team"] == "Payments"), projects[0])
        self.apps.append(App(
            application="payments-gateway", project=pay["project"], company=pay["company"],
            app_type="gateway", build_technology="maven", deploy_technology="helm",
            deploy_platform="ocp", repository_name="payments-gateway",
            build_image_name=f"registry.corp/{pay['project'].lower()}/payments-gateway",
            build_image_tag="{version}",
            deploy_image_name=f"registry.corp/{pay['project'].lower()}/payments-gateway",
            deploy_image_tag="{version}",
            teams={"dev_team": ["Payments"], "qc_team": ["QA-Central"],
                   "uat_team": ["CoreBanking"], "prd_team": ["CoreBanking"]},
            namespaces={e: f"{pay['project'].lower()}-{e}" for e in ENVS},
        ))
        self.apps.append(App(
            application="legacy-batch-core", project=pay["project"], company=pay["company"],
            app_type="batch", build_technology="ant", deploy_technology="ansible",
            deploy_platform="vm", repository_name="legacy-batch-core",
            teams={"dev_team": ["CoreBanking"], "qc_team": ["QA-Central"],
                   "uat_team": ["CoreBanking"], "prd_team": ["CoreBanking"]},
            is_legacy=True,
        ))

    def _build_events_and_stages(self):
        r = self.rng
        people_by_team: dict[str, list[Person]] = {}
        for pr in self.people:
            for t in pr.teams:
                people_by_team.setdefault(t, []).append(pr)

        def someone(team: str) -> Person:
            pool = people_by_team.get(team) or self.people
            return r.choice(pool)

        eid = 1000
        for app in self.apps:
            dev_team = (app.teams.get("dev_team") or ["Platform"])[0]
            qc_team = (app.teams.get("qc_team") or ["QA-Central"])[0]
            ops_team = (app.teams.get("prd_team") or ["Platform"])[0]
            n_vers = r.randint(2, 8) if not app.is_legacy else 1
            versions = self._ver_chain(n_vers)
            base_when = NOW - timedelta(days=r.randint(60, 330))
            step = timedelta(days=r.uniform(12, 45))
            for vi, ver in enumerate(versions):
                t0 = base_when + step * vi
                if t0 > NOW:
                    break
                is_latest = vi == len(versions) - 1
                dev_p, qc_p, ops_p = someone(dev_team), someone(qc_team), someone(ops_team)

                # commits
                for _ in range(r.randint(1, 5)):
                    t0 += timedelta(hours=r.uniform(1, 20))
                    self.events.append(dict(
                        id=(eid := eid + 1), type="commit", app=app.application,
                        project=app.project, company=app.company, version=ver,
                        status="ok", when=t0, user=dev_p.display_name, email=dev_p.email,
                        detail=f"{r.choice(['fix', 'feat', 'chore', 'refactor'])}: "
                               f"{r.choice(['handle timeout', 'add validation', 'bump deps', 'improve logging', 'tune pool size', 'new endpoint'])}",
                        env="", branch=r.choice(["develop", "develop", "release"]),
                    ))
                # build (develop then release)
                for branch in (["develop", "release"] if r.random() < 0.8 else ["release"]):
                    t0 += timedelta(hours=r.uniform(0.5, 6))
                    ok = r.random() > 0.08
                    self.events.append(dict(
                        id=(eid := eid + 1), type=f"build-{branch}", app=app.application,
                        project=app.project, company=app.company, version=ver,
                        status="ok" if ok else "failed", when=t0, user=dev_p.display_name,
                        email=dev_p.email, detail=f"Build {branch} #{r.randint(100, 999)}",
                        env="", branch=branch, duration_s=int(r.uniform(90, 900)),
                    ))
                    if ok and branch == "release":
                        app.stages["build"] = dict(version=ver, status="ok", when=t0.isoformat(),
                                                   by=dev_p.display_name)
                # release record
                t0 += timedelta(hours=r.uniform(0.2, 3))
                self.events.append(dict(
                    id=(eid := eid + 1), type="release", app=app.application,
                    project=app.project, company=app.company, version=ver, status="ok",
                    when=t0, user=qc_p.display_name, email=qc_p.email,
                    detail=f"Release {ver} cut from release branch", env="", branch="release",
                ))
                app.stages["release"] = dict(version=ver, status="ok", when=t0.isoformat(),
                                             by=qc_p.display_name)

                # deploy chain dev→qc→uat→prd; later envs less likely for latest version
                reach = 4 if not is_latest else r.choices([1, 2, 3, 4], weights=[15, 25, 25, 35])[0]
                for env_i, env in enumerate(ENVS[:reach]):
                    t0 += timedelta(hours=r.uniform(2, 48))
                    if t0 > NOW:
                        break
                    actor = dev_p if env == "dev" else qc_p if env == "qc" else ops_p
                    # request then deploy for gated envs
                    if env in ("uat", "prd"):
                        self.events.append(dict(
                            id=(eid := eid + 1), type="request", app=app.application,
                            project=app.project, company=app.company, version=ver,
                            status="approved", when=t0 - timedelta(hours=1),
                            user=actor.display_name, email=actor.email,
                            detail=f"Deploy request → {env.upper()} approved", env=env, branch="",
                        ))
                    fail = r.random() < (0.06 if env != "prd" else 0.09)
                    self.events.append(dict(
                        id=(eid := eid + 1), type="deploy", app=app.application,
                        project=app.project, company=app.company, version=ver,
                        status="failed" if fail else "ok", when=t0, user=actor.display_name,
                        email=actor.email,
                        detail=f"Deploy {ver} → {env.upper()}"
                               + (" — rollout failed, auto-rollback" if fail else ""),
                        env=env, branch="", reason=r.choice(["Upgrade", "Upgrade", "Redeployment",
                                                             "ConfigChange"]),
                    ))
                    if not fail:
                        app.stages[env] = dict(version=ver, status="ok", when=t0.isoformat(),
                                               by=actor.display_name)
                    else:
                        break

            # next-version lookup per branch
            last = versions[-1]
            maj, mi, pa = (int(x) for x in last.split("."))
            app.next_versions = {
                "develop": f"{maj}.{mi}.{pa + 1}",
                "release": f"{maj}.{mi + 1}.0",
                "hotfix": f"{maj}.{mi}.{pa + 1}",
                "stress": f"{maj}.{mi}.{pa + 1}-stress",
            }

        # ---- the payments-gateway PRD failure story (config parity) ----------
        gw = next(a for a in self.apps if a.application == "payments-gateway")
        story_ver = "2.14.3"
        t = NOW - timedelta(hours=6)
        actors = [p for p in self.people if "CoreBanking" in p.teams] or self.people
        ops = r.choice(actors)
        gw.stages["build"] = dict(version=story_ver, status="ok",
                                  when=(t - timedelta(hours=30)).isoformat(), by="Sara Hassan")
        gw.stages["release"] = dict(version=story_ver, status="ok",
                                    when=(t - timedelta(hours=28)).isoformat(), by="Nour Ibrahim")
        for env, hrs in (("dev", 26), ("qc", 20), ("uat", 12)):
            gw.stages[env] = dict(version=story_ver, status="ok",
                                  when=(t - timedelta(hours=hrs)).isoformat(), by=ops.display_name)
            self.events.append(dict(
                id=(eid := eid + 1), type="deploy", app=gw.application, project=gw.project,
                company=gw.company, version=story_ver, status="ok",
                when=t - timedelta(hours=hrs), user=ops.display_name, email=ops.email,
                detail=f"Deploy {story_ver} → {env.upper()}", env=env, branch="", reason="Upgrade",
            ))
        self.events.append(dict(
            id=(eid := eid + 1), type="request", app=gw.application, project=gw.project,
            company=gw.company, version=story_ver, status="approved", when=t - timedelta(hours=2),
            user=ops.display_name, email=ops.email,
            detail="Deploy request → PRD approved", env="prd", branch="",
        ))
        self.events.append(dict(
            id=(eid := eid + 1), type="deploy", app=gw.application, project=gw.project,
            company=gw.company, version=story_ver, status="failed", when=t,
            user=ops.display_name, email=ops.email,
            detail=f"Deploy {story_ver} → PRD — readiness probe 503, retried ×3, auto-rollback",
            env="prd", branch="", reason="Upgrade",
        ))
        # PRD still on previous version
        gw.stages["prd"] = dict(version="2.14.1", status="ok",
                                when=(NOW - timedelta(days=9)).isoformat(), by=ops.display_name)

    def _build_scans(self):
        r = self.rng
        for app in self.apps:
            seen = {s.get("version") for s in app.stages.values() if s}
            # sorted: set iteration order depends on string-hash randomization and would
            # make scan counts differ across process restarts
            for ver in sorted(v for v in seen if v):
                for scanner in ("prismacloud", "invicti", "zap", "trufflehog"):
                    heavy = app.is_legacy or r.random() < 0.12
                    base = dict(
                        critical=r.randint(1, 6) if heavy else (1 if r.random() < 0.08 else 0),
                        high=r.randint(2, 14) if heavy else r.randint(0, 4),
                        medium=r.randint(4, 30), low=r.randint(5, 60),
                        status="ok", when=(NOW - timedelta(days=r.uniform(0, 20))).isoformat(),
                    )
                    if scanner == "trufflehog":
                        base = dict(critical=0, high=1 if (heavy and r.random() < 0.5) else 0,
                                    medium=r.randint(0, 2), low=r.randint(0, 3),
                                    status="ok", when=base["when"])
                    self.scans[(scanner, app.application, ver)] = base

    def _build_incidents(self):
        fails = [e for e in self.events if e["type"] == "deploy" and e["status"] == "failed"]
        fails.sort(key=lambda e: e["when"], reverse=True)
        for i, e in enumerate(fails[:24]):
            self.incidents.append(dict(
                id=4211 + i * 7,
                app=e["app"], project=e["project"], company=e["company"],
                version=e["version"], env=e["env"], when=e["when"].isoformat(),
                failed_stage="rollout", retries=3, auto_rollback=True,
                requester=e["user"], status="open" if i < 3 else "analyzed",
            ))

    def _build_architecture(self):
        r = self.rng
        for env in ENVS:
            model = {}
            for app in self.apps:
                if env not in app.stages and not app.is_legacy:
                    continue
                conns = []
                # db
                if r.random() < 0.75 or app.is_legacy:
                    conns.append(dict(target=f"{app.project.lower()}-{r.choice(['ledger', 'core', 'ops'])}-db",
                                      kind="db", scheme=r.choice(["postgresql", "oracle"]),
                                      endpoint=f"db-{env}.corp:5432"))
                # queue
                if r.random() < 0.35:
                    conns.append(dict(target="events-broker", kind="queue", scheme="kafka",
                                      endpoint=f"kafka-{env}.corp:9092"))
                # peer services (same project)
                peers = [a for a in self.apps if a.project == app.project
                         and a.application != app.application]
                for peer in r.sample(peers, min(len(peers), r.randint(0, 3))):
                    conns.append(dict(target=f"{peer.application}-service", kind="service",
                                      scheme=r.choice(["http", "grpc"]),
                                      endpoint=f"{peer.application}-service.{app.project.lower()}-{env}:8080"))
                if r.random() < 0.25:
                    conns.append(dict(target="corp-ldap", kind="ldap", scheme="ldap",
                                      endpoint="ldap.corp:389"))
                model[app.application] = dict(
                    project=app.project, connections=conns,
                    provenance=dict(
                        commit=f"{r.getrandbits(40):010x}", author=r.choice(self.people).display_name,
                        commit_date=(NOW - timedelta(days=r.uniform(1, 90))).isoformat(),
                        deployed_version=(app.stages.get(env) or {}).get("version", ""),
                        is_head=r.random() < 0.8,
                    ),
                )
            self.architecture[env] = model
        # story: legacy writes straight to ledger DB in prd; dev env points at prd DB (repeated URL)
        prd = self.architecture["prd"]
        if "legacy-batch-core" in prd:
            prd["legacy-batch-core"]["connections"] = [
                dict(target="ledger-db", kind="db", scheme="oracle",
                     endpoint="ledger-db-prd.corp:1521"),
                dict(target="reporting-replica", kind="db", scheme="oracle",
                     endpoint="reporting-prd.corp:1521"),
            ]
        dev = self.architecture["dev"]
        if "payments-gateway" in dev and "payments-gateway" in prd:
            leak = dict(target="ledger-db", kind="db", scheme="postgresql",
                        endpoint="db-prd.corp:5432")  # same endpoint as PRD → repeated-URL warning
            dev["payments-gateway"]["connections"].append(leak)
            prd["payments-gateway"]["connections"].append(dict(leak))

    def _build_ado(self):
        r = self.rng
        repos, orphans = [], []
        required = ["develop", "release", "hotfixes", "stress"]
        for app in self.apps:
            pipelined = not app.is_legacy and r.random() > 0.07
            hooks = required.copy()
            if pipelined and r.random() < 0.15:
                hooks = r.sample(required, r.randint(1, 3))
            team = (app.teams.get("dev_team") or [""])[0]
            if r.random() < 0.06:
                team = r.choice(TEAMS)  # team mismatch story
            repos.append(dict(
                collection="DefaultCollection", project=app.project, repo=app.repository_name,
                app=app.application, pipelined=pipelined, hooks=hooks if pipelined else [],
                ado_team=team, azure_pipeline=r.random() < 0.04,
            ))
        for i in range(9):
            orphans.append(dict(collection="DefaultCollection",
                                project=r.choice([a.project for a in self.apps]),
                                repo=f"poc-{r.choice(APP_WORDS_A)}-{i}", app=None,
                                pipelined=False, hooks=[], ado_team=r.choice(TEAMS),
                                azure_pipeline=False))
        self.ado = dict(collections=1, projects=len({a.project for a in self.apps}),
                        repos=repos + orphans, required_hooks=required)

    def _build_drift(self):
        r = self.rng
        apps = r.sample(self.apps, 6)
        self.drift["inventory_es"] = dict(
            only_git=[f"{r.choice(APP_WORDS_A)}-poc" for _ in range(2)],
            only_es=[apps[0].application],
            field_diffs=[
                dict(app=apps[1].application, field="deploy_platform",
                     git=apps[1].deploy_platform or "ocp", es="k8s"),
                dict(app=apps[2].application, field="qc_team",
                     git=", ".join(apps[2].teams.get("qc_team", [])), es="QA-Central-Old"),
                dict(app=apps[3].application, field="build_technology",
                     git=apps[3].build_technology or "maven", es="gradle"),
            ],
            last_run=(NOW - timedelta(minutes=r.randint(4, 50))).isoformat(),
        )
        projects = sorted({a.project for a in self.apps})
        self.drift["postgres"] = dict(
            only_inventory=[projects[1]],
            only_postgres=["RetiredProj"],
            team_diffs=[dict(project=projects[2], field="dev_team",
                             inventory="Payments", postgres="Cards")],
            ops_inconsistent=[dict(project=projects[3], uat_team="CoreBanking",
                                   prd_team="Treasury", preprod_team="")],
            last_run=(NOW - timedelta(minutes=r.randint(4, 50))).isoformat(),
        )
        self.drift["ldap"] = dict(
            last_sync=(NOW - timedelta(hours=r.randint(1, 20))).isoformat(),
            status="success", users=len(self.people), teams=len(TEAMS),
            added_users=[self.people[-1].display_name],
            removed_users=["Old Contractor"],
            field_changes=[dict(user=self.people[3].display_name, field="title",
                                old="Software Engineer", new="Senior Software Engineer")],
            added_memberships=[dict(user=self.people[5].display_name, team="Payments")],
            removed_memberships=[dict(user=self.people[8].display_name, team="Risk")],
        )

    def _build_tool_access(self):
        r = self.rng
        tools = ["ADO", "JIRA", "Jenkins"]
        for p in r.sample(self.people, 60):
            for tool in r.sample(tools, r.randint(1, 2)):
                proj = r.choice(self.apps)
                # ~10% grants point at a project the person's team doesn't own → audit finding
                self.tool_access.append(dict(
                    user=p.display_name, email=p.email, team=p.teams[0], tool=tool,
                    project=proj.project,
                    privilege=r.choice(["read", "contribute", "admin"]),
                    is_active=True,
                    last_updated=(NOW - timedelta(days=r.randint(1, 200))).isoformat(),
                ))

    def _build_history_jobs(self):
        r = self.rng
        indices = ["inventory", "versions", "commits", "jira", "requests", "builds",
                   "deployments", "releases", "prismacloud", "invicti", "zap",
                   "trufflehog", "devops_projects", "tools_access"]
        for k in indices:
            total = r.randint(4_000, 900_000)
            done = int(total * r.choice([1, 1, 1, 0.62, 0.31, 0]))
            self.history_jobs[k] = dict(
                index_key=k, es_index=f"ef-cicd-{k}", table=f"history_es_{k}",
                total=total, migrated=done,
                status="done" if done == total else ("idle" if done == 0 else "paused"),
                mode="full", is_lookup=k in ("inventory", "devops_projects"),
                error="", updated=(NOW - timedelta(hours=r.uniform(1, 90))).isoformat(),
            )


_world: World | None = None


def get_world() -> World:
    global _world
    if _world is None:
        _world = World(get_settings().demo_seed)
    return _world
