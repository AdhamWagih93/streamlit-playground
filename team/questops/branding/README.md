# Branding

Drop your company logo here as **`logo.png`** — that's all.

- docker-compose mounts this folder at `/app/branding` (read-only), so the file is
  picked up automatically: sidebar, login card, and the printed Projects report.
- No `.env` change is needed. `QO_COMPANY_LOGO` is only for a logo that lives
  somewhere else (give a path that exists *inside the container*, or a path
  relative to this `questops/` folder).
- Restart the `app` container after adding/replacing the file.
- `GET /api/branding` tells you which paths were checked and which one is used.

PNG is expected; `.svg`, `.jpg`, `.jpeg`, `.webp` also work.
