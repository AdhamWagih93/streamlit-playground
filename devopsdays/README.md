# From Platform Engineering to AI Engineering — DevOpsDays Cairo demo

A **5-minute, screen-recordable demo** of an Internal Developer Platform that
evolved into an AI-powered Engineering Platform. Static HTML/CSS/JS — no build,
no backend, nothing can fail on camera.

Grounded in the real platform: a governed delivery fleet (source-of-truth
inventory, pipeline orchestration, per-team config repos, RBAC, vault-brokered
integrations) with **on-prem AI embedded into the workflows** — incident
analysis, a doc-grounded knowledge assistant, and architecture discovery.
All components are described **by role, never by tool name**.

## The nine scenes (= the 5-minute video)

| Key | Page | Scene | The one action |
|-----|------|-------|----------------|
| `1` | `intro.html` | Title + speaker intro | ambient — staggered reveal |
| `2` | `lastyear.html` | DevOpsDays 2025 recap — the five foundations | ambient |
| `3` | `index.html` | Platform overview (stacks, business lines, borders) | ambient — counters, live event stream |
| `4` | `ai.html` | AI — embedded, not bolted on | ambient — capability cards |
| `5` | `assistant.html` | Knowledge assistant — identity auto-detected | **Ask ▸** (or Enter) |
| `6` | `architecture.html` | AI architecture discovery | **⌬ Discover architecture** (or Enter) |
| `7` | `incident.html` | AI incident analysis | **✦ Analyze with AI** (or Enter) |
| `8` | `value.html` | Today — what actually got better | ambient |
| `9` | `closing.html` | Closing statement + talk teaser | ambient — hold the line |

Silent recording aids: **1–9** jump to a scene · **← / →** previous / next
scene · **Enter** runs the page's
main action · **H** hides the sidebar (the company logo stays on screen) ·
**T** hides the top bar · **Esc** restores both (H/T persist across pages).

## Run it

```bash
docker compose up -d --build
# open http://localhost:8181
```

The `site/` folder is live-mounted read-only into the container, so copy tweaks
show up on refresh — no rebuild needed (`Cache-Control: no-store` is set).

No Docker? It's fully static: `cd site && python3 -m http.server <port>`, or
double-click `site/index.html`. Google Fonts are the only external asset and it
degrades gracefully offline.

## Branding

The sidebar shows the **e-finance DevOps Portal** logo
(`site/assets/img/efdevopslogo.png`, on a white plate) and the platform is
called **DevOps Platform** — both set in `site/assets/js/platform.js` →
`BRAND`. Colors live as CSS variables at the top of
`site/assets/css/platform.css` (gold `--gold`, teal `--teal`).

## Record it

Word-for-word timed script + recording workflow: **`DEMO_VOICEOVER.md`**.

## Structure

```
devopsdays/
├── site/
│   ├── intro.html          # scene 1 — title + speaker
│   ├── lastyear.html       # scene 2 — 2025 recap, five foundations
│   ├── index.html          # scene 3 — overview
│   ├── ai.html             # scene 4 — AI embedded, not bolted on
│   ├── assistant.html      # scene 5 — knowledge assistant
│   ├── architecture.html   # scene 6 — architecture discovery
│   ├── incident.html       # scene 7 — AI incident analysis
│   ├── value.html          # scene 8 — today
│   ├── closing.html        # scene 9 — closing statement + teaser
│   └── assets/{css,js}/    # shared design system + shell
├── Dockerfile              # nginx:alpine + the site
├── nginx.conf              # no-store caching for live edits
├── docker-compose.yml      # serves on :8181, live-mounts site/
├── DEMO_VOICEOVER.md       # timed 5-min script + recording tips
└── README.md
```

*The 20-minute talk breakdown (for young engineers / fresh grads) comes later —
this demo's five scenes are designed to expand 1:4 into that talk.*
