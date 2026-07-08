# MERIDIAN — Engineering Platform

High-performance rebuild of the CI/CD Command Center (formerly a single-file Streamlit app)
as a **React + TypeScript** frontend and a **FastAPI** backend — same capabilities, same
external integrations, plus the AI services from the DevOpsDays vision (incident analysis,
knowledge assistant, architecture discovery), all governed by the same RBAC model.

## Run locally (demo data, no auth)

```bash
./run-local.sh
# frontend → http://localhost:5173   (backend on :8000, proxied)
```

Requires Python ≥3.10 and Node ≥20. No external services needed: `DATA_MODE=demo` serves a
seeded, internally consistent fleet (~120 apps, 20 projects, 18 teams, a year of events,
scan results, a failed-PRD-deploy incident story, a legacy system, drift rows, …).
`AUTH_MODE=none` auto-signs you in; click your avatar (top right) to preview the app as
Admin / CLevel / Developer / QC / Operations — RBAC scoping is enforced server-side.

## Integrations model

**The only external integration configured via `.env` is PostgreSQL** — the platform's
own database (docker-compose bundles `postgres:17`, or point `DATABASE_URL` at an
existing instance). Everything else — Elasticsearch, Jenkins, Azure DevOps, S3/MinIO,
LDAP directory, Ollama, Vault — is configured inside the app at **Settings →
Integrations** (admin-only) and stored **encrypted at rest** (Fernet; key from
`SETTINGS_ENCRYPTION_KEY`) in that database. Each integration card shows a connection
test, enable/disable, and exactly which features it powers.

Every page carries an **integration strip** showing which integrations that page's
features require, which are missing, and what breaks without them — with a one-click
path to Settings. In demo mode the strip shows what live mode will need; in live mode
missing integrations are flagged red and their endpoints return a clear 503 pointing
at Settings — never silent demo data.

## Deploy (live integrations, real auth)

```bash
cp .env.example .env   # set POSTGRES_PASSWORD (or DATABASE_URL), auth + secrets
docker compose up -d --build
# → http://host:8080 — then Settings → Integrations to connect your systems
```

- `AUTH_MODE=entra` — OIDC auth-code flow against Entra ID (tenant/client/secret/redirect
  from env; group→role JSON map).
- `AUTH_MODE=ldap` — direct bind + group→role mapping (login form).
- The container **refuses** `AUTH_MODE=none` unless `ALLOW_INSECURE_NO_AUTH=true`.

## Deploy to Kubernetes (Helm + Gateway API, HA)

```bash
# build & push the image, then:
helm upgrade --install meridian deploy/helm/meridian \
  --namespace meridian --create-namespace \
  --set image.repository=registry.corp/platform/meridian \
  --set image.tag=1.0.0 \
  --set existingSecret=meridian-secrets \
  --set gatewayApi.hostnames='{meridian.corp.example.com}' \
  --set gatewayApi.parentRefs[0].name=corp-gateway \
  --set gatewayApi.parentRefs[0].namespace=gateway-system
```

The chart (`deploy/helm/meridian`) ships HA by default:
- **3 replicas** behind an HPA (3→8 on CPU/memory), rolling updates with
  `maxUnavailable: 0`, PodDisruptionBudget `minAvailable: 2`
- **Spreading**: hard anti-affinity across nodes, soft spread across zones
- **Gateway API**: an `HTTPRoute` bound to your existing `Gateway` (TLS at the
  listener); the `/api/ai` prefix gets `timeouts.request: 0s` so SSE streams
  (incident analysis, assistant, discovery) are never cut off mid-stream
- **Hardened pods**: non-root (uid 10001), read-only root FS (writable emptyDirs for
  `/tmp` and repo clones), no privilege escalation, `automountServiceAccountToken: false`
- Config via ConfigMap; secrets via your own `existingSecret` (external-secrets /
  sealed-secrets friendly). The app itself refuses to start with `AUTH_MODE=none`
  in-cluster. The app is stateless in live mode, so replicas scale freely.

## Architecture

```
backend/
  app/main.py                 FastAPI app + SPA static serving
  app/config.py               all env-driven settings
  app/auth/                   session JWT · RBAC model · none/entra/ldap flows
  app/routers/<slice>.py      thin HTTP layer (RBAC enforced here)
  app/providers/demo/         seeded world + per-slice demo logic
  app/providers/live/         real integrations (Vault-brokered ES/PG/Jenkins/ADO/S3/Ollama)
frontend/
  src/components/             MERIDIAN design system (lapis night + gold)
  src/pages/                  one page per platform area
docs/PLATFORM_SPEC.md         the rebuild contract (features, RBAC, endpoints)
```

Slices: overview · fleet inventory · events · actions (pipeline triggers) · security
(Prisma/Invicti/ZAP/TruffleHog) · AI incidents · AI assistant · architecture (+discovery) ·
tech & platforms · teams & members · people insights · governance (sync checks, ADO
coverage, history→PG migration, tool-access audit, glossary).

## Why it's fast where Streamlit wasn't

The old app re-ran a 37k-line script top-to-bottom on every interaction. Here every panel
is an independent component fetching a scoped JSON endpoint (cached client-side via
TanStack Query), streaming panels use SSE, and polling only runs while something is
actually in flight (e.g. migration jobs tick 2s only when a job is running).
