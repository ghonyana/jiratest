# Vercel Log Drain Integration Setup Guide

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Vercel Log Drain Creation](#vercel-log-drain-creation)
4. [Webhook Secret Configuration](#webhook-secret-configuration)
5. [Signature Verification](#signature-verification)
6. [Payload Format Reference](#payload-format-reference)
7. [Deep Link Construction](#deep-link-construction)
8. [Testing the Integration](#testing-the-integration)
9. [Troubleshooting](#troubleshooting)
10. [Advanced Configuration](#advanced-configuration)

---

## Overview

This guide provides step-by-step instructions for configuring Vercel Log Drains to integrate with the Error Triage → Jira Upserter service. Vercel Log Drains enable automatic forwarding of application logs from your Vercel deployments to the Error Triage service, which processes error events and creates or updates Jira issues accordingly.

**Integration Flow:**
```
Vercel Deployment → Log Drain Webhook → Error Triage Service → Jira Issue
```

**Key Features:**
- Real-time error forwarding from Vercel deployments
- HMAC-SHA256 signature verification for webhook security
- Support for production, preview, and development environments
- Automatic error grouping and Jira issue management
- Deep links back to Vercel logs for debugging

---

## Prerequisites

Before configuring the Vercel integration, ensure the following requirements are met:

### Vercel Account Requirements

- **Vercel Pro or Enterprise Account**: Log Drains feature is available only on Pro and Enterprise plans
- **Project Access**: Admin or Owner role on the Vercel project you want to monitor
- **Organization Settings**: Access to configure integrations at the project or organization level

### Error Triage Service Requirements

- **Service Deployed**: Error Triage service running and accessible at `https://error-triage.jiratest.com/events`
- **HTTPS/TLS**: Endpoint must be accessible via HTTPS (Vercel requires secure endpoints)
- **Service Health**: Verify service is healthy via `/healthz` endpoint

### AWS Configuration

- **Secrets Manager Access**: Ability to create and manage secrets in AWS Secrets Manager
- **IAM Permissions**: ECS task role must have `secretsmanager:GetSecretValue` permission
- **Secret ARN**: Ready to reference secret in ECS task definition environment variables

### Network Security

- **IP Whitelisting**: Vercel webhook IP ranges must be allowed in your ALB security group
  - **IP Range**: `76.76.21.0/24`
  - **Port**: HTTPS (443)
  - **Direction**: Inbound from Vercel to ALB

**Verification Commands:**

```bash
# Test service accessibility
curl -f https://error-triage.jiratest.com/healthz

# Verify security group allows Vercel IPs
aws ec2 describe-security-groups \
  --group-ids sg-xxxxx \
  --query 'SecurityGroups[0].IpPermissions[?ToPort==`443`]'
```

---

## Vercel Log Drain Creation

Follow these steps to create a Log Drain in the Vercel dashboard:

### Step 1: Navigate to Log Drains

1. Log in to the [Vercel Dashboard](https://vercel.com/dashboard)
2. Select your **Project**
3. Click **Settings** in the top navigation
4. In the left sidebar, navigate to **Integrations** → **Log Drains**

### Step 2: Create New Log Drain

1. Click the **"Add Log Drain"** button
2. You'll be presented with the Log Drain configuration form

### Step 3: Configure Log Drain Settings

Fill in the following configuration parameters:

| Field | Value | Description |
|-------|-------|-------------|
| **Name** | `Error Triage` | Friendly name for the log drain |
| **Delivery URL** | `https://error-triage.jiratest.com/events` | Error Triage webhook endpoint |
| **Environments** | ☑ Production<br>☐ Preview (optional)<br>☐ Development (optional) | Select environments to monitor |
| **Log Levels** | ☑ error<br>☑ warning<br>☐ info (optional) | Select log severity levels to forward |

**Environment Selection Guidelines:**
- **Production**: Always enable for production error monitoring
- **Preview**: Enable if you want to track errors in preview deployments (staging/QA)
- **Development**: Generally not recommended; generates high volume of logs

**Log Level Selection Guidelines:**
- **error**: Critical errors that should always create Jira issues (required)
- **warning**: Important warnings that may indicate problems (recommended)
- **info**: Informational logs (optional; high volume, use for verbose debugging only)

### Step 4: Generate Webhook Secret

1. In the Log Drain configuration form, locate the **"Webhook Secret"** section
2. Click **"Generate Secret"**
3. Vercel will generate a cryptographically secure secret in the format: `wh_secret_...`
4. **CRITICAL**: Copy this secret value immediately and store it securely
5. You will need this secret for the next section (AWS Secrets Manager configuration)

**Security Note:** This secret is shown only once. If you lose it, you must regenerate the secret and update all configurations.

### Step 5: Save and Enable

1. Review all configuration settings
2. Click **"Save"** or **"Create Log Drain"**
3. Vercel will display the Log Drain in your list with status: **Enabled**
4. Note the **Log Drain ID** for future reference

**Visual Confirmation:**
```
✓ Error Triage
  https://error-triage.jiratest.com/events
  Environments: Production, Preview
  Status: Enabled
  Last delivery: --
```

---

## Webhook Secret Configuration

The webhook secret must be stored securely in AWS Secrets Manager and referenced by the ECS task definition.

### Step 1: Store Secret in AWS Secrets Manager

Use the AWS CLI to create or update the webhook secret:

```bash
# Set your environment (dev, staging, prod)
export ENV=production

# Create the secret (first time only)
aws secretsmanager create-secret \
  --name "jira/jiratest/${ENV}/webhook-secret" \
  --description "Webhook secrets for Error Triage service (${ENV})" \
  --secret-string '{
    "vercel": "wh_secret_YOUR_ACTUAL_SECRET_HERE",
    "gcp_audience": "https://error-triage.jiratest.com/events"
  }' \
  --region us-east-1

# OR update existing secret
aws secretsmanager update-secret \
  --secret-id "jira/jiratest/${ENV}/webhook-secret" \
  --secret-string '{
    "vercel": "wh_secret_YOUR_ACTUAL_SECRET_HERE",
    "gcp_audience": "https://error-triage.jiratest.com/events"
  }' \
  --region us-east-1
```

**Secret Format:**
```json
{
  "vercel": "wh_secret_abc123def456...",
  "gcp_audience": "https://error-triage.jiratest.com/events"
}
```

**Field Descriptions:**
- `vercel`: The webhook secret generated by Vercel in the previous step
- `gcp_audience`: OIDC audience for GCP Pub/Sub authentication (used for GCP integration)

### Step 2: Update ECS Task Definition

The ECS task definition must reference this secret to load it at runtime.

**Option A: Via AWS Console**

1. Navigate to **ECS** → **Task Definitions**
2. Select `jiratest-error-triage` task definition
3. Click **Create new revision**
4. In the **Environment** section, locate **Secrets** (not Environment Variables)
5. Add secret with:
   - **Key**: `WEBHOOK_SECRET`
   - **ValueFrom**: `arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:jira/jiratest/prod/webhook-secret`
6. Save and create new revision

**Option B: Via Terraform (Recommended)**

Update your Terraform ECS task definition:

```hcl
resource "aws_ecs_task_definition" "error_triage" {
  # ... other configuration ...

  container_definitions = jsonencode([{
    name  = "error-triage"
    # ... other container config ...
    
    secrets = [
      {
        name      = "WEBHOOK_SECRET"
        valueFrom = aws_secretsmanager_secret.webhook_secret.arn
      }
    ]
  }])
}
```

### Step 3: Restart ECS Service

After updating the task definition, restart the service to load the new secret:

```bash
# Force new deployment with updated task definition
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --force-new-deployment \
  --region us-east-1

# Wait for service to stabilize
aws ecs wait services-stable \
  --cluster jiratest-production \
  --services error-triage \
  --region us-east-1
```

### Step 4: Verify Secret Loading

Check that the service successfully loaded the secret:

```bash
# View CloudWatch logs for startup messages
aws logs tail /aws/ecs/jiratest-error-triage-production \
  --follow \
  --filter-pattern "secret" \
  --region us-east-1
```

Look for log entries indicating successful secret retrieval:
```json
{
  "timestamp": "2025-01-15T10:30:45.123Z",
  "level": "INFO",
  "message": "Loaded webhook secrets from AWS Secrets Manager",
  "secret_keys": ["vercel", "gcp_audience"]
}
```

---

## Signature Verification

Vercel signs all webhook requests using HMAC-SHA256 to ensure authenticity and prevent unauthorized access.

### How Signature Verification Works

**Vercel's Signing Process:**
1. Vercel computes HMAC-SHA256 digest of the request body using the webhook secret
2. Vercel includes the signature in the `x-vercel-signature` HTTP header
3. Vercel sends the POST request to your endpoint

**Error Triage Service Validation:**
1. Service receives the request and extracts the `x-vercel-signature` header
2. Service computes the expected signature using the same secret and request body
3. Service compares signatures using timing-safe comparison
4. Request is accepted if signatures match, rejected with 401 if they don't

### Signature Computation Algorithm

```python
import hmac
import hashlib

def verify_vercel_signature(request_body: bytes, signature_header: str, secret: str) -> bool:
    """
    Verify Vercel webhook signature.
    
    Args:
        request_body: Raw request body bytes (not decoded)
        signature_header: Value of x-vercel-signature header
        secret: Webhook secret from AWS Secrets Manager
    
    Returns:
        True if signature is valid, False otherwise
    """
    # Compute expected signature
    expected_signature = hmac.new(
        key=secret.encode('utf-8'),
        msg=request_body,
        digestmod=hashlib.sha256
    ).hexdigest()
    
    # Timing-safe comparison to prevent timing attacks
    return hmac.compare_digest(expected_signature, signature_header)
```

### Security Properties

**Timing-Attack Resistance:**
- Uses `hmac.compare_digest()` instead of `==` operator
- Prevents attackers from guessing the signature byte-by-byte through timing analysis

**Replay Attack Prevention:**
- Event deduplication using `traceId` field stored in Redis with TTL
- Duplicate events (same `traceId`) are dropped within 1-hour window

**Request Integrity:**
- HMAC ensures the request body has not been tampered with
- Any modification to the payload invalidates the signature

### Error Responses

| Scenario | HTTP Status | Response Body | Action |
|----------|-------------|---------------|---------|
| Missing `x-vercel-signature` header | 401 Unauthorized | `{"error": "Missing signature header"}` | Check Vercel Log Drain configuration |
| Invalid signature | 401 Unauthorized | `{"error": "Invalid signature"}` | Verify webhook secret matches in AWS Secrets Manager |
| Signature valid | 202 Accepted | `{"status": "accepted", "event_id": "..."}` | Event processed successfully |

---

## Payload Format Reference

Vercel sends structured JSON payloads to the webhook endpoint. Understanding the payload structure is essential for debugging and customization.

### Complete Payload Example

```json
{
  "source": "vercel",
  "deployment": {
    "id": "dpl_BcB8ZQRmCyQZ8xQ9Z2Z3Z4Z5",
    "url": "my-app-abc123.vercel.app",
    "name": "my-app",
    "region": "sfo1"
  },
  "message": "TypeError: Cannot read property 'user' of undefined",
  "level": "error",
  "timestamp": "2025-01-15T10:30:45.123Z",
  "environment": "production",
  "path": "/api/checkout",
  "traceId": "abc123def456",
  "requestId": "req_xyz789",
  "projectId": "prj_abc123",
  "teamId": "team_xyz789"
}
```

### Field Mapping to NormalizedErrorEvent

The Error Triage service transforms Vercel payloads into a normalized internal format:

| Vercel Field | NormalizedErrorEvent Field | Description | Required |
|--------------|----------------------------|-------------|----------|
| `source` | `source` | Always "vercel" | Yes |
| `deployment.name` | `service` | Service name (e.g., "my-app") | Yes |
| `environment` | `environment` | Environment: production, preview, development | Yes |
| `level` | `error_class` | Error severity: error, warning | Yes |
| `message` | `message` | Full error message text | Yes |
| *(extracted)* | `stack_trace` | Stack trace if present in message | No |
| `path` | `path` | Request path (e.g., "/api/checkout") | No |
| `deployment.url` | `url` | Deployment URL | Yes |
| *(computed)* | `release` | Deployment ID used as release identifier | No |
| *(constructed)* | `log_url` | Deep link to Vercel logs | Yes |
| `traceId` | `event_id` | Unique event identifier for deduplication | Yes |
| `timestamp` | `occurred_at` | ISO 8601 timestamp | Yes |

### Field Descriptions

#### Top-Level Fields

- **`source`** (string, always `"vercel"`): Identifies the webhook source for adapter selection
- **`message`** (string): Complete error message, including stack trace if available
- **`level`** (string): Log severity level: `"error"`, `"warning"`, or `"info"`
- **`timestamp`** (string, ISO 8601): When the error occurred in UTC
- **`environment`** (string): Deployment environment: `"production"`, `"preview"`, or `"development"`
- **`path`** (string, optional): HTTP request path where error occurred (e.g., `"/api/users/123"`)
- **`traceId`** (string): Unique identifier for this log entry, used for deduplication
- **`requestId`** (string, optional): Vercel request ID for cross-referencing
- **`projectId`** (string): Vercel project identifier
- **`teamId`** (string): Vercel team/organization identifier

#### Deployment Object

- **`deployment.id`** (string): Unique deployment identifier (e.g., `"dpl_..."`)
- **`deployment.url`** (string): Deployment URL (e.g., `"my-app-abc123.vercel.app"`)
- **`deployment.name`** (string): Project/service name
- **`deployment.region`** (string): Vercel region code (e.g., `"sfo1"`, `"iad1"`)

### Stack Trace Extraction

If the `message` field contains a stack trace, the service extracts it automatically:

**Example Message with Stack Trace:**
```
TypeError: Cannot read property 'user' of undefined
    at handleCheckout (/var/task/pages/api/checkout.js:45:12)
    at Layer.handle [as handle_request] (/var/task/node_modules/express/lib/router/layer.js:95:5)
    at next (/var/task/node_modules/express/lib/router/route.js:137:13)
```

**Extracted Fields:**
- `message`: "TypeError: Cannot read property 'user' of undefined"
- `stack_trace`: Full stack trace text
- `error_class`: "TypeError"
- Top stack frame: `handleCheckout@/var/task/pages/api/checkout.js:45:12` (used in fingerprint)

### Missing Field Handling

The service provides sensible defaults for optional fields:

| Missing Field | Default Value | Impact |
|---------------|---------------|--------|
| `path` | `null` | Path-based ownership routing skipped |
| `traceId` | Generated UUID | Deduplication still works with generated ID |
| `deployment.region` | `"unknown"` | Region not included in Jira description |
| `requestId` | `null` | Request correlation not available |

---

## Deep Link Construction

The Error Triage service automatically generates deep links to Vercel logs for every error event, enabling developers to quickly navigate to the full log context.

### Vercel Log URL Format

Deep links are constructed using the following pattern:

```
https://vercel.com/{team}/{project}/logs?q=traceId:{traceId}
```

**URL Components:**
- **`{team}`**: Your Vercel organization/team slug (e.g., "acme-corp")
- **`{project}`**: Your Vercel project name (e.g., "my-app")
- **`traceId`**: The unique trace ID from the webhook payload

### Example Deep Links

**Production Error:**
```
https://vercel.com/acme-corp/my-app/logs?q=traceId:abc123def456
```

**Preview Deployment Error:**
```
https://vercel.com/acme-corp/my-app/logs?q=traceId:preview-xyz789
```

### Deep Link Placement in Jira

Deep links are included in two locations for maximum visibility:

#### 1. Jira Issue Description

When a new Jira issue is created, the description includes a formatted section:

```markdown
## Error Details

**Service:** my-app  
**Environment:** production  
**Error Class:** TypeError  
**Path:** /api/checkout  
**Occurred At:** 2025-01-15 10:30:45 UTC

**View Logs:** [Vercel Logs](https://vercel.com/acme-corp/my-app/logs?q=traceId:abc123def456)

## Stack Trace

```
TypeError: Cannot read property 'user' of undefined
    at handleCheckout (/var/task/pages/api/checkout.js:45:12)
    ...
```
```

#### 2. Jira Issue Comments

When an error recurs and a comment is added:

```
Error reoccurred 15× in last 5 minutes.  
**Severity:** SEV2  
**View Latest Occurrence:** [Vercel Logs](https://vercel.com/acme-corp/my-app/logs?q=traceId:abc123def456)

_Timestamp: 2025-01-15 10:35:45 UTC_
```

### Advanced Query Parameters

You can enhance deep links with additional Vercel log filters:

**Time Range Filter:**
```
https://vercel.com/{team}/{project}/logs?q=traceId:{traceId}&since=10m
```

**Deployment Filter:**
```
https://vercel.com/{team}/{project}/logs?q=traceId:{traceId}&deploymentId=dpl_abc123
```

**Level Filter:**
```
https://vercel.com/{team}/{project}/logs?q=traceId:{traceId}&level=error
```

---

## Testing the Integration

Thorough testing ensures the Vercel integration is working correctly before relying on it for production error monitoring.

### Phase 1: Test Event Generation

#### Option A: Trigger Real Error in Vercel Deployment

1. Deploy a test application with an intentional error:

```javascript
// pages/api/test-error.js
export default function handler(req, res) {
  // Intentional error for testing
  const user = null;
  console.log(user.name); // Will throw TypeError
  
  res.status(200).json({ message: 'This will not be reached' });
}
```

2. Deploy to Vercel:
```bash
vercel --prod
```

3. Trigger the error endpoint:
```bash
curl https://my-app.vercel.app/api/test-error
```

#### Option B: Use Vercel Log Drain Test Delivery

1. In Vercel Dashboard, navigate to your Log Drain configuration
2. Click the **"Test Delivery"** button
3. Vercel sends a test payload to your endpoint
4. Verify response is `202 Accepted`

### Phase 2: Verify Webhook Delivery

#### Check Vercel Log Drains Dashboard

1. Navigate to **Settings** → **Integrations** → **Log Drains**
2. Locate your "Error Triage" log drain
3. Check **Last Delivery** timestamp (should update within seconds)
4. Click **"View Delivery Logs"** to see delivery status:
   - ✓ `202 Accepted` - Success
   - ✗ `401 Unauthorized` - Signature mismatch
   - ✗ `500 Internal Server Error` - Service error

#### Check Delivery Metrics

```
Last 24 hours:
  Delivered: 127 events
  Failed: 0 events
  Success Rate: 100%
```

### Phase 3: Verify Service Receipt

#### Check CloudWatch Logs

```bash
# View recent events received
aws logs tail /aws/ecs/jiratest-error-triage-production \
  --follow \
  --filter-pattern "{ $.action = \"webhook_received\" && $.source = \"vercel\" }" \
  --format json \
  --region us-east-1
```

**Expected Log Output:**
```json
{
  "timestamp": "2025-01-15T10:30:45.500Z",
  "level": "INFO",
  "service": "error-triage",
  "environment": "production",
  "source": "vercel",
  "event_id": "abc123def456",
  "action": "webhook_received",
  "duration_ms": 25,
  "message": "Received webhook event from vercel"
}
```

#### Check Prometheus Metrics

```bash
# Query events_received_total metric
curl https://error-triage.jiratest.com/metrics | grep 'events_received_total{.*source="vercel"}'
```

**Expected Output:**
```
events_received_total{environment="production",source="vercel"} 127
```

### Phase 4: Verify Jira Issue Creation

#### Search for Test Issue

1. Open Jira and navigate to your Error Triage project (e.g., "ET")
2. Use JQL query:
```
project = ET AND labels = "source:vercel" AND created >= -1h
```

3. Verify the issue was created with:
   - **Summary**: `[production] my-app: TypeError — Cannot read property 'user' of undefined`
   - **Labels**: `source:vercel`, `env:production`, `service:my-app`, `errfp:<hash>`
   - **Description**: Contains error details and Vercel log link
   - **Priority**: Set according to severity rules
   - **Custom Severity Field**: Populated (e.g., "SEV2")

#### Verify Deep Link

1. In the Jira issue description, click the **"View Logs"** link
2. Verify it opens Vercel logs filtered to the specific trace ID
3. Confirm the log entry matches the error in Jira

### Phase 5: Test Signature Validation

Verify that invalid signatures are rejected:

```bash
# Send request with invalid signature
curl -X POST https://error-triage.jiratest.com/events \
  -H "Content-Type: application/json" \
  -H "x-vercel-signature: invalid_signature_12345" \
  -d '{
    "source": "vercel",
    "message": "Test error",
    "level": "error",
    "timestamp": "2025-01-15T10:30:45.123Z"
  }'
```

**Expected Response:**
```json
{
  "error": "Invalid signature"
}
```
**Expected Status Code:** `401 Unauthorized`

### Phase 6: Test Deduplication

Send the same event twice and verify only one Jira issue is created:

```bash
# Send first event
curl -X POST https://error-triage.jiratest.com/events \
  -H "Content-Type: application/json" \
  -H "x-vercel-signature: <valid_signature>" \
  -d @test-event.json

# Wait 5 seconds

# Send identical event (same traceId)
curl -X POST https://error-triage.jiratest.com/events \
  -H "Content-Type: application/json" \
  -H "x-vercel-signature: <valid_signature>" \
  -d @test-event.json
```

**Expected Behavior:**
- First request: Jira issue created
- Second request: Event dropped (duplicate `traceId`), no new issue
- CloudWatch logs show: `"action": "event_deduplicated"`

### Test Checklist

- [ ] Test event triggered in Vercel deployment
- [ ] Vercel Log Drains dashboard shows successful delivery (202 Accepted)
- [ ] CloudWatch logs show `webhook_received` action for Vercel source
- [ ] `events_received_total{source="vercel"}` metric incremented
- [ ] Jira issue created with `source:vercel` label
- [ ] Jira issue description contains Vercel log deep link
- [ ] Deep link navigates to correct log entry in Vercel
- [ ] Invalid signature rejected with 401 Unauthorized
- [ ] Duplicate events (same traceId) are deduplicated

---

## Troubleshooting

### Issue: 401 Unauthorized Responses

**Symptoms:**
- Vercel Log Drains dashboard shows failed deliveries with 401 status code
- CloudWatch logs show: `"error": "Invalid webhook signature"`
- No Jira issues are created for Vercel errors

**Root Causes:**
1. Webhook secret mismatch between Vercel and AWS Secrets Manager
2. Secret not loaded correctly by ECS task
3. Secret rotation occurred but ECS service not restarted

**Diagnostic Steps:**

1. Verify secret in AWS Secrets Manager:
```bash
aws secretsmanager get-secret-value \
  --secret-id jira/jiratest/production/webhook-secret \
  --query 'SecretString' \
  --output text | jq .vercel
```

2. Compare with Vercel Log Drain configuration:
   - In Vercel dashboard, view Log Drain settings
   - Note: Secret is not visible in UI after creation

3. Check ECS task logs for secret loading errors:
```bash
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern "secret" \
  --limit 20
```

**Resolution Steps:**

1. **If secrets don't match:**
   - Regenerate webhook secret in Vercel Log Drain configuration
   - Update AWS Secrets Manager with new secret:
   ```bash
   aws secretsmanager update-secret \
     --secret-id jira/jiratest/production/webhook-secret \
     --secret-string '{"vercel": "wh_secret_NEW_VALUE", "gcp_audience": "..."}'
   ```
   - Restart ECS service to load new secret

2. **If secret not loaded:**
   - Verify ECS task role has `secretsmanager:GetSecretValue` permission
   - Check task definition references correct secret ARN
   - Review CloudWatch logs for permission errors

3. **After secret rotation:**
   - Always restart ECS service: `aws ecs update-service --force-new-deployment`
   - Wait for service to stabilize: `aws ecs wait services-stable`
   - Test with Vercel "Test Delivery" button

### Issue: Webhook Timeouts

**Symptoms:**
- Vercel Log Drains dashboard shows timeout errors
- Events not received despite signature verification passing
- Intermittent 503 Service Unavailable responses

**Root Causes:**
1. ALB security group doesn't allow Vercel IP ranges
2. Service /healthz endpoint failing (tasks not healthy)
3. Backend dependencies (Redis, Jira) slow or unavailable
4. ECS tasks at capacity (CPU/memory exhausted)

**Diagnostic Steps:**

1. Check ALB security group rules:
```bash
aws ec2 describe-security-groups \
  --group-ids sg-xxxxx \
  --query 'SecurityGroups[0].IpPermissions[?ToPort==`443`].IpRanges'
```

2. Verify ECS service health:
```bash
aws ecs describe-services \
  --cluster jiratest-production \
  --services error-triage \
  --query 'services[0].{RunningCount:runningCount,DesiredCount:desiredCount,HealthyCount:events[0].message}'
```

3. Test service directly:
```bash
curl -f https://error-triage.jiratest.com/healthz
```

4. Check ALB target health:
```bash
aws elbv2 describe-target-health \
  --target-group-arn <target-group-arn>
```

**Resolution Steps:**

1. **Update security group for Vercel IPs:**
```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxx \
  --protocol tcp \
  --port 443 \
  --cidr 76.76.21.0/24 \
  --description "Vercel webhook IP range"
```

2. **Investigate health check failures:**
   - Review `/healthz` response for dependency failures
   - Check Redis connectivity: `redis-cli -h <redis-host> PING`
   - Check Jira connectivity: `curl https://{org}.atlassian.net/rest/api/3/serverInfo`

3. **Scale up ECS service if needed:**
```bash
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --desired-count 6
```

### Issue: Missing Events

**Symptoms:**
- Vercel Log Drains shows successful deliveries (202 Accepted)
- CloudWatch logs show `webhook_received` events
- But no Jira issues are created

**Root Causes:**
1. Log Drain filtering excludes relevant log levels
2. Events being deduplicated (duplicate traceIds)
3. Jira integration failing silently
4. Error frequency below severity threshold (won't create issue)

**Diagnostic Steps:**

1. Check Vercel Log Drain configuration:
   - Verify "error" log level is selected
   - Confirm correct environments are enabled (production, preview, etc.)

2. Check CloudWatch logs for Jira actions:
```bash
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern "{ $.action = \"jira_issue_created\" || $.action = \"jira_comment_added\" }" \
  --start-time $(date -d '1 hour ago' +%s)000
```

3. Check for deduplication:
```bash
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern "{ $.action = \"event_deduplicated\" && $.source = \"vercel\" }"
```

4. Check Jira API errors:
```bash
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern "{ $.error_type = \"jira_error\" }"
```

**Resolution Steps:**

1. **Adjust Log Drain filtering:**
   - In Vercel dashboard, edit Log Drain configuration
   - Ensure "error" and "warning" levels are selected
   - Remove overly restrictive environment filters

2. **Review severity rules:**
   - Check `config/severity_rules.yaml` for environment-specific thresholds
   - Single errors may not meet threshold (e.g., prod requires ≥10 in 5min for SEV2)
   - Consider adjusting thresholds or checking staging environment rules

3. **Investigate Jira integration:**
   - Test Jira API connectivity manually
   - Verify Jira project key exists and is accessible
   - Check Jira API token hasn't expired

### Issue: Rate Limiting (429 Too Many Requests)

**Symptoms:**
- Vercel Log Drains shows 429 status codes
- CloudWatch logs show: `"error": "Rate limit exceeded"`
- Events are dropped during high-traffic periods

**Root Causes:**
1. Vercel deployment generating excessive log volume
2. Service rate limit set too low for traffic patterns
3. Burst of errors during incident overwhelming service

**Diagnostic Steps:**

1. Check current event rate:
```bash
# Query Prometheus metrics
curl https://error-triage.jiratest.com/metrics | grep 'events_received_total{source="vercel"}'

# Calculate rate per minute (compare two samples 60s apart)
```

2. Review rate limit configuration:
   - Production: 100 req/min
   - Staging: 50 req/min
   - Development: 50 req/min

3. Check traffic patterns in CloudWatch:
   - Look for unusual spikes in `events_received_total` counter
   - Identify services generating high error volume

**Resolution Steps:**

1. **Short-term mitigation:**
   - Increase rate limit via environment variable: `RATE_LIMIT_PER_MINUTE=150`
   - Restart ECS service to apply new limit

2. **Long-term solutions:**
   - Review error sources: Some errors may be noisy and can be filtered
   - Adjust Vercel Log Drain to exclude info-level logs
   - Consider sampling high-frequency errors (future enhancement)
   - Scale up ECS service to handle higher throughput

3. **Incident response:**
   - During active incidents, temporarily increase rate limit
   - Focus on SEV1/SEV2 errors by adjusting threshold rules
   - After incident, review and tune configuration

### Issue: Payload Parsing Errors (400 Bad Request)

**Symptoms:**
- Vercel Log Drains shows 400 status codes
- CloudWatch logs show: `"error": "Malformed payload"` or `"error": "JSON decode error"`
- Service rejects valid-looking webhook requests

**Root Causes:**
1. Vercel changed webhook payload format (rare but possible)
2. Encoding issues with special characters in error messages
3. Request body too large (exceeds limits)

**Diagnostic Steps:**

1. Capture raw webhook payload:
```bash
# Enable debug logging temporarily
export LOG_LEVEL=DEBUG

# View detailed request logs
aws logs tail /aws/ecs/jiratest-error-triage-production \
  --follow \
  --filter-pattern "{ $.level = \"DEBUG\" && $.action = \"webhook_received\" }"
```

2. Inspect payload structure in CloudWatch logs

3. Compare with expected Vercel format (see [Payload Format Reference](#payload-format-reference))

**Resolution Steps:**

1. **If Vercel changed format:**
   - Update payload adapter logic in `src/services/payload_adapters.py`
   - Add backward compatibility for old format
   - Deploy updated service

2. **If encoding issue:**
   - Verify request Content-Type is `application/json; charset=utf-8`
   - Check for non-UTF-8 characters in error messages
   - Enhance payload adapter to handle edge cases

3. **If payload too large:**
   - Review ALB request size limits (default 1 MB)
   - Adjust if necessary or truncate large payloads

### Issue: No Deep Links in Jira

**Symptoms:**
- Jira issues created successfully
- Issue description missing "View Logs" link
- Or link is present but broken (404 in Vercel)

**Root Causes:**
1. `traceId` field missing from webhook payload
2. Deep link construction logic error
3. Vercel organization/project slug incorrect

**Diagnostic Steps:**

1. Check CloudWatch logs for link construction:
```bash
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern "log_url" \
  --limit 20
```

2. Verify `traceId` in webhook payload:
```json
{
  "event_id": "abc123def456",
  "log_url": "https://vercel.com/acme-corp/my-app/logs?q=traceId:abc123def456"
}
```

3. Test link manually in browser

**Resolution Steps:**

1. **If traceId missing:**
   - Vercel should always include traceId; contact Vercel support if consistently missing
   - Service generates fallback UUID for deduplication

2. **If link broken:**
   - Verify Vercel team slug and project name in link
   - Update `LogLinkBuilder` configuration if team/project changed
   - Ensure Vercel project is accessible (not private)

### Escalation Contacts

If issues persist after troubleshooting:

- **Platform Team**: #jiratest-platform Slack channel
- **On-Call Engineer**: See PagerDuty rotation schedule
- **Vercel Support**: https://vercel.com/support (for webhook delivery issues)
- **AWS Support**: Create case via AWS Console (for infrastructure issues)

---

## Advanced Configuration

### Multi-Environment Setup

Configure separate Log Drains for different environments:

**Production:**
- Name: `Error Triage - Production`
- URL: `https://error-triage.jiratest.com/events`
- Environments: Production only
- Levels: error, warning

**Staging:**
- Name: `Error Triage - Staging`
- URL: `https://error-triage-staging.jiratest.com/events`
- Environments: Preview only
- Levels: error, warning

**Benefits:**
- Separate Jira projects for prod vs staging errors
- Different severity thresholds per environment
- Independent monitoring and alerting

### Custom Log Filtering

Vercel allows filtering logs before they're sent to the drain:

**Example: Exclude Health Check Logs**
```javascript
// vercel.json - Not directly supported; filter in Error Triage service
{
  "logDrain": {
    "name": "Error Triage",
    "url": "https://error-triage.jiratest.com/events"
  }
}
```

**Workaround: Filter in Service**
Add exclusion logic in `src/services/payload_adapters.py`:
```python
def should_process_event(event: dict) -> bool:
    # Skip health check endpoints
    if event.get('path') in ['/health', '/healthz', '/ping']:
        return False
    
    # Skip expected errors
    if 'HealthCheckError' in event.get('message', ''):
        return False
    
    return True
```

### Log Enrichment

Add custom metadata to Vercel logs for better error triaging:

```javascript
// In your Vercel application
console.error('Payment processing failed', {
  userId: user.id,
  orderId: order.id,
  paymentMethod: 'stripe',
  environment: process.env.NODE_ENV
});
```

The Error Triage service will extract these fields for:
- Enhanced Jira issue descriptions
- Ownership routing based on metadata
- Better fingerprint generation

### High-Volume Optimization

For high-traffic Vercel deployments:

1. **Enable Sampling** (future feature):
   - Configure service to sample high-frequency errors
   - Example: After 100 occurrences, only log every 10th error

2. **Adjust Rate Limits:**
   ```bash
   # Increase production rate limit
   export RATE_LIMIT_PER_MINUTE=200
   ```

3. **Scale ECS Service:**
   ```bash
   # Scale to 8 tasks for higher throughput
   aws ecs update-service --desired-count 8
   ```

4. **Use Redis Cluster:**
   - Enable Redis cluster mode for distributed counter storage
   - Increases capacity beyond single-node limits

---

## Summary

You have successfully configured Vercel Log Drain integration with the Error Triage → Jira Upserter service. This integration enables:

✓ **Automatic Error Forwarding**: Vercel errors automatically trigger Jira issue creation  
✓ **Secure Webhooks**: HMAC-SHA256 signature verification prevents unauthorized access  
✓ **Intelligent Grouping**: Similar errors are grouped using stable fingerprints  
✓ **Frequency-Based Severity**: Severity escalates as errors recur  
✓ **Deep Link Navigation**: Direct links from Jira back to Vercel logs  
✓ **Deduplication**: Duplicate events are filtered out automatically  

**Next Steps:**
- Review [Monitoring Guide](./monitoring.md) to set up dashboards and alerts
- Consult [Troubleshooting Runbook](./runbook.md) for operational procedures
- Configure [GCP Integration](./gcp-setup.md) for additional error sources

For questions or issues, contact the platform team at #jiratest-platform.
