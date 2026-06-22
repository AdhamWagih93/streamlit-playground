# Day of a DevOps Engineer · 2026 — demo suite
# Tiny, stage-safe static site served by nginx. No build step, no Streamlit.
FROM nginx:1.27-alpine

LABEL org.opencontainers.image.title="DevOps Engineer 2026 — Mission Control demos"
LABEL org.opencontainers.image.description="DevOpsDays keynote demo suite (hand-written HTML/CSS/JS)"

# Custom nginx config (clean URLs, no-cache so live edits show instantly on stage)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# The whole site
COPY site/ /usr/share/nginx/html/

EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1
