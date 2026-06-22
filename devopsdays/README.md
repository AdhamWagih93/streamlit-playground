# Day of a DevOps Engineer · 2026 🛰️

A **DevOpsDays keynote demo suite** — an interactive, dark "mission control" walkthrough of
one ordinary Monday in the life of **Maya, an SRE in 2026**, where the hardest problems are
no longer technical — they're about **trust, sustainability, and working alongside machines
that act on their own.**

Built with **hand-written HTML / CSS / JS** and served from a single ~50 MB nginx container.
**No Streamlit, no build step, no framework** — it just runs.

> 🎤 Audience: DevOps beginners & enthusiasts. Every screen explains itself in plain language.

---

## What's inside

The talk follows Maya's day through **7 interactive stations** + a recap of last year:

| # | Time | Station | The (unspoken) theme |
|---|------|---------|----------------------|
| **ST-01** | 09:15 | **Standup with a Machine** | Working with an agentic AI coworker |
| **ST-02** | 09:40 | **The Guardrail Room** | Autonomous governance, ethics & trust |
| **ST-03** | 11:00 | **The Pulse** | Anti-burnout & human sustainability |
| **ST-04** | 13:30 | **No Blame, Just Signal** | Cultural & psychological safety |
| **ST-05** | 15:00 | **Vibe Shipping** | Vibe engineering & faster cycles |
| **ST-06** | 16:20 | **The New Org Chart** | Skill shift & emerging roles |
| **ST-07** | 17:10 | **The Privacy Membrane** | AI privacy & security |
| 📼 | — | **Previously, in 2025** | Recap: platform eng · on-prem Ollama · Streamlit UIs · MCP · n8n |

Each station is a **real, clickable demo** — approve an agent's risky deploy, watch a burnout
get auto-remediated, flip an incident review from "blame" to "blameless," scan a prompt for
secrets before it reaches a model, and more.

---

## Run it (stage-safe: one container)

```bash
# from this folder
docker compose up --build
# open http://localhost:8080
```

That's the whole talk in one container. One command, nothing to orchestrate live.

### Or without Docker (just a static folder)

```bash
cd site
python3 -m http.server 8080
# open http://localhost:8080
```

…or literally double-click `site/index.html`. It's all static.

### Optional: show real containerization on stage

Bring the **hero demos up as their own containers** to make the "everything is independently
shippable" point physically:

```bash
docker compose --profile split up --build
#   hub        -> http://localhost:8080
#   ST-01      -> http://localhost:8081
#   ST-02      -> http://localhost:8082
#   ST-03      -> http://localhost:8083
#   ST-05      -> http://localhost:8085
```

---

## Presenting it

- **`TALK_SCRIPT.md`** — a full **~20-minute** talk script (the story, what to click, what to say,
  and the line to land at each station, plus the 2025 recap).
- **`DEMO_VOICEOVER.md`** — a tight **~5-minute** voice-over script for recording a screen-capture
  demo (timed, click-by-click, with the recap folded in).

### Recording tips
- Run the single-container version; full-screen the browser, hide the cursor when not clicking.
- Each demo's main action is **one big button** — easy to hit on camera.
- Pages are individually linkable, so you can jump straight to any station for a re-take.
- `Cache-Control: no-store` is set, so a refresh always shows the latest if you tweak copy.

---

## Project layout

```
devopsdays/
├── site/                      # the entire static site (this is what ships)
│   ├── index.html             # Mission Control hub
│   ├── recap/                 # "Previously, in 2025"
│   ├── demos/                 # the 7 stations (one folder each)
│   └── assets/
│       ├── css/system.css     # the shared "mission control" design system
│       └── js/common.js       # shared helpers (clock, log stream, counters)
├── Dockerfile                 # nginx:alpine + the site
├── nginx.conf                 # clean URLs, no-cache for live edits
├── docker-compose.yml         # 1 service by default; `--profile split` for per-demo containers
├── TALK_SCRIPT.md             # 20-min talk
└── DEMO_VOICEOVER.md          # 5-min demo voice-over
```

## Design notes

- **Type:** Chakra Petch (HUD display) · IBM Plex Sans (body) · IBM Plex Mono (telemetry).
- **Palette:** mission-control amber + cyan on near-black, with status green/amber/red.
- **No dependencies** beyond Google Fonts (and it degrades fine offline to system fonts).
- Everything respects `prefers-reduced-motion`.

---

*Last year we talked about the foundation — platform engineering, on-prem models with Ollama,
AI-generated UIs in Streamlit, MCP, and agentic workflows in n8n. This year we live in the
world that foundation built.*
