# Error Triage → Jira Upserter Service Architecture

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component Architecture](#2-component-architecture)
3. [Webhook Ingestion Flow](#3-webhook-ingestion-flow)
4. [Error Processing Pipeline](#4-error-processing-pipeline)
5. [Jira Integration Sequence](#5-jira-integration-sequence)
6. [Data Structures](#6-data-structures)
7. [Architecture Principles](#7-architecture-principles)
8. [Network Topology](#8-network-topology)
9. [Security Architecture](#9-security-architecture)
10. [Performance Characteristics](#10-performance-characteristics)

---

## 1. System Overview

### 1.1 Purpose

The Error Triage → Jira Upserter service is an intelligent error management system that bridges external error sources (Vercel and Google Cloud Platform) with Jira issue tracking. The service provides:

- **Multi-source error ingestion** via unified HTTPS POST endpoint
- **Intelligent error fingerprinting** with PII sanitization
- **Frequency-based severity classification** using rolling 5-minute occurrence counters
- **Automated Jira issue lifecycle management** (create, comment, escalate)
- **Configuration-driven ownership routing** and operational behavior
- **Noise control** through deduplication and comment rate limiting

### 1.2 High-Level Component Diagram

```mermaid
graph TB
    subgraph "External Sources"
        V[Vercel Log Drain]
        G[GCP Cloud Logging<br/>via Pub/Sub]
    end
    
    subgraph "AWS Cloud - Private Subnet"
        ALB[Application Load Balancer<br/>TLS Termination]
        
        subgraph "ECS Fargate Service"
            APP1[Error Triage Service<br/>Task 1]
            APP2[Error Triage Service<br/>Task 2]
            APP3[Error Triage Service<br/>Task N]
        end
        
        REDIS[(Redis ElastiCache<br/>Frequency Counters<br/>Deduplication<br/>Rate Limits)]
        
        SECRETS[AWS Secrets Manager<br/>Jira Credentials<br/>Webhook Secrets]
        
        CW[CloudWatch Logs<br/>Structured JSON Logging]
    end
    
    subgraph "External Services"
        JIRA[Jira Cloud API<br/>Issue Management]
        MONGO[(MongoDB Atlas<br/>Audit Trails<br/>Action Logs)]
    end
    
    subgraph "Monitoring"
        PROM[Prometheus<br/>Metrics Scraping]
    end
    
    V -->|POST /events<br/>HMAC-SHA256| ALB
    G -->|POST /events<br/>OIDC JWT| ALB
    
    ALB --> APP1
    ALB --> APP2
    ALB --> APP3
    
    APP1 <-->|INCR, GET, SETEX| REDIS
    APP2 <-->|INCR, GET, SETEX| REDIS
    APP3 <-->|INCR, GET, SETEX| REDIS
    
    APP1 -->|GetSecretValue| SECRETS
    APP2 -->|GetSecretValue| SECRETS
    APP3 -->|GetSecretValue| SECRETS
    
    APP1 -->|Structured Logs| CW
    APP2 -->|Structured Logs| CW
    APP3 -->|Structured Logs| CW
    
    APP1 <-->|JQL Search<br/>Create Issue<br/>Add Comment<br/>Update Priority| JIRA
    APP2 <-->|JQL Search<br/>Create Issue<br/>Add Comment<br/>Update Priority| JIRA
    APP3 <-->|JQL Search<br/>Create Issue<br/>Add Comment<br/>Update Priority| JIRA
    
    APP1 -->|Audit Events| MONGO
    APP2 -->|Audit Events| MONGO
    APP3 -->|Audit Events| MONGO
    
    PROM -->|Scrape GET /metrics| APP1
    PROM -->|Scrape GET /metrics| APP2
    PROM -->|Scrape GET /metrics| APP3
    
    style V fill:#4285f4
    style G fill:#4285f4
    style JIRA fill:#0052cc
    style MONGO fill:#00ed64
    style REDIS fill:#dc382d
    style ALB fill:#ff9900
    style SECRETS fill:#ff9900
    style CW fill:#ff9900
```

### 1.3 Core Capabilities

| Capability | Implementation | Technology |
|------------|----------------|------------|
| **Multi-Source Ingestion** | Unified `/events` endpoint with adapter pattern | Flask 3.1+, Pydantic |
| **Error Fingerprinting** | `hash(service + env + error_class + top_stack_frame + sanitized_message)` | SHA-256, Regex parsing |
| **PII Sanitization** | Regex-based pattern matching and replacement | Configurable YAML patterns |
| **Frequency Tracking** | Rolling 5-minute counters per (env, fingerprint) | Redis INCR with 300s TTL |
| **Severity Classification** | YAML-based threshold rules mapping frequency to priority | Environment-specific rules |
| **Jira Upsert Logic** | JQL search + create/comment/escalate decision tree | Jira Python SDK 3.10+ |
| **Deduplication** | Event ID tracking with 1-hour TTL | Redis SETEX |
| **Comment Rate Limiting** | Per-issue 15-minute minimum interval with severity override | Redis timestamps |
| **Deep Linking** | Platform-specific log URL construction | Vercel trace IDs, GCP insertIds |
| **Observability** | Prometheus metrics + structured JSON logs | prometheus-client, python-json-logger |

---

## 2. Component Architecture

### 2.1 Service Layer Architecture

```mermaid
graph TB
    subgraph "Flask Application Layer"
        ROUTES[Route Handlers<br/>/events, /healthz, /metrics]
    end
    
    subgraph "Payload Processing"
        FACTORY[PayloadAdapterFactory]
        VERCEL[VercelPayloadAdapter]
        GCP[GCPPayloadAdapter]
        NORM[NormalizedErrorEvent]
    end
    
    subgraph "Error Intelligence"
        FINGER[ErrorFingerprinter]
        SANIT[PIISanitizer]
        DEDUP[DeduplicationService]
    end
    
    subgraph "Frequency & Severity"
        FREQ[FrequencyTracker]
        SEV[SeverityRulesEngine]
        OWNER[OwnershipResolver]
    end
    
    subgraph "Jira Operations"
        JIRA_SVC[JiraIntegrationService]
        RATE[CommentRateLimiter]
        LINK[LogLinkBuilder]
    end
    
    subgraph "Infrastructure Services"
        AUTH[WebhookAuthenticator]
        METRICS[MetricsCollector]
        LOG[StructuredLogger]
    end
    
    ROUTES --> FACTORY
    ROUTES --> AUTH
    
    FACTORY --> VERCEL
    FACTORY --> GCP
    VERCEL --> NORM
    GCP --> NORM
    
    NORM --> FINGER
    FINGER --> SANIT
    NORM --> DEDUP
    
    NORM --> FREQ
    FREQ --> SEV
    NORM --> OWNER
    
    SEV --> JIRA_SVC
    OWNER --> JIRA_SVC
    JIRA_SVC --> RATE
    JIRA_SVC --> LINK
    
    ROUTES --> METRICS
    ROUTES --> LOG
    
    style ROUTES fill:#e1f5ff
    style JIRA_SVC fill:#fff3cd
    style FREQ fill:#ffe5e5
```

### 2.2 Service Responsibilities

| Service | Responsibility | Key Methods | Dependencies |
|---------|----------------|-------------|--------------|
| **PayloadAdapterFactory** | Detect source and route to appropriate adapter | `get_adapter(source)` | None |
| **VercelPayloadAdapter** | Transform Vercel webhooks to canonical format | `transform(payload) -> NormalizedErrorEvent` | None |
| **GCPPayloadAdapter** | Transform GCP Pub/Sub payloads to canonical format | `transform(payload) -> NormalizedErrorEvent` | base64, google-auth |
| **ErrorFingerprinter** | Generate stable SHA-256 fingerprints | `generate_fingerprint(event) -> str` | PIISanitizer |
| **PIISanitizer** | Remove PII using regex patterns | `sanitize(text) -> str` | YAML config |
| **DeduplicationService** | Track processed event IDs | `is_duplicate(event_id) -> bool`<br/>`mark_processed(event_id)` | Redis |
| **FrequencyTracker** | Manage rolling occurrence counters | `increment(env, fingerprint) -> int`<br/>`get_count(env, fingerprint) -> int` | Redis |
| **SeverityRulesEngine** | Evaluate threshold rules | `evaluate(env, count) -> (priority, severity)` | YAML config |
| **OwnershipResolver** | Determine assignee from rules | `resolve(event) -> Optional[Dict]` | YAML config |
| **JiraIntegrationService** | Jira API operations | `search_issue_by_fingerprint(fp) -> Optional[str]`<br/>`create_bug_issue(...) -> str`<br/>`add_comment(...)`<br/>`escalate_priority(...)` | Jira SDK, Redis |
| **CommentRateLimiter** | Enforce per-issue comment intervals | `should_comment(issue_key, severity_increased) -> bool`<br/>`record_comment(issue_key)` | Redis |
| **LogLinkBuilder** | Construct platform-specific deep links | `build_vercel_link(...) -> str`<br/>`build_gcp_link(...) -> str` | None |
| **WebhookAuthenticator** | Validate webhook signatures/tokens | `verify_vercel_signature(request, secret) -> bool`<br/>`verify_gcp_token(request, audience) -> bool` | hmac, google-auth |
| **MetricsCollector** | Prometheus metrics registry | `increment_counter(name, labels)`<br/>`observe_histogram(name, value, labels)` | prometheus-client |

### 2.3 Data Models

```python
@dataclass
class NormalizedErrorEvent:
    """Canonical error event structure across all sources"""
    source: str              # "vercel" | "gcp"
    service: str             # Service/application name
    environment: str         # "prod" | "staging" | "dev"
    error_class: str         # Exception type or error category
    message: str             # Error message
    stack_trace: Optional[str]  # Full stack trace
    path: Optional[str]      # Request path if HTTP error
    url: Optional[str]       # Full request URL
    release: Optional[str]   # Deployment version/commit SHA
    log_url: str             # Deep link to platform logs
    event_id: str            # Unique event identifier for deduplication
    occurred_at: datetime    # Timestamp of error occurrence
```

---

## 3. Webhook Ingestion Flow

### 3.1 Request Flow Diagram

```mermaid
sequenceDiagram
    participant Vercel as Vercel/GCP<br/>Webhook
    participant ALB as Application<br/>Load Balancer
    participant Auth as Webhook<br/>Authenticator
    participant Adapter as Payload<br/>Adapter
    participant Dedup as Deduplication<br/>Service
    participant Queue as Event<br/>Processor

    Vercel->>ALB: POST /events<br/>(JSON payload + signature)
    ALB->>Auth: Forward request
    
    Auth->>Auth: Verify HMAC-SHA256<br/>or OIDC JWT token
    
    alt Authentication Failed
        Auth-->>Vercel: 401 Unauthorized
    end
    
    Auth->>Adapter: Authenticated request
    Adapter->>Adapter: Detect source<br/>(Vercel vs GCP)
    Adapter->>Adapter: Parse payload<br/>Extract fields
    Adapter->>Adapter: Transform to<br/>NormalizedErrorEvent
    
    alt Invalid Payload Structure
        Adapter-->>Vercel: 400 Bad Request
    end
    
    Adapter->>Dedup: Check is_duplicate(event_id)
    
    alt Duplicate Event
        Dedup->>Dedup: Log duplicate detection
        Dedup-->>Vercel: 202 Accepted<br/>(already processed)
    end
    
    Dedup->>Dedup: mark_processed(event_id)<br/>Redis SETEX 1h TTL
    Dedup->>Queue: Enqueue normalized event
    Queue-->>Vercel: 202 Accepted<br/>(< 200ms p95)
    
    Note over Queue: Asynchronous processing<br/>continues in background
```

### 3.2 Authentication Mechanisms

#### 3.2.1 Vercel Webhook Authentication

```python
# Vercel uses HMAC-SHA256 signature verification
def verify_vercel_signature(request: Request, secret: str) -> bool:
    """
    Verify x-vercel-signature header against payload HMAC.
    
    Args:
        request: Flask request object with headers and body
        secret: Webhook secret from AWS Secrets Manager
        
    Returns:
        True if signature valid, False otherwise
    """
    signature_header = request.headers.get('x-vercel-signature')
    if not signature_header:
        return False
    
    # Compute HMAC-SHA256 over raw request body
    expected_signature = hmac.new(
        secret.encode(),
        request.data,  # Raw bytes, not parsed JSON
        hashlib.sha256
    ).hexdigest()
    
    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(signature_header, expected_signature)
```

#### 3.2.2 GCP Pub/Sub Push Authentication

```python
# GCP uses OIDC JWT token validation
def verify_gcp_token(request: Request, audience: str) -> bool:
    """
    Verify Authorization Bearer token from GCP Pub/Sub push.
    
    Args:
        request: Flask request with Authorization header
        audience: Expected audience (service endpoint URL)
        
    Returns:
        True if token valid, False otherwise
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return False
    
    token = auth_header[len('Bearer '):]
    
    try:
        # Verify JWT signature and claims
        info = id_token.verify_oauth2_token(
            token, 
            requests.Request(), 
            audience
        )
        
        # Validate issuer is Google
        valid_issuers = [
            'https://accounts.google.com',
            'https://www.googleapis.com'
        ]
        return info.get('iss') in valid_issuers
        
    except ValueError:
        return False
```

### 3.3 Payload Transformation Examples

#### 3.3.1 Vercel Payload

```json
{
  "source": "vercel",
  "deployment": {
    "id": "dpl_abc123xyz789",
    "url": "my-app-xyz123.vercel.app"
  },
  "message": "TypeError: Cannot read property 'id' of undefined",
  "level": "error",
  "timestamp": "2025-01-15T10:30:45.123Z",
  "environment": "production",
  "path": "/api/checkout",
  "traceId": "trace_abc123def456"
}
```

**Transforms to:**

```python
NormalizedErrorEvent(
    source="vercel",
    service="my-app",  # Extracted from deployment.url
    environment="production",
    error_class="TypeError",
    message="Cannot read property 'id' of undefined",
    stack_trace=None,  # Vercel doesn't include stack in webhooks
    path="/api/checkout",
    url="https://my-app-xyz123.vercel.app/api/checkout",
    release="dpl_abc123xyz789",
    log_url="https://vercel.com/{org}/{project}/logs?q=traceId:trace_abc123def456",
    event_id="vercel-dpl_abc123xyz789-trace_abc123def456",
    occurred_at=datetime(2025, 1, 15, 10, 30, 45, 123000)
)
```

#### 3.3.2 GCP Pub/Sub Payload

```json
{
  "message": {
    "data": "eyJzZXZlcml0eSI6ICJFU...==",  # Base64 encoded
    "messageId": "123456789",
    "publishTime": "2025-01-15T10:30:45.123Z"
  },
  "subscription": "projects/my-project/subscriptions/error-events-push"
}
```

**Decoded `message.data`:**

```json
{
  "severity": "ERROR",
  "textPayload": "ValueError: Invalid configuration value for database_url",
  "resource": {
    "type": "cloud_run_revision",
    "labels": {
      "service_name": "api-service",
      "revision_name": "api-service-00042-xyz"
    }
  },
  "insertId": "abc123def456",
  "timestamp": "2025-01-15T10:30:45.123Z",
  "labels": {
    "environment": "production"
  }
}
```

**Transforms to:**

```python
NormalizedErrorEvent(
    source="gcp",
    service="api-service",  # From resource.labels.service_name
    environment="production",  # From labels.environment
    error_class="ValueError",
    message="Invalid configuration value for database_url",
    stack_trace=None,  # Parsed from textPayload if present
    path=None,
    url=None,
    release="api-service-00042-xyz",  # From resource.labels.revision_name
    log_url="https://console.cloud.google.com/logs/query;query=insertId%3Dabc123def456?project=my-project",
    event_id="gcp-abc123def456",  # GCP insertId
    occurred_at=datetime(2025, 1, 15, 10, 30, 45, 123000)
)
```

---

## 4. Error Processing Pipeline

### 4.1 Processing Flow Diagram

```mermaid
graph TB
    START[Normalized Error Event] --> SANIT[PII Sanitization]
    SANIT --> |Sanitized Message| FINGER[Fingerprint Generation]
    
    FINGER --> |SHA-256 Hash| FREQ[Frequency Tracking]
    FREQ --> |Redis INCR| COUNT[Get Occurrence Count]
    
    COUNT --> SEV[Severity Evaluation]
    SEV --> |Load YAML Rules| MATCH[Match Threshold]
    
    MATCH --> |Priority + Severity| OWNER[Ownership Resolution]
    OWNER --> |Load YAML Rules| ASSIGN[Determine Assignee]
    
    ASSIGN --> SEARCH[Jira Issue Search]
    SEARCH --> |JQL Query by Fingerprint| EXISTS{Issue Exists?}
    
    EXISTS -->|No| CREATE[Create New Jira Bug]
    CREATE --> MONGO_CREATE[Log to MongoDB<br/>error_events collection]
    CREATE --> END[Return Success]
    
    EXISTS -->|Yes| RATE{Rate Limit<br/>Expired?}
    
    RATE -->|No - Within 15min| SEV_CHECK{Severity<br/>Increased?}
    
    SEV_CHECK -->|No| SUPPRESS[Suppress Comment<br/>Log Suppression]
    SUPPRESS --> END
    
    SEV_CHECK -->|Yes| ESCALATE[Update Priority<br/>Add Comment<br/>Override Rate Limit]
    ESCALATE --> MONGO_ESC[Log to MongoDB<br/>jira_actions: escalate]
    ESCALATE --> END
    
    RATE -->|Yes - Expired| COMMENT[Add Comment<br/>with Count + Log Link]
    COMMENT --> MONGO_COMM[Log to MongoDB<br/>jira_actions: comment]
    COMMENT --> RATE_RECORD[Record Comment Timestamp<br/>Redis SETEX 15min]
    RATE_RECORD --> END
    
    style CREATE fill:#d4edda
    style COMMENT fill:#fff3cd
    style ESCALATE fill:#f8d7da
    style SUPPRESS fill:#e2e3e5
```

### 4.2 Fingerprint Generation Algorithm

```python
def generate_fingerprint(event: NormalizedErrorEvent) -> str:
    """
    Generate stable SHA-256 fingerprint for error grouping.
    
    Formula: hash(service + env + error_class + top_stack_frame + sanitized_message)
    
    Args:
        event: Normalized error event with all fields populated
        
    Returns:
        64-character hexadecimal SHA-256 hash
    """
    # 1. Sanitize message BEFORE hashing for stable grouping
    sanitized_msg = PIISanitizer.sanitize(event.message)
    
    # 2. Extract top stack frame (first non-library frame)
    top_frame = extract_top_stack_frame(event.stack_trace) if event.stack_trace else ""
    
    # 3. Combine components in consistent order
    components = [
        event.service,
        event.environment,
        event.error_class,
        top_frame,
        sanitized_msg[:200]  # First 200 chars of sanitized message
    ]
    
    combined = "|".join(components)
    
    # 4. Generate SHA-256 hash
    fingerprint = hashlib.sha256(combined.encode('utf-8')).hexdigest()
    
    return fingerprint


def extract_top_stack_frame(stack_trace: str) -> str:
    """
    Extract first application code frame, skipping library/framework frames.
    
    Patterns to skip:
    - node_modules/
    - site-packages/
    - /usr/lib/
    - /opt/python/
    
    Returns:
        String like "checkout.tsx:123:45" or "" if no frames found
    """
    # Regex: "at <file>:<line>:<col>" or "File \"<file>\", line <line>"
    frame_pattern = r'at ([\w/<>\.]+):(\d+):(\d+)|File "([^"]+)", line (\d+)'
    
    skip_patterns = ['node_modules', 'site-packages', '/usr/lib', '/opt/python']
    
    for match in re.finditer(frame_pattern, stack_trace):
        file_path = match.group(1) or match.group(4)
        
        # Skip if file path contains library markers
        if not any(pattern in file_path for pattern in skip_patterns):
            line = match.group(2) or match.group(5)
            col = match.group(3) or ""
            return f"{file_path}:{line}:{col}" if col else f"{file_path}:{line}"
    
    return ""
```

### 4.3 PII Sanitization Patterns

| PII Category | Regex Pattern | Replacement | Example |
|--------------|---------------|-------------|---------|
| **Email Addresses** | `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b` | `[EMAIL]` | `user@example.com` → `[EMAIL]` |
| **UUIDs** | `\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b` | `[UUID]` | `550e8400-e29b-41d4-a716-446655440000` → `[UUID]` |
| **Numeric IDs** | `(user_id|userId|customer_id|order_id)[:=]\s*\d+` | `$1=[ID]` | `user_id=12345` → `user_id=[ID]` |
| **Bearer Tokens** | `Bearer\s+[A-Za-z0-9\-._~+/]+=*` | `Bearer [TOKEN]` | `Bearer eyJhbGc...` → `Bearer [TOKEN]` |
| **IP Addresses** | `\b(?:\d{1,3}\.){3}\d{1,3}\b` | `[IP]` | `192.168.1.100` → `[IP]` |
| **Phone Numbers** | `\b\d{3}[-.]?\d{3}[-.]?\d{4}\b` | `[PHONE]` | `555-123-4567` → `[PHONE]` |

**Configuration File:** `config/sanitization_patterns.yaml`

```yaml
patterns:
  - pattern: '\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    replacement: '[EMAIL]'
    description: "Email addresses"
    
  - pattern: '\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'
    replacement: '[UUID]'
    description: "UUID identifiers"
    
  - pattern: '(user_id|userId|customer_id|customerId|order_id|orderId)[:=]\s*\d+'
    replacement: '\1=[ID]'
    description: "Numeric user/customer/order IDs"
    
  - pattern: 'Bearer\s+[A-Za-z0-9\-._~+/]+=*'
    replacement: 'Bearer [TOKEN]'
    description: "Bearer authentication tokens"
```

### 4.4 Frequency Tracking Implementation

```python
class FrequencyTracker:
    """
    Manage Redis-based rolling 5-minute occurrence counters.
    
    Key Pattern: freq:{env}:{fingerprint}
    TTL: 300 seconds (5 minutes)
    """
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.ttl = 300  # 5 minutes
    
    def increment(self, env: str, fingerprint: str) -> int:
        """
        Atomically increment counter and return current count.
        
        Redis operations:
        1. INCR freq:{env}:{fingerprint}
        2. EXPIRE freq:{env}:{fingerprint} 300
        
        Returns:
            Current occurrence count within 5-minute window
        """
        key = f"freq:{env}:{fingerprint}"
        
        # Use pipeline for atomic INCR + EXPIRE
        pipe = self.redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, self.ttl)
        results = pipe.execute()
        
        count = results[0]  # INCR returns new value
        return count
    
    def get_count(self, env: str, fingerprint: str) -> int:
        """
        Retrieve current counter value without incrementing.
        
        Returns:
            Current count or 0 if key expired/missing
        """
        key = f"freq:{env}:{fingerprint}"
        value = self.redis.get(key)
        return int(value) if value else 0
```

### 4.5 Severity Classification Rules

**Configuration File:** `config/severity_rules.yaml`

```yaml
# Production environment thresholds
production:
  - threshold: 50
    priority: "Highest"
    severity: "SEV1"
    description: "Critical production outage affecting all users"
    
  - threshold: 20
    priority: "High"
    severity: "SEV2"
    description: "Major production issue affecting subset of users"
    
  - threshold: 5
    priority: "Medium"
    severity: "SEV3"
    description: "Moderate production issue"
    
  - threshold: 1
    priority: "Low"
    severity: "SEV4"
    description: "Low frequency production error"

# Staging environment thresholds
staging:
  - threshold: 20
    priority: "Medium"
    severity: "SEV3"
    description: "High frequency in staging requires investigation"
    
  - threshold: 5
    priority: "Low"
    severity: "SEV4"
    description: "Moderate staging errors"
    
  - threshold: 1
    priority: "Lowest"
    severity: "SEV5"
    description: "Informational staging errors"

# Development environment (informational only)
development:
  - threshold: 1
    priority: "Lowest"
    severity: "SEV5"
    description: "Development environment errors"
```

**Evaluation Logic:**

```python
class SeverityRulesEngine:
    """Load and evaluate severity threshold rules."""
    
    def evaluate(self, env: str, count: int) -> Tuple[str, str]:
        """
        Find first matching rule where count >= threshold.
        
        Rules are pre-sorted by threshold descending for efficiency.
        
        Args:
            env: Environment name (production, staging, development)
            count: Current 5-minute occurrence count
            
        Returns:
            Tuple of (priority, severity), e.g., ("Highest", "SEV1")
        """
        rules = self.rules.get(env, self.rules.get('default', []))
        
        for rule in rules:
            if count >= rule['threshold']:
                return (rule['priority'], rule['severity'])
        
        # Fallback if no rules match
        return ("Low", "SEV4")
```

---

## 5. Jira Integration Sequence

### 5.1 Jira Upsert Decision Flow

```mermaid
sequenceDiagram
    participant EP as Event Processor
    participant JS as JiraIntegrationService
    participant JC as Jira Cloud API
    participant RL as CommentRateLimiter
    participant RD as Redis
    participant MO as MongoDB

    EP->>JS: process_event(event, fingerprint, priority, severity)
    
    JS->>JC: JQL Search<br/>labels = "errfp:{fingerprint}"<br/>statusCategory != Done
    
    alt No Existing Issue
        JC-->>JS: No results
        JS->>JS: build_issue_fields()<br/>Summary, Description, Labels
        JS->>JC: POST /rest/api/3/issue<br/>(Create Bug)
        JC-->>JS: Issue created: ET-1234
        JS->>MO: Log jira_actions<br/>(action: create)
        JS->>MO: Log error_events<br/>(jira_issue_key: ET-1234)
        JS-->>EP: Created: ET-1234
    end
    
    alt Existing Issue Found
        JC-->>JS: Issue: ET-1234
        
        JS->>RL: should_comment(ET-1234, severity_increased=False)
        RL->>RD: GET comment_limit:ET-1234
        
        alt Rate Limit Active (< 15 min)
            RD-->>RL: Timestamp: 5 min ago
            RL-->>JS: should_comment = False
            
            JS->>JS: Check severity escalation<br/>Current SEV2 vs Previous SEV3
            
            alt Severity Increased
                JS->>RL: should_comment(ET-1234, severity_increased=True)
                RL-->>JS: should_comment = True (override)
                
                JS->>JC: PUT /rest/api/3/issue/ET-1234<br/>(Update Priority + Severity)
                JS->>JC: POST /rest/api/3/issue/ET-1234/comment<br/>(Escalation notice)
                
                JS->>MO: Log jira_actions<br/>(action: escalate)
                JS->>RL: record_comment(ET-1234)
                RL->>RD: SETEX comment_limit:ET-1234 900 {now}
                JS-->>EP: Escalated: ET-1234
            else Severity Same
                JS->>MO: Log suppressed comment
                JS-->>EP: Suppressed (rate limit)
            end
        end
        
        alt Rate Limit Expired (>= 15 min)
            RD-->>RL: Key not found or expired
            RL-->>JS: should_comment = True
            
            JS->>JC: POST /rest/api/3/issue/ET-1234/comment<br/>(Count: 15, Severity: SEV2)
            
            JS->>MO: Log jira_actions<br/>(action: comment)
            JS->>RL: record_comment(ET-1234)
            RL->>RD: SETEX comment_limit:ET-1234 900 {now}
            JS-->>EP: Commented: ET-1234
        end
    end
```

### 5.2 JQL Search Query

```sql
-- Search for open issues with matching fingerprint label
project = ET 
AND labels = "errfp:abc123def456..." 
AND statusCategory != Done
```

**Query Construction:**

```python
def search_issue_by_fingerprint(self, fingerprint: str) -> Optional[str]:
    """
    Search for existing open Jira issue with fingerprint label.
    
    Args:
        fingerprint: 64-character SHA-256 hash
        
    Returns:
        Issue key (e.g., "ET-1234") or None if not found
    """
    jql = (
        f'project = {self.project_key} '
        f'AND labels = "errfp:{fingerprint}" '
        f'AND statusCategory != Done'
    )
    
    issues = self.jira.search_issues(jql, maxResults=1)
    
    if issues:
        return issues[0].key
    else:
        return None
```

### 5.3 Issue Creation Fields

```python
def create_bug_issue(
    self,
    event: NormalizedErrorEvent,
    fingerprint: str,
    priority: str,
    severity: str,
    assignee: Optional[str] = None
) -> str:
    """
    Create new Jira Bug issue with comprehensive metadata.
    
    Returns:
        Created issue key (e.g., "ET-1234")
    """
    # Build summary: [env:service] error_class — message
    summary = (
        f"[{event.environment}:{event.service}] "
        f"{event.error_class} — "
        f"{event.message[:80]}"
    )
    
    # Build markdown description
    description = f"""
h2. Error Details

*Environment:* {event.environment}
*Service:* {event.service}
*Error Class:* {event.error_class}
*Occurred At:* {event.occurred_at.isoformat()}
*Release:* {event.release or 'Unknown'}

h2. Error Message

{{code}}
{event.message}
{{code}}

h2. Stack Trace

{{code:python}}
{event.stack_trace[:1000] if event.stack_trace else 'Not available'}
{{code}}

h2. Log Links

[View in {event.source.title()} Logs|{event.log_url}]

h2. Fingerprint

{fingerprint}
"""
    
    # Build issue fields
    fields = {
        'project': {'key': self.project_key},
        'issuetype': {'name': 'Bug'},
        'summary': summary,
        'description': description,
        'priority': {'name': priority},
        'labels': [
            f"source:{event.source}",
            f"env:{event.environment}",
            f"service:{event.service}",
            f"errfp:{fingerprint}"
        ]
    }
    
    # Set custom severity field
    fields[self.severity_field_id] = {'value': severity}
    
    # Set assignee if provided
    if assignee:
        fields['assignee'] = {'accountId': assignee}
    
    # Create issue
    new_issue = self.jira.create_issue(fields=fields)
    
    return new_issue.key
```

### 5.4 Comment Format

```python
def add_comment(
    self,
    issue_key: str,
    count: int,
    severity: str,
    log_url: str
) -> None:
    """
    Add timestamped comment with occurrence count and log link.
    """
    comment_text = (
        f"Error reoccurred *{count}× in last 5 minutes*. "
        f"Current severity: *{severity}*. "
        f"[View logs|{log_url}]"
    )
    
    self.jira.add_comment(issue_key, comment_text)
```

### 5.5 Priority Escalation

```python
def escalate_priority(
    self,
    issue_key: str,
    new_priority: str,
    new_severity: str
) -> None:
    """
    Update issue priority and severity when thresholds crossed.
    
    Adds comment documenting the escalation.
    """
    # Update priority and custom severity field
    issue = self.jira.issue(issue_key)
    issue.update(
        priority={'name': new_priority},
        fields={
            self.severity_field_id: {'value': new_severity}
        }
    )
    
    # Add escalation comment
    escalation_comment = (
        f"⚠️ *Priority escalated to {new_priority}* "
        f"(Severity: {new_severity}) due to increased error frequency."
    )
    self.jira.add_comment(issue_key, escalation_comment)
```

---

## 6. Data Structures

### 6.1 Redis Key Patterns

| Key Pattern | Purpose | TTL | Value Type | Operations |
|-------------|---------|-----|------------|------------|
| `freq:{env}:{fingerprint}` | Rolling 5-minute occurrence counters | 300 seconds | Integer | `INCR`, `EXPIRE`, `GET` |
| `dedup:{event_id}` | Event deduplication tracking | 3600 seconds (1 hour) | String (timestamp) | `SETEX`, `EXISTS` |
| `comment_limit:{issue_key}` | Per-issue comment rate limiting | 900 seconds (15 minutes) | String (timestamp) | `SETEX`, `GET` |

#### 6.1.1 Frequency Counter Example

```
Key: freq:production:abc123def456...
Value: 15
TTL: 300 seconds (auto-expires after 5 minutes)
```

**Redis Operations:**

```python
# Increment counter atomically with TTL refresh
redis.pipeline()
    .incr("freq:production:abc123def456...")
    .expire("freq:production:abc123def456...", 300)
    .execute()

# Result: [15, True]  # 15 is new count, True indicates EXPIRE succeeded
```

#### 6.1.2 Deduplication Cache Example

```
Key: dedup:vercel-dpl_abc123-trace_def456
Value: "2025-01-15T10:30:45.123Z"
TTL: 3600 seconds (1 hour)
```

**Redis Operations:**

```python
# Check if event already processed
exists = redis.exists("dedup:vercel-dpl_abc123-trace_def456")
# Returns: 1 if exists, 0 if not found

# Mark event as processed
redis.setex(
    "dedup:vercel-dpl_abc123-trace_def456",
    3600,  # 1 hour TTL
    datetime.utcnow().isoformat()
)
```

#### 6.1.3 Comment Rate Limit Example

```
Key: comment_limit:ET-1234
Value: "2025-01-15T10:30:45.123Z"
TTL: 900 seconds (15 minutes)
```

**Redis Operations:**

```python
# Check if rate limit active
last_comment_time = redis.get("comment_limit:ET-1234")
if last_comment_time:
    elapsed = datetime.utcnow() - datetime.fromisoformat(last_comment_time)
    if elapsed.total_seconds() < 900:
        # Rate limit active, suppress comment
        return False

# Record new comment timestamp
redis.setex(
    "comment_limit:ET-1234",
    900,  # 15 minutes
    datetime.utcnow().isoformat()
)
```

### 6.2 MongoDB Collections

#### 6.2.1 `error_events` Collection

**Purpose:** Comprehensive audit trail of all processed error events with Jira action outcomes.

**Schema:**

```javascript
{
  "_id": ObjectId("..."),
  "event_id": "vercel-dpl_abc123-trace_def456",
  "fingerprint": "abc123def456789...",
  "source": "vercel",
  "service": "web-app",
  "environment": "production",
  "error_class": "TypeError",
  "message": "Cannot read properties of undefined",
  "sanitized_message": "Cannot read properties of [REDACTED]",
  "stack_trace": "TypeError: ...\n at /app/pages/checkout.tsx:123:45\n ...",
  "path": "/api/checkout",
  "url": "https://my-app-xyz.vercel.app/api/checkout",
  "release": "dpl_abc123xyz789",
  "log_url": "https://vercel.com/org/project/logs?q=traceId:trace_def456",
  "occurred_at": ISODate("2025-01-15T10:30:45.123Z"),
  "jira_issue_key": "ET-1234",
  "jira_action": "commented",  // "created" | "commented" | "escalated" | "suppressed"
  "frequency_count": 15,
  "severity": "SEV2",
  "priority": "High",
  "created_at": ISODate("2025-01-15T10:30:46.000Z")
}
```

**Indexes:**

```javascript
db.error_events.createIndex({ fingerprint: 1, environment: 1, occurred_at: -1 })
db.error_events.createIndex({ event_id: 1 }, { unique: true })
db.error_events.createIndex({ jira_issue_key: 1, occurred_at: -1 })
db.error_events.createIndex({ service: 1, environment: 1, occurred_at: -1 })
```

**Retention:** 7 years for compliance requirements

#### 6.2.2 `jira_actions` Collection

**Purpose:** Detailed log of all Jira operations (create, comment, escalate) with before/after state tracking.

**Schema:**

```javascript
{
  "_id": ObjectId("..."),
  "issue_key": "ET-1234",
  "action_type": "escalate",  // "create" | "comment" | "escalate"
  "fingerprint": "abc123def456789...",
  "event_id": "vercel-dpl_abc123-trace_def456",
  "before_priority": "Medium",
  "after_priority": "High",
  "before_severity": "SEV3",
  "after_severity": "SEV2",
  "comment_id": "10050",  // Jira comment ID if action added comment
  "frequency_count": 25,
  "performed_at": ISODate("2025-01-15T10:30:46.500Z"),
  "success": true,
  "error_message": null,
  "duration_ms": 185
}
```

**Indexes:**

```javascript
db.jira_actions.createIndex({ issue_key: 1, performed_at: -1 })
db.jira_actions.createIndex({ fingerprint: 1, performed_at: -1 })
db.jira_actions.createIndex({ action_type: 1, performed_at: -1 })
```

**Retention:** 7 years for compliance requirements

#### 6.2.3 `configuration_history` Collection

**Purpose:** Version control for YAML configuration file changes (severity rules, ownership rules, sanitization patterns).

**Schema:**

```javascript
{
  "_id": ObjectId("..."),
  "config_file": "severity_rules.yaml",
  "version": 3,
  "content": "production:\n  - threshold: 50\n    priority: Highest\n    severity: SEV1\n...",
  "changed_by": "user@example.com",
  "change_summary": "Increased production SEV1 threshold from 40 to 50",
  "applied_at": ISODate("2025-01-15T09:00:00.000Z"),
  "checksum": "sha256:abc123..."
}
```

**Indexes:**

```javascript
db.configuration_history.createIndex({ config_file: 1, applied_at: -1 })
db.configuration_history.createIndex({ version: -1 })
```

**Retention:** 7 years for audit trail

### 6.3 MongoDB Connection Configuration

```python
from pymongo import MongoClient, WriteConcern, ReadPreference

# Connection with optimized settings
mongo_client = MongoClient(
    os.getenv("MONGODB_URI"),
    maxPoolSize=100,
    minPoolSize=10,
    connectTimeoutMS=5000,
    serverSelectionTimeoutMS=5000,
    retryWrites=True,
    w="majority",  # Write concern for durability
    readPreference=ReadPreference.SECONDARY_PREFERRED  # Read from secondaries for non-critical queries
)

# Database and collection references
db = mongo_client[f"jiratest-{environment}"]
error_events_collection = db.error_events
jira_actions_collection = db.jira_actions
config_history_collection = db.configuration_history
```

---

## 7. Architecture Principles

### 7.1 Stateless Service Design

**Principle:** The service maintains zero in-process state, enabling horizontal autoscaling without coordination overhead.

**Implementation:**

- **Transient State → Redis:** All short-lived state (frequency counters, deduplication cache, rate limits) stored in Redis with TTL-based expiration
- **Persistent State → MongoDB:** All long-term data (audit trails, configuration versions) stored in MongoDB Atlas
- **No Session Affinity:** Load balancer distributes requests arbitrarily across tasks; any task can handle any request
- **Zero Shared Memory:** No shared variables, caches, or locks between application instances

**Benefits:**

- ECS tasks scale independently based on CPU/request rate
- Rolling deployments without state migration
- Instance failures don't result in data loss
- Simplified operational model

### 7.2 Configuration-Driven Behavior

**Principle:** All business logic thresholds externalized to YAML files, enabling non-developer rule updates.

**Externalized Configuration:**

| Configuration File | Controlled Behavior | Update Method |
|--------------------|---------------------|---------------|
| `config/severity_rules.yaml` | Frequency thresholds → priority/severity mapping | Git commit + SIGHUP signal |
| `config/ownership_rules.yaml` | Service/path/error patterns → assignee routing | Git commit + SIGHUP signal |
| `config/sanitization_patterns.yaml` | PII detection regex patterns | Git commit + SIGHUP signal |

**Hot-Reload Mechanism:**

```python
import signal

def reload_configuration(signum, frame):
    """
    Signal handler for SIGHUP: reload configuration from disk.
    
    Triggered by: kill -SIGHUP <pid>
    """
    logger.info("SIGHUP received, reloading configuration files")
    
    # Reload each configuration file
    PIISanitizer.reload_patterns()
    SeverityRulesEngine.reload_rules()
    OwnershipResolver.reload_rules()
    
    logger.info("Configuration reload complete")

# Register signal handler
signal.signal(signal.SIGHUP, reload_configuration)
```

### 7.3 Fail-Fast Validation

**Principle:** Validate inputs early and reject invalid requests immediately.

**Validation Points:**

1. **Webhook Authentication:** Reject with 401 Unauthorized if signature/token invalid
2. **Payload Structure:** Reject with 400 Bad Request if required fields missing
3. **Configuration Startup:** Fail application startup if YAML files invalid
4. **Environment Variables:** Fail startup if required config missing (REDIS_HOST, JIRA_BASE_URL)

### 7.4 Graceful Degradation

**Principle:** Continue processing when non-critical dependencies temporarily fail.

**Degradation Strategies:**

| Dependency | Failure Mode | Degradation Behavior |
|------------|--------------|----------------------|
| **Redis** | Connection timeout | Fall back to frequency count = 1; suppress deduplication; log degraded mode warning; continue processing |
| **Jira API** | HTTP 503 Service Unavailable | Log error to MongoDB for retry; return 202 Accepted to webhook; metric: jira_api_errors_total |
| **MongoDB** | Connection failure | Skip audit logging; continue Jira operations; log warning; metric: mongodb_write_errors_total |

**Redis Degradation Example:**

```python
try:
    count = frequency_tracker.increment(env, fingerprint)
except redis.exceptions.ConnectionError:
    logger.warning("Redis unavailable, falling back to count=1")
    metrics.increment_counter("redis_degradation_total")
    count = 1  # Treat as first occurrence
```

### 7.5 Observability First

**Principle:** Every operation emits logs and metrics for visibility.

**Structured Logging:**

```python
logger.info(
    "Jira issue created",
    extra={
        "event_id": event.event_id,
        "fingerprint": fingerprint,
        "jira_issue_key": issue_key,
        "priority": priority,
        "severity": severity,
        "duration_ms": 185,
        "action": "jira_create"
    }
)
```

**Metrics Emission:**

```python
metrics.increment_counter("jira_issues_created_total", {
    "environment": event.environment,
    "priority": priority
})

metrics.observe_histogram("jira_api_latency_seconds", 0.185, {
    "environment": event.environment,
    "operation": "create_issue"
})
```

### 7.6 Security by Default

**Principle:** Defense-in-depth security with multiple layers.

**Security Layers:**

1. **Network Level:** Private subnets, security groups, TLS encryption
2. **Application Level:** Webhook authentication, PII sanitization, rate limiting
3. **Data Level:** Secrets Manager for credentials, encrypted connections
4. **Operational Level:** Audit logging, metrics alerting, incident response

---

## 8. Network Topology

### 8.1 AWS VPC Architecture

```mermaid
graph TB
    subgraph "Internet"
        VERCEL[Vercel Webhooks<br/>76.76.21.0/24]
        GCP[GCP Pub/Sub Push<br/>35.191.0.0/16<br/>35.187.0.0/16]
        JIRA[Jira Cloud API<br/>*.atlassian.net]
        MONGO[MongoDB Atlas<br/>cluster0.mongodb.net]
    end
    
    subgraph "AWS VPC 10.0.0.0/16"
        subgraph "Public Subnets"
            ALB[Application Load Balancer<br/>TLS Termination]
            NAT[NAT Gateway<br/>Outbound Internet]
        end
        
        subgraph "Private Subnet 10.0.1.0/24 (AZ-A)"
            TASK1[ECS Task 1<br/>10.0.1.10]
            REDIS1[Redis Primary<br/>10.0.1.50]
        end
        
        subgraph "Private Subnet 10.0.2.0/24 (AZ-B)"
            TASK2[ECS Task 2<br/>10.0.2.10]
            REDIS2[Redis Replica<br/>10.0.2.50]
        end
        
        subgraph "VPC Endpoints"
            SME[Secrets Manager<br/>Endpoint]
            CWE[CloudWatch Logs<br/>Endpoint]
        end
    end
    
    VERCEL -->|HTTPS 443| ALB
    GCP -->|HTTPS 443| ALB
    
    ALB -->|HTTP 8080| TASK1
    ALB -->|HTTP 8080| TASK2
    
    TASK1 <-->|TCP 6379| REDIS1
    TASK2 <-->|TCP 6379| REDIS2
    REDIS1 <-->|Replication| REDIS2
    
    TASK1 -->|HTTPS via NAT| NAT
    TASK2 -->|HTTPS via NAT| NAT
    
    NAT -->|HTTPS 443| JIRA
    NAT -->|HTTPS 27017| MONGO
    
    TASK1 -->|HTTPS Internal| SME
    TASK2 -->|HTTPS Internal| SME
    
    TASK1 -->|Logs| CWE
    TASK2 -->|Logs| CWE
    
    style VERCEL fill:#4285f4
    style GCP fill:#4285f4
    style JIRA fill:#0052cc
    style MONGO fill:#00ed64
    style ALB fill:#ff9900
    style NAT fill:#ff9900
    style REDIS1 fill:#dc382d
    style REDIS2 fill:#dc382d
```

### 8.2 Security Group Rules

#### 8.2.1 ALB Security Group (`jiratest-alb-sg`)

| Direction | Protocol/Port | Source/Destination | Purpose |
|-----------|---------------|-------------------|---------|
| Inbound | TCP/443 (HTTPS) | `76.76.21.0/24` (Vercel) | Accept Vercel Log Drain webhooks |
| Inbound | TCP/443 (HTTPS) | `35.191.0.0/16`, `35.187.0.0/16` (GCP) | Accept GCP Pub/Sub push webhooks |
| Outbound | TCP/8080 | `jiratest-ecs-tasks-sg` | Forward to ECS tasks |

#### 8.2.2 ECS Tasks Security Group (`jiratest-ecs-tasks-sg`)

| Direction | Protocol/Port | Source/Destination | Purpose |
|-----------|---------------|-------------------|---------|
| Inbound | TCP/8080 | `jiratest-alb-sg` | Receive traffic from ALB |
| Outbound | TCP/6379 | `jiratest-redis-sg` | Redis operations |
| Outbound | TCP/443 | `0.0.0.0/0` | HTTPS to Jira Cloud, MongoDB Atlas via NAT |
| Outbound | TCP/443 | VPC CIDR | Secrets Manager and CloudWatch endpoints |

#### 8.2.3 Redis Security Group (`jiratest-redis-sg`)

| Direction | Protocol/Port | Source/Destination | Purpose |
|-----------|---------------|-------------------|---------|
| Inbound | TCP/6379 | `jiratest-ecs-tasks-sg` | Allow ECS task connections |
| Outbound | TCP/6379 | `jiratest-redis-sg` | Replication between nodes |

### 8.3 Subnet Architecture

| Subnet Type | CIDR Block | Availability Zone | Resources |
|-------------|------------|-------------------|-----------|
| **Public Subnet A** | 10.0.0.0/24 | us-east-1a | ALB, NAT Gateway A |
| **Public Subnet B** | 10.0.100.0/24 | us-east-1b | NAT Gateway B |
| **Private Subnet A** | 10.0.1.0/24 | us-east-1a | ECS Task 1, Redis Primary |
| **Private Subnet B** | 10.0.2.0/24 | us-east-1b | ECS Task 2, Redis Replica |

### 8.4 IP Whitelisting for MongoDB Atlas

**NAT Gateway Elastic IPs:** MongoDB Atlas network access whitelist must include NAT Gateway Elastic IPs for outbound connections from ECS tasks.

```terraform
# Example NAT Gateway EIP allocation
resource "aws_eip" "nat_gw_a" {
  vpc = true
  tags = {
    Name = "jiratest-nat-gw-a-eip"
  }
}

# Output for MongoDB Atlas whitelist configuration
output "nat_gateway_ips" {
  value = [
    aws_eip.nat_gw_a.public_ip,
    aws_eip.nat_gw_b.public_ip
  ]
  description = "Add these IPs to MongoDB Atlas network access whitelist"
}
```

---

## 9. Security Architecture

### 9.1 Defense-in-Depth Layers

```mermaid
graph TB
    subgraph "Layer 1: Network Security"
        L1A[Private Subnets<br/>No Public IPs]
        L1B[Security Groups<br/>Least Privilege]
        L1C[TLS 1.2+ Enforcement<br/>All External Traffic]
    end
    
    subgraph "Layer 2: Application Security"
        L2A[Webhook Authentication<br/>HMAC + OIDC]
        L2B[HTTP Rate Limiting<br/>100 req/min per IP]
        L2C[Input Validation<br/>Pydantic Models]
    end
    
    subgraph "Layer 3: Data Security"
        L3A[PII Sanitization<br/>Before Fingerprinting]
        L3B[Encrypted Connections<br/>Redis TLS Optional]
        L3C[Secrets Manager<br/>No Plaintext Credentials]
    end
    
    subgraph "Layer 4: Operational Security"
        L4A[Audit Logging<br/>All Actions to MongoDB]
        L4B[Metrics Alerting<br/>Auth Failure Threshold]
        L4C[IAM Least Privilege<br/>Task Role Restrictions]
    end
    
    style L1A fill:#ffebee
    style L1B fill:#ffebee
    style L1C fill:#ffebee
    style L2A fill:#fff3e0
    style L2B fill:#fff3e0
    style L2C fill:#fff3e0
    style L3A fill:#e8f5e9
    style L3B fill:#e8f5e9
    style L3C fill:#e8f5e9
    style L4A fill:#e3f2fd
    style L4B fill:#e3f2fd
    style L4C fill:#e3f2fd
```

### 9.2 Authentication Flow

#### 9.2.1 Vercel Webhook Verification

```python
def verify_vercel_request(request: Request) -> bool:
    """
    Verify Vercel webhook signature using HMAC-SHA256.
    
    Steps:
    1. Extract x-vercel-signature header
    2. Compute HMAC over raw request body
    3. Constant-time comparison
    
    Returns:
        True if signature valid, False otherwise
    """
    signature_header = request.headers.get('x-vercel-signature')
    if not signature_header:
        logger.warning("Missing x-vercel-signature header")
        return False
    
    # Load secret from AWS Secrets Manager
    webhook_secret = secrets_manager.get_secret('jira/jiratest/prod/webhook-secret')
    
    # Compute expected signature
    expected_signature = hmac.new(
        webhook_secret.encode('utf-8'),
        request.data,  # Raw bytes, NOT parsed JSON
        hashlib.sha256
    ).hexdigest()
    
    # Constant-time comparison to prevent timing attacks
    is_valid = hmac.compare_digest(signature_header, expected_signature)
    
    if not is_valid:
        logger.warning("Vercel signature verification failed")
        metrics.increment_counter("webhook_auth_failures_total", {"source": "vercel"})
    
    return is_valid
```

#### 9.2.2 GCP OIDC Token Verification

```python
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

def verify_gcp_request(request: Request) -> bool:
    """
    Verify GCP Pub/Sub push OIDC JWT token.
    
    Steps:
    1. Extract Authorization Bearer token
    2. Verify JWT signature and claims
    3. Validate issuer and audience
    
    Returns:
        True if token valid, False otherwise
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        logger.warning("Missing or invalid Authorization header")
        return False
    
    token = auth_header[len('Bearer '):]
    
    # Expected audience is service endpoint URL
    audience = os.getenv('SERVICE_URL')  # e.g., https://error-triage.jiratest.com/events
    
    try:
        # Verify JWT signature and decode claims
        info = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience
        )
        
        # Validate issuer is Google
        valid_issuers = [
            'https://accounts.google.com',
            'https://www.googleapis.com'
        ]
        
        if info.get('iss') not in valid_issuers:
            logger.warning(f"Invalid issuer: {info.get('iss')}")
            return False
        
        return True
        
    except ValueError as e:
        logger.warning(f"GCP token verification failed: {e}")
        metrics.increment_counter("webhook_auth_failures_total", {"source": "gcp"})
        return False
```

### 9.3 PII Sanitization Process

```mermaid
graph LR
    RAW[Raw Error Message] --> PATTERNS[Load Regex Patterns]
    PATTERNS --> EMAIL[Replace Emails<br/>[EMAIL]]
    EMAIL --> UUID[Replace UUIDs<br/>[UUID]]
    UUID --> IDS[Replace IDs<br/>[ID]]
    IDS --> TOKENS[Replace Tokens<br/>[TOKEN]]
    TOKENS --> SANITIZED[Sanitized Message]
    
    SANITIZED --> FINGER[Fingerprint Generation]
    SANITIZED --> JIRA[Jira Transmission]
    
    style RAW fill:#ffcdd2
    style SANITIZED fill:#c8e6c9
```

### 9.4 Secrets Management

**AWS Secrets Manager Structure:**

```
jira/jiratest/prod/credentials
{
  "jira_base_url": "https://myorg.atlassian.net",
  "jira_api_token": "ATATT3xFfGF0...",
  "jira_project_key": "ET",
  "jira_severity_field_id": "customfield_10050"
}

jira/jiratest/prod/webhook-secret
{
  "vercel_webhook_secret": "whsec_abc123..."
}

mongodb/jiratest/prod/connection-string
{
  "connection_uri": "mongodb+srv://username:password@cluster0.mongodb.net/jiratest-prod"
}
```

**Rotation Schedule:**

| Secret Type | Rotation Frequency | Method |
|-------------|-------------------|--------|
| **Jira API Token** | 90 days | Manual via Jira UI + Secrets Manager update |
| **Webhook Secret** | 180 days | Manual regeneration + coordinate with Vercel/GCP |
| **MongoDB Password** | 90 days | MongoDB Atlas automatic rotation |

### 9.5 IAM Roles

#### 9.5.1 ECS Task Execution Role

**Purpose:** Pull container images, write logs, retrieve secrets during task startup.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:us-east-1:*:log-group:/aws/ecs/jiratest-error-triage-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": [
        "arn:aws:secretsmanager:us-east-1:*:secret:jira/jiratest/*",
        "arn:aws:secretsmanager:us-east-1:*:secret:mongodb/jiratest/*"
      ]
    }
  ]
}
```

#### 9.5.2 ECS Task Role

**Purpose:** Application-level permissions for runtime operations.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:us-east-1:*:log-group:/aws/ecs/jiratest-error-triage-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": [
        "arn:aws:secretsmanager:us-east-1:*:secret:jira/jiratest/*"
      ]
    }
  ]
}
```

---

## 10. Performance Characteristics

### 10.1 Performance Targets

| Metric | Target | Measurement | Current Status |
|--------|--------|-------------|----------------|
| **`/events` Endpoint Latency (p95)** | < 200ms | Histogram: `event_processing_duration_seconds` | Target for production launch |
| **`/events` Endpoint Latency (p99)** | < 500ms | Histogram: `event_processing_duration_seconds` | Target for production launch |
| **Sustained Throughput** | 100 requests/second | Counter: `events_received_total` | Target for production launch |
| **Peak Burst Throughput** | 500 requests/second | Load testing with 10-second burst | Target for production launch |
| **Jira API Call Timeout** | 10 seconds | Configuration: `JIRA_API_TIMEOUT` | Implemented |
| **Redis Operation Latency (p99)** | < 5ms | Histogram: `redis_operation_latency_seconds` | ElastiCache default performance |

### 10.2 Scalability Architecture

```mermaid
graph TB
    subgraph "Autoscaling Trigger Conditions"
        CPU[CPU Utilization > 70%]
        REQ[Request Count > Threshold]
    end
    
    subgraph "ECS Service Autoscaling"
        MIN[Min Tasks: 2]
        MAX[Max Tasks: 10]
        TARGET[Target Tasks: Dynamic]
    end
    
    subgraph "Stateless Task Pool"
        T1[Task 1]
        T2[Task 2]
        T3[Task 3]
        TN[Task N]
    end
    
    CPU --> TARGET
    REQ --> TARGET
    
    TARGET --> T1
    TARGET --> T2
    TARGET --> T3
    TARGET --> TN
    
    T1 --> REDIS[(Redis<br/>Shared State)]
    T2 --> REDIS
    T3 --> REDIS
    TN --> REDIS
    
    style CPU fill:#ffebee
    style REQ fill:#ffebee
    style REDIS fill:#dc382d
```

### 10.3 Performance Optimization Strategies

#### 10.3.1 Redis Connection Pooling

```python
# Connection pool configuration
redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST"),
    port=6379,
    db=0,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
    retry_on_timeout=True,
    health_check_interval=30,
    max_connections=50,  # Pool size per ECS task
    connection_pool=redis.ConnectionPool(
        max_connections=50,
        socket_keepalive=True,
        socket_keepalive_options={
            socket.TCP_KEEPIDLE: 60,
            socket.TCP_KEEPINTVL: 10,
            socket.TCP_KEEPCNT: 3
        }
    )
)
```

**Benefits:**

- Reuses connections across requests
- Reduces TCP handshake overhead
- Maintains persistent connections with keep-alive

#### 10.3.2 Redis Pipeline Operations

```python
# Atomic INCR + EXPIRE without round-trip overhead
def increment_with_ttl(key: str, ttl: int) -> int:
    """
    Increment counter and set TTL in single pipeline.
    
    Reduces latency from 2 round-trips to 1.
    """
    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, ttl)
    results = pipe.execute()
    
    return results[0]  # INCR returns new value
```

**Performance Impact:**

- Single Redis command: ~1ms
- Two sequential commands: ~2ms
- Pipelined commands: ~1.2ms (20% reduction)

#### 10.3.3 Compiled Regex Caching

```python
class PIISanitizer:
    """Cache compiled regex patterns for performance."""
    
    def __init__(self, patterns_yaml: str):
        # Compile patterns once at initialization
        self.patterns = self._load_and_compile_patterns(patterns_yaml)
    
    def _load_and_compile_patterns(self, yaml_path: str) -> List[Tuple[re.Pattern, str]]:
        """Compile regex patterns for reuse."""
        with open(yaml_path) as f:
            config = yaml.safe_load(f)
        
        compiled = []
        for pattern_config in config['patterns']:
            compiled_pattern = re.compile(
                pattern_config['pattern'],
                re.IGNORECASE | re.MULTILINE
            )
            replacement = pattern_config['replacement']
            compiled.append((compiled_pattern, replacement))
        
        return compiled
    
    def sanitize(self, text: str) -> str:
        """Apply all patterns with zero compilation overhead."""
        for pattern, replacement in self.patterns:
            text = pattern.sub(replacement, text)
        return text
```

**Performance Impact:**

- Regex compilation: ~5ms per pattern (one-time at startup)
- Regex matching with compiled pattern: ~0.5ms
- Regex matching with runtime compilation: ~5.5ms (10x slower)

#### 10.3.4 Gunicorn Worker Configuration

```python
# gunicorn.conf.py
import multiprocessing

# Worker processes
workers = 4  # Fixed for predictable resource usage
worker_class = "sync"  # Sync workers for CPU-bound fingerprinting

# Worker connections
worker_connections = 100  # Max simultaneous connections per worker

# Timeouts
timeout = 30  # Worker timeout (seconds)
graceful_timeout = 30  # Graceful shutdown timeout

# Keep-alive
keepalive = 5  # TCP keep-alive timeout

# Logging
accesslog = "-"  # stdout
errorlog = "-"  # stderr
loglevel = "info"
```

**Capacity Calculation:**

- Workers: 4
- Connections per worker: 100
- Total capacity: 400 concurrent requests
- With 200ms average latency: **2,000 requests/second theoretical max**
- Practical sustained: **100-300 requests/second** (conservatively accounting for Jira API latency)

### 10.4 Bottleneck Analysis

| Component | Latency Contribution | Mitigation Strategy |
|-----------|---------------------|---------------------|
| **Webhook Authentication** | 1-2ms (HMAC) / 50-100ms (OIDC) | Cache OIDC public keys; use connection pooling for key fetch |
| **Payload Parsing** | 2-5ms | Use Pydantic for compiled validation; avoid unnecessary copies |
| **PII Sanitization** | 5-10ms | Compiled regex patterns; limit to first 1000 chars of message |
| **Fingerprint Generation** | 3-5ms (SHA-256) | Acceptable CPU cost for deterministic hashing |
| **Redis Operations** | 1-3ms (p95) | Connection pooling, pipeline operations, co-located in VPC |
| **Jira API Calls** | 100-500ms (p95) | **PRIMARY BOTTLENECK** - Asynchronous processing required |
| **MongoDB Writes** | 10-50ms (p95) | Batch writes, fire-and-forget for audit logs |

**Critical Path Optimization:**

To meet < 200ms p95 latency target for `/events` endpoint:

1. **Return 202 Accepted immediately** after authentication and validation
2. **Enqueue event to SQS or Redis Streams** for asynchronous processing
3. **Background worker processes** handle Jira API calls outside request path
4. **Webhook sender receives fast response**, retries handled gracefully via idempotency

### 10.5 Monitoring Dashboards

#### 10.5.1 CloudWatch Dashboard Configuration

**Metrics to Display:**

- `events_received_total` (rate per minute)
- `event_processing_duration_seconds` histogram (p50, p95, p99)
- `jira_issues_created_total` (rate per hour)
- `jira_comments_added_total` (rate per hour)
- `jira_escalations_total` (count)
- `errors_total` by error type
- ECS CPU utilization (%)
- ECS memory utilization (%)
- Redis CPU utilization (ElastiCache)
- Redis memory utilization (ElastiCache)

#### 10.5.2 Alert Thresholds

| Alert | Condition | Severity | Action |
|-------|-----------|----------|--------|
| **High Error Rate** | `errors_total` > 5% of `events_received_total` for 5 minutes | Critical | Page on-call; investigate logs |
| **Jira API Latency** | `jira_api_latency_seconds` p99 > 5 seconds for 5 minutes | High | Check Jira Cloud status page |
| **Redis Connection Failures** | `redis_connection_errors_total` > 10 in 5 minutes | Critical | Check ElastiCache cluster health |
| **Authentication Failures** | `webhook_auth_failures_total` > 20 in 5 minutes | Critical | Potential brute-force attack; review source IPs |
| **ECS CPU High** | CPU utilization > 80% for 10 minutes | Warning | Review autoscaling settings |

---

## Appendix A: Technology Stack Summary

| Category | Technology | Version | Purpose |
|----------|-----------|---------|---------|
| **Runtime** | Python | 3.11+ | Application language |
| **Web Framework** | Flask | 3.1.2 | HTTP API endpoints |
| **WSGI Server** | Gunicorn | 23.0.0 | Production server |
| **Jira Client** | jira | 3.10.5 | Jira REST API integration |
| **Redis Client** | redis-py | 6.4.0 | Redis operations |
| **MongoDB Client** | pymongo | 4.10.1 | MongoDB operations |
| **Validation** | Pydantic | 2.10.4 | Request/response validation |
| **Configuration** | PyYAML | 6.0.2 | YAML parsing |
| **AWS SDK** | boto3 | 1.35.90 | Secrets Manager integration |
| **Metrics** | prometheus-client | 0.21.0 | Metrics exposition |
| **Logging** | python-json-logger | 3.2.1 | Structured JSON logs |
| **Authentication** | google-auth | 2.37.0 | GCP OIDC validation |
| **Container Base** | python:3.11-slim | latest | Docker image |
| **Orchestration** | AWS ECS Fargate | 1.4.0 | Container orchestration |
| **Cache** | Redis ElastiCache | 7.2 | Frequency counters, deduplication |
| **Database** | MongoDB Atlas | 7.0+ | Audit trails, action logs |

---

## Appendix B: References

- **Jira REST API Documentation:** https://developer.atlassian.com/cloud/jira/platform/rest/v3/
- **Vercel Log Drains:** https://vercel.com/docs/observability/log-drains
- **GCP Pub/Sub Push Subscriptions:** https://cloud.google.com/pubsub/docs/push
- **Redis Commands Reference:** https://redis.io/commands/
- **MongoDB Aggregation Pipeline:** https://www.mongodb.com/docs/manual/aggregation/
- **AWS ECS Best Practices:** https://docs.aws.amazon.com/AmazonECS/latest/bestpracticesguide/
- **Prometheus Exposition Formats:** https://prometheus.io/docs/instrumenting/exposition_formats/

---

**Document Version:** 1.0  
**Last Updated:** 2025-01-15  
**Status:** Initial Release
