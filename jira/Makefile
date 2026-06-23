# Trackly — convenience targets around docker compose.
# Usage examples:
#   make up
#   make logs
#   make migrate PROJECTS=ENG
#   make psql

COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help up down build rebuild logs ps migrate migrate-test migrate-list \
        backend-shell psql seed test

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Build (if needed) and start the full stack in the background
	$(COMPOSE) up --build -d

down: ## Stop and remove containers (keeps named volumes)
	$(COMPOSE) down

build: ## Build all images
	$(COMPOSE) build

rebuild: ## Rebuild all images from scratch (no cache)
	$(COMPOSE) build --no-cache

logs: ## Tail logs from all services
	$(COMPOSE) logs -f

ps: ## Show running services
	$(COMPOSE) ps

# --- Jira migration ----------------------------------------------------------
migrate: ## Run the Jira import. Pass PROJECTS=KEY1,KEY2 (omit to import all)
	$(COMPOSE) run --rm migrator run $(if $(PROJECTS),--projects $(PROJECTS),)

migrate-test: ## Verify Jira credentials
	$(COMPOSE) run --rm migrator test-connection

migrate-list: ## List Jira projects visible to the configured account
	$(COMPOSE) run --rm migrator list-projects

# --- Shells / DB -------------------------------------------------------------
backend-shell: ## Open a shell in the running backend container
	$(COMPOSE) exec backend bash

psql: ## Open a psql prompt against the db service
	$(COMPOSE) exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

seed: ## (optional) Seed demo data, if a seed module is present
	$(COMPOSE) exec backend python -m app.seed || \
	  echo "No seed module found — the bootstrap admin is created automatically on first boot."

test: ## Run the backend test suite inside the backend image
	$(COMPOSE) run --rm --no-deps backend python -m pytest -q
