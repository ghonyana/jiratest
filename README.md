# Error Triage → Jira Upserter

> Intelligent error management system that automatically triages errors from multiple sources and creates or updates Jira issues based on frequency-driven severity rules.

## Overview

The **Error Triage → Jira Upserter** is a production-ready microservice that bridges external error sources (Vercel Log Drain and Google Cloud Platform Cloud Logging) with Atlassian Jira for intelligent issue tracking. The service processes error events in real-time, groups them by stable fingerprints, applies frequency-based severity classification, and automatically creates or updates Jira Bug issues with appropriate priority and ownership.

**Key Benefits:**
- **Automated Error Management**: Eliminates manual error triage and Jira ticket creation
- **Intelligent Grouping**: Stable fingerprinting algorithm ensures consistent error grouping across occurrences
- **Context-Rich Issues**: Every Jira issue includes sanitized error details, stack traces, and deep links to source logs
- **Noise Reduction**: Comment rate limiting and deduplication prevent Jira spam
- **Configurable Behavior**: All rules, thresholds, and assignments managed via YAML configuration files

## Key Features

### Multi-Source Error Ingestion
- **Vercel Log Drain**: Accepts webhook events from Vercel deployments with HMAC signature verification
- **GCP Cloud Logging**: Processes Pub/Sub push subscription messages with OIDC token validation
- **Unified Pipeline**: Normalizes disparate payload formats into consistent internal schema

### Intelligent Error Fingerprinting
- **Stable Algorithm**: `hash(service + env + error_class + top_stack_frame + sanitized_message)`
- **PII Sanitization**: Removes emails, UUIDs, tokens, and numeric IDs before fingerprinting
- **Collision Resistance**: SHA-256 hashing ensures unique fingerprints for distinct errors

### Frequency-Based Severity Classification
- **Rolling Counters**: 5-minute occurrence windows tracked per (environment, fingerprint) in Redis
- **Configurable Rules**: YAML-defined thresholds map frequencies to Jira priority and custom severity fields
- **Environment-Aware**: Separate thresholds for production, staging, and development environments
- **Example**: Production errors occurring ≥50 times in 5 minutes → Priority: Highest, Severity: SEV1

### Jira Upsert Logic
- **Issue Search**: JQL queries locate existing issues by fingerprint label
- **Create New**: Generate Bug issues with formatted summary, description, labels, and ownership
- **Update Existing**: Add timestamped comments with occurrence count and log links
- **Priority Escalation**: Automatically increase priority when frequency thresholds crossed

### Automatic Ownership Routing
- **Pattern-Based Assignment**: Match service names, path regex, and error class patterns to assignees
- **Component Routing**: Support Jira components with default assignee configuration
- **Fallback Defaults**: Graceful handling when no rules match

### Noise Control & Rate Limiting
- **Comment Rate Limits**: Maximum one comment per issue per 15 minutes (configurable)
- **Escalation Override**: Allow comments when severity level increases
- **Event Deduplication**: Drop duplicate events using event_id/insertId with TTL cache

### Security Features
- **Webhook Authentication**: Vercel HMAC signature and GCP OIDC token verification
- **PII Protection**: Sanitize all error messages and stack traces before Jira transmission
- **Secret Management**: AWS Secrets Manager integration for credentials
- **TLS Encryption**: All external communications over HTTPS

### Operational Excellence
- **Health Checks**: `/healthz` endpoint validates Redis, MongoDB, and Jira connectivity
- **Prometheus Metrics**: `/metrics` endpoint exposes counters, histograms for creates, comments, escalations, errors
- **Structured Logging**: JSON logs with correlation IDs for CloudWatch Logs Insights
- **Hot Configuration Reload**: Update rules via SIGHUP signal without service restart

## Architecture

### High-Level Component Diagram

```
┌─────────────┐         ┌─────────────┐
│   Vercel    │         │     GCP     │
│  Log Drain  │         │   Pub/Sub   │
└──────┬──────┘         └──────┬──────┘
       │ POST /events          │ POST /events
       │ (HMAC signed)         │ (OIDC JWT)
       └───────────┬───────────┘
                   │
            ┌──────▼──────┐
            │   Flask     │
            │  Webhook    │◄──── AWS Secrets Manager
            │  Endpoint   │       (Jira creds, secrets)
            └──────┬──────┘
                   │
       ┌───────────┴───────────┐
       │                       │
┌──────▼──────┐         ┌──────▼──────┐
│  Payload    │         │    Event    │
│  Adapters   │         │Deduplication│
│(Vercel/GCP) │         │   (Redis)   │
└──────┬──────┘         └─────────────┘
       │
       │ NormalizedErrorEvent
       │
┌──────▼──────┐         ┌─────────────┐
│    Error    │────────►│     PII     │
│Fingerprinter│         │  Sanitizer  │
└──────┬──────┘         └─────────────┘
       │
       │ fingerprint
       │
┌──────▼──────┐         ┌─────────────┐
│  Frequency  │◄───────►│    Redis    │
│   Tracker   │         │  (Counters) │
└──────┬──────┘         └─────────────┘
       │
       │ (env, count)
       │
┌──────▼──────┐         ┌─────────────┐
│  Severity   │◄────────│ YAML Rules  │
│    Engine   │         │(severity_   │
└──────┬──────┘         │ rules.yaml) │
       │                └─────────────┘
       │ (priority, severity)
       │
┌──────▼──────┐         ┌─────────────┐
│ Ownership   │◄────────│ YAML Rules  │
│  Resolver   │         │(ownership_  │
└──────┬──────┘         │ rules.yaml) │
       │                └─────────────┘
       │ (assignee)
       │
┌──────▼──────┐
│    Jira     │◄────────► Jira Cloud API
│ Integration │           (REST API v3)
│   Service   │
└─────────────┘
       │
       │ Create/Update/Comment
       │
┌──────▼──────┐
│  MongoDB    │ (Optional: Audit Logs)
│   Audit     │
└─────────────┘
```

### Technology Stack

- **Runtime**: Python 3.11+
- **Web Framework**: Flask 3.1.2
- **Caching Layer**: Redis 7.2+ (ElastiCache)
- **Audit Storage**: MongoDB Atlas 7.0+ (optional)
- **Deployment**: AWS ECS Fargate / EKS
- **Monitoring**: CloudWatch Logs + Metrics, Prometheus
- **Infrastructure**: Terraform-managed AWS resources
- **CI/CD**: GitHub Actions workflows

## Quick Start

### Prerequisites

- Python 3.11 or higher
- Docker and Docker Compose
- AWS CLI configured (for deployment)
- Jira Cloud instance with API access
- Redis server (local or remote)

### Installation

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd jiratest
   ```

2. **Install dependencies:**
   ```bash
   make install
   ```
   This creates a virtual environment and installs all required packages.

3. **Configure environment variables:**
   ```bash
   cp config/.env.example .env
   ```
   Edit `.env` and set:
   - `JIRA_BASE_URL`: Your Jira Cloud URL (e.g., `https://yourorg.atlassian.net`)
   - `JIRA_API_TOKEN`: Jira API token from your account settings
   - `JIRA_PROJECT_KEY`: Target Jira project key (e.g., `ET`)
   - `REDIS_HOST`: Redis server hostname
   - `VERCEL_WEBHOOK_SECRET`: Shared secret for Vercel webhook signature verification
   - `GCP_AUDIENCE`: Expected audience for GCP OIDC token validation
   - `MONGODB_URI`: MongoDB connection string (optional, for audit logs)

4. **Configure rules:**
   Review and customize:
   - `config/severity_rules.yaml` - Frequency thresholds and severity mappings
   - `config/ownership_rules.yaml` - Service/path/error class to assignee mappings
   - `config/sanitization_patterns.yaml` - PII detection regex patterns

### Running Locally

**Option 1: Using Make (recommended for development)**
```bash
make run-local
```
Service available at `http://localhost:8080`

**Option 2: Using Docker Compose (full stack)**
```bash
docker-compose up
```
Includes application, Redis, and MongoDB containers.

### Testing the Service

**Send a test Vercel-style event:**
```bash
curl -X POST http://localhost:8080/events \
  -H "Content-Type: application/json" \
  -H "x-vercel-signature: <computed-hmac>" \
  -d '{
    "source": "vercel",
    "deployment": {"id": "dpl_test", "url": "test.vercel.app"},
    "message": "Error: Test error message",
    "level": "error",
    "timestamp": "2025-01-15T10:30:45.123Z",
    "environment": "production",
    "path": "/api/test"
  }'
```

**Check health:**
```bash
curl http://localhost:8080/healthz
```

**View metrics:**
```bash
curl http://localhost:8080/metrics
```

## Configuration

All service behavior is controlled via YAML configuration files in the `config/` directory:

### Severity Rules (`config/severity_rules.yaml`)

Maps error occurrence frequencies to Jira priority and custom severity fields:

```yaml
production:
  - threshold: 50
    priority: "Highest"
    severity: "SEV1"
  - threshold: 10
    priority: "High"
    severity: "SEV2"
staging:
  - threshold: 20
    priority: "Medium"
    severity: "SEV3"
```

**How it works:** Errors in production occurring ≥50 times in a 5-minute window are assigned Priority: Highest and Severity: SEV1. Rules are evaluated in order; first match wins.

### Ownership Rules (`config/ownership_rules.yaml`)

Determines Jira issue assignee based on service, path, or error class patterns:

```yaml
rules:
  - service: "web-app"
    path_regex: "/api/.*"
    assignee: "5f8e9a1b2c3d4e5f6a7b8c9d"  # Atlassian account ID
  - service: "web-app"
    error_class: "TypeError"
    component: "Frontend"  # Uses component's default assignee
```

**How it works:** Rules are evaluated in order. First matching rule assigns either a direct `assignee` (Atlassian account ID) or a `component` (uses Jira component's default assignee).

### Sanitization Patterns (`config/sanitization_patterns.yaml`)

Regular expressions for detecting and removing PII from error messages:

```yaml
patterns:
  - pattern: '\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'
    replacement: '[UUID]'
  - pattern: '\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    replacement: '[EMAIL]'
```

**How it works:** Patterns are applied to error messages before fingerprinting and before sending to Jira, ensuring no PII leakage and stable fingerprints.

### Environment Variables (`.env`)

See `config/.env.example` for complete list of required and optional environment variables.

## Deployment

### AWS Infrastructure Setup

The service deploys to AWS using Terraform-managed infrastructure:

1. **Prerequisites:**
   - AWS account with appropriate permissions
   - Terraform 1.5+ installed
   - Configured AWS CLI (`aws configure`)

2. **Initialize Terraform:**
   ```bash
   cd deploy/terraform
   terraform init
   ```

3. **Create secrets in AWS Secrets Manager:**
   ```bash
   aws secretsmanager create-secret \
     --name jira/jiratest/prod/credentials \
     --secret-string '{"base_url":"https://yourorg.atlassian.net","api_token":"your-token"}'
   ```

4. **Plan and apply infrastructure:**
   ```bash
   terraform plan -var-file=environments/prod.tfvars
   terraform apply -var-file=environments/prod.tfvars
   ```

5. **Deploy application:**
   GitHub Actions CI/CD pipeline automatically builds Docker images and deploys to ECS on merge to `main` branch.

**Deployed Resources:**
- ECS Fargate service with auto-scaling (2-10 tasks)
- ElastiCache Redis cluster (cache.t4g.medium)
- Application Load Balancer with HTTPS
- CloudWatch log groups and metric namespaces
- IAM roles and security groups

For detailed deployment instructions, see **[docs/deployment.md](docs/deployment.md)**.

## Monitoring

### Health Check Endpoint

**GET `/healthz`** - Returns health status of all dependencies:

```json
{
  "status": "healthy",
  "checks": {
    "redis": {"status": "up", "latency_ms": 2},
    "mongodb": {"status": "up", "latency_ms": 15},
    "jira": {"status": "up", "latency_ms": 85}
  }
}
```

**Status Codes:**
- `200 OK` - All required dependencies are healthy
- `503 Service Unavailable` - One or more required dependencies unavailable

### Prometheus Metrics Endpoint

**GET `/metrics`** - Exposes Prometheus-compatible metrics:

**Key Metrics:**
- `events_received_total{environment, source}` - Total webhook events received
- `events_processed_total{environment, source}` - Successfully processed events
- `events_deduplicated_total{environment}` - Duplicate events dropped
- `jira_issues_created_total{environment, project}` - New Jira issues created
- `jira_comments_added_total{environment, project}` - Comments added to existing issues
- `jira_escalations_total{environment, priority}` - Issues with escalated priority
- `event_processing_duration_seconds` - Event processing latency histogram
- `jira_api_latency_seconds{operation}` - Jira API call duration histogram
- `errors_total{environment, error_type}` - Application errors by type

### Structured Logging

All logs emitted in JSON format for CloudWatch Logs Insights:

```json
{
  "timestamp": "2025-01-15T10:30:46.123Z",
  "level": "INFO",
  "service": "error-triage",
  "environment": "production",
  "event_id": "vercel-xyz-123",
  "fingerprint": "a3f5b9c8d2e1...",
  "jira_issue_key": "ET-1234",
  "action": "jira_comment_added",
  "duration_ms": 125,
  "message": "Added comment to existing issue ET-1234"
}
```

### CloudWatch Integration

- **Log Group**: `/aws/ecs/jiratest-error-triage-{env}`
- **Metric Namespace**: `Jiratest/ErrorTriage`
- **Dimensions**: `Environment`, `Service`, `Source`

**Sample CloudWatch Logs Insights Query:**
```sql
fields @timestamp, event_id, fingerprint, action, jira_issue_key
| filter action = "jira_issue_created"
| sort @timestamp desc
| limit 20
```

For complete monitoring setup, see **[docs/monitoring.md](docs/monitoring.md)**.

## Testing

### Run All Tests
```bash
make test
```

### Unit Tests Only
```bash
make test-unit
```
Tests individual services in isolation with mocked dependencies:
- Fingerprinting logic (stability, collision resistance)
- PII sanitization (pattern matching, edge cases)
- Severity rule evaluation (threshold logic, precedence)
- Payload adapters (Vercel and GCP transformations)
- Frequency tracking (Redis operations, TTL handling)

### Integration Tests Only
```bash
make test-integration
```
Tests end-to-end flows with real Redis (via Docker Compose):
- Webhook processing (/events endpoint)
- Jira operations (create, search, comment, escalate) with mock Jira
- Event deduplication flow
- Comment rate limiting

### Coverage Report
```bash
make coverage
```
Generates HTML coverage report in `htmlcov/index.html`. Target: 90%+ code coverage.

### Test Configuration

Tests use:
- `pytest` as the test framework
- `fakeredis` for Redis mocking in unit tests
- `responses` for HTTP request mocking
- `freezegun` for time-based test scenarios
- Docker Compose for integration test dependencies

## Development

### Code Quality Tools

**Format code:**
```bash
make format
```
Runs `black` (code formatter) and `isort` (import sorter).

**Lint code:**
```bash
make lint
```
Runs `flake8` (style checker) and `mypy` (type checker).

**Security scan:**
```bash
make security
```
Runs `bandit` to detect security vulnerabilities.

### Pre-Commit Hooks

Install Git hooks for automatic code quality checks:
```bash
pre-commit install
```

Hooks run automatically on `git commit`:
- Black code formatting
- Flake8 linting
- Mypy type checking
- Trailing whitespace removal
- YAML validation

### Development Workflow

1. Create feature branch: `git checkout -b feature/your-feature`
2. Make changes and write tests
3. Run tests locally: `make test`
4. Format and lint: `make format lint`
5. Commit changes (pre-commit hooks run automatically)
6. Push and create pull request

### Hot Configuration Reload

Update rules without restarting the service:

1. Edit YAML configuration files in `config/`
2. Commit changes to Git
3. Send SIGHUP signal to running process:
   ```bash
   kill -HUP $(pgrep -f "gunicorn.*error-triage")
   ```
4. Service reloads configuration and validates rules
5. New rules apply immediately to incoming events

### Local Development with Docker Compose

```bash
docker-compose up --build
```

Services started:
- **app**: Flask application with hot-reload (port 8080)
- **redis**: Redis 7.2 (port 6379)
- **mongodb**: MongoDB 7.0 (port 27017, optional)

Code changes in `src/` automatically reload the application.

## Documentation

Comprehensive documentation available in the `docs/` directory:

- **[docs/architecture.md](docs/architecture.md)** - System design, component diagrams, data flow
- **[docs/api.md](docs/api.md)** - API endpoint specifications, request/response examples
- **[docs/configuration.md](docs/configuration.md)** - Complete configuration reference, environment variables, rule formats
- **[docs/deployment.md](docs/deployment.md)** - Deployment runbook, AWS setup, Terraform usage
- **[docs/monitoring.md](docs/monitoring.md)** - Monitoring setup, alerting thresholds, dashboard configuration
- **[docs/runbook.md](docs/runbook.md)** - Operational procedures, secret rotation, troubleshooting
- **[docs/vercel-setup.md](docs/vercel-setup.md)** - Vercel Log Drain configuration guide
- **[docs/gcp-setup.md](docs/gcp-setup.md)** - GCP Cloud Logging and Pub/Sub setup guide

## License

This project is licensed under the **MIT License**. See `LICENSE` file for details.

## Contributing

Contributions are welcome! Please follow these guidelines:

1. **Fork the repository** and create a feature branch
2. **Write tests** for all new functionality (maintain 90%+ coverage)
3. **Follow code style** - run `make format lint` before committing
4. **Update documentation** - reflect changes in relevant `docs/` files
5. **Submit pull request** with clear description of changes

**Code Review Checklist:**
- [ ] All tests pass (`make test`)
- [ ] Code coverage maintained or improved
- [ ] Linting and type checking pass (`make lint`)
- [ ] Security scan clean (`make security`)
- [ ] Documentation updated
- [ ] Configuration examples provided for new features

For questions or issues, please open a GitHub issue with detailed information.

---

**Project Status**: Production-ready v1.0

**Maintained by**: Jira Test Platform Team

**Support**: See [docs/runbook.md](docs/runbook.md) for operational support procedures
