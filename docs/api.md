# API Reference Documentation

**Service**: Error Triage → Jira Upserter  
**Version**: 1.0.0  
**Base URL**: `https://error-triage.jiratest.com`

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Endpoints](#endpoints)
   - [POST /events](#post-events)
   - [GET /healthz](#get-healthz)
   - [GET /metrics](#get-metrics)
4. [Error Codes Reference](#error-codes-reference)
5. [OpenAPI Specification](#openapi-specification)
6. [Examples](#examples)

---

## Overview

The Error Triage service provides HTTP endpoints for:

- **Webhook ingestion** from Vercel Log Drain and GCP Cloud Logging
- **Health monitoring** for container orchestration
- **Metrics exposition** for Prometheus scraping

All endpoints return JSON responses (except `/metrics` which returns Prometheus text format).

### Base URL

- **Production**: `https://error-triage.jiratest.com`
- **Staging**: `https://error-triage-staging.jiratest.com`
- **Development**: `http://localhost:8080`

### Content Type

All request and response bodies use `application/json` except where noted.

---

## Authentication

### Vercel Webhook Authentication

Vercel webhooks use HMAC-SHA256 signature verification:

**Header**: `x-vercel-signature`

**Algorithm**:
```
signature = HMAC-SHA256(webhook_secret, request_body)
```

**Verification Process**:
1. Extract raw request body as bytes
2. Compute HMAC-SHA256 using shared secret
3. Compare computed signature with header value
4. Reject if signatures don't match

**Configuration**:
- Webhook secret stored in AWS Secrets Manager: `jira/jiratest/{env}/webhook-secret`
- Secret rotation: every 180 days

### GCP Cloud Logging Authentication

GCP Pub/Sub push subscriptions use OIDC JWT token verification:

**Header**: `Authorization: Bearer <JWT_TOKEN>`

**Token Validation**:
1. Extract JWT token from Authorization header
2. Verify token signature using Google's public keys
3. Validate token audience matches service endpoint URL
4. Verify issuer is `https://accounts.google.com` or `https://www.googleapis.com`
5. Check token expiration

**Configuration**:
- Service account: `error-triage@{project}.iam.gserviceaccount.com`
- Audience: Service endpoint URL (e.g., `https://error-triage.jiratest.com/events`)

---

## Endpoints

### POST /events

Accepts error event webhooks from Vercel Log Drain and GCP Cloud Logging Pub/Sub push subscriptions.

#### Request

**URL**: `/events`

**Method**: `POST`

**Headers**:

| Header | Required | Description |
|--------|----------|-------------|
| `Content-Type` | Yes | Must be `application/json` |
| `x-vercel-signature` | Conditional | Required for Vercel webhooks (HMAC-SHA256 signature) |
| `Authorization` | Conditional | Required for GCP webhooks (Bearer JWT token) |

#### Vercel Payload Format

```json
{
  "source": "vercel",
  "deployment": {
    "id": "dpl_xyz123",
    "url": "my-app-abc123.vercel.app",
    "name": "web-app",
    "env": "production"
  },
  "message": "Error: Cannot read property 'x' of undefined",
  "level": "error",
  "timestamp": "2025-01-15T10:30:45.123Z",
  "environment": "production",
  "path": "/api/checkout",
  "url": "https://my-app.com/api/checkout",
  "traceId": "abc123def456",
  "stack": "TypeError: Cannot read property 'x' of undefined\n    at /app/pages/checkout.tsx:123:45\n    at processRequest (/app/lib/api.ts:67:12)"
}
```

**Vercel Payload Fields**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | No | Identifies payload source (auto-detected if missing) |
| `deployment.id` | string | Yes | Vercel deployment ID |
| `deployment.url` | string | Yes | Deployment URL |
| `deployment.name` | string | No | Service name (defaults to deployment name) |
| `deployment.env` | string | No | Environment (production/staging/dev) |
| `message` | string | Yes | Error message |
| `level` | string | Yes | Log level (error/warning/info) |
| `timestamp` | string | Yes | ISO 8601 timestamp |
| `environment` | string | No | Runtime environment |
| `path` | string | No | Request path where error occurred |
| `url` | string | No | Full request URL |
| `traceId` | string | No | Trace ID for log correlation |
| `stack` | string | No | Stack trace |

#### GCP Payload Format

```json
{
  "message": {
    "data": "eyJzZXZlcml0eSI6IkVSUk9SIiwidGV4dFBheWxvYWQiOiJUeXBlRXJyb3I6IENhbm5vdCByZWFkIHByb3BlcnR5ICd4JyBvZiB1bmRlZmluZWQiLCJyZXNvdXJjZSI6eyJ0eXBlIjoiY2xvdWRfcnVuX3JldmlzaW9uIiwibGFiZWxzIjp7InNlcnZpY2VfbmFtZSI6ImFwaS1zZXJ2aWNlIiwicmV2aXNpb25fbmFtZSI6ImFwaS1zZXJ2aWNlLTAwMDQyLXh5eiJ9fSwiaW5zZXJ0SWQiOiJhYmMxMjMiLCJ0aW1lc3RhbXAiOiIyMDI1LTAxLTE1VDEwOjMwOjQ1LjEyM1oifQ==",
    "messageId": "123456789",
    "publishTime": "2025-01-15T10:30:45.123Z"
  },
  "subscription": "projects/my-project/subscriptions/error-events-push"
}
```

**GCP Decoded Log Entry** (base64 decoded from `message.data`):

```json
{
  "severity": "ERROR",
  "textPayload": "TypeError: Cannot read property 'x' of undefined",
  "jsonPayload": {
    "error": {
      "message": "Cannot read property 'x' of undefined",
      "stack": "TypeError: Cannot read property 'x' of undefined\n    at /app/api/handler.js:45:12"
    },
    "context": {
      "path": "/api/checkout",
      "method": "POST"
    }
  },
  "resource": {
    "type": "cloud_run_revision",
    "labels": {
      "service_name": "api-service",
      "revision_name": "api-service-00042-xyz",
      "location": "us-central1"
    }
  },
  "insertId": "abc123",
  "timestamp": "2025-01-15T10:30:45.123Z",
  "labels": {
    "environment": "production"
  }
}
```

**GCP Log Entry Fields**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `severity` | string | Yes | Log severity (ERROR/WARNING/INFO) |
| `textPayload` | string | Conditional | Plain text log message |
| `jsonPayload` | object | Conditional | Structured JSON log data |
| `resource.labels.service_name` | string | Yes | Service identifier |
| `resource.labels.revision_name` | string | No | Service revision/release |
| `insertId` | string | Yes | Unique log entry ID for deduplication |
| `timestamp` | string | Yes | ISO 8601 timestamp |
| `labels.environment` | string | No | Environment (prod/staging/dev) |

#### Response

**Status Code**: `202 Accepted`

**Body**:
```json
{
  "status": "accepted",
  "event_id": "vercel-dpl_xyz123-abc123def456",
  "message": "Event queued for processing"
}
```

**Response Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always "accepted" for successful requests |
| `event_id` | string | Unique event identifier for tracking |
| `message` | string | Human-readable confirmation message |

#### Error Responses

**400 Bad Request** - Malformed Payload

```json
{
  "error": "Bad Request",
  "message": "Invalid JSON payload: missing required field 'message'",
  "code": "INVALID_PAYLOAD"
}
```

**401 Unauthorized** - Authentication Failed

```json
{
  "error": "Unauthorized",
  "message": "Invalid signature: HMAC verification failed",
  "code": "AUTH_FAILED"
}
```

**429 Too Many Requests** - Rate Limit Exceeded

```json
{
  "error": "Too Many Requests",
  "message": "Rate limit exceeded: 100 requests per minute allowed",
  "code": "RATE_LIMIT_EXCEEDED",
  "retry_after": 45
}
```

**500 Internal Server Error** - Server Error

```json
{
  "error": "Internal Server Error",
  "message": "Failed to process event: Redis connection timeout",
  "code": "INTERNAL_ERROR",
  "request_id": "req_abc123"
}
```

#### Rate Limiting

| Environment | Limit | Window | Scope |
|-------------|-------|--------|-------|
| Production | 100 req/min | 60 seconds | Per source IP |
| Staging | 50 req/min | 60 seconds | Per source IP |
| Development | 20 req/min | 60 seconds | Per source IP |

**Rate Limit Headers** (included in all responses):

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1610707845
```

#### Performance SLO

- **Response Time**: < 200ms (p95)
- **Success Rate**: > 99.9%
- **Availability**: > 99.95%

#### Processing Flow

1. **Authentication**: Verify webhook signature or JWT token
2. **Validation**: Parse and validate payload structure
3. **Deduplication**: Check event_id/insertId against Redis cache
4. **Normalization**: Transform to internal `NormalizedErrorEvent` format
5. **Queue**: Enqueue to SQS (or Redis Streams) for async processing
6. **Response**: Return 202 Accepted immediately

Background processing (async):
- Generate error fingerprint
- Track frequency in Redis
- Evaluate severity rules
- Search/create/update Jira issue
- Emit metrics and logs

---

### GET /healthz

Health check endpoint for container orchestration (ECS, Kubernetes).

#### Request

**URL**: `/healthz`

**Method**: `GET`

**Headers**: None required

**Authentication**: None (internal endpoint)

#### Response

**Status Code**: `200 OK` (healthy) or `503 Service Unavailable` (unhealthy)

**Body** (Healthy):

```json
{
  "status": "healthy",
  "timestamp": "2025-01-15T10:30:45.123Z",
  "version": "1.0.0",
  "checks": {
    "redis": {
      "status": "up",
      "latency_ms": 2,
      "message": "PONG received"
    },
    "jira": {
      "status": "up",
      "latency_ms": 85,
      "message": "API reachable"
    },
    "mongodb": {
      "status": "up",
      "latency_ms": 15,
      "message": "Ping successful"
    }
  }
}
```

**Body** (Degraded - Optional Dependency Down):

```json
{
  "status": "degraded",
  "timestamp": "2025-01-15T10:30:45.123Z",
  "version": "1.0.0",
  "checks": {
    "redis": {
      "status": "up",
      "latency_ms": 2,
      "message": "PONG received"
    },
    "jira": {
      "status": "up",
      "latency_ms": 85,
      "message": "API reachable"
    },
    "mongodb": {
      "status": "down",
      "latency_ms": null,
      "message": "Connection timeout after 5000ms"
    }
  }
}
```

**Body** (Unhealthy - Required Dependency Down):

```json
{
  "status": "unhealthy",
  "timestamp": "2025-01-15T10:30:45.123Z",
  "version": "1.0.0",
  "checks": {
    "redis": {
      "status": "down",
      "latency_ms": null,
      "message": "Connection refused"
    },
    "jira": {
      "status": "up",
      "latency_ms": 85,
      "message": "API reachable"
    },
    "mongodb": {
      "status": "up",
      "latency_ms": 15,
      "message": "Ping successful"
    }
  }
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Overall health status: "healthy", "degraded", "unhealthy" |
| `timestamp` | string | ISO 8601 timestamp of health check |
| `version` | string | Service version |
| `checks` | object | Individual dependency health checks |
| `checks.<service>.status` | string | Dependency status: "up", "down" |
| `checks.<service>.latency_ms` | number | Response time in milliseconds (null if down) |
| `checks.<service>.message` | string | Human-readable status description |

#### Dependency Classification

**Required Dependencies** (failure causes status "unhealthy", returns 503):
- **Redis**: Required for frequency tracking and deduplication
- **Jira**: Required for issue creation and updates

**Optional Dependencies** (failure causes status "degraded", returns 200):
- **MongoDB**: Optional audit log storage (if `ENABLE_MONGO=true`)

#### Health Check Operations

| Dependency | Check Operation | Timeout | Retry |
|------------|----------------|---------|-------|
| Redis | `PING` command | 2 seconds | No |
| Jira | `GET /rest/api/3/serverInfo` | 5 seconds | No |
| MongoDB | `db.admin().command('ping')` | 3 seconds | No |

#### Use Cases

- **ECS Health Checks**: Configure ECS to call `/healthz` every 30 seconds
- **Kubernetes Liveness Probe**: Check service is running
- **Kubernetes Readiness Probe**: Check service can accept traffic
- **Monitoring Alerts**: Alert if health check fails for > 2 minutes

---

### GET /metrics

Prometheus metrics endpoint for monitoring and alerting.

#### Request

**URL**: `/metrics`

**Method**: `GET`

**Headers**: None required

**Authentication**: None (internal endpoint, scraped by Prometheus)

#### Response

**Status Code**: `200 OK`

**Content-Type**: `text/plain; version=0.0.4; charset=utf-8`

**Body** (Prometheus Text Format):

```
# HELP events_received_total Total number of webhook events received
# TYPE events_received_total counter
events_received_total{environment="production",source="vercel"} 1523
events_received_total{environment="production",source="gcp"} 847
events_received_total{environment="staging",source="vercel"} 342

# HELP events_processed_total Successfully processed events
# TYPE events_processed_total counter
events_processed_total{environment="production",source="vercel"} 1518
events_processed_total{environment="production",source="gcp"} 845

# HELP events_deduplicated_total Duplicate events dropped
# TYPE events_deduplicated_total counter
events_deduplicated_total{environment="production"} 234

# HELP jira_issues_created_total New Jira issues created
# TYPE jira_issues_created_total counter
jira_issues_created_total{environment="production",project="ET"} 87

# HELP jira_comments_added_total Comments added to existing issues
# TYPE jira_comments_added_total counter
jira_comments_added_total{environment="production",project="ET"} 456

# HELP jira_escalations_total Issues with escalated priority
# TYPE jira_escalations_total counter
jira_escalations_total{environment="production",priority="High"} 12
jira_escalations_total{environment="production",priority="Highest"} 3

# HELP event_processing_duration_seconds Event processing latency
# TYPE event_processing_duration_seconds histogram
event_processing_duration_seconds_bucket{environment="production",le="0.05"} 1234
event_processing_duration_seconds_bucket{environment="production",le="0.1"} 1456
event_processing_duration_seconds_bucket{environment="production",le="0.2"} 1502
event_processing_duration_seconds_bucket{environment="production",le="0.5"} 1515
event_processing_duration_seconds_bucket{environment="production",le="+Inf"} 1518
event_processing_duration_seconds_sum{environment="production"} 125.34
event_processing_duration_seconds_count{environment="production"} 1518

# HELP jira_api_latency_seconds Jira API call duration
# TYPE jira_api_latency_seconds histogram
jira_api_latency_seconds_bucket{environment="production",operation="search",le="0.5"} 423
jira_api_latency_seconds_bucket{environment="production",operation="search",le="1.0"} 498
jira_api_latency_seconds_bucket{environment="production",operation="search",le="2.0"} 512
jira_api_latency_seconds_bucket{environment="production",operation="search",le="+Inf"} 515
jira_api_latency_seconds_sum{environment="production",operation="search"} 387.56
jira_api_latency_seconds_count{environment="production",operation="search"} 515

# HELP redis_latency_seconds Redis operation duration
# TYPE redis_latency_seconds histogram
redis_latency_seconds_bucket{environment="production",operation="incr",le="0.001"} 1345
redis_latency_seconds_bucket{environment="production",operation="incr",le="0.005"} 1512
redis_latency_seconds_bucket{environment="production",operation="incr",le="0.01"} 1518
redis_latency_seconds_bucket{environment="production",operation="incr",le="+Inf"} 1518
redis_latency_seconds_sum{environment="production",operation="incr"} 2.34
redis_latency_seconds_count{environment="production",operation="incr"} 1518

# HELP errors_total Application errors by type
# TYPE errors_total counter
errors_total{environment="production",error_type="jira_api_error"} 5
errors_total{environment="production",error_type="redis_timeout"} 2
errors_total{environment="production",error_type="auth_failed"} 12
errors_total{environment="production",error_type="invalid_payload"} 8
```

#### Metrics Catalog

##### Counter Metrics

| Metric Name | Labels | Description |
|-------------|--------|-------------|
| `events_received_total` | environment, source | Total webhook events received |
| `events_processed_total` | environment, source | Successfully processed events |
| `events_deduplicated_total` | environment | Duplicate events dropped |
| `jira_issues_created_total` | environment, project | New Jira issues created |
| `jira_comments_added_total` | environment, project | Comments added to existing issues |
| `jira_escalations_total` | environment, priority | Issues with escalated priority |
| `errors_total` | environment, error_type | Application errors by type |

##### Histogram Metrics

| Metric Name | Labels | Description | Buckets |
|-------------|--------|-------------|---------|
| `event_processing_duration_seconds` | environment | Event processing latency | 0.05, 0.1, 0.2, 0.5, +Inf |
| `jira_api_latency_seconds` | environment, operation | Jira API call duration | 0.5, 1.0, 2.0, 5.0, +Inf |
| `redis_latency_seconds` | environment, operation | Redis operation duration | 0.001, 0.005, 0.01, 0.05, +Inf |

#### Metric Labels

| Label | Values | Description |
|-------|--------|-------------|
| `environment` | prod, staging, dev | Deployment environment |
| `source` | vercel, gcp | Webhook source |
| `project` | ET | Jira project key |
| `priority` | Highest, High, Medium, Low | Jira priority level |
| `operation` | search, create, comment, escalate | Jira operation type |
| `error_type` | jira_api_error, redis_timeout, auth_failed, invalid_payload, dlq | Error classification |

#### Prometheus Scraping Configuration

**Scrape Interval**: 15 seconds

**Prometheus Configuration**:

```yaml
scrape_configs:
  - job_name: 'error-triage'
    scrape_interval: 15s
    scrape_timeout: 10s
    metrics_path: '/metrics'
    static_configs:
      - targets:
          - 'error-triage.jiratest.com:8080'
        labels:
          service: 'error-triage'
          environment: 'production'
```

#### Alerting Rules Example

```yaml
groups:
  - name: error_triage_alerts
    interval: 30s
    rules:
      - alert: HighErrorRate
        expr: rate(errors_total[5m]) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High error rate detected"
          description: "Error rate is {{ $value }} errors/sec"

      - alert: JiraAPILatencyHigh
        expr: histogram_quantile(0.99, rate(jira_api_latency_seconds_bucket[5m])) > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Jira API p99 latency > 5s"
          description: "Jira API p99 latency is {{ $value }}s"

      - alert: ServiceUnhealthy
        expr: up{job="error-triage"} == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Error Triage service is down"
          description: "Service has been unavailable for 2+ minutes"
```

---

## Error Codes Reference

| HTTP Status | Error Code | Description | Troubleshooting |
|-------------|------------|-------------|-----------------|
| 400 | `INVALID_PAYLOAD` | Request body is malformed or missing required fields | Verify JSON syntax and required fields match schema |
| 400 | `INVALID_CONTENT_TYPE` | Content-Type header is not application/json | Set header: `Content-Type: application/json` |
| 401 | `AUTH_FAILED` | Webhook signature or JWT token is invalid | Verify webhook secret or check GCP service account permissions |
| 401 | `MISSING_AUTH_HEADER` | Required authentication header is missing | Add `x-vercel-signature` or `Authorization` header |
| 401 | `TOKEN_EXPIRED` | GCP JWT token has expired | Ensure system clocks are synchronized |
| 429 | `RATE_LIMIT_EXCEEDED` | Too many requests from same IP address | Implement exponential backoff, check X-RateLimit-Reset header |
| 500 | `INTERNAL_ERROR` | Unexpected server error occurred | Check CloudWatch logs with request_id, contact support |
| 500 | `REDIS_UNAVAILABLE` | Redis connection failed | Check Redis cluster health, verify network connectivity |
| 500 | `JIRA_API_ERROR` | Jira API request failed | Verify Jira credentials, check Jira service status |
| 503 | `SERVICE_UNAVAILABLE` | Service is temporarily unavailable | Check /healthz endpoint, verify required dependencies |
| 503 | `DEPENDENCY_DOWN` | Required dependency is unreachable | Check health check response for failed dependency |

### Error Response Schema

All error responses follow this consistent structure:

```json
{
  "error": "Human-readable error name",
  "message": "Detailed error description",
  "code": "MACHINE_READABLE_ERROR_CODE",
  "request_id": "req_abc123",
  "timestamp": "2025-01-15T10:30:45.123Z",
  "details": {
    "field": "Additional context-specific information"
  }
}
```

---

## OpenAPI Specification

Below is an OpenAPI 3.0 specification snippet for the Error Triage API:

```yaml
openapi: 3.0.3
info:
  title: Error Triage → Jira Upserter API
  description: |
    Intelligent error management service integrating Vercel and GCP error sources
    with Jira issue tracking.
  version: 1.0.0
  contact:
    name: Jira Test Team
    email: support@jiratest.com

servers:
  - url: https://error-triage.jiratest.com
    description: Production
  - url: https://error-triage-staging.jiratest.com
    description: Staging
  - url: http://localhost:8080
    description: Local Development

paths:
  /events:
    post:
      summary: Accept error webhook events
      description: |
        Receives error events from Vercel Log Drain or GCP Cloud Logging Pub/Sub push.
        Events are authenticated, validated, deduplicated, and queued for async processing.
      operationId: handleEvent
      tags:
        - Webhooks
      security:
        - VercelSignature: []
        - GCPBearerToken: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              oneOf:
                - $ref: '#/components/schemas/VercelPayload'
                - $ref: '#/components/schemas/GCPPayload'
            examples:
              vercel:
                summary: Vercel Log Drain payload
                value:
                  source: vercel
                  deployment:
                    id: dpl_xyz123
                    url: my-app-abc123.vercel.app
                    name: web-app
                  message: "Error: Cannot read property 'x' of undefined"
                  level: error
                  timestamp: "2025-01-15T10:30:45.123Z"
                  traceId: abc123def456
              gcp:
                summary: GCP Pub/Sub push payload
                value:
                  message:
                    data: eyJzZXZlcml0eSI6IkVSUk9SIn0=
                    messageId: "123456789"
                    publishTime: "2025-01-15T10:30:45.123Z"
                  subscription: projects/my-project/subscriptions/error-events-push
      responses:
        '202':
          description: Event accepted and queued for processing
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AcceptedResponse'
        '400':
          description: Invalid payload
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '401':
          description: Authentication failed
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '429':
          description: Rate limit exceeded
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/RateLimitError'
        '500':
          description: Internal server error
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'

  /healthz:
    get:
      summary: Health check endpoint
      description: |
        Returns service health status and dependency connectivity checks.
        Used by ECS/Kubernetes for container orchestration.
      operationId: getHealth
      tags:
        - Operations
      responses:
        '200':
          description: Service is healthy or degraded (optional dependency down)
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/HealthResponse'
        '503':
          description: Service is unhealthy (required dependency down)
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/HealthResponse'

  /metrics:
    get:
      summary: Prometheus metrics endpoint
      description: |
        Exposes Prometheus-format metrics for monitoring and alerting.
        Scraped by Prometheus every 15 seconds.
      operationId: getMetrics
      tags:
        - Operations
      responses:
        '200':
          description: Prometheus metrics in text format
          content:
            text/plain:
              schema:
                type: string
                example: |
                  # HELP events_received_total Total webhook events received
                  # TYPE events_received_total counter
                  events_received_total{environment="production",source="vercel"} 1523

components:
  securitySchemes:
    VercelSignature:
      type: apiKey
      in: header
      name: x-vercel-signature
      description: HMAC-SHA256 signature of request body
    GCPBearerToken:
      type: http
      scheme: bearer
      bearerFormat: JWT
      description: GCP OIDC JWT token

  schemas:
    VercelPayload:
      type: object
      required:
        - deployment
        - message
        - level
        - timestamp
      properties:
        source:
          type: string
          enum: [vercel]
        deployment:
          type: object
          required:
            - id
            - url
          properties:
            id:
              type: string
              example: dpl_xyz123
            url:
              type: string
              example: my-app-abc123.vercel.app
            name:
              type: string
              example: web-app
        message:
          type: string
          example: "Error: Cannot read property 'x' of undefined"
        level:
          type: string
          enum: [error, warning, info]
        timestamp:
          type: string
          format: date-time
        traceId:
          type: string
        stack:
          type: string

    GCPPayload:
      type: object
      required:
        - message
      properties:
        message:
          type: object
          required:
            - data
            - messageId
          properties:
            data:
              type: string
              format: byte
              description: Base64-encoded log entry
            messageId:
              type: string
            publishTime:
              type: string
              format: date-time
        subscription:
          type: string

    AcceptedResponse:
      type: object
      properties:
        status:
          type: string
          enum: [accepted]
        event_id:
          type: string
        message:
          type: string

    ErrorResponse:
      type: object
      properties:
        error:
          type: string
        message:
          type: string
        code:
          type: string
        request_id:
          type: string
        timestamp:
          type: string
          format: date-time

    RateLimitError:
      allOf:
        - $ref: '#/components/schemas/ErrorResponse'
        - type: object
          properties:
            retry_after:
              type: integer
              description: Seconds until rate limit resets

    HealthResponse:
      type: object
      properties:
        status:
          type: string
          enum: [healthy, degraded, unhealthy]
        timestamp:
          type: string
          format: date-time
        version:
          type: string
        checks:
          type: object
          additionalProperties:
            type: object
            properties:
              status:
                type: string
                enum: [up, down]
              latency_ms:
                type: number
                nullable: true
              message:
                type: string

tags:
  - name: Webhooks
    description: Webhook endpoints for error ingestion
  - name: Operations
    description: Operational endpoints for monitoring and health checks
```

---

## Examples

### Example 1: Send Vercel Error Event

```bash
curl -X POST https://error-triage.jiratest.com/events \
  -H "Content-Type: application/json" \
  -H "x-vercel-signature: a3f5b9c8d2e1f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1" \
  -d '{
    "source": "vercel",
    "deployment": {
      "id": "dpl_abc123xyz",
      "url": "my-app-prod.vercel.app",
      "name": "web-app"
    },
    "message": "TypeError: Cannot read property '\''user'\'' of undefined",
    "level": "error",
    "timestamp": "2025-01-15T10:30:45.123Z",
    "environment": "production",
    "path": "/api/checkout",
    "traceId": "trace_xyz789",
    "stack": "TypeError: Cannot read property '\''user'\'' of undefined\n    at /app/api/checkout.js:45:12\n    at processRequest (/app/lib/handler.js:123:8)"
  }'
```

**Response**:
```json
{
  "status": "accepted",
  "event_id": "vercel-dpl_abc123xyz-trace_xyz789",
  "message": "Event queued for processing"
}
```

### Example 2: Send GCP Error Event

```bash
curl -X POST https://error-triage.jiratest.com/events \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6ImFiYzEyMyIsInR5cCI6IkpXVCJ9..." \
  -d '{
    "message": {
      "data": "eyJzZXZlcml0eSI6IkVSUk9SIiwidGV4dFBheWxvYWQiOiJFcnJvcjogQ29ubmVjdGlvbiByZWZ1c2VkIiwicmVzb3VyY2UiOnsidHlwZSI6ImNsb3VkX3J1bl9yZXZpc2lvbiIsImxhYmVscyI6eyJzZXJ2aWNlX25hbWUiOiJhcGktc2VydmljZSJ9fSwiaW5zZXJ0SWQiOiJpbnNlcnRfMTIzNDU2In0=",
      "messageId": "msg_789012",
      "publishTime": "2025-01-15T10:30:45.123Z"
    },
    "subscription": "projects/my-gcp-project/subscriptions/error-events-push"
  }'
```

**Response**:
```json
{
  "status": "accepted",
  "event_id": "gcp-insert_123456",
  "message": "Event queued for processing"
}
```

### Example 3: Check Service Health

```bash
curl -X GET https://error-triage.jiratest.com/healthz
```

**Response** (Healthy):
```json
{
  "status": "healthy",
  "timestamp": "2025-01-15T10:30:45.123Z",
  "version": "1.0.0",
  "checks": {
    "redis": {
      "status": "up",
      "latency_ms": 2,
      "message": "PONG received"
    },
    "jira": {
      "status": "up",
      "latency_ms": 85,
      "message": "API reachable"
    },
    "mongodb": {
      "status": "up",
      "latency_ms": 15,
      "message": "Ping successful"
    }
  }
}
```

### Example 4: Fetch Prometheus Metrics

```bash
curl -X GET https://error-triage.jiratest.com/metrics
```

**Response** (truncated):
```
# HELP events_received_total Total number of webhook events received
# TYPE events_received_total counter
events_received_total{environment="production",source="vercel"} 1523
events_received_total{environment="production",source="gcp"} 847

# HELP jira_issues_created_total New Jira issues created
# TYPE jira_issues_created_total counter
jira_issues_created_total{environment="production",project="ET"} 87

# HELP event_processing_duration_seconds Event processing latency
# TYPE event_processing_duration_seconds histogram
event_processing_duration_seconds_bucket{environment="production",le="0.1"} 1456
event_processing_duration_seconds_bucket{environment="production",le="0.2"} 1502
event_processing_duration_seconds_sum{environment="production"} 125.34
event_processing_duration_seconds_count{environment="production"} 1518
```

### Example 5: Test Authentication Failure

```bash
curl -X POST https://error-triage.jiratest.com/events \
  -H "Content-Type: application/json" \
  -H "x-vercel-signature: invalid_signature" \
  -d '{"message": "test"}'
```

**Response**:
```json
{
  "error": "Unauthorized",
  "message": "Invalid signature: HMAC verification failed",
  "code": "AUTH_FAILED",
  "request_id": "req_abc123",
  "timestamp": "2025-01-15T10:30:45.123Z"
}
```

### Example 6: Generate Webhook Signature (Python)

```python
import hmac
import hashlib
import json

# Your webhook secret from AWS Secrets Manager
webhook_secret = "your-webhook-secret-here"

# Request payload
payload = {
    "source": "vercel",
    "deployment": {"id": "dpl_123", "url": "app.vercel.app"},
    "message": "Error occurred",
    "level": "error",
    "timestamp": "2025-01-15T10:30:45.123Z"
}

# Convert payload to JSON bytes
payload_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')

# Generate HMAC-SHA256 signature
signature = hmac.new(
    webhook_secret.encode('utf-8'),
    payload_bytes,
    hashlib.sha256
).hexdigest()

print(f"x-vercel-signature: {signature}")
```

### Example 7: Verify GCP JWT Token (Python)

```python
from google.oauth2 import id_token
from google.auth.transport import requests

def verify_gcp_token(token: str, audience: str) -> bool:
    try:
        # Verify token and extract claims
        info = id_token.verify_oauth2_token(
            token,
            requests.Request(),
            audience
        )
        
        # Verify issuer
        if info.get('iss') not in [
            'https://accounts.google.com',
            'https://www.googleapis.com'
        ]:
            return False
        
        print(f"Token valid for: {info.get('email')}")
        return True
    except ValueError as e:
        print(f"Token validation failed: {e}")
        return False

# Usage
token = "eyJhbGciOiJSUzI1NiIs..."
audience = "https://error-triage.jiratest.com/events"
is_valid = verify_gcp_token(token, audience)
```

---

## Rate Limiting Best Practices

When integrating with the Error Triage API, follow these best practices:

1. **Respect Rate Limits**: Check `X-RateLimit-Remaining` header in responses
2. **Implement Exponential Backoff**: On 429 responses, wait `retry_after` seconds before retrying
3. **Batch Events**: If possible, batch multiple log entries into single requests (future enhancement)
4. **Monitor Quota Usage**: Track your request volume to stay within limits
5. **Handle Retries**: Implement retry logic with exponential backoff for transient errors (500, 503)

**Exponential Backoff Example** (Python):

```python
import time
import requests

def send_event_with_retry(url, payload, headers, max_retries=5):
    """Send event with exponential backoff retry logic."""
    for attempt in range(max_retries):
        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code == 202:
            return response.json()
        
        if response.status_code == 429:
            # Rate limit exceeded
            retry_after = int(response.headers.get('X-RateLimit-Reset', 60))
            print(f"Rate limited. Retrying after {retry_after}s")
            time.sleep(retry_after)
            continue
        
        if response.status_code >= 500:
            # Server error - exponential backoff
            wait_time = (2 ** attempt) + random.uniform(0, 1)
            print(f"Server error. Retrying after {wait_time:.2f}s")
            time.sleep(wait_time)
            continue
        
        # Client error (4xx) - don't retry
        response.raise_for_status()
    
    raise Exception(f"Failed after {max_retries} retries")
```

---

## Testing and Development

### Local Development

To test the API locally using docker-compose:

```bash
# Start local development stack
make run-local

# API will be available at http://localhost:8080

# Test health check
curl http://localhost:8080/healthz

# Test metrics endpoint
curl http://localhost:8080/metrics

# Test webhook endpoint (no auth required in dev mode)
curl -X POST http://localhost:8080/events \
  -H "Content-Type: application/json" \
  -d '{"source": "vercel", "deployment": {"id": "test", "url": "test.app"}, "message": "Test error", "level": "error", "timestamp": "2025-01-15T10:00:00Z"}'
```

### Integration Testing

Example integration test using pytest and requests:

```python
import pytest
import requests
import json

BASE_URL = "https://error-triage-staging.jiratest.com"

def test_vercel_webhook_creates_jira_issue():
    """Test that Vercel webhook creates a new Jira issue."""
    payload = {
        "source": "vercel",
        "deployment": {
            "id": "dpl_test_123",
            "url": "test-app.vercel.app",
            "name": "test-service"
        },
        "message": "Test error for integration test",
        "level": "error",
        "timestamp": "2025-01-15T10:00:00.000Z",
        "environment": "staging",
        "path": "/api/test"
    }
    
    headers = {
        "Content-Type": "application/json",
        "x-vercel-signature": generate_signature(payload)
    }
    
    response = requests.post(
        f"{BASE_URL}/events",
        json=payload,
        headers=headers
    )
    
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert "event_id" in response.json()
```

---

## Support and Troubleshooting

### Common Issues

**Issue**: 401 Unauthorized - Invalid signature

**Solution**: 
- Verify webhook secret matches value in AWS Secrets Manager
- Ensure signature is computed on raw request body (before any parsing)
- Check that HMAC algorithm is SHA-256, not SHA-1

---

**Issue**: 429 Rate Limit Exceeded

**Solution**:
- Implement exponential backoff retry logic
- Check if multiple services are sending from same IP
- Contact support to increase rate limit if needed

---

**Issue**: 500 Internal Server Error - Redis unavailable

**Solution**:
- Check ElastiCache cluster status in AWS console
- Verify security group allows traffic from ECS tasks
- Check CloudWatch logs for Redis connection errors

---

**Issue**: Events not creating Jira issues

**Solution**:
- Verify Jira credentials in AWS Secrets Manager
- Check `/healthz` endpoint to confirm Jira connectivity
- Review CloudWatch logs for Jira API errors
- Confirm Jira project key is correct in configuration

---

### Contact Support

For additional assistance:

- **Email**: support@jiratest.com
- **Slack**: #error-triage-support
- **Documentation**: https://docs.jiratest.com/error-triage
- **Status Page**: https://status.jiratest.com

---

**Document Version**: 1.0.0  
**Last Updated**: 2025-01-15  
**Maintained By**: Jira Test Platform Team
