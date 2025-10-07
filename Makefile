# Makefile for Error Triage → Jira Upserter Service
# Provides standardized development task automation per Section 0.2.1

# Declare all targets as phony (not associated with files)
.PHONY: help install install-prod test test-unit test-integration lint format security \
        run-local docker-build docker-run docker-compose-up docker-compose-down clean

# Default target: display help
.DEFAULT_GOAL := help

#==============================================================================
# HELP TARGET
#==============================================================================

help: ## Display available targets with descriptions
	@echo "Error Triage Service - Development Commands"
	@echo "==========================================="
	@echo ""
	@echo "Setup Commands:"
	@echo "  make install              - Create venv, install all dependencies, setup pre-commit hooks"
	@echo "  make install-prod         - Install only production dependencies"
	@echo ""
	@echo "Testing Commands:"
	@echo "  make test                 - Run full test suite with coverage report"
	@echo "  make test-unit            - Run unit tests only"
	@echo "  make test-integration     - Run integration tests only"
	@echo ""
	@echo "Code Quality Commands:"
	@echo "  make lint                 - Run all linters (black, flake8, mypy, isort)"
	@echo "  make format               - Auto-format code with black and isort"
	@echo "  make security             - Run security vulnerability scans with bandit"
	@echo ""
	@echo "Local Development:"
	@echo "  make run-local            - Start Flask development server on port 8080"
	@echo ""
	@echo "Docker Commands:"
	@echo "  make docker-build         - Build Docker image"
	@echo "  make docker-run           - Run Docker container with environment variables"
	@echo "  make docker-compose-up    - Start all services with docker-compose"
	@echo "  make docker-compose-down  - Stop all docker-compose services"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean                - Remove cache files, coverage reports, build artifacts"
	@echo ""

#==============================================================================
# INSTALLATION TARGETS
#==============================================================================

install: ## Create virtual environment, install dependencies, setup pre-commit hooks
	@echo "Creating virtual environment..."
	python3 -m venv venv || python -m venv venv
	@echo "Installing production dependencies..."
	./venv/bin/pip install --upgrade pip setuptools wheel
	./venv/bin/pip install -r requirements.txt
	@echo "Installing development dependencies..."
	./venv/bin/pip install -r requirements-dev.txt
	@echo "Installing pre-commit hooks..."
	./venv/bin/pre-commit install
	@echo ""
	@echo "✓ Installation complete!"
	@echo "  Activate virtual environment: source venv/bin/activate"

install-prod: ## Install only production dependencies
	@echo "Installing production dependencies..."
	pip install --upgrade pip setuptools wheel
	pip install -r requirements.txt
	@echo "✓ Production dependencies installed"

#==============================================================================
# TESTING TARGETS
#==============================================================================

test: ## Run pytest with coverage (HTML + terminal report)
	@echo "Running full test suite with coverage..."
	pytest --cov=src --cov-report=html --cov-report=term tests/
	@echo ""
	@echo "✓ Coverage report generated in htmlcov/index.html"

test-unit: ## Run unit tests only
	@echo "Running unit tests..."
	pytest tests/unit/ -v

test-integration: ## Run integration tests only
	@echo "Running integration tests..."
	pytest tests/integration/ -v

#==============================================================================
# CODE QUALITY TARGETS
#==============================================================================

lint: ## Run all linters (black, flake8, mypy, isort)
	@echo "Running code quality checks..."
	@echo "→ Checking code formatting with black..."
	black --check src/ tests/
	@echo "→ Checking import order with isort..."
	isort --check-only src/ tests/
	@echo "→ Running flake8 linter..."
	flake8 src/ tests/
	@echo "→ Running mypy type checker..."
	mypy src/
	@echo ""
	@echo "✓ All linting checks passed"

format: ## Auto-format code (black, isort)
	@echo "Formatting code..."
	black src/ tests/
	isort src/ tests/
	@echo "✓ Code formatted"

security: ## Run security scans (bandit)
	@echo "Running security vulnerability scan..."
	bandit -r src/ -f screen
	@echo "✓ Security scan complete"

#==============================================================================
# LOCAL DEVELOPMENT TARGETS
#==============================================================================

run-local: ## Start Flask development server
	@echo "Starting Flask development server on http://0.0.0.0:8080..."
	@echo "Press Ctrl+C to stop"
	FLASK_APP=src.app:create_app FLASK_ENV=development flask run --host=0.0.0.0 --port=8080

#==============================================================================
# DOCKER TARGETS
#==============================================================================

docker-build: ## Build Docker image
	@echo "Building Docker image: jiratest/error-triage:latest"
	docker build -t jiratest/error-triage:latest .
	@echo "✓ Docker image built successfully"

docker-run: ## Run Docker container locally
	@echo "Running Docker container on port 8080..."
	@echo "Ensure .env file exists with required configuration"
	docker run -p 8080:8080 --env-file .env jiratest/error-triage:latest

docker-compose-up: ## Start all services with docker-compose
	@echo "Starting services with docker-compose..."
	docker-compose up -d
	@echo "✓ Services started"
	@echo "  Application: http://localhost:8080"
	@echo "  View logs: docker-compose logs -f"

docker-compose-down: ## Stop all docker-compose services
	@echo "Stopping docker-compose services..."
	docker-compose down
	@echo "✓ Services stopped"

#==============================================================================
# CLEANUP TARGETS
#==============================================================================

clean: ## Remove cache files, coverage reports, build artifacts
	@echo "Cleaning up build artifacts and cache files..."
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache
	rm -rf .coverage htmlcov/
	rm -rf .mypy_cache/
	rm -rf build/ dist/
	rm -rf src/**/__pycache__
	rm -rf tests/**/__pycache__
	@echo "✓ Cleanup complete"
