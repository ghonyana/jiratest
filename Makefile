.PHONY: help install install-dev test lint format run-local docker-build docker-run clean

help:
	@echo "Available commands:"
	@echo "  make install      - Install production dependencies"
	@echo "  make install-dev  - Install development dependencies"
	@echo "  make test         - Run test suite"
	@echo "  make lint         - Run linters (flake8, mypy)"
	@echo "  make format       - Format code (black, isort)"
	@echo "  make run-local    - Run application locally"
	@echo "  make docker-build - Build Docker image"
	@echo "  make docker-run   - Run Docker container"
	@echo "  make clean        - Clean build artifacts"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements.txt
	pip install -r requirements-dev.txt

test:
	pytest tests/ -v --cov=src --cov-report=term-missing

lint:
	flake8 src/ tests/
	mypy src/

format:
	black src/ tests/
	isort src/ tests/

run-local:
	FLASK_APP=src.app:create_app FLASK_ENV=development flask run --port 8080

docker-build:
	docker build -t jiratest-error-triage:latest .

docker-run:
	docker-compose up

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	rm -rf .pytest_cache .coverage htmlcov/ .mypy_cache/
	rm -rf build/ dist/ *.egg-info/
