# Build-slice conventions (read this + PLATFORM_SPEC.md fully before coding)

Repo: `/home/adham/new-platform`. FastAPI backend (`backend/`) + React/TS/Vite frontend (`frontend/`).
Everything already boots; you are filling in one feature slice. **Only touch the files your task
assigns you.** Routers and page routes are already registered — replace stub file contents.

## Reference implementations (copy these patterns exactly)

- Backend router: `backend/app/routers/overview.py` (+ `meta.py` for admin-gated)
- Demo provider: `backend/app/providers/demo/overview.py` and `demo/meta.py`
- The demo world (all data comes from here): `backend/app/providers/demo/world.py` —
  read it fully. Use `get_world()`, `scope.visible_apps(user)`, `scope.app_by_name()`.
  If your slice genuinely needs data the world lacks, you may ADD a new builder method +
  field to world.py — append-only, never modify existing fields/semantics (other agents
  depend on them).
- Frontend page: `frontend/src/pages/Overview.tsx`. UI primitives: `frontend/src/components/ui.tsx`
  (Card, Kpi, Chip, Tag, Segmented, Pager, Drawer, SevTiles, HBar, Empty, Spinner).
  Design tokens/classes: `frontend/src/styles/*.css` (use existing classes: .card .btn .tag .chip
  .dt .event-row .console .steps .ai-card .sev .hbar .grid .cols-N .reveal .input .seg …).
  API client: `apiGet/apiPost/apiStream` from `src/lib/api.ts`; session user via `useAuth()`
  (`src/lib/auth.tsx`); formatting via `src/lib/format.ts`.

## Rules

1. **RBAC server-side.** Every endpoint takes `user: User = Depends(current_user)` (or
   `admin_user`). Scope data with `visible_apps(user)`; never trust client filters.
   Role semantics live in `backend/app/auth/rbac.py` — use them, don't reimplement.
2. **Provider split.** Router = thin; logic in `backend/app/providers/demo/<slice>.py`.
   Call via `impl("<slice>")` (see overview router). Function signatures live in the demo
   module; if you also write a live module it must mirror them exactly.
3. **Live mode** (`backend/app/providers/live/<slice>.py`): use shared clients from
   `live/clients.py` (vault_secrets, es(), IDX, pg_conn, jenkins_creds, s3_client,
   safe_ident). Implement what the spec documents (ES indices/fields are in
   PLATFORM_SPEC.md); anything you can't implement faithfully: raise
   `IntegrationUnavailable(name, detail)` — never fake data in live mode.
4. **SSE endpoints** stream via `StreamingResponse` with media_type `text/event-stream`,
   lines `event: <name>\ndata: <json>\n\n`. Frontend consumes with `apiStream()`.
5. **Charts**: use the validated palette vars `--chart-1..5` in fixed order (never cycle,
   never use status colors as series). Bars via `.hbar`/`HBar`. Text never wears series color.
   ≥2 series ⇒ legend. Prefer stat tiles/HBars over exotic charts.
6. **TypeScript strict**: `cd frontend && npx tsc -b` must pass. No new npm/pip deps
   without strong need (react, react-router, tanstack-query, fontsource only).
7. **Verify before finishing**: backend —
   `cd backend && .venv/bin/python -c "from fastapi.testclient import TestClient; from app.main import app; c=TestClient(app); <hit your endpoints, print results>"`;
   frontend — `cd frontend && npx tsc -b && npm run build` (build must succeed).
8. Follow the MERIDIAN look: dark, mono for versions/timestamps (`.mono`, `Tag`),
   uppercase mono kickers, gold reserved for AI/CTA, teal for governance/positive.
   Numbers tabular. Keep pages information-dense but breathable (grid gap 14).
9. Timestamps: backend returns ISO strings (UTC); frontend renders `relTime()`/`fmtDt()`.
10. Your final message: list files written, endpoints added, and paste the verification output.
