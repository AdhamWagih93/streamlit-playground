# Trackly Helm chart

Deploy Trackly (FastAPI backend + React/nginx frontend + PostgreSQL) to
Kubernetes. Complements the Docker Compose setup for local use.

## Prerequisites

- Kubernetes 1.23+ and Helm 3.8+
- The Trackly images available to the cluster. Build and push them, e.g.:
  ```bash
  docker build -t <registry>/trackly-backend:1.0.0  jira/backend
  docker build -t <registry>/trackly-frontend:1.0.0 jira/frontend
  docker push <registry>/trackly-backend:1.0.0
  docker push <registry>/trackly-frontend:1.0.0
  ```
  For local clusters you can side-load instead (e.g. `kind load docker-image`,
  `minikube image load`) and leave `image.registry` empty.

## Quick start (demo — bundled PostgreSQL)

```bash
helm install trackly ./jira/deploy/helm/trackly \
  -n trackly --create-namespace \
  --set image.registry=<registry>

# reach it
kubectl -n trackly port-forward svc/trackly-frontend 8080:80
# open http://localhost:8080  (admin@trackly.local / the BOOTSTRAP_ADMIN_PASSWORD secret)
```

The bundled PostgreSQL is for demos only. For real use, point at a managed DB.

## Production

Use the worked example and override what you need:

```bash
helm upgrade --install trackly ./jira/deploy/helm/trackly \
  -n trackly --create-namespace \
  -f jira/deploy/helm/trackly/values-example.yaml
```

That example sets: your registry/tags, an **external PostgreSQL**
(`postgresql.enabled=false` + `externalDatabase.*`), autoscaling, persistence,
and an **ingress with TLS**. Before going live:

- Set a strong `secrets.secretKey` (signs JWTs and encrypts stored
  mail/Jira/LDAP/Entra credentials) — or, better, create the Secret out-of-band
  and reference it (`secrets.create=false`, `secrets.existingSecret=...` with
  keys `SECRET_KEY`, `POSTGRES_PASSWORD`, `BOOTSTRAP_ADMIN_PASSWORD`).
- Change the bootstrap admin password.
- Enable ingress + TLS.

## How it fits together

- **frontend** (nginx) serves the SPA and proxies `/api` + `/health` to the
  **backend** Service. The proxy target is templated from a ConfigMap, so it
  always points at this release's backend — no hardcoded names, CORS-free.
- **backend** (uvicorn) reads non-secret config from a ConfigMap and secrets
  (`SECRET_KEY`, `POSTGRES_PASSWORD`, `BOOTSTRAP_ADMIN_PASSWORD`) from a Secret;
  attachments live on a PVC. On first boot it creates tables, seeds defaults and
  the admin user, and additively reconciles the schema on upgrades.
- **ingress** (optional) routes to the frontend Service.

## Key values

| Key | Default | Description |
|-----|---------|-------------|
| `image.registry` | `""` | Registry prefix for both images |
| `image.backend.repository` / `.tag` | `trackly-backend` / appVersion | Backend image |
| `image.frontend.repository` / `.tag` | `trackly-frontend` / appVersion | Frontend image |
| `secrets.secretKey` | `""` (auto-generated, stable) | JWT signing + credential encryption key |
| `secrets.bootstrapAdminPassword` | `admin` | First-run admin password |
| `postgresql.enabled` | `true` | Bundle an in-cluster PostgreSQL (demo) |
| `externalDatabase.host` | `""` | External DB host (when `postgresql.enabled=false`) |
| `backend.persistence.*` | 5Gi RWO | Attachment storage |
| `backend.autoscaling.enabled` | `false` | HPA for the backend |
| `ingress.enabled` | `false` | Expose via Ingress |

See `values.yaml` for the full set and `values-example.yaml` for a production
configuration.

## Uninstall

```bash
helm uninstall trackly -n trackly
# PVCs are retained by design; delete them to remove data:
kubectl -n trackly delete pvc -l app.kubernetes.io/part-of=trackly
```
