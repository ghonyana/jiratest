# Configuration Reference Guide

## Table of Contents

1. [Overview](#overview)
2. [Environment Variables](#environment-variables)
3. [YAML Configuration Files](#yaml-configuration-files)
4. [AWS Secrets Manager](#aws-secrets-manager)
5. [Connection Parameters](#connection-parameters)
6. [Configuration Examples](#configuration-examples)
7. [Configuration Validation](#configuration-validation)
8. [Hot-Reload Configuration](#hot-reload-configuration)
9. [Secret Rotation](#secret-rotation)

---

## Overview

The Error Triage → Jira Upserter service uses a multi-layered configuration approach:

- **Environment Variables**: Runtime settings for Flask, service endpoints, and feature flags
- **YAML Rule Files**: Business logic configuration for severity classification, ownership routing, and PII sanitization
- **AWS Secrets Manager**: Secure storage for Jira credentials, webhook secrets, and MongoDB connection strings
- **Configuration Validation**: Fail-fast startup validation ensuring all required settings are present

All configuration is externalized to support environment-specific deployments (development, staging, production) without code changes.

---

## Environment Variables

### Flask Application Configuration

| Variable | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `FLASK_ENV` | string | No | `production` | Flask environment mode (`development`, `production`) |
| `FLASK_APP` | string | Yes | - | Flask application entry point (`src.app:create_app`) |
| `SECRET_KEY` | string | Yes | - | Flask secret key for session signing (generate with `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `DEBUG` | boolean | No | `false` | Enable Flask debug mode (NEVER set to `true` in production) |

### Service Endpoints

| Variable | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `REDIS_HOST` | string | Yes | - | Redis server hostname (e.g., `jiratest-error-triage-redis-prod.abc123.cache.amazonaws.com`) |
| `REDIS_PORT` | integer | No | `6379` | Redis server port |
| `REDIS_DB` | integer | No | `0` | Redis database number (0-15) |
| `REDIS_PASSWORD` | string | No | - | Redis authentication password (if ElastiCache auth enabled) |
| `REDIS_SSL` | boolean | No | `false` | Enable SSL/TLS for Redis connection |
| `MONGODB_URI` | string | No | - | MongoDB Atlas connection string (optional; only required if `ENABLE_MONGO=true`) |
| `JIRA_BASE_URL` | string | Yes | - | Jira Cloud base URL (e.g., `https://yourorg.atlassian.net`) loaded from Secrets Manager |

### AWS Configuration

| Variable | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `AWS_REGION` | string | Yes | - | AWS region for Secrets Manager and other services (e.g., `us-east-1`) |
| `AWS_DEFAULT_REGION` | string | No | `AWS_REGION` | Fallback region for boto3 SDK |
| `AWS_SECRETS_MANAGER_PREFIX` | string | No | `jira/jiratest` | Prefix for secret paths in Secrets Manager |

### Application Settings

| Variable | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `LOG_LEVEL` | string | No | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `ENVIRONMENT` | string | Yes | - | Deployment environment (`dev`, `staging`, `prod`) - affects severity rule selection |
| `COMMENT_RATE_LIMIT_MINUTES` | integer | No | `15` | Minimum minutes between Jira comments on the same issue (unless severity escalates) |
| `EVENT_DEDUP_TTL_SECONDS` | integer | No | `3600` | TTL for event deduplication cache (1 hour prevents duplicate processing of retried webhooks) |
| `FREQUENCY_WINDOW_SECONDS` | integer | No | `300` | Rolling window for error frequency counting (5 minutes) |
| `JIRA_API_TIMEOUT` | integer | No | `10` | Jira API request timeout in seconds |
| `JIRA_PROJECT_KEY` | string | Yes | - | Jira project key for issue creation (e.g., `ET` for Error Triage) |
| `JIRA_CUSTOM_SEVERITY_FIELD` | string | No | `customfield_10050` | Jira custom field ID for severity (SEV1-SEV4) |

### Feature Flags

| Variable | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `ENABLE_MONGO` | boolean | No | `false` | Enable MongoDB audit logging (stores error_events and jira_actions collections) |
| `ENABLE_RATE_LIMITING` | boolean | No | `true` | Enable comment rate limiting per issue (set to `false` for testing) |
| `ENABLE_AUTO_RESOLUTION` | boolean | No | `false` | Enable automatic issue resolution after silence period (future feature) |

### Webhook Authentication

| Variable | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `VERCEL_WEBHOOK_SECRET` | string | Yes* | - | Secret for HMAC verification of Vercel webhook signatures (loaded from Secrets Manager) |
| `GCP_OIDC_AUDIENCE` | string | Yes* | - | Expected audience for GCP Pub/Sub OIDC token validation (service URL, e.g., `https://error-triage.jiratest.com`) |

*Required if processing webhooks from that source

### Performance Tuning

| Variable | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `GUNICORN_WORKERS` | integer | No | `4` | Number of Gunicorn worker processes |
| `GUNICORN_THREADS` | integer | No | `2` | Number of threads per worker |
| `GUNICORN_TIMEOUT` | integer | No | `30` | Worker timeout in seconds |
| `GUNICORN_BIND` | string | No | `0.0.0.0:8080` | Bind address and port |

### Example .env File

```bash
# Flask Configuration
FLASK_ENV=production
FLASK_APP=src.app:create_app
SECRET_KEY=your-secret-key-here-generate-with-python-secrets
DEBUG=false

# Service Endpoints
REDIS_HOST=jiratest-error-triage-redis-prod.abc123.cache.amazonaws.com
REDIS_PORT=6379
REDIS_DB=0
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/jiratest-prod

# AWS Configuration
AWS_REGION=us-east-1
AWS_SECRETS_MANAGER_PREFIX=jira/jiratest

# Application Settings
LOG_LEVEL=INFO
ENVIRONMENT=prod
COMMENT_RATE_LIMIT_MINUTES=15
EVENT_DEDUP_TTL_SECONDS=3600
FREQUENCY_WINDOW_SECONDS=300
JIRA_PROJECT_KEY=ET
JIRA_CUSTOM_SEVERITY_FIELD=customfield_10050

# Feature Flags
ENABLE_MONGO=true
ENABLE_RATE_LIMITING=true

# Performance Tuning
GUNICORN_WORKERS=4
GUNICORN_THREADS=2
GUNICORN_TIMEOUT=30
```

---

## YAML Configuration Files

### Severity Rules (config/severity_rules.yaml)

**Purpose**: Map error frequency thresholds to Jira priority and custom severity levels per environment.

**Schema**:
```yaml
<environment>:
  - threshold: <integer>  # Minimum error count in frequency window
    priority: <string>    # Jira priority name (Highest, High, Medium, Low)
    severity: <string>    # Custom severity value (SEV1, SEV2, SEV3, SEV4)
```

**Evaluation Rules**:
- Rules are evaluated in descending threshold order (highest threshold first)
- First matching rule (count >= threshold) is selected
- Environment-specific rules apply when ENVIRONMENT matches the key
- `default` section applies when no environment-specific match found
- If no rule matches, fallback: `priority: "Low"`, `severity: "SEV4"`

**Complete Example**:
```yaml
# Production environment: strict thresholds for high-traffic services
production:
  - threshold: 50
    priority: "Highest"
    severity: "SEV1"
    description: "Critical: 50+ errors in 5 minutes indicates major incident"
  
  - threshold: 10
    priority: "High"
    severity: "SEV2"
    description: "High impact: 10+ errors require immediate attention"
  
  - threshold: 3
    priority: "Medium"
    severity: "SEV3"
    description: "Moderate issue: investigate within 4 hours"
  
  - threshold: 1
    priority: "Low"
    severity: "SEV4"
    description: "Single occurrence: monitor for recurrence"

# Staging environment: relaxed thresholds for pre-production testing
staging:
  - threshold: 20
    priority: "High"
    severity: "SEV2"
    description: "Frequent errors in staging may indicate release blocker"
  
  - threshold: 5
    priority: "Medium"
    severity: "SEV3"
    description: "Moderate staging issue"
  
  - threshold: 1
    priority: "Low"
    severity: "SEV4"
    description: "Single staging error"

# Development environment: lenient thresholds for local testing
development:
  - threshold: 10
    priority: "Medium"
    severity: "SEV3"
    description: "Repeated dev error"
  
  - threshold: 1
    priority: "Low"
    severity: "SEV4"
    description: "Dev error - expected during development"

# Default fallback for unknown environments
default:
  - threshold: 1
    priority: "Low"
    severity: "SEV4"
    description: "Unknown environment error"
```

**Custom Severity Field Mapping**:
The `severity` value is written to Jira's custom field specified by `JIRA_CUSTOM_SEVERITY_FIELD`. Ensure this field:
- Exists in your Jira project
- Is configured as a Select List (single choice) field
- Contains options: SEV1, SEV2, SEV3, SEV4
- Is visible on the Create Issue and Edit Issue screens

**Priority Escalation**:
When an error's frequency crosses a higher threshold, the service automatically:
1. Updates the issue's priority field in Jira
2. Updates the custom severity field
3. Adds a comment noting the escalation
4. Bypasses comment rate limiting for the escalation event

---

### Ownership Rules (config/ownership_rules.yaml)

**Purpose**: Route errors to appropriate team members or components based on service, path patterns, and error classes.

**Schema**:
```yaml
rules:
  - service: <string>           # Service name (exact match)
    path_regex: <regex>          # Optional: HTTP path pattern
    error_class: <string>        # Optional: Error class pattern
    assignee: <string>           # Optional: Atlassian account ID
    component: <string>          # Optional: Jira component name
    priority_order: <integer>    # Optional: Rule evaluation order (lower = higher priority)
```

**Evaluation Rules**:
1. Rules are evaluated in order of `priority_order` (ascending), or definition order if not specified
2. For each rule, check conditions in this order:
   - `service` must match (required field)
   - If `error_class` specified, must match error class exactly or as regex pattern
   - If `path_regex` specified, must match error path using Python `re.search()`
3. First matching rule determines assignment
4. If rule specifies `assignee`, issue is assigned to that Atlassian account ID
5. If rule specifies `component`, issue is added to that component (inherits component's default assignee if configured)
6. If no rules match, issue is created with project default assignment

**Complete Example**:
```yaml
rules:
  # High-priority specific error patterns
  - service: "web-app"
    error_class: "SecurityError"
    assignee: "5f8e9a1b2c3d4e5f6a7b8c9d"  # Security team lead
    priority_order: 1
    description: "Security errors always go to security team"
  
  # Backend API errors by path
  - service: "web-app"
    path_regex: "^/api/payments/"
    component: "Payments"
    priority_order: 10
    description: "Payment API errors to Payments component"
  
  - service: "web-app"
    path_regex: "^/api/auth/"
    assignee: "6a7b8c9d0e1f2a3b4c5d6e7f"  # Auth team lead
    priority_order: 10
    description: "Authentication errors to auth team"
  
  - service: "web-app"
    path_regex: "^/api/.*"
    component: "Backend"
    priority_order: 50
    description: "General API errors to Backend component"
  
  # Frontend errors by error class
  - service: "web-app"
    error_class: "TypeError|ReferenceError"
    component: "Frontend"
    priority_order: 20
    description: "JavaScript type errors to Frontend component"
  
  - service: "web-app"
    error_class: "NetworkError"
    component: "Infrastructure"
    priority_order: 20
    description: "Network errors to Infrastructure component"
  
  # Service-level defaults
  - service: "api-service"
    component: "Backend"
    priority_order: 100
    description: "All API service errors to Backend"
  
  - service: "worker-service"
    assignee: "7b8c9d0e1f2a3b4c5d6e7f8a"  # Worker team lead
    priority_order: 100
    description: "All worker errors to worker team"
  
  # Catch-all for unrouted services
  - service: ".*"
    component: "Triage"
    priority_order: 999
    description: "Unrouted errors to Triage component for manual assignment"
```

**Atlassian Account ID Format**:
- Find account IDs in Jira: User Profile → Account Settings → URL contains account ID
- Format: 24-character alphanumeric string (e.g., `5f8e9a1b2c3d4e5f6a7b8c9d`)
- Alternative: Email address (deprecated; use account ID for API stability)

**Component-Based Routing**:
- Components must exist in the Jira project before use
- Component default assignees are configured in Jira Project Settings → Components
- If component has no default assignee, issue remains unassigned

**Testing Ownership Rules**:
```python
# Test rule matching logic
from services.ownership_resolver import OwnershipResolver
from models.error_event import NormalizedErrorEvent

resolver = OwnershipResolver(config_path="config/ownership_rules.yaml")
event = NormalizedErrorEvent(
    service="web-app",
    environment="prod",
    error_class="TypeError",
    path="/api/checkout",
    # ... other fields
)

assignment = resolver.resolve(event)
# Returns: {"component": "Backend"} or {"assignee": "account_id"} or None
```

---

### Sanitization Patterns (config/sanitization_patterns.yaml)

**Purpose**: Define regex patterns to identify and remove PII from error messages and stack traces before fingerprint generation and Jira transmission.

**Schema**:
```yaml
patterns:
  - name: <string>            # Pattern identifier (for logging)
    pattern: <regex>           # Python regex pattern to match PII
    replacement: <string>      # Replacement token (e.g., [EMAIL], [UUID])
    flags: <string>            # Optional: regex flags (IGNORECASE, MULTILINE, etc.)
    description: <string>      # Pattern purpose documentation
```

**Pattern Application**:
1. Patterns are applied in definition order
2. Each pattern is compiled once at startup for performance
3. Sanitization occurs twice per event:
   - Before fingerprint generation (ensures consistent grouping)
   - Before Jira issue creation/comment (ensures no PII in Jira)
4. Replacement tokens are descriptive placeholders preserving error structure

**Complete Example**:
```yaml
patterns:
  # Email addresses
  - name: "email"
    pattern: '\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    replacement: '[EMAIL]'
    flags: "IGNORECASE"
    description: "RFC 5322 compliant email addresses"
  
  # UUID v4
  - name: "uuid"
    pattern: '\b[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b'
    replacement: '[UUID]'
    flags: "IGNORECASE"
    description: "UUID version 4 identifiers"
  
  # Generic UUIDs (all versions)
  - name: "uuid_generic"
    pattern: '\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'
    replacement: '[UUID]'
    flags: "IGNORECASE"
    description: "Generic UUID identifiers"
  
  # Numeric IDs in key-value patterns
  - name: "numeric_id"
    pattern: '\b(user_id|userId|customer_id|customerId|order_id|orderId|account_id|accountId|id)[:=]\s*\d+'
    replacement: '\1=[ID]'
    flags: "IGNORECASE"
    description: "Numeric IDs in key-value format"
  
  # Bearer tokens
  - name: "bearer_token"
    pattern: '\bBearer\s+[A-Za-z0-9_\-\.]+\b'
    replacement: 'Bearer [TOKEN]'
    description: "OAuth Bearer tokens in Authorization headers"
  
  # API keys (common formats)
  - name: "api_key"
    pattern: '\b(api_key|apiKey|api-key|apikey)[:=]\s*[A-Za-z0-9_\-]{20,}\b'
    replacement: '\1=[API_KEY]'
    flags: "IGNORECASE"
    description: "API keys in various formats"
  
  # Credit card numbers (basic pattern)
  - name: "credit_card"
    pattern: '\b(?:\d{4}[-\s]?){3}\d{4}\b'
    replacement: '[CARD]'
    description: "Credit card numbers (16 digits with optional separators)"
  
  # IPv4 addresses
  - name: "ipv4"
    pattern: '\b(?:\d{1,3}\.){3}\d{1,3}\b'
    replacement: '[IP]'
    description: "IPv4 addresses"
  
  # Phone numbers (US format)
  - name: "phone_us"
    pattern: '\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'
    replacement: '[PHONE]'
    description: "US phone numbers in various formats"
  
  # Social Security Numbers (US)
  - name: "ssn"
    pattern: '\b\d{3}-\d{2}-\d{4}\b'
    replacement: '[SSN]'
    description: "US Social Security Numbers"
  
  # Database connection strings (PostgreSQL, MongoDB)
  - name: "db_connection"
    pattern: '\b(postgres|postgresql|mongodb|mysql)://[^@]+@[^\s]+'
    replacement: '\1://[CREDENTIALS]@[HOST]'
    description: "Database connection strings with credentials"
  
  # JSON Web Tokens
  - name: "jwt"
    pattern: '\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+'
    replacement: '[JWT]'
    description: "JSON Web Tokens (JWTs)"
```

**Custom Pattern Addition**:
When adding custom patterns:
1. **Test thoroughly**: Use regex101.com or similar tool to validate pattern
2. **Order matters**: More specific patterns should come before generic ones
3. **Capture groups**: Use `\1`, `\2` in replacement to preserve context (e.g., field names)
4. **Performance**: Avoid catastrophic backtracking; use atomic groups or possessive quantifiers
5. **Validate**: Test against sample error messages to ensure accuracy

**Example Pattern Testing**:
```python
import re
import yaml

# Load patterns
with open('config/sanitization_patterns.yaml') as f:
    config = yaml.safe_load(f)

# Test message
message = "Error: user_id=12345 failed authentication with email john@example.com"

# Apply patterns
for pattern_config in config['patterns']:
    pattern = re.compile(pattern_config['pattern'], re.IGNORECASE)
    message = pattern.sub(pattern_config['replacement'], message)

print(message)
# Output: "Error: user_id=[ID] failed authentication with email [EMAIL]"
```

---

## AWS Secrets Manager

### Secret Structure

All sensitive credentials are stored in AWS Secrets Manager with standardized paths and JSON structures.

#### Jira Credentials Secret

**Path**: `jira/jiratest/{env}/credentials`

**Structure**:
```json
{
  "base_url": "https://yourorg.atlassian.net",
  "api_token": "ATATT3xFfGF0...",
  "email": "api-user@yourorg.com",
  "project_key": "ET",
  "custom_severity_field": "customfield_10050"
}
```

**Fields**:
- `base_url`: Jira Cloud instance URL (no trailing slash)
- `api_token`: Jira API token generated from Account Settings → Security → API tokens
- `email`: Email address associated with the API token
- `project_key`: Jira project key for issue creation (e.g., `ET` for Error Triage)
- `custom_severity_field`: Custom field ID for severity classification (optional, defaults to `customfield_10050`)

**Creation Command**:
```bash
aws secretsmanager create-secret \
  --name jira/jiratest/prod/credentials \
  --description "Jira API credentials for Error Triage service (production)" \
  --secret-string '{
    "base_url": "https://yourorg.atlassian.net",
    "api_token": "ATATT3xFfGF0...",
    "email": "api-user@yourorg.com",
    "project_key": "ET",
    "custom_severity_field": "customfield_10050"
  }' \
  --region us-east-1
```

#### Webhook Secret

**Path**: `jira/jiratest/{env}/webhook-secret`

**Structure**:
```json
{
  "vercel": "your-vercel-webhook-secret-here",
  "gcp_audience": "https://error-triage.jiratest.com"
}
```

**Fields**:
- `vercel`: Shared secret for HMAC verification of Vercel webhook signatures (configured in Vercel dashboard)
- `gcp_audience`: Expected audience claim in GCP OIDC tokens (must match service URL)

**Creation Command**:
```bash
aws secretsmanager create-secret \
  --name jira/jiratest/prod/webhook-secret \
  --description "Webhook authentication secrets for Error Triage service" \
  --secret-string '{
    "vercel": "'"$(openssl rand -hex 32)"'",
    "gcp_audience": "https://error-triage.jiratest.com"
  }' \
  --region us-east-1
```

#### MongoDB Connection String Secret

**Path**: `mongodb/jiratest/{env}/connection-string`

**Structure**:
```json
{
  "uri": "mongodb+srv://username:password@cluster.mongodb.net/jiratest-prod?retryWrites=true&w=majority"
}
```

**Fields**:
- `uri`: Complete MongoDB Atlas connection string including credentials, cluster, and database name

**Creation Command**:
```bash
aws secretsmanager create-secret \
  --name mongodb/jiratest/prod/connection-string \
  --description "MongoDB Atlas connection string for Error Triage audit logs" \
  --secret-string '{
    "uri": "mongodb+srv://username:password@cluster.mongodb.net/jiratest-prod?retryWrites=true&w=majority"
  }' \
  --region us-east-1
```

**Note**: This secret is only required if `ENABLE_MONGO=true`.

### Secret Access in Application

**Python Code Example**:
```python
import boto3
import json

def get_secret(secret_name: str, region: str = "us-east-1") -> dict:
    """Retrieve secret from AWS Secrets Manager."""
    client = boto3.client('secretsmanager', region_name=region)
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response['SecretString'])

# Load Jira credentials
jira_creds = get_secret('jira/jiratest/prod/credentials')
jira_base_url = jira_creds['base_url']
jira_api_token = jira_creds['api_token']
```

### IAM Permissions

**Required IAM Policy** (attach to ECS task role):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": [
        "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:jira/jiratest/*",
        "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:mongodb/jiratest/*"
      ]
    }
  ]
}
```

---

## Connection Parameters

### Redis Connection

**Standard Configuration**:
```python
import redis

redis_client = redis.Redis(
    host=os.getenv('REDIS_HOST'),
    port=int(os.getenv('REDIS_PORT', 6379)),
    db=int(os.getenv('REDIS_DB', 0)),
    password=os.getenv('REDIS_PASSWORD'),  # Optional
    ssl=os.getenv('REDIS_SSL', 'false').lower() == 'true',
    decode_responses=True,  # Automatically decode bytes to strings
    socket_connect_timeout=5,
    socket_timeout=5,
    retry_on_timeout=True,
    health_check_interval=30,
    max_connections=50  # Connection pool size
)
```

**Connection URL Format** (alternative):
```
redis://[:password@]host:port/db
rediss://[:password@]host:port/db  # SSL/TLS
```

**ElastiCache-Specific Settings**:
- **Encryption in transit**: Use `rediss://` protocol and set `REDIS_SSL=true`
- **Auth token**: Set `REDIS_PASSWORD` if authentication enabled
- **Cluster mode**: Service uses standalone mode (not cluster mode)
- **Endpoint discovery**: Use primary endpoint for read/write operations

**Key Patterns Used**:
| Pattern | Purpose | TTL | Example |
|---------|---------|-----|---------|
| `freq:{env}:{fingerprint}` | 5-minute error frequency counter | 300s | `freq:prod:a3f5b9c8d2e1...` |
| `dedup:{event_id}` | Event deduplication tracking | 3600s | `dedup:vercel-dpl_xyz123` |
| `comment_limit:{issue_key}` | Last comment timestamp per issue | 900s | `comment_limit:ET-1234` |

### MongoDB Connection

**Connection String Format**:
```
mongodb+srv://<username>:<password>@<cluster>.mongodb.net/<database>?retryWrites=true&w=majority
```

**Connection Options**:
```python
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# Standard connection
client = MongoClient(
    os.getenv('MONGODB_URI'),
    serverSelectionTimeoutMS=5000,  # 5 second timeout
    connectTimeoutMS=10000,          # 10 second connection timeout
    socketTimeoutMS=10000,
    retryWrites=True,
    maxPoolSize=10,
    minPoolSize=1
)

# Test connection
try:
    client.admin.command('ping')
    print("MongoDB connection successful")
except ConnectionFailure:
    print("MongoDB connection failed")
```

**Collections and Indexes**:

**Collection**: `error_events`
```python
# Create indexes
db.error_events.create_index([
    ("fingerprint", 1),
    ("environment", 1),
    ("occurred_at", -1)
])
db.error_events.create_index([("event_id", 1)], unique=True)
db.error_events.create_index([("jira_issue_key", 1)])
```

**Collection**: `jira_actions`
```python
# Create indexes
db.jira_actions.create_index([
    ("issue_key", 1),
    ("performed_at", -1)
])
db.jira_actions.create_index([("fingerprint", 1)])
```

**MongoDB Atlas Recommendations**:
- **Cluster tier**: M10 or higher for production (2 GB RAM minimum)
- **Region**: Same region as ECS deployment for low latency
- **Backup**: Enable continuous backups with 7-day retention
- **Network access**: Add ECS NAT Gateway IPs to IP whitelist

---

## Configuration Examples

### Local Development Environment

**docker-compose.yml**:
```yaml
version: '3.8'
services:
  app:
    build: .
    ports:
      - "8080:8080"
    environment:
      FLASK_ENV: development
      DEBUG: "true"
      REDIS_HOST: redis
      MONGODB_URI: mongodb://mongo:27017/jiratest-dev
      ENVIRONMENT: dev
      LOG_LEVEL: DEBUG
      ENABLE_MONGO: "true"
      ENABLE_RATE_LIMITING: "false"
    volumes:
      - ./src:/app/src
      - ./config:/app/config
    depends_on:
      - redis
      - mongo
  
  redis:
    image: redis:7.2-alpine
    ports:
      - "6379:6379"
    command: redis-server --appendonly yes
  
  mongo:
    image: mongo:7.0
    ports:
      - "27017:27017"
    environment:
      MONGO_INITDB_DATABASE: jiratest-dev
```

**.env.local**:
```bash
FLASK_ENV=development
FLASK_APP=src.app:create_app
SECRET_KEY=dev-secret-key-not-for-production
DEBUG=true

REDIS_HOST=localhost
REDIS_PORT=6379
MONGODB_URI=mongodb://localhost:27017/jiratest-dev

AWS_REGION=us-east-1
ENVIRONMENT=dev
LOG_LEVEL=DEBUG

JIRA_PROJECT_KEY=ET
COMMENT_RATE_LIMIT_MINUTES=1  # Faster testing
EVENT_DEDUP_TTL_SECONDS=300   # Shorter TTL for testing

ENABLE_MONGO=true
ENABLE_RATE_LIMITING=false  # Disable for easier testing

GUNICORN_WORKERS=2
GUNICORN_TIMEOUT=120  # Longer timeout for debugging
```

**Run Locally**:
```bash
# Start services
docker-compose up -d

# Run application with hot-reload
export $(cat .env.local | xargs)
flask run --host=0.0.0.0 --port=8080 --reload

# Test webhook endpoint
curl -X POST http://localhost:8080/events \
  -H "Content-Type: application/json" \
  -H "x-vercel-signature: test" \
  -d @tests/fixtures/vercel_payload.json
```

### Staging Environment

**.env.staging**:
```bash
FLASK_ENV=production
FLASK_APP=src.app:create_app
DEBUG=false

REDIS_HOST=jiratest-error-triage-redis-staging.abc123.cache.amazonaws.com
REDIS_PORT=6379
REDIS_SSL=true
MONGODB_URI=  # Loaded from Secrets Manager

AWS_REGION=us-east-1
AWS_SECRETS_MANAGER_PREFIX=jira/jiratest
ENVIRONMENT=staging

LOG_LEVEL=INFO
JIRA_PROJECT_KEY=ET
COMMENT_RATE_LIMIT_MINUTES=10  # Slightly relaxed for staging
EVENT_DEDUP_TTL_SECONDS=3600
FREQUENCY_WINDOW_SECONDS=300

ENABLE_MONGO=true
ENABLE_RATE_LIMITING=true

GUNICORN_WORKERS=4
GUNICORN_THREADS=2
GUNICORN_TIMEOUT=30
```

**Staging-Specific Configuration**:
- Relaxed severity thresholds in `config/severity_rules.yaml` (staging section)
- Test assignees in `config/ownership_rules.yaml`
- Shorter comment rate limits for faster testing
- MongoDB audit logging enabled for validation

### Production Environment

**.env.production**:
```bash
FLASK_ENV=production
FLASK_APP=src.app:create_app
DEBUG=false

REDIS_HOST=jiratest-error-triage-redis-prod.xyz789.cache.amazonaws.com
REDIS_PORT=6379
REDIS_SSL=true
REDIS_PASSWORD=  # Loaded from Secrets Manager if auth enabled
MONGODB_URI=  # Loaded from Secrets Manager

AWS_REGION=us-east-1
AWS_SECRETS_MANAGER_PREFIX=jira/jiratest
ENVIRONMENT=prod

LOG_LEVEL=INFO
JIRA_PROJECT_KEY=ET
JIRA_CUSTOM_SEVERITY_FIELD=customfield_10050
JIRA_API_TIMEOUT=10
COMMENT_RATE_LIMIT_MINUTES=15
EVENT_DEDUP_TTL_SECONDS=3600
FREQUENCY_WINDOW_SECONDS=300

ENABLE_MONGO=true
ENABLE_RATE_LIMITING=true

GUNICORN_WORKERS=4
GUNICORN_THREADS=2
GUNICORN_TIMEOUT=30
GUNICORN_BIND=0.0.0.0:8080
```

**Production-Specific Configuration**:
- Strict severity thresholds (≥50 errors → SEV1)
- Real team member assignments in ownership rules
- Full audit logging to MongoDB
- Enhanced security: SSL/TLS for all connections
- Optimized performance settings

**Terraform Variables** (prod.tfvars):
```hcl
environment           = "prod"
region               = "us-east-1"
redis_node_type      = "cache.t4g.medium"
redis_num_cache_nodes = 2
ecs_task_cpu         = "512"
ecs_task_memory      = "1024"
ecs_desired_count    = 2
ecs_max_count        = 10
enable_auto_scaling  = true
```

---

## Configuration Validation

### Startup Validation

The application performs comprehensive configuration validation at startup:

**Required Variables Check**:
```python
# src/app/config.py
def validate_config(config: dict) -> List[str]:
    """Validate configuration and return list of errors."""
    errors = []
    
    # Required environment variables
    required_vars = [
        'FLASK_APP',
        'SECRET_KEY',
        'REDIS_HOST',
        'AWS_REGION',
        'ENVIRONMENT',
        'JIRA_PROJECT_KEY'
    ]
    
    for var in required_vars:
        if not config.get(var):
            errors.append(f"Missing required environment variable: {var}")
    
    # Conditional requirements
    if config.get('ENABLE_MONGO') == 'true' and not config.get('MONGODB_URI'):
        errors.append("MONGODB_URI required when ENABLE_MONGO=true")
    
    # Value validation
    if config.get('ENVIRONMENT') not in ['dev', 'staging', 'prod']:
        errors.append(f"Invalid ENVIRONMENT value: {config.get('ENVIRONMENT')}")
    
    # Numeric ranges
    if config.get('GUNICORN_WORKERS'):
        workers = int(config.get('GUNICORN_WORKERS'))
        if workers < 1 or workers > 16:
            errors.append(f"GUNICORN_WORKERS must be 1-16, got {workers}")
    
    return errors
```

**YAML Configuration Validation**:
```python
# src/services/severity_engine.py
def validate_severity_rules(rules: dict) -> List[str]:
    """Validate severity rules configuration."""
    errors = []
    
    valid_priorities = ['Highest', 'High', 'Medium', 'Low']
    valid_severities = ['SEV1', 'SEV2', 'SEV3', 'SEV4']
    
    for env, env_rules in rules.items():
        if not isinstance(env_rules, list):
            errors.append(f"Rules for environment '{env}' must be a list")
            continue
        
        for idx, rule in enumerate(env_rules):
            # Required fields
            if 'threshold' not in rule:
                errors.append(f"{env}[{idx}]: missing 'threshold' field")
            if 'priority' not in rule:
                errors.append(f"{env}[{idx}]: missing 'priority' field")
            if 'severity' not in rule:
                errors.append(f"{env}[{idx}]: missing 'severity' field")
            
            # Value validation
            if rule.get('priority') not in valid_priorities:
                errors.append(f"{env}[{idx}]: invalid priority '{rule.get('priority')}'")
            if rule.get('severity') not in valid_severities:
                errors.append(f"{env}[{idx}]: invalid severity '{rule.get('severity')}'")
            
            # Threshold must be positive integer
            threshold = rule.get('threshold')
            if not isinstance(threshold, int) or threshold < 1:
                errors.append(f"{env}[{idx}]: threshold must be positive integer")
    
    return errors
```

**Fail-Fast Behavior**:
```python
# src/app/__init__.py
def create_app(config_name='production'):
    app = Flask(__name__)
    
    # Load configuration
    app.config.from_object(Config)
    
    # Validate configuration
    errors = validate_config(app.config)
    if errors:
        for error in errors:
            app.logger.error(f"Configuration error: {error}")
        raise ValueError(f"Configuration validation failed: {errors}")
    
    # Validate YAML files
    severity_errors = validate_severity_rules_file('config/severity_rules.yaml')
    if severity_errors:
        raise ValueError(f"Severity rules validation failed: {severity_errors}")
    
    # ... rest of initialization
```

### Runtime Health Checks

**Configuration Health Endpoint**:
```python
@health_bp.route('/healthz/config', methods=['GET'])
def config_health():
    """Check configuration file integrity."""
    checks = {}
    
    # Check YAML files exist and are valid
    for config_file in ['severity_rules.yaml', 'ownership_rules.yaml', 'sanitization_patterns.yaml']:
        path = f'config/{config_file}'
        try:
            with open(path) as f:
                yaml.safe_load(f)
            checks[config_file] = {"status": "ok", "path": path}
        except FileNotFoundError:
            checks[config_file] = {"status": "missing", "path": path}
        except yaml.YAMLError as e:
            checks[config_file] = {"status": "invalid", "error": str(e)}
    
    all_ok = all(check["status"] == "ok" for check in checks.values())
    status_code = 200 if all_ok else 503
    
    return jsonify({"status": "healthy" if all_ok else "unhealthy", "checks": checks}), status_code
```

---

## Hot-Reload Configuration

The service supports hot-reloading YAML configuration files without restarting containers, enabling zero-downtime rule updates.

### Signal-Based Reload

**SIGHUP Handler**:
```python
# src/app/__init__.py
import signal
import logging

logger = logging.getLogger(__name__)

def reload_configuration():
    """Reload all YAML configuration files."""
    try:
        logger.info("Reloading configuration files...")
        
        # Reload severity rules
        from services.severity_engine import SeverityRulesEngine
        SeverityRulesEngine.reload()
        
        # Reload ownership rules
        from services.ownership_resolver import OwnershipResolver
        OwnershipResolver.reload()
        
        # Reload sanitization patterns
        from services.sanitizer import PIISanitizer
        PIISanitizer.reload()
        
        logger.info("Configuration reload complete")
    except Exception as e:
        logger.error(f"Configuration reload failed: {e}")

def setup_signal_handlers(app):
    """Register signal handlers for graceful reload."""
    def sighup_handler(signum, frame):
        with app.app_context():
            reload_configuration()
    
    signal.signal(signal.SIGHUP, sighup_handler)
```

### Triggering Reload

**In ECS Container**:
```bash
# Find container process ID
PID=$(pgrep -f gunicorn)

# Send SIGHUP signal
kill -HUP $PID

# Or use ECS exec
aws ecs execute-command \
  --cluster jiratest-error-triage-prod \
  --task <task-id> \
  --container error-triage \
  --interactive \
  --command "/bin/sh -c 'kill -HUP 1'"
```

**Via ConfigMap Update** (for Kubernetes deployments):
```bash
# Update ConfigMap
kubectl create configmap error-triage-config \
  --from-file=config/ \
  --dry-run=client -o yaml | kubectl apply -f -

# Trigger reload
kubectl exec deployment/error-triage -- kill -HUP 1
```

### File Watching (Alternative)

**Automatic Reload on File Change**:
```python
# src/utils/config_watcher.py
import time
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class ConfigFileHandler(FileSystemEventHandler):
    """Watch configuration directory for changes."""
    
    def __init__(self, reload_callback):
        self.reload_callback = reload_callback
        self.last_reload = time.time()
    
    def on_modified(self, event):
        if event.src_path.endswith('.yaml'):
            # Debounce: only reload if >5 seconds since last reload
            if time.time() - self.last_reload > 5:
                logger.info(f"Configuration file changed: {event.src_path}")
                self.reload_callback()
                self.last_reload = time.time()

def start_config_watcher(app):
    """Start file watcher for configuration directory."""
    event_handler = ConfigFileHandler(reload_callback=reload_configuration)
    observer = Observer()
    observer.schedule(event_handler, path='config/', recursive=False)
    observer.start()
    logger.info("Configuration file watcher started")
```

### Reload Safety

**Atomic Operations**:
```python
# src/services/severity_engine.py
import threading

class SeverityRulesEngine:
    _lock = threading.RLock()
    _rules = None
    
    @classmethod
    def reload(cls):
        """Thread-safe configuration reload."""
        with cls._lock:
            try:
                new_rules = cls._load_rules('config/severity_rules.yaml')
                errors = cls._validate_rules(new_rules)
                if errors:
                    raise ValueError(f"Invalid rules: {errors}")
                cls._rules = new_rules
                logger.info("Severity rules reloaded successfully")
            except Exception as e:
                logger.error(f"Failed to reload severity rules: {e}")
                # Keep existing rules on failure
                raise
    
    @classmethod
    def evaluate(cls, env: str, count: int) -> Tuple[str, str]:
        """Use current rules (thread-safe read)."""
        with cls._lock:
            if cls._rules is None:
                cls.reload()
            return cls._evaluate_with_rules(cls._rules, env, count)
```

---

## Secret Rotation

### Rotation Schedule

| Secret Type | Rotation Frequency | Automated | Notes |
|-------------|-------------------|-----------|-------|
| Jira API Token | 90 days | Manual | Generate new token in Jira, update Secrets Manager |
| Webhook Secrets | 180 days | Manual | Coordinate with Vercel/GCP configuration updates |
| MongoDB Password | 90 days | Semi-automated | Use Atlas automatic rotation, update secret |
| Redis Password | 90 days | Manual | Update ElastiCache auth token, update application |

### Jira API Token Rotation

**Procedure**:
1. Generate new API token in Jira:
   - Navigate to Account Settings → Security → API tokens
   - Click "Create API token"
   - Copy token immediately (shown only once)

2. Update Secrets Manager with new token:
```bash
aws secretsmanager update-secret \
  --secret-id jira/jiratest/prod/credentials \
  --secret-string '{
    "base_url": "https://yourorg.atlassian.net",
    "api_token": "NEW_TOKEN_HERE",
    "email": "api-user@yourorg.com",
    "project_key": "ET",
    "custom_severity_field": "customfield_10050"
  }' \
  --region us-east-1
```

3. Restart ECS tasks to pick up new secret:
```bash
aws ecs update-service \
  --cluster jiratest-error-triage-prod \
  --service error-triage \
  --force-new-deployment \
  --region us-east-1
```

4. Verify new token works:
```bash
curl -u "api-user@yourorg.com:NEW_TOKEN_HERE" \
  https://yourorg.atlassian.net/rest/api/3/myself
```

5. Revoke old token in Jira after successful verification

### Webhook Secret Rotation

**Vercel Webhook**:
1. Generate new secret: `openssl rand -hex 32`
2. Update Secrets Manager:
```bash
aws secretsmanager update-secret \
  --secret-id jira/jiratest/prod/webhook-secret \
  --secret-string '{
    "vercel": "NEW_SECRET_HERE",
    "gcp_audience": "https://error-triage.jiratest.com"
  }'
```

3. Restart ECS service (see above)
4. Update Vercel Log Drain configuration with new secret
5. Test webhook delivery

**GCP Pub/Sub**:
- OIDC audience changes are rare (only if service URL changes)
- No rotation required for audience; OIDC tokens are self-rotating

### MongoDB Atlas Password Rotation

**Automated Rotation** (if using AWS Secrets Manager rotation):
1. Enable automatic rotation in Secrets Manager (30-day interval)
2. Use rotation Lambda function to:
   - Generate new password in MongoDB Atlas
   - Update secret value
   - Verify connectivity

**Manual Rotation**:
1. Create new database user in Atlas with temporary name
2. Update secret with new connection string
3. Restart application
4. Verify connectivity
5. Delete old database user

### Zero-Downtime Rotation Strategy

**Dual-Secret Support**:
```python
# Support both old and new secrets during rotation window
def get_jira_client():
    try:
        # Try new secret first
        creds = get_secret('jira/jiratest/prod/credentials')
        return JIRA(server=creds['base_url'], basic_auth=(creds['email'], creds['api_token']))
    except JiraError:
        # Fall back to old secret
        logger.warning("New Jira credentials failed, trying backup")
        creds = get_secret('jira/jiratest/prod/credentials-old')
        return JIRA(server=creds['base_url'], basic_auth=(creds['email'], creds['api_token']))
```

**Blue-Green Deployment for Secret Updates**:
1. Deploy new task definition with updated secret references
2. Keep old tasks running until new tasks are healthy
3. Drain connections from old tasks
4. Terminate old tasks after 5-minute grace period

---

## Additional Resources

- **AWS Secrets Manager Documentation**: https://docs.aws.amazon.com/secretsmanager/
- **Jira Cloud REST API**: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
- **Redis Command Reference**: https://redis.io/commands/
- **MongoDB Connection String Format**: https://www.mongodb.com/docs/manual/reference/connection-string/
- **YAML Specification**: https://yaml.org/spec/1.2/spec.html
- **Python Regex Documentation**: https://docs.python.org/3/library/re.html

---

## Next Steps

1. **Initial Setup**: Copy `.env.example` to `.env` and fill in required values
2. **Create Secrets**: Use AWS CLI commands above to create Secrets Manager secrets
3. **Validate Configuration**: Run `python -m src.app` to test configuration validation
4. **Deploy Infrastructure**: Apply Terraform modules to provision AWS resources
5. **Test Webhooks**: Send sample payloads to `/events` endpoint and verify Jira issue creation

For deployment instructions, see [docs/deployment.md](./deployment.md).
For troubleshooting configuration issues, see [docs/runbook.md](./runbook.md).
