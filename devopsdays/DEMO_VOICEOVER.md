# 5-Minute Demo — Voice-Over Script

**Target: 5:00 · ~660 words · pace ≈ 130 wpm (calm, confident)**

Every scene is one page; the AI scenes each have one big gold button.

Silent recording aids (nothing about them appears on screen):

- **1–9** — jump straight to a scene · **← / →** — previous / next scene
- **Enter** — run the page's main action (Analyze / Ask / Discover)
- **H** — hide/show the sidebar · **T** — hide/show the top bar (both persist
  across pages; the company logo stays on screen when the sidebar is hidden)
- **Esc** — restore both bars instantly; small edge handles also appear when a
  bar is hidden — click to restore

The demo is branded with the e-finance DevOps Portal logo and called
**"DevOps Platform"** (both set in `site/assets/js/platform.js` → `BRAND`).

---

## SCENE 1 — Intro (0:00 – 0:20) · key `1`

> Screen: Intro page. Title and speaker card reveal on load. Hold; no clicking.

**VO:**

"Hi, I'm Adham — a DevOps engineer. Today I want to show you how our Internal
Developer Platform evolved into an AI-powered DevOps Platform — without giving
up governance or security."

---

## SCENE 2 — Last Year (0:20 – 0:50) · key `2`

> Screen: Last Year page. The five foundation cards reveal on load; the
> epilogue line lands last. Deliver the last sentence slowly.

**VO:**

"A quick word about where we left off. Last year at DevOpsDays we shared five
foundations: platform engineering… models hosted on our own infrastructure…
internal tools generated with AI… MCP servers that expose our systems as
tools AI agents can call… and agentic workflows.

Looking back… they were never five projects. They were one platform, waiting
to happen."

---

## SCENE 3 — The Platform (0:50 – 1:30) · key `3` (Overview)

> Screen: Overview page. Let the counters animate and the event stream tick,
> hover slowly over the fleet table, then the security posture table.

**VO:**

"This is the platform. The single source of truth for every application,
environment, pipeline and team — over a thousand registered pipelines, more
than thirty teams, four governed environments from dev to production.

And the applications are not all the same: every one brings its own build
technology, its own deployment target, its own business line. A JVM payments
gateway on containers, a legacy batch system on virtual machines — the
platform shows them side by side without forcing them into one mold.

Security is ambient here too: every build is scanned for vulnerabilities, the
findings sit right next to the versions they belong to, and nothing with a
critical finding reaches production. Even the work items are here — every
issue linked to the builds, deployments and incidents it belongs to.

Just as important: each team sees only its own scope. A Digital Channels
engineer never sees Core Banking's world — governed, and on our own
infrastructure."

---

## SCENE 4 — Embedded AI (1:30 – 1:50) · key `4`

> Screen: Embedded AI page. The three capability cards and governance strip
> reveal on load. Hold; no clicking.

**VO:**

"A platform that governs everything also *knows* everything. So this year, we
taught it to think.

We didn't build another chatbot next to the platform — we embedded AI inside
the workflows it already governs. Three services: a knowledge assistant,
architecture discovery, and incident analysis. Same access control, same
audit trail, same boundary. Let me show you each one."

---

## SCENE 5 — Knowledge Assistant (1:50 – 2:40) · key `5`

> Screen: Assistant page. Note the identity chip in the header and the
> personalised greeting. The question is pre-filled — press **Enter** to ask.
> While the answer streams, gesture at the citations.

**VO:**

"Let's start where engineers spend most of their day — questions. And notice:
the assistant already knows who I am. I never told it — the platform detected
me from the identity directory. I'm Adham, a DevOps engineer, so every answer
is scoped to exactly what my role is entitled to see.

And here's the interesting part: the documentation it answers from is written
by the platform itself — an agent reads each application's source code and
generates its docs, so they never go stale. I ask how the payments gateway
authenticates to the ledger, and the answer comes back in seconds — with
sources: every answer cites the exact documents it came from. Engineers can
verify, not just trust.

Why not a public chatbot? Because these documents are confidential. Here,
nothing leaves the building, and every exchange is audited. And it's not only
for engineers like me: a business analyst drafts a BRD section; a tester
generates a regression suite. One knowledge source — every persona, each
inside their own borders."

---

## SCENE 6 — Architecture Discovery (2:40 – 3:25) · key `6`

> Screen: Architecture page. Press **Enter** and let the graph draw itself
> while you speak. Point at the red edge when it appears.

**VO:**

"My favourite one. Every enterprise has systems nobody fully remembers —
usually the ones running the business.

The platform already governs the source code and configuration, so we let the
AI read them. It scans the repositories, extracts services, data stores and
dependencies, and reconstructs the architecture — from source, not from a
diagram someone drew three years ago.

Then it finds what's wrong. That red line? A legacy batch system writing
straight into the ledger database, bypassing every service contract, on a
runtime that left support long ago. Nobody had documented that. The platform
found it — and gave us a baseline architecture for every legacy system we
own."

---

## SCENE 7 — AI Incident Analysis (3:25 – 4:10) · key `7`

> Screen: Incident page. Read the failure card first, then press **Enter**
> and narrate over the animation.

**VO:**

"And when something breaks anyway? It's Monday morning, and a production
deployment of the payments gateway has just failed.
Normally an engineer would spend the better part of an hour across four
different systems — rollout logs, platform events, config repos, release
notes.

Instead — one click.

The platform pulls the rollout logs… correlates the events around the
failure… diffs the environment configuration between UAT and production…
checks what changed in this release… and the on-prem model reasons over all of
that evidence together.

And there's the answer: this isn't a code bug. A configuration key was added
to UAT but never promoted to production. Root cause, three recommended actions
— including a new promotion gate so this never happens again — and the whole
analysis is logged to the incident record."

---

## SCENE 8 — Today (4:15 – 4:45) · key `8`

> Screen: Today page. The improvement cards and lessons reveal on load.

**VO:**

"Put all of these features in one platform, and here's what they result in:
root cause in minutes, not meetings. New engineers find answers on day one.
Confidential documents work with AI safely, because the model runs inside the
company. And our legacy systems are mapped from the source code itself."

---

## SCENE 9 — Closing (4:45 – 5:00) · key `9`

> Screen: Closing page. One statement on screen; hold it and deliver slowly.

**VO:**

"A solid Internal Developer Platform already holds your enterprise's context —
the code, the configs, the events, the people. And context is what turns AI
into services engineers actually use.

That's the *what*. In the talk, we'll go through the *how* — the principles
and approaches that got us here. Thank you — see you at DevOpsDays Cairo."

---

## Recording workflow (one clean take per scene)

1. **Serve the site**: `docker compose up -d --build` → open
   `http://localhost:8181` in a clean browser profile, **F11 full-screen**,
   100% zoom, 1920×1080 display.
2. **Record** with OBS Studio (free): Display Capture + your mic. Or record
   screen and voice separately and align in the editor — easier to fix stumbles.
3. Record **one scene per take**. Refresh before each take to reset animations.
4. Do a silent "click-through rehearsal" take first — the animations are timed
   so each scene's action finishes comfortably inside its slot.
5. Mic tips: quiet room, phone on silent, 10 cm from the mic, read the script
   twice out loud before recording. Keep sentences as written — they're timed.
6. Stitch the nine takes in any editor (DaVinci Resolve is free) with simple
   cuts — no transitions needed; the scene changes read as deliberate.
