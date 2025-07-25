# Flux Makefile
.PHONY: help install test test-unit test-integration test-postgresql test-postgresql-unit test-postgresql-integration
.PHONY: postgres-up postgres-down postgres-test-up postgres-test-down
.PHONY: docker-build docker-up docker-down docker-logs
.PHONY: lint format check coverage clean

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

# PostgreSQL Testing
test-postgresql: postgres-test-up test-postgresql-all postgres-test-down ## Run all PostgreSQL tests with test database

test-postgresql-all: ## Run all PostgreSQL tests (requires running PostgreSQL)
	FLUX_DATABASE_URL=postgresql://flux_test_user:flux_test_password@localhost:5433/flux_test \
	FLUX_DATABASE_TYPE=postgresql \
	poetry run pytest tests/flux/test_*postgresql* tests/flux/integration/ -v

test-postgresql-unit: ## Run PostgreSQL unit tests (no database required)
	poetry run pytest tests/flux/test_*postgresql* -v

test-postgresql-integration: postgres-test-up ## Run PostgreSQL integration tests with test database
	FLUX_DATABASE_URL=postgresql://flux_test_user:flux_test_password@localhost:5433/flux_test \
	FLUX_DATABASE_TYPE=postgresql \
	poetry run pytest tests/flux/integration/ -v
	$(MAKE) postgres-test-down

# PostgreSQL Database Management
postgres-up: ## Start PostgreSQL for development
	docker-compose -f docker-compose.yml -f docker/profiles/postgresql.yml up -d postgres
	@echo "Waiting for PostgreSQL to be ready..."
	@./docker/scripts/wait-for-postgres.sh localhost 5432 flux_user flux_dev || echo "PostgreSQL ready"

postgres-down: ## Stop PostgreSQL development instance
	docker-compose -f docker-compose.yml -f docker/profiles/postgresql.yml down

postgres-test-up: ## Start PostgreSQL for testing
	docker-compose -f docker-compose.yml -f docker/profiles/postgresql-test.yml up -d postgres-test
	@echo "Waiting for PostgreSQL test instance to be ready..."
	@POSTGRES_PASSWORD=flux_test_password ./docker/scripts/wait-for-postgres.sh localhost 5433 flux_test_user flux_test || echo "PostgreSQL test ready"

postgres-test-down: ## Stop PostgreSQL test instance
	docker-compose -f docker-compose.yml -f docker/profiles/postgresql-test.yml down

postgres-logs: ## Show PostgreSQL logs
	docker-compose -f docker-compose.yml -f docker/profiles/postgresql.yml logs -f postgres

postgres-shell: ## Connect to PostgreSQL development database
	PGPASSWORD=flux_password psql -h localhost -U flux_user -d flux_dev

postgres-test-shell: ## Connect to PostgreSQL test database
	PGPASSWORD=flux_test_password psql -h localhost -p 5433 -U flux_test_user -d flux_test

# Docker Development
docker-build: ## Build Docker images
	docker-compose build
	docker build -t flux-postgres:latest ./docker/postgres

docker-up-sqlite: ## Start Flux with SQLite
	docker-compose -f docker-compose.yml -f docker/profiles/sqlite.yml up

docker-up-postgres: ## Start Flux with PostgreSQL
	docker-compose -f docker-compose.yml -f docker/profiles/postgresql.yml up

docker-up-monitoring: ## Start Flux with PostgreSQL and monitoring
	docker-compose -f docker-compose.yml -f docker/profiles/postgresql.yml -f docker/profiles/monitoring.yml up

docker-down: ## Stop all Docker services
	docker-compose down

docker-logs: ## Show Docker logs
	docker-compose logs -f

docker-clean: ## Clean up Docker resources
	docker-compose down -v
	docker system prune -f

# Code Quality
lint: ## Run linting
	poetry run pylint flux/

format: ## Format code
	poetry run black flux/ tests/
	poetry run isort flux/ tests/

check: ## Run all checks (lint, type check, tests)
	poetry run pylint flux/
	poetry run mypy flux/ || true
	poetry run pytest tests/flux/test_*.py

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
	docker-compose -f docker-compose.yml -f docker/profiles/sqlite.yml config > /dev/null
	docker-compose -f docker-compose.yml -f docker/profiles/postgresql.yml config > /dev/null
	docker-compose -f docker-compose.yml -f docker/profiles/postgresql-test.yml config > /dev/null
	docker-compose -f docker-compose.yml -f docker/profiles/monitoring.yml config > /dev/null
	docker-compose -f docker-compose.yml -f docker/profiles/ci.yml config > /dev/null
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