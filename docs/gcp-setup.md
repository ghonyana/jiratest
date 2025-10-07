# GCP Cloud Logging Integration Setup Guide

This guide provides comprehensive instructions for configuring Google Cloud Platform (GCP) Cloud Logging to send error events to the Error Triage service via Pub/Sub push subscriptions.

## Table of Contents

- [Prerequisites and Requirements](#prerequisites-and-requirements)
- [Cloud Logging Sink Creation](#cloud-logging-sink-creation)
- [Pub/Sub Topic and Subscription Setup](#pubsub-topic-and-subscription-setup)
- [Service Account Configuration](#service-account-configuration)
- [OIDC Token Validation Mechanism](#oidc-token-validation-mechanism)
- [Payload Format Reference](#payload-format-reference)
- [Deep Link Construction](#deep-link-construction)
- [Testing the Integration](#testing-the-integration)
- [Troubleshooting Common Issues](#troubleshooting-common-issues)
- [Advanced Configuration](#advanced-configuration)

---

## Prerequisites and Requirements

Before setting up the GCP integration, ensure the following requirements are met:

### GCP Project Setup

- **GCP Project**: Active GCP project with billing enabled
- **Required APIs**: Cloud Logging API, Pub/Sub API, and IAM API must be enabled
  ```bash
  gcloud services enable logging.googleapis.com
  gcloud services enable pubsub.googleapis.com
  gcloud services enable iam.googleapis.com
  ```

### IAM Permissions

Your GCP user account or service account must have the following permissions:

- `logging.sinks.create` - Create log sinks
- `pubsub.topics.create` - Create Pub/Sub topics
- `pubsub.subscriptions.create` - Create Pub/Sub subscriptions
- `iam.serviceAccounts.create` - Create service accounts
- `iam.serviceAccounts.keys.create` - Create service account keys (if needed)
- `pubsub.subscriptions.setIamPolicy` - Configure push authentication

**Recommended Role**: `roles/logging.configWriter`, `roles/pubsub.admin`, `roles/iam.serviceAccountAdmin`

### Error Triage Service Deployment

- **Service Endpoint**: Error Triage service must be deployed and accessible at `https://error-triage.jiratest.com/events`
- **HTTPS/TLS**: Endpoint must use HTTPS with valid SSL certificate
- **Network Security**: GCP Pub/Sub push IP ranges must be whitelisted in your AWS ALB security group:
  - `35.191.0.0/16`
  - `35.187.0.0/16`
  
  **AWS Security Group Rule Example**:
  ```hcl
  # Terraform configuration
  ingress {
    description = "GCP Pub/Sub Push"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["35.191.0.0/16", "35.187.0.0/16"]
  }
  ```

### Network Connectivity

- Verify connectivity from GCP to your service endpoint:
  ```bash
  curl -I https://error-triage.jiratest.com/healthz
  ```
  Expected response: `200 OK`

---

## Cloud Logging Sink Creation

Cloud Logging sinks route log entries from GCP services to Pub/Sub topics based on filter expressions.

### Step 1: Navigate to Log Router

1. Open the [GCP Console](https://console.cloud.google.com)
2. Navigate to **Logging** → **Log Router**
3. Click **Create Sink**

### Step 2: Configure Sink Details

**Sink Name**: `error-triage-sink`

**Sink Description**: Routes error logs to Error Triage service for Jira issue creation

### Step 3: Select Sink Destination

1. **Sink Service**: Select **Cloud Pub/Sub topic**
2. **Sink Destination**: 
   - Click **Create new Cloud Pub/Sub topic**
   - **Topic Name**: `error-events`
   - **Topic Location**: Select same region as your primary logging source (e.g., `us-central1`)
3. Click **Create Topic**

### Step 4: Build Inclusion Filter

The inclusion filter determines which log entries are routed to the sink. Use the following base filter to capture error-level logs from common GCP services:

```
severity >= ERROR AND resource.type = (cloud_run_revision OR cloud_function OR gke_container)
```

#### Filter Expression Components

- **`severity >= ERROR`**: Captures ERROR, CRITICAL, and ALERT level logs
- **`resource.type`**: Specifies the resource types to monitor

#### Example Filter Expressions

**Filter by Specific Service**:
```
severity >= ERROR AND resource.type = "cloud_run_revision" AND resource.labels.service_name = "api-service"
```

**Filter by Error Pattern**:
```
severity >= ERROR AND (
  textPayload =~ "TypeError.*" OR
  jsonPayload.error.type = "TypeError" OR
  textPayload =~ "Error:.*undefined"
)
```

**Filter by Environment Label**:
```
severity >= ERROR AND labels.environment = "production"
```

**Combined Filter (Recommended)**:
```
severity >= ERROR 
AND resource.type = (cloud_run_revision OR cloud_function OR gke_container)
AND NOT textPayload =~ "healthcheck"
AND labels.environment = ("production" OR "staging")
```

### Step 5: Configure Exclusion Filters (Optional)

Exclude health check logs, expected errors, and test traffic to reduce noise:

```
resource.type = "cloud_run_revision" AND textPayload =~ "/healthz"
```

```
jsonPayload.message =~ "Expected error for testing"
```

```
labels.environment = "test"
```

### Step 6: Review and Create

1. Review the sink configuration
2. Click **Create Sink**
3. Verify the sink appears in the Log Router list with status "Active"

### Command-Line Alternative

Create the sink using `gcloud` CLI:

```bash
gcloud logging sinks create error-triage-sink \
  pubsub.googleapis.com/projects/YOUR_PROJECT_ID/topics/error-events \
  --log-filter='severity >= ERROR AND resource.type = (cloud_run_revision OR cloud_function OR gke_container)'
```

---

## Pub/Sub Topic and Subscription Setup

### Step 1: Verify Topic Creation

The topic `error-events` should have been created automatically during sink setup. Verify:

```bash
gcloud pubsub topics describe error-events
```

If the topic doesn't exist, create it manually:

```bash
gcloud pubsub topics create error-events --message-retention-duration=7d
```

### Step 2: Create Push Subscription

Navigate to **Pub/Sub** → **Subscriptions** → **Create Subscription**

#### Subscription Configuration

**Subscription ID**: `error-events-push`

**Select a Cloud Pub/Sub topic**: `error-events`

**Delivery type**: **Push**

**Endpoint URL**: `https://error-triage.jiratest.com/events`

**Acknowledgment deadline**: `10 seconds`

> **Important**: The Error Triage service must respond with a 2xx status code within 10 seconds. The service returns `202 Accepted` immediately after validation.

#### Retry Policy

Configure exponential backoff for failed delivery attempts:

- **Minimum backoff**: 10 seconds
- **Maximum backoff**: 600 seconds (10 minutes)

#### Dead Letter Topic

Configure a dead letter topic for messages that fail after 5 delivery attempts:

1. **Create dead letter topic**:
   ```bash
   gcloud pubsub topics create error-events-dlq
   ```

2. **Set dead letter policy** in subscription:
   - **Dead letter topic**: `error-events-dlq`
   - **Maximum delivery attempts**: 5

#### Message Retention

Set message retention duration to **7 days** for debugging failed deliveries.

### Step 3: Create Subscription via CLI

```bash
gcloud pubsub subscriptions create error-events-push \
  --topic=error-events \
  --push-endpoint=https://error-triage.jiratest.com/events \
  --ack-deadline=10 \
  --min-retry-delay=10s \
  --max-retry-delay=600s \
  --dead-letter-topic=error-events-dlq \
  --max-delivery-attempts=5 \
  --message-retention-duration=7d
```

---

## Service Account Configuration

A dedicated service account is used for authenticating push subscription requests to the Error Triage service.

### Step 1: Create Service Account

Navigate to **IAM & Admin** → **Service Accounts** → **Create Service Account**

**Service Account Name**: `error-triage`

**Service Account ID**: `error-triage@YOUR_PROJECT_ID.iam.gserviceaccount.com`

**Service Account Description**: Service account for Error Triage Pub/Sub push authentication

Click **Create and Continue**

### Step 2: Grant Required Roles

**Role**: `roles/pubsub.publisher`

This role allows the service account to be used for push subscription authentication.

Click **Continue** → **Done**

### Step 3: Enable OIDC Token Generation

The service account must have the "Service Account Token Creator" role on itself to generate OIDC tokens:

```bash
gcloud iam service-accounts add-iam-policy-binding \
  error-triage@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --member="serviceAccount:error-triage@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"
```

### Step 4: Configure Push Subscription Authentication

Update the push subscription to use the service account for OIDC token generation:

```bash
gcloud pubsub subscriptions update error-events-push \
  --push-auth-service-account=error-triage@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

This configuration causes GCP to include an `Authorization: Bearer {jwt_token}` header in all push requests to your service endpoint.

### Step 5: Set OIDC Audience

The OIDC token audience must match your service endpoint URL:

```bash
gcloud pubsub subscriptions update error-events-push \
  --push-auth-token-audience=https://error-triage.jiratest.com/events
```

### Command-Line Service Account Setup

Complete setup in one script:

```bash
#!/bin/bash
PROJECT_ID="your-gcp-project-id"
SA_NAME="error-triage"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Create service account
gcloud iam service-accounts create ${SA_NAME} \
  --display-name="Error Triage Push Authentication" \
  --description="Service account for Error Triage Pub/Sub push authentication"

# Grant publisher role
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/pubsub.publisher"

# Enable token creation
gcloud iam service-accounts add-iam-policy-binding ${SA_EMAIL} \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/iam.serviceAccountTokenCreator"

# Configure push subscription
gcloud pubsub subscriptions update error-events-push \
  --push-auth-service-account=${SA_EMAIL} \
  --push-auth-token-audience=https://error-triage.jiratest.com/events

echo "Service account setup complete: ${SA_EMAIL}"
```

---

## OIDC Token Validation Mechanism

The Error Triage service validates incoming GCP push requests using OIDC token verification.

### Request Authentication Flow

1. **GCP Pub/Sub** generates an OIDC JWT token using the configured service account
2. **Push Request** includes the token in the `Authorization` header:
   ```
   Authorization: Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6...
   ```
3. **Error Triage Service** extracts and validates the token

### Token Validation Steps

The service performs the following validation checks using the `google-auth` Python library:

1. **Extract Token**: Parse `Authorization: Bearer {token}` header
2. **Verify Signature**: Validate JWT signature using Google's public keys
3. **Check Expiration**: Ensure token is not expired (`exp` claim)
4. **Validate Audience**: Confirm `aud` claim matches `https://error-triage.jiratest.com/events`
5. **Verify Issuer**: Ensure `iss` claim is `https://accounts.google.com` or `https://www.googleapis.com`
6. **Check Email**: Validate `email` claim matches the configured service account email

### Implementation Example

The Error Triage service implements token validation in `src/utils/auth.py`:

```python
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

def verify_gcp_token(request, audience: str, expected_email: str) -> bool:
    """
    Verify GCP Pub/Sub push request OIDC token.
    
    Args:
        request: Flask request object
        audience: Expected audience (service endpoint URL)
        expected_email: Expected service account email
        
    Returns:
        True if token is valid, False otherwise
    """
    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    
    token = auth_header[len("Bearer "):]
    
    try:
        # Verify token signature and claims
        info = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience
        )
        
        # Validate issuer
        if info.get("iss") not in (
            "https://accounts.google.com",
            "https://www.googleapis.com"
        ):
            return False
        
        # Validate service account email
        if info.get("email") != expected_email:
            return False
        
        return True
        
    except ValueError as e:
        # Token validation failed
        return False
```

### Rejection Response

Invalid or missing tokens result in a `401 Unauthorized` response:

```json
{
  "error": "Unauthorized",
  "message": "Invalid or missing OIDC token"
}
```

### Testing Token Validation

Test the authentication mechanism:

```bash
# Without token (should fail)
curl -X POST https://error-triage.jiratest.com/events \
  -H "Content-Type: application/json" \
  -d '{"test": "data"}'

# Expected response: 401 Unauthorized
```

---

## Payload Format Reference

GCP Pub/Sub push subscriptions deliver log entries wrapped in a standard message envelope.

### Push Subscription Wrapper

```json
{
  "message": {
    "data": "eyJzZXZlcml0eSI6IkVSUk9SIiwidGV4dFBheWxvYWQiOiJUeXBlRXJyb3I6IENhbm5vdCByZWFkIHByb3BlcnR5ICd4JyBvZiB1bmRlZmluZWQiLCJyZXNvdXJjZSI6eyJ0eXBlIjoiY2xvdWRfcnVuX3JldmlzaW9uIiwibGFiZWxzIjp7InNlcnZpY2VfbmFtZSI6ImFwaS1zZXJ2aWNlIiwicmV2aXNpb25fbmFtZSI6ImFwaS1zZXJ2aWNlLTAwMDQyLXh5eiJ9fSwiaW5zZXJ0SWQiOiJhYmMxMjMiLCJ0aW1lc3RhbXAiOiIyMDI1LTAxLTE1VDEwOjMwOjQ1LjEyM1oifQ==",
    "messageId": "123456789",
    "publishTime": "2025-01-15T10:30:45.123Z"
  },
  "subscription": "projects/your-project/subscriptions/error-events-push"
}
```

#### Wrapper Fields

- **`message.data`**: Base64-encoded JSON log entry
- **`message.messageId`**: Unique Pub/Sub message ID
- **`message.publishTime`**: Timestamp when message was published
- **`subscription`**: Full subscription resource name

### Base64 Decoding

Decode the `message.data` field to access the log entry:

```python
import base64
import json

# Extract and decode
encoded_data = payload['message']['data']
decoded_json = base64.b64decode(encoded_data).decode('utf-8')
log_entry = json.loads(decoded_json)
```

### Decoded Log Entry Structure

#### Text Payload Example

```json
{
  "severity": "ERROR",
  "textPayload": "TypeError: Cannot read property 'x' of undefined\n  at /app/pages/checkout.tsx:123:45\n  at processCheckout (/app/lib/checkout.ts:89:12)",
  "resource": {
    "type": "cloud_run_revision",
    "labels": {
      "service_name": "api-service",
      "revision_name": "api-service-00042-xyz",
      "location": "us-central1",
      "project_id": "your-project-id"
    }
  },
  "insertId": "abc123def456",
  "timestamp": "2025-01-15T10:30:45.123456Z",
  "trace": "projects/your-project/traces/1234567890abcdef",
  "labels": {
    "environment": "production",
    "release": "v1.2.3"
  }
}
```

#### JSON Payload Example

```json
{
  "severity": "ERROR",
  "jsonPayload": {
    "message": "Database connection failed",
    "error": {
      "type": "ConnectionError",
      "stack": "ConnectionError: ECONNREFUSED 10.0.1.50:5432\n  at Socket.emit (events.js:400:28)\n  at TCPConnectWrap.afterConnect [as oncomplete] (net.js:1144:16)"
    },
    "context": {
      "user_id": "user_12345",
      "request_id": "req_abc123"
    }
  },
  "resource": {
    "type": "cloud_function",
    "labels": {
      "function_name": "processPayment",
      "region": "us-central1"
    }
  },
  "insertId": "xyz789",
  "timestamp": "2025-01-15T10:31:00.456789Z"
}
```

### Field Mapping to NormalizedErrorEvent

The Error Triage service maps GCP log fields to the internal `NormalizedErrorEvent` structure:

| GCP Field | NormalizedErrorEvent Field | Notes |
|-----------|---------------------------|-------|
| `insertId` | `event_id` | Unique identifier for deduplication |
| `severity` | `error_class` (context) | Used to determine log level |
| `textPayload` or `jsonPayload.message` | `message` | Error message text |
| `jsonPayload.error.stack` or extracted from `textPayload` | `stack_trace` | Full stack trace |
| `resource.labels.service_name` or `resource.labels.function_name` | `service` | Service identifier |
| `labels.environment` | `environment` | Environment (production, staging, dev) |
| `timestamp` | `occurred_at` | When the error occurred |
| `trace` | Used for deep linking | GCP trace ID |
| `resource.type` | Metadata | Resource type (cloud_run_revision, cloud_function, etc.) |
| `labels.release` | `release` | Release version |

### Stack Trace Extraction

**From textPayload**:
```python
# Parse multi-line text payload to extract stack trace
if '\n' in log_entry.get('textPayload', ''):
    lines = log_entry['textPayload'].split('\n')
    message = lines[0]  # First line is error message
    stack_trace = '\n'.join(lines[1:])  # Remaining lines are stack
```

**From jsonPayload**:
```python
# Extract from structured JSON
message = log_entry['jsonPayload']['message']
stack_trace = log_entry['jsonPayload'].get('error', {}).get('stack', '')
```

### Error Class Extraction

Extract error class from message or payload:

```python
# From textPayload
error_class = log_entry['textPayload'].split(':')[0]  # "TypeError"

# From jsonPayload
error_class = log_entry['jsonPayload']['error']['type']  # "ConnectionError"
```

---

## Deep Link Construction

The Error Triage service constructs deep links to the GCP Log Explorer for direct navigation to specific log entries.

### URL Format

```
https://console.cloud.google.com/logs/query;query=<ENCODED_QUERY>?project=<PROJECT_ID>
```

### Query by Insert ID

Direct link to a specific log entry using `insertId`:

```
https://console.cloud.google.com/logs/query;query=insertId%3D"abc123def456"?project=your-project-id
```

### Implementation Example

```python
import urllib.parse

def build_gcp_log_link(project_id: str, insert_id: str) -> str:
    """
    Construct a deep link to GCP Log Explorer for a specific log entry.
    
    Args:
        project_id: GCP project ID
        insert_id: Unique log entry insert ID
        
    Returns:
        Full URL to Log Explorer with insertId filter
    """
    # Build query: insertId="<insert_id>"
    query = f'insertId="{insert_id}"'
    
    # URL encode the query
    encoded_query = urllib.parse.quote(query)
    
    # Construct full URL
    base_url = "https://console.cloud.google.com/logs/query"
    full_url = f"{base_url};query={encoded_query}?project={project_id}"
    
    return full_url
```

### Advanced Query Filters

**Filter by Trace ID**:
```
https://console.cloud.google.com/logs/query;query=trace%3D"projects/your-project/traces/1234567890abcdef"?project=your-project-id
```

**Filter by Resource Labels**:
```
https://console.cloud.google.com/logs/query;query=resource.labels.service_name%3D"api-service"%0Aseverity%3E%3DERROR?project=your-project-id
```

**Filter by Time Window**:
```
https://console.cloud.google.com/logs/query;query=timestamp%3E%3D"2025-01-15T10:30:00Z"%0Atimestamp%3C%3D"2025-01-15T10:31:00Z"?project=your-project-id
```

### Link Usage in Jira

Deep links are included in Jira issues:

**Issue Description**:
```markdown
## Error Details

- **Service**: api-service
- **Environment**: production
- **Error Class**: TypeError
- **Occurred At**: 2025-01-15 10:30:45 UTC

[View in GCP Log Explorer](https://console.cloud.google.com/logs/query;query=insertId%3D"abc123def456"?project=your-project-id)
```

**Issue Comments**:
```markdown
Error reoccurred 15× in last 5 minutes. Severity: SEV2

[View latest occurrence](https://console.cloud.google.com/logs/query;query=insertId%3D"xyz789def123"?project=your-project-id)
```

---

## Testing the Integration

Validate the complete GCP to Error Triage integration end-to-end.

### Step 1: Trigger Test Error

Deploy a GCP Cloud Function or Cloud Run service with an intentional exception:

**Cloud Function Example (Node.js)**:
```javascript
exports.testError = (req, res) => {
  console.error('Test error for Error Triage integration');
  throw new Error('Intentional test error from Cloud Function');
};
```

**Cloud Run Example (Python)**:
```python
from flask import Flask
import logging

app = Flask(__name__)

@app.route('/test-error')
def test_error():
    logging.error('Test error for Error Triage integration', 
                  extra={'environment': 'production'})
    raise TypeError('Intentional test error from Cloud Run')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
```

Deploy and invoke the function/service:

```bash
# Cloud Function
gcloud functions call testError

# Cloud Run
curl https://your-cloud-run-service.run.app/test-error
```

### Step 2: Verify Sink Routing

Check Cloud Logging to confirm the error log was generated and matches the sink filter:

1. Navigate to **Logging** → **Logs Explorer**
2. Apply filter: `severity >= ERROR AND resource.labels.service_name="your-service"`
3. Verify test error appears in results
4. Note the `insertId` for tracking

### Step 3: Confirm Pub/Sub Topic Receipt

Verify the log entry was routed to the Pub/Sub topic:

```bash
# Check topic metrics
gcloud pubsub topics describe error-events

# View subscription backlog (should be 0 if service is processing)
gcloud pubsub subscriptions describe error-events-push
```

Expected output:
```
ackDeadlineSeconds: 10
pushConfig:
  pushEndpoint: https://error-triage.jiratest.com/events
  ...
messageRetentionDuration: 604800s
```

### Step 4: Validate Service Receipt

Check the Error Triage service CloudWatch logs for GCP source events:

```bash
aws logs tail /aws/ecs/jiratest-error-triage-production --follow --filter-pattern "gcp"
```

Expected log entry:
```json
{
  "timestamp": "2025-01-15T10:30:46.123Z",
  "level": "INFO",
  "service": "error-triage",
  "environment": "production",
  "source": "gcp",
  "event_id": "abc123def456",
  "message": "Received GCP event",
  "service_name": "your-service"
}
```

### Step 5: Verify Jira Issue Creation

Search for the Jira issue created from the test error:

1. Navigate to your Jira project
2. Search: `labels = "source:gcp" AND labels = "env:production" ORDER BY created DESC`
3. Open the most recent issue
4. Verify:
   - Summary includes service name and error class
   - Description contains error message and stack trace
   - Labels include `source:gcp`, `env:production`, `service:your-service`, `errfp:<fingerprint>`
   - GCP Log Explorer link is present and functional

**Click the GCP Log Explorer link** and confirm it opens directly to the log entry.

### Step 6: Test OIDC Validation

Attempt to send a request without proper authentication:

```bash
curl -X POST https://error-triage.jiratest.com/events \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "data": "dGVzdA==",
      "messageId": "test123"
    }
  }'
```

Expected response:
```json
{
  "error": "Unauthorized",
  "message": "Invalid or missing OIDC token"
}
```

HTTP Status: `401 Unauthorized`

### Integration Test Checklist

- [ ] Test error triggered in GCP service
- [ ] Log entry appears in Cloud Logging with severity ERROR
- [ ] Log sink routes entry to Pub/Sub topic `error-events`
- [ ] Push subscription delivers message to Error Triage service
- [ ] Service logs show "Received GCP event" with correct `insertId`
- [ ] Jira issue created with `source:gcp` label
- [ ] Issue description contains GCP Log Explorer deep link
- [ ] Deep link opens correct log entry in GCP console
- [ ] Unauthenticated requests return 401 Unauthorized

---

## Troubleshooting Common Issues

### Issue: 401 Unauthorized Responses

**Symptoms**: All push requests fail with 401, CloudWatch logs show "Invalid or missing OIDC token"

**Possible Causes**:
1. Service account not configured in push subscription
2. OIDC audience mismatch
3. Service account lacks token creation permission

**Resolution**:

```bash
# Verify push subscription authentication config
gcloud pubsub subscriptions describe error-events-push

# Should include:
# pushConfig:
#   oidcToken:
#     audience: https://error-triage.jiratest.com/events
#     serviceAccountEmail: error-triage@your-project.iam.gserviceaccount.com

# Update if missing
gcloud pubsub subscriptions update error-events-push \
  --push-auth-service-account=error-triage@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --push-auth-token-audience=https://error-triage.jiratest.com/events

# Verify service account permissions
gcloud iam service-accounts get-iam-policy \
  error-triage@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

### Issue: No Events Received

**Symptoms**: No GCP events appear in service logs, Pub/Sub subscription shows message backlog

**Possible Causes**:
1. Log sink filter doesn't match error logs
2. Pub/Sub topic not receiving messages
3. Push subscription endpoint URL incorrect
4. Network connectivity issues

**Resolution**:

**Check Sink Filter**:
```bash
# View sink configuration
gcloud logging sinks describe error-triage-sink

# Test filter in Logs Explorer
# Navigate to Logging → Logs Explorer
# Apply sink filter to verify matching logs exist
```

**Verify Topic Messages**:
```bash
# Check topic message count
gcloud pubsub topics list-subscriptions error-events

# Pull a message manually (creates temporary subscription)
gcloud pubsub subscriptions pull error-events-push --limit=1
```

**Validate Endpoint URL**:
```bash
# Check push subscription config
gcloud pubsub subscriptions describe error-events-push | grep pushEndpoint

# Test endpoint connectivity from external network
curl -I https://error-triage.jiratest.com/healthz
```

**Check Network Security**:
- Verify AWS ALB security group allows traffic from GCP IP ranges (35.191.0.0/16, 35.187.0.0/16)
- Check AWS WAF rules don't block GCP requests
- Ensure SSL certificate is valid and not expired

### Issue: Base64 Decoding Errors

**Symptoms**: Service logs show "Invalid payload format" or "Base64 decode error", returns 400 Bad Request

**Possible Causes**:
1. Malformed message data
2. Incorrect field extraction (wrong path to `message.data`)
3. Non-standard encoding

**Resolution**:

**Inspect Raw Payload**:
```bash
# Pull message and view raw data
gcloud pubsub subscriptions pull error-events-push --limit=1 --format=json
```

**Validate Message Structure**:
```python
# Test decoding logic
import base64
import json

# Example message.data value
encoded = "eyJzZXZlcml0eSI6IkVSUk9SIn0="

try:
    decoded = base64.b64decode(encoded).decode('utf-8')
    log_entry = json.loads(decoded)
    print("Decoded successfully:", log_entry)
except Exception as e:
    print("Decode failed:", e)
```

**Check Message Attributes**:
- Verify `message.data` field exists
- Confirm encoding is base64 (not base64url)
- Check for BOM or other encoding issues

### Issue: Service Timeout Errors

**Symptoms**: Pub/Sub shows "Deadline exceeded" errors, messages redelivered repeatedly

**Possible Causes**:
1. Service processing takes longer than 10-second acknowledgment deadline
2. Dependency latency (Redis, MongoDB, Jira API)
3. Service under heavy load

**Resolution**:

**Check Service Health**:
```bash
# Test health check endpoint
curl https://error-triage.jiratest.com/healthz

# Expected response:
# {"status": "healthy", "checks": {"redis": "up", "jira": "up", ...}}
```

**Monitor Dependency Latency**:
```bash
# View CloudWatch metrics
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name JiraAPILatency \
  --dimensions Name=Environment,Value=production \
  --statistics Average \
  --start-time 2025-01-15T10:00:00Z \
  --end-time 2025-01-15T11:00:00Z \
  --period 300
```

**Increase Acknowledgment Deadline** (if dependencies consistently slow):
```bash
gcloud pubsub subscriptions update error-events-push --ack-deadline=20
```

**Scale Service** (if under load):
```bash
# Increase ECS task count
aws ecs update-service \
  --cluster jiratest-error-triage-production \
  --service jiratest-error-triage \
  --desired-count 4
```

### Issue: Rate Limiting (429 Responses)

**Symptoms**: Service returns 429 Too Many Requests, messages redelivered

**Possible Causes**:
1. Staging environment 50 req/min limit exceeded
2. Burst traffic from multiple error sources
3. Retry loop amplification

**Resolution**:

**Check Current Request Rate**:
```bash
# View request metrics
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name EventsReceived \
  --dimensions Name=Environment,Value=staging \
  --statistics Sum \
  --start-time 2025-01-15T10:00:00Z \
  --end-time 2025-01-15T10:05:00Z \
  --period 60
```

**Temporary Mitigation**:
- Increase rate limit in application configuration (staging only)
- Reduce log sink scope to filter out noisy sources

**Long-Term Solutions**:
- Implement sampling for high-frequency errors (deduplicate more aggressively)
- Upgrade to production tier with higher limits
- Add rate limiting at ALB level with more sophisticated rules

### Issue: Dead Letter Queue Accumulation

**Symptoms**: `error-events-dlq` topic has growing message count

**Possible Causes**:
1. Persistent service errors preventing successful processing
2. Invalid message format
3. Service dependencies unavailable

**Resolution**:

**Review DLQ Messages**:
```bash
# Pull messages from DLQ
gcloud pubsub subscriptions pull error-events-dlq --limit=10 --format=json > dlq-messages.json

# Analyze message patterns
cat dlq-messages.json | jq '.[] | .message.data' | base64 -d
```

**Check Service Error Logs**:
```bash
# Filter for processing errors
aws logs tail /aws/ecs/jiratest-error-triage-production \
  --filter-pattern "ERROR" \
  --since 1h
```

**Common DLQ Scenarios**:

1. **Invalid Event Format**: Review and fix payload adapter logic
2. **Jira API Errors**: Check Jira credentials and project configuration
3. **Redis Connection Failures**: Verify ElastiCache cluster health

**Reprocess DLQ Messages** (after fixing root cause):
```bash
# Create temporary pull subscription on DLQ
gcloud pubsub subscriptions create dlq-reprocess --topic=error-events-dlq

# Pull and republish to main topic
# (manual script or Cloud Function required)
```

### Issue: Missing Stack Traces in Jira

**Symptoms**: Jira issues created but stack trace field is empty

**Possible Causes**:
1. GCP log entry uses `textPayload` without newlines
2. Stack trace extraction regex mismatch
3. JSON payload structure different than expected

**Resolution**:

**Inspect Log Entry**:
```bash
# Pull recent log entries
gcloud logging read "severity >= ERROR" --limit=5 --format=json > sample-logs.json

# Check structure
cat sample-logs.json | jq '.[0] | {textPayload, jsonPayload}'
```

**Verify Extraction Logic**:
- Review `src/services/payload_adapters.py` GCP adapter
- Test with actual log entry samples
- Update extraction regex if log format changed

**Temporary Workaround**:
- Include full `textPayload` or `jsonPayload` in Jira description if stack extraction fails

---

## Advanced Configuration

### Multi-Project Setup

Route logs from multiple GCP projects to a single Error Triage instance:

**Option 1: Shared Pub/Sub Topic** (Recommended)

1. Create Pub/Sub topic in central project:
   ```bash
   gcloud pubsub topics create error-events --project=central-project-id
   ```

2. Grant publisher permission to sink service accounts from other projects:
   ```bash
   gcloud pubsub topics add-iam-policy-binding error-events \
     --member="serviceAccount:cloud-logs@system.gserviceaccount.com" \
     --role="roles/pubsub.publisher" \
     --project=central-project-id
   ```

3. Create sinks in each project pointing to central topic:
   ```bash
   gcloud logging sinks create error-triage-sink \
     pubsub.googleapis.com/projects/central-project-id/topics/error-events \
     --project=source-project-1
   
   gcloud logging sinks create error-triage-sink \
     pubsub.googleapis.com/projects/central-project-id/topics/error-events \
     --project=source-project-2
   ```

**Option 2: Multiple Push Subscriptions**

- Create separate push subscriptions per project
- All subscriptions push to same Error Triage endpoint
- Service differentiates by project label in log entries

### Environment-Specific Sinks

Separate production and staging errors into different Jira projects:

**Production Sink**:
```bash
gcloud logging sinks create error-triage-prod \
  pubsub.googleapis.com/projects/YOUR_PROJECT_ID/topics/error-events-prod \
  --log-filter='severity >= ERROR AND labels.environment = "production"'
```

**Staging Sink**:
```bash
gcloud logging sinks create error-triage-staging \
  pubsub.googleapis.com/projects/YOUR_PROJECT_ID/topics/error-events-staging \
  --log-filter='severity >= ERROR AND labels.environment = "staging"'
```

**Separate Push Subscriptions**:
- `error-events-prod-push` → Jira Project "PROD"
- `error-events-staging-push` → Jira Project "STG"

Configure different Error Triage service endpoints or use path-based routing:
- `https://error-triage.jiratest.com/events/prod`
- `https://error-triage.jiratest.com/events/staging`

### Custom Metadata Enrichment

Enhance log entries with custom labels for better ownership routing:

**Add Labels to Cloud Run Services**:
```yaml
# service.yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: api-service
  labels:
    team: backend
    component: api
spec:
  template:
    metadata:
      labels:
        environment: production
```

**Add Labels to Cloud Functions**:
```bash
gcloud functions deploy processPayment \
  --runtime=python39 \
  --trigger-http \
  --update-labels=team=payments,environment=production
```

**Use Labels in Sink Filter**:
```
severity >= ERROR AND labels.team = "backend"
```

**Access in Ownership Rules** (`config/ownership_rules.yaml`):
```yaml
rules:
  - service: "api-service"
    labels:
      team: "backend"
    assignee: "5f8e9a1b2c3d4e5f6a7b8c9d"  # Backend team lead
```

### Monitoring Sink Health

Set up alerts for sink and subscription issues:

**CloudWatch Alarms**:
```bash
# Alert on high DLQ message count
aws cloudwatch put-metric-alarm \
  --alarm-name error-triage-dlq-high \
  --metric-name ApproximateNumberOfMessagesVisible \
  --namespace AWS/SQS \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 100 \
  --comparison-operator GreaterThanThreshold
```

**GCP Monitoring**:
```bash
# Alert on push subscription errors
gcloud alpha monitoring policies create \
  --notification-channels=CHANNEL_ID \
  --display-name="Error Triage Push Failures" \
  --condition-display-name="High push error rate" \
  --condition-threshold-value=0.05 \
  --condition-threshold-duration=300s \
  --condition-filter='resource.type="pubsub_subscription" AND resource.labels.subscription_id="error-events-push" AND metric.type="pubsub.googleapis.com/subscription/push_request_count" AND metric.labels.result="failure"'
```

### Performance Optimization

**Batch Message Processing** (if needed):

If message volume is very high, consider switching from push to pull subscription with batch processing:

```bash
# Create pull subscription instead of push
gcloud pubsub subscriptions create error-events-pull \
  --topic=error-events \
  --ack-deadline=60
```

**Worker Process** (Python example):
```python
from google.cloud import pubsub_v1

subscriber = pubsub_v1.SubscriberClient()
subscription_path = subscriber.subscription_path(PROJECT_ID, 'error-events-pull')

def callback(message):
    # Process message
    process_error_event(message.data)
    message.ack()

# Pull messages in batches
future = subscriber.subscribe(subscription_path, callback=callback)
future.result()
```

**Pros**: Higher throughput, better backpressure handling  
**Cons**: More complex deployment, requires persistent worker process

---

## Summary

You have successfully configured GCP Cloud Logging to send error events to the Error Triage service. The integration provides:

✓ **Automated Error Detection**: GCP services automatically send ERROR+ level logs  
✓ **Secure Authentication**: OIDC token validation ensures only authorized requests  
✓ **Reliable Delivery**: Exponential backoff retry and dead letter queue for failed messages  
✓ **Direct Log Access**: Deep links to GCP Log Explorer for investigation  
✓ **Environment Isolation**: Separate sinks and subscriptions for prod/staging  

For additional assistance, refer to:
- [API Documentation](api.md) - Request/response schemas
- [Configuration Guide](configuration.md) - Rule customization
- [Monitoring Guide](monitoring.md) - Metrics and alerts
- [Runbook](runbook.md) - Operational procedures

For GCP-specific issues, consult the [GCP Cloud Logging documentation](https://cloud.google.com/logging/docs) and [Pub/Sub documentation](https://cloud.google.com/pubsub/docs).
