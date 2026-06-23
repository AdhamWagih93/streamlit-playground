# Trackly frontend — build the Vite/React SPA, serve it with nginx.

# --- Stage 1: build the static assets ---------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# Install dependencies first for layer caching. Prefer a reproducible `npm ci`
# (needs package-lock.json); fall back to `npm install` if no lockfile exists.
COPY package*.json ./
RUN npm ci || npm install

# Build the production bundle into /app/dist.
COPY . .
RUN npm run build

# --- Stage 2: serve with nginx ----------------------------------------------
FROM nginx:alpine AS runtime

# SPA routing + API proxy config.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Static assets produced by the build stage.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
