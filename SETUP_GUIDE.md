# Error Triage → Jira Upserter - Setup Guide

## Setup Summary

**Date**: 2025-10-07  
**Status**: ✅ **SETUP SUCCESSFUL**  
**Python Version**: 3.12.3 (compatible with requirements: Python 3.11+)  
**Virtual Environment**: Created and activated at `./venv/`

---

## Environment Details

### Python Runtime
- **Installed Version**: Python 3.12.3
- **Required Version**: Python 3.11+
- **Compatibility**: ✅ All dependencies support Python 3.12
- **Virtual Environment Location**: `/tmp/blitzy/jiratest/blitzy12872b516/venv`

### Installed Dependencies

#### Production Dependencies (15 packages)
All packages from `requirements.txt` installed successfully:

| Package | Version | Purpose |
|---------|---------|---------|
| flask | 3.1.2 | Web framework for HTTP endpoints |
| gunicorn | 23.0.0 | Production WSGI server |
| jira | 3.10.5 | Atlassian Jira REST API client |
| redis | 6.4.0 | Redis client for caching and counters |
| pymongo | 4.10.1 | MongoDB client for audit logs |
| pydantic | 2.10.4 | Data validation and settings management |
| pyyaml | 6.0.2 | YAML configuration file parsing |
| python-dotenv | 1.0.1 | Load environment variables from .env |
| boto3 | 1.35.90 | AWS SDK for Secrets Manager |
| prometheus-client | 0.21.0 | Prometheus metrics exposition |
| python-json-logger | 3.2.1 | Structured JSON logging |
| requests | 2.32.3 | HTTP library |
| cryptography | 44.0.0 | Cryptographic functions |
| google-auth | 2.37.0 | Google Cloud authentication |

#### Development Dependencies (13 packages)
All packages from `requirements-dev.txt` installed successfully:

| Package | Version | Purpose |
|---------|---------|---------|
| pytest | 8.3.4 | Testing framework |
| pytest-cov | 6.0.0 | Code coverage reporting |
| pytest-mock | 3.14.0 | Mocking utilities |
| pytest-asyncio | 0.24.0 | Async test support |
| black | 24.10.0 | Code formatter |
| flake8 | 7.1.1 | Linting and style checking |
| mypy | 1.14.0 | Static type checker |
| isort | 5.13.2 | Import sorting |
| bandit | 1.8.0 | Security vulnerability scanner |
| fakeredis | 2.27.0 | Fake Redis for testing |
| responses | 0.25.3 | HTTP request mocking |
| freezegun | 1.5.1 | Time mocking for tests |
| pre-commit | 4.0.1 | Git hook management |

**Note**: Original spec listed fakeredis==2.27.2, but this version doesn't exist in PyPI. Used 2.27.0 instead (latest available in 2.27.x series).

---

## Project Structure Created

```
jiratest/
├── .dockerignore          # Docker build exclusions
├── .flake8                # Flake8 linter configuration
├── .gitignore            # Git exclusions (venv, __pycache__, etc.)
├── Makefile              # Development task automation
├── pyproject.toml        # Python project metadata and tool configs
├── README.md             # Project overview (existing)
├── requirements.txt      # Production dependencies
├── requirements-dev.txt  # Development dependencies
├── SETUP_GUIDE.md        # This document
├── config/
│   └── .env.example      # Environment variables template
├── src/
│   ├── __init__.py
│   ├── app/
│   │   └── __init__.py
│   ├── models/
│   │   └── __init__.py
│   ├── services/
│   │   └── __init__.py
│   └── utils/
│       └── __init__.py
├── tests/
│   ├── __init__.py
│   ├── integration/
│   │   └── __init__.py
│   └── unit/
│       └── __init__.py
├── docs/                 # Documentation directory (empty)
├── deploy/               # Infrastructure as Code (empty)
└── venv/                 # Virtual environment (not tracked in git)
```

---

## Setup Commands Executed

### 1. Virtual Environment Creation
```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Package Manager Upgrade
```bash
pip install --upgrade pip setuptools wheel
```
- pip upgraded: 24.0 → 25.2
- setuptools installed: 80.9.0
- wheel installed: 0.45.1

### 3. Dependency Installation
```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```
Both installations completed successfully with all dependencies resolved.

### 4. Directory Structure
```bash
mkdir -p src/app src/services src/models src/utils
mkdir -p tests/unit tests/integration
mkdir -p config docs deploy
touch src/__init__.py src/app/__init__.py src/services/__init__.py
touch src/models/__init__.py src/utils/__init__.py
touch tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py
```

---

## Configuration Files Created

### pyproject.toml
- Build system configuration (setuptools)
- Black formatter settings (line length: 100)
- isort configuration (black-compatible profile)
- mypy type checking settings (strict mode)
- pytest configuration (coverage, test discovery)

### .flake8
- Line length: 100
- Ignored rules: E203, E266, E501, W503 (black-compatible)
- Max complexity: 10

### .gitignore
- Python artifacts (__pycache__, *.pyc)
- Virtual environments (venv/, .venv/)
- Testing artifacts (.pytest_cache, .coverage)
- IDE files (.vscode/, .idea/)
- Environment files (.env)
- Build artifacts (dist/, build/)

### .dockerignore
- Git repository (.git)
- Tests and documentation
- Virtual environments
- Development files
- IDE configurations

### Makefile
Development commands available:
- `make install` - Install production dependencies
- `make install-dev` - Install all dependencies
- `make test` - Run test suite with coverage
- `make lint` - Run flake8 and mypy
- `make format` - Format code with black and isort
- `make run-local` - Run Flask development server
- `make docker-build` - Build Docker image
- `make docker-run` - Start docker-compose stack
- `make clean` - Remove build artifacts

### config/.env.example
Template for required environment variables:
- Flask configuration (app, environment, secret)
- Redis connection (host, port, database)
- MongoDB connection (optional)
- Jira credentials (base URL, API token, project key)
- Webhook secrets (Vercel, GCP)
- AWS configuration (region, Secrets Manager)
- Application settings (rate limits, TTLs)
- Logging configuration

---

## Validation Performed

### 1. Python Version Check
```bash
$ python --version
Python 3.12.3
```
✅ Compatible with requirements (Python 3.11+)

### 2. Critical Package Import Test
```bash
$ python -c "import flask; import jira; import redis; import pymongo"
```
✅ All critical packages imported successfully

### 3. Flask Version Verification
```bash
$ python -c "import flask; print(flask.__version__)"
3.1.2
```
✅ Flask 3.1.2 installed (matches requirements)

---

## Known Issues and Notes

### ⚠️ Source Code Not Yet Implemented
- **Status**: This is a greenfield project. All source files listed in the Agent Action Plan need to be CREATED by implementation agents.
- **Impact**: Cannot compile or run application yet - no source code exists.
- **Next Steps**: Implementation agents will create all application files according to the execution plan in Section 0.5.

### ⚠️ External Dependencies Not Available
The following external services are required at runtime but not available during setup:

1. **Redis** (required)
   - Purpose: Frequency counters, event deduplication, rate limiting
   - Version: 7.2+
   - Setup: Will be provided via AWS ElastiCache or docker-compose

2. **MongoDB** (optional)
   - Purpose: Audit logs for error events and Jira actions
   - Version: 7.0+
   - Setup: Can be enabled with ENABLE_MONGO=true
   - Default: Disabled in development

3. **Jira Cloud API** (required)
   - Purpose: Issue creation, search, commenting, escalation
   - Authentication: API token
   - Setup: Credentials stored in AWS Secrets Manager (production) or .env (development)

4. **AWS Secrets Manager** (production only)
   - Purpose: Secure credential storage
   - Setup: Required for production deployment, optional for development

### 📝 Configuration Notes

#### Version Compatibility
- All dependency versions are pinned for reproducible builds
- Python 3.12.3 is fully compatible with all dependencies
- Flask 3.1.2 supports Python 3.9+ (verified compatible)
- jira 3.10.5 requires Python 3.10+ (verified compatible)

#### Development vs Production
- Development: Use .env file for configuration
- Production: Use AWS Secrets Manager for sensitive credentials
- Redis/MongoDB: Use docker-compose locally, AWS services in production

#### Testing Strategy
- Unit tests: Mock all external dependencies (Redis, Jira, MongoDB)
- Integration tests: Use fakeredis for Redis, mock Jira API
- Full E2E tests: Require running Redis and mock Jira service

---

## Quick Start (After Implementation)

Once source files are implemented, developers can:

### 1. Clone and Setup
```bash
git clone <repository>
cd jiratest
python3 -m venv venv
source venv/bin/activate
make install-dev
```

### 2. Configure Environment
```bash
cp config/.env.example .env
# Edit .env with your credentials
```

### 3. Run Tests
```bash
make test
```

### 4. Run Locally
```bash
make run-local
```

### 5. Run with Docker
```bash
make docker-build
make docker-run
```

---

## Next Steps for Implementation Agents

### Critical Path Files to Create (Priority P0)
1. `src/app/__init__.py` - Flask application factory
2. `src/app/config.py` - Configuration management
3. `src/app/routes/events.py` - POST /events webhook endpoint
4. `src/services/payload_adapters.py` - Payload normalization
5. `src/services/fingerprinter.py` - Error fingerprinting
6. `src/services/jira_integration.py` - Jira API integration
7. `src/models/error_event.py` - Data models
8. `requirements.txt` - Already created ✅
9. `Dockerfile` - Container image definition
10. `README.md` - Update with usage instructions

### Testing Infrastructure (Priority P1)
1. `tests/conftest.py` - Shared fixtures
2. `tests/unit/test_fingerprinter.py` - Fingerprinting tests
3. `tests/unit/test_payload_adapters.py` - Adapter tests
4. `tests/integration/test_events_endpoint.py` - E2E webhook tests

### Configuration Files (Priority P1)
1. `config/severity_rules.yaml` - Frequency-to-severity mappings
2. `config/ownership_rules.yaml` - Assignee determination rules
3. `config/sanitization_patterns.yaml` - PII detection patterns

### Infrastructure (Priority P2)
1. `Dockerfile` - Multi-stage build
2. `docker-compose.yml` - Local development stack
3. `deploy/terraform/` - AWS infrastructure definitions
4. `.github/workflows/` - CI/CD pipelines

### Documentation (Priority P2)
1. `docs/architecture.md` - System design
2. `docs/configuration.md` - Config reference
3. `docs/api.md` - API documentation
4. `docs/deployment.md` - Deployment runbook

---

## Setup Success Criteria

✅ **All Criteria Met**

- [x] Virtual environment created with Python 3.12.3
- [x] All production dependencies installed (15 packages)
- [x] All development dependencies installed (13 packages)
- [x] Project directory structure created
- [x] Configuration files created (.gitignore, pyproject.toml, etc.)
- [x] Development tools configured (Makefile, .flake8, etc.)
- [x] Environment template created (config/.env.example)
- [x] Critical package imports validated
- [x] Setup guide documented

---

## Troubleshooting

### Virtual Environment Issues
If `source venv/bin/activate` doesn't work:
```bash
# On Windows
venv\Scripts\activate

# Recreate if corrupted
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

### Dependency Installation Failures
If pip install fails:
```bash
# Upgrade pip first
pip install --upgrade pip setuptools wheel

# Clear cache and retry
pip cache purge
pip install -r requirements.txt --no-cache-dir
```

### Import Errors
If packages can't be imported:
```bash
# Verify you're in the virtual environment
which python
# Should show: /path/to/project/venv/bin/python

# Reinstall if needed
pip install --force-reinstall -r requirements.txt
```

---

## Contact and Support

For questions about this setup, refer to:
- Agent Action Plan (Section 0): Project overview and file execution plan
- Technical Specification: Complete system requirements
- This document: Setup procedures and environment details

**Setup completed successfully by**: DevOps and Build Engineering Agent  
**Setup date**: October 7, 2025  
**Next agent**: Implementation agents to create source files
