# Flux Makefile
.PHONY: help install test test-unit test-integration test-docker docker-test-image test-postgresql test-postgresql-unit test-postgresql-integration
.PHONY: postgres-up postgres-down postgres-test-up postgres-test-down
.PHONY: docker-build docker-up docker-down docker-logs
.PHONY: perf perf-postgresql
.PHONY: lint format check coverage clean

# Use the Docker Compose v2 plugin (`docker compose`). The legacy standalone
# binary can't reach a Docker Desktop daemon (it looks for a unix socket that
# Docker Desktop doesn't expose). Override via the DOCKER_COMPOSE variable if
# your setup needs the legacy binary instead.
DOCKER_COMPOSE ?= docker compose

# PostgreSQL test-database connections (match docker/profiles/postgresql-test.yml).
# The perf T6 "violence" env runs a second, isolated Flux server, so it gets its
# own database (two Flux servers must not share one).
PG_TEST_URL := postgresql://flux_test_user:flux_test_password@localhost:5433/flux_test
PG_VIOLENCE_URL := postgresql://flux_test_user:flux_test_password@localhost:5433/flux_test_violence

# Default target
help: ## Show this help message
	@echo "Flux Development Commands"
	@echo "========================"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

# Installation
install: ## Install dependencies
	poetry install

install-postgres: ## Install dependencies with PostgreSQL support
	poetry install --extras postgresql

# Testing
test: ## Run all tests
	poetry run pytest tests/ -v

test-unit: ## Run unit tests only
	poetry run pytest tests/flux/test_*.py -v

test-integration: ## Run integration tests only
	poetry run pytest tests/flux/integration/ -v

# Docker/airgapped runner integration tests: build an image with the working
# tree's flux-core, then run the container-gated tests against it.
test-docker: docker-test-image ## Run docker + airgapped runner tests against a locally built image
	FLUX_TEST_DOCKER_IMAGE=flux-test:local \
	poetry run pytest tests/flux/test_docker_runner.py tests/flux/test_airgapped_runner.py -v

docker-test-image: ## Build the runner test image from the working tree
	rm -f dist/*.whl docker/test/*.whl
	poetry build -f wheel
	cp dist/*.whl docker/test/
	docker build -t flux-test:local docker/test

# PostgreSQL Testing
test-postgresql: postgres-test-up test-postgresql-all postgres-test-down ## Run all PostgreSQL tests with test database

# Selects by marker rather than by path: tests/flux/integration/ has not
# existed for some time, so this target could only ever exit 4 ("no tests
# collected"). The marker is also what CI's migrations-postgres job uses,
# so the two stay in step.
test-postgresql-all: ## Run all PostgreSQL tests (requires running PostgreSQL)
	FLUX_DATABASE_URL=$(PG_TEST_URL) \
	FLUX_DATABASE_TYPE=postgresql \
	FLUX_WORKERS__BOOTSTRAP_TOKEN=make-token \
	FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY=make-encryption-key-000000000000 \
	poetry run pytest tests/ --ignore=tests/e2e --ignore=tests/perf -m postgresql -v

test-postgresql-unit: ## Run PostgreSQL unit tests (no database required)
	poetry run pytest tests/flux/test_*postgresql* -v

# Tears the container down on failure too: a make rule stops at the first
# failing line, so `$(MAKE) postgres-test-down` on its own line only runs
# when the tests pass -- leaving a stray instance behind exactly when
# someone is mid-debug.
test-postgresql-integration: postgres-test-up ## Run the PostgreSQL-marked tests against the test database
	@FLUX_DATABASE_URL=$(PG_TEST_URL) \
	FLUX_DATABASE_TYPE=postgresql \
	FLUX_WORKERS__BOOTSTRAP_TOKEN=make-token \
	FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY=make-encryption-key-000000000000 \
	poetry run pytest tests/ --ignore=tests/e2e --ignore=tests/perf -m postgresql -v; \
		status=$$?; \
		$(MAKE) postgres-test-down; \
		exit $$status

# Performance Testing (progress-streaming perf suite, tests/perf; opt-in).
# Pass T=<id> to run a single scenario (e.g. `make perf-postgresql T=t3`) and
# PROFILE=ci|workstation|full to select measurement windows (default ci).
# Needs the postgresql extra for the PG variant: `make install-postgres`.
perf: ## Run perf suite on SQLite (no docker). T=<id> one scenario; PROFILE=ci|workstation|full.
	FLUX_PERF=1 $(if $(PROFILE),FLUX_PERF_PROFILE=$(PROFILE)) poetry run pytest tests/perf $(if $(T),-k "$(T)") -v

perf-postgresql: postgres-test-up ## Run perf suite vs dockerized PostgreSQL (up->run->down). T=<id> one scenario; PROFILE=ci|workstation|full.
	@FLUX_PERF=1 $(if $(PROFILE),FLUX_PERF_PROFILE=$(PROFILE)) \
		FLUX_PERF_DATABASE_URL=$(PG_TEST_URL) \
		FLUX_PERF_DATABASE_URL_VIOLENCE=$(PG_VIOLENCE_URL) \
		poetry run pytest tests/perf $(if $(T),-k "$(T)") -v; \
		status=$$?; \
		$(MAKE) postgres-test-down; \
		exit $$status

# Engine benchmarks (B series, tests/perf/test_b*.py; issue #259). The three
# core metrics -- dispatch latency, sustained throughput, replay cost -- that
# every other performance ticket reports before/after against.
bench: ## Run the engine benchmark suite (B series). B=<id> one benchmark; PROFILE=ci|workstation|full.
	FLUX_PERF=1 $(if $(PROFILE),FLUX_PERF_PROFILE=$(PROFILE)) \
		poetry run pytest tests/perf -k "$(if $(B),$(B),b1 or b2 or b3)" -v

bench-postgresql: postgres-test-up ## Run the engine benchmarks vs dockerized PostgreSQL.
	@FLUX_PERF=1 $(if $(PROFILE),FLUX_PERF_PROFILE=$(PROFILE)) \
		FLUX_PERF_DATABASE_URL=$(PG_TEST_URL) \
		poetry run pytest tests/perf -k "$(if $(B),$(B),b1 or b2 or b3)" -v; \
		status=$$?; \
		$(MAKE) postgres-test-down; \
		exit $$status

bench-profile: ## Flame-graph one benchmark under py-spy (needs py-spy on PATH). B=<id> required.
	@poetry run python -c "import shutil,sys; sys.exit(0 if shutil.which('py-spy') else 1)" \
		|| { echo "py-spy not found: poetry run pip install py-spy (it is a profiling tool, not a project dependency)"; exit 1; }
	@test -n "$(B)" || { echo "B=<b1|b2|b3> is required"; exit 1; }
	@mkdir -p docs/benchmarks/flamegraphs
	FLUX_PERF=1 FLUX_PERF_PROFILE=$(if $(PROFILE),$(PROFILE),ci) FLUX_BENCH_PYSPY=1 \
		poetry run pytest tests/perf -k "$(B)" -v

# PostgreSQL Database Management
postgres-up: ## Start PostgreSQL for development
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/postgresql.yml up -d postgres
	@echo "Waiting for PostgreSQL to be ready..."
	@./docker/scripts/wait-for-postgres.sh localhost 5432 flux_user flux_dev || echo "PostgreSQL ready"

postgres-down: ## Stop PostgreSQL development instance
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/postgresql.yml down

postgres-test-up: ## Start PostgreSQL for testing (rebuilds so init SQL is current)
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/postgresql-test.yml up -d --build postgres-test
	@echo "Waiting for PostgreSQL test instance to be ready..."
	@POSTGRES_PASSWORD=flux_test_password ./docker/scripts/wait-for-postgres.sh localhost 5433 flux_test_user flux_test || echo "PostgreSQL test ready"

postgres-test-down: ## Stop PostgreSQL test instance
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/postgresql-test.yml down

postgres-logs: ## Show PostgreSQL logs
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/postgresql.yml logs -f postgres

postgres-shell: ## Connect to PostgreSQL development database
	PGPASSWORD=flux_password psql -h localhost -U flux_user -d flux_dev

postgres-test-shell: ## Connect to PostgreSQL test database
	PGPASSWORD=flux_test_password psql -h localhost -p 5433 -U flux_test_user -d flux_test

# Docker Development
docker-build: ## Build Docker images
	$(DOCKER_COMPOSE) build
	docker build -t flux-postgres:latest ./docker/postgres

docker-up-sqlite: ## Start Flux with SQLite
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/sqlite.yml up

docker-up-postgres: ## Start Flux with PostgreSQL
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/postgresql.yml up

docker-up-monitoring: ## Start Flux with PostgreSQL and monitoring
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/postgresql.yml -f docker/profiles/monitoring.yml up

docker-down: ## Stop all Docker services
	$(DOCKER_COMPOSE) down

docker-logs: ## Show Docker logs
	$(DOCKER_COMPOSE) logs -f

docker-clean: ## Clean up Docker resources
	$(DOCKER_COMPOSE) down -v
	docker system prune -f

# Code Quality
lint: ## Run linting (ruff + mypy via pre-commit, matches CI)
	poetry run pre-commit run --all-files

format: ## Format code (ruff format + autofix)
	poetry run ruff format flux/ tests/
	poetry run ruff check --fix flux/ tests/

check: ## Run all checks (lint/format/type via pre-commit, then unit tests)
	poetry run pre-commit run --all-files
	poetry run pytest tests/ --ignore=tests/e2e

coverage: ## Run tests with coverage
	poetry run pytest tests/ --cov=flux --cov-report=html --cov-report=term-missing

coverage-postgresql: postgres-test-up ## Run PostgreSQL tests with coverage
	FLUX_DATABASE_URL=postgresql://flux_test_user:flux_test_password@localhost:5433/flux_test \
	poetry run pytest tests/flux/test_*postgresql* tests/flux/integration/ \
	--cov=flux.models --cov=flux.config --cov=flux.catalogs --cov=flux.errors \
	--cov-report=html --cov-report=term-missing
	$(MAKE) postgres-test-down

# Cleanup
clean: ## Clean up generated files
	rm -rf .coverage htmlcov/ .pytest_cache/
	rm -rf dist/ build/ *.egg-info/
	find . -type d -name __pycache__ -delete
	find . -type f -name "*.pyc" -delete

# Development workflows
dev-postgres: install-postgres postgres-up ## Set up PostgreSQL development environment
	@echo "PostgreSQL development environment ready!"
	@echo "Connection: postgresql://flux_user:flux_password@localhost:5432/flux_dev"

dev-sqlite: install ## Set up SQLite development environment
	@echo "SQLite development environment ready!"
	@echo "Database: .flux/flux.db"

# CI simulation
ci-test: ## Simulate CI testing
	$(MAKE) test-postgresql-unit
	$(MAKE) test-postgresql-integration
	$(MAKE) test

# Validation
validate-profiles: ## Validate Docker Compose profiles
	@echo "Validating Docker Compose profiles..."
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/sqlite.yml config > /dev/null
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/postgresql.yml config > /dev/null
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/postgresql-test.yml config > /dev/null
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/monitoring.yml config > /dev/null
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker/profiles/ci.yml config > /dev/null
	@echo "All profiles are valid ✓"

# Information
info: ## Show development environment information
	@echo "Flux Development Environment"
	@echo "============================"
	@echo "Python version: $$(poetry run python --version)"
	@echo "Poetry version: $$(poetry --version)"
	@echo "Dependencies installed: $$(poetry show | wc -l) packages"
	@echo ""
	@echo "Database Configuration:"
	@echo "  Default: SQLite (.flux/flux.db)"
	@echo "  PostgreSQL Dev: postgresql://flux_user:flux_password@localhost:5432/flux_dev"
	@echo "  PostgreSQL Test: postgresql://flux_test_user:flux_test_password@localhost:5433/flux_test"
	@echo ""
	@echo "Available Commands:"
	@$(MAKE) help
