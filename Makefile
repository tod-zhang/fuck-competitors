# Docker shortcuts.
#
# Two flags are baked in so `make up` works even when the repo sits under a non-ASCII
# directory path (Compose can't derive a project name from one, and BuildKit rejects it in
# a gRPC header). They're harmless on a plain ASCII path too — just keep using `make up`.
#   -p fuck-competitors  -> stable project + volume name, independent of the folder name
#   DOCKER_BUILDKIT=0     -> classic builder, avoids the non-ASCII build-context crash
PROJECT := fuck-competitors
COMPOSE := DOCKER_BUILDKIT=0 COMPOSE_BAKE=false docker compose -p $(PROJECT)

.PHONY: up down restart logs ps build

# Build (if needed) and start app (:9527) + mcp (:9528) in the background.
up:
	$(COMPOSE) up -d --build

# Stop and remove the containers; the fc-data volume (your DB) is kept.
down:
	$(COMPOSE) down

# Rebuild and recreate after code changes.
restart:
	$(COMPOSE) up -d --build

# Follow logs from both services.
logs:
	$(COMPOSE) logs -f

# Show container status.
ps:
	$(COMPOSE) ps

# Build images only (no start).
build:
	$(COMPOSE) build
