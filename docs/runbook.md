# Error Triage Service - Operational Runbook

**Version:** 1.0  
**Last Updated:** 2025-01-15  
**Service:** Error Triage → Jira Upserter  
**Maintainer:** Platform Team

---

## Table of Contents

1. [Service Overview](#service-overview)
2. [Secret Rotation Procedures](#secret-rotation-procedures)
3. [Configuration Update Procedures](#configuration-update-procedures)
4. [Troubleshooting Common Issues](#troubleshooting-common-issues)
5. [Rollback Procedures](#rollback-procedures)
6. [Incident Response Playbooks](#incident-response-playbooks)
7. [Capacity Planning and Scaling](#capacity-planning-and-scaling)
8. [Maintenance Windows](#maintenance-windows)
9. [On-Call Responsibilities](#on-call-responsibilities)
10. [Regular Operational Tasks](#regular-operational-tasks)
11. [Contact Information and Escalation](#contact-information-and-escalation)

---

## Service Overview

The Error Triage service is a lightweight microservice that ingests error events from external sources (Vercel Log Drain and GCP Cloud Logging) and intelligently creates or updates Jira issues based on error frequency and severity.

**Key Components:**
- Flask application running on AWS ECS Fargate
- Redis (ElastiCache) for frequency tracking and deduplication
- MongoDB Atlas (optional) for audit logging
- Jira Cloud for issue management

**Critical Metrics:**
- `events_received_total` - Total webhook events received
- `jira_issues_created_total` - New Jira issues created
- `jira_comments_added_total` - Comments added to existing issues
- `errors_total` - Application errors by type

**Service Endpoints:**
- `POST /events` - Webhook ingestion endpoint
- `GET /healthz` - Health check endpoint
- `GET /metrics` - Prometheus metrics endpoint

---

## Secret Rotation Procedures

All secrets are stored in AWS Secrets Manager and must be rotated on a regular schedule to maintain security. The service is designed to support zero-downtime secret rotation.

### 1. Jira API Token Rotation

**Rotation Schedule:** Every 90 days  
**Impact:** None with proper procedure  
**Rollback Time:** < 1 minute

#### Procedure

**Step 1: Generate New API Token**

1. Log in to Atlassian account settings: https://id.atlassian.com/manage-profile/security/api-tokens
2. Click "Create API token"
3. Name it: `jiratest-error-triage-{env}-{YYYY-MM}`
4. Copy the generated token securely

**Step 2: Update AWS Secrets Manager**

```bash
# Retrieve current secret to preserve structure
current_secret=$(aws secretsmanager get-secret-value \
  --secret-id jira/jiratest/production/credentials \
  --region us-east-1 \
  --query SecretString \
  --output text)

# Update with new token (replace NEW_TOKEN_HERE)
aws secretsmanager update-secret \
  --secret-id jira/jiratest/production/credentials \
  --region us-east-1 \
  --secret-string '{
    "base_url": "https://your-org.atlassian.net",
    "api_token": "NEW_TOKEN_HERE",
    "email": "api-token-user@example.com",
    "project_key": "ET"
  }'
```

**Step 3: Wait for Secret Cache Refresh**

The service caches secrets for 30 seconds. Wait at least 30 seconds before validation.

```bash
echo "Waiting 30 seconds for secret cache refresh..."
sleep 30
```

**Step 4: Validate New Token**

```bash
# Test health check endpoint
curl -f https://error-triage-production.jiratest.com/healthz

# Expected response:
# {
#   "status": "healthy",
#   "checks": {
#     "redis": {"status": "up", "latency_ms": 2},
#     "jira": {"status": "up", "latency_ms": 85}
#   }
# }
```

Check CloudWatch logs for successful Jira API calls:

```bash
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"jira_api_call" "success"' \
  --start-time $(date -u -d '1 minute ago' +%s)000 \
  --limit 5
```

**Step 5: Rollback if Validation Fails**

If health check fails or Jira errors appear in logs:

```bash
# Immediately restore previous token
aws secretsmanager update-secret \
  --secret-id jira/jiratest/production/credentials \
  --region us-east-1 \
  --secret-string "$current_secret"

# Wait 30 seconds and re-validate
sleep 30
curl -f https://error-triage-production.jiratest.com/healthz
```

**Step 6: Revoke Old API Token**

**CRITICAL:** Only revoke the old token after 24 hours of successful operation with the new token.

1. Return to Atlassian API tokens page
2. Find the old token (previous month's naming)
3. Click "Revoke"
4. Confirm revocation

#### Automation (Optional)

Set up AWS Secrets Manager automatic rotation:

```bash
# Create Lambda rotation function
aws lambda create-function \
  --function-name jira-token-rotator \
  --runtime python3.11 \
  --role arn:aws:iam::ACCOUNT_ID:role/lambda-secrets-rotation \
  --handler index.handler \
  --zip-file fileb://rotation-function.zip

# Enable automatic rotation
aws secretsmanager rotate-secret \
  --secret-id jira/jiratest/production/credentials \
  --rotation-lambda-arn arn:aws:lambda:us-east-1:ACCOUNT_ID:function:jira-token-rotator \
  --rotation-rules AutomaticallyAfterDays=90
```

---

### 2. Webhook Secret Rotation

**Rotation Schedule:** Every 180 days  
**Impact:** None with proper procedure  
**Rollback Time:** < 2 minutes

#### Procedure for Vercel

**Step 1: Generate New Webhook Secret**

```bash
# Generate cryptographically secure random string
new_secret=$(openssl rand -hex 32)
echo "New Vercel webhook secret: $new_secret"
```

**Step 2: Update Vercel Log Drain Configuration**

1. Log in to Vercel dashboard
2. Navigate to Project → Settings → Log Drains
3. Click on the Error Triage log drain
4. Update "Secret" field with `$new_secret`
5. Click "Save"

**Step 3: Update AWS Secrets Manager**

```bash
# Update secret
aws secretsmanager update-secret \
  --secret-id jira/jiratest/production/webhook-secret \
  --region us-east-1 \
  --secret-string "{
    \"vercel\": \"$new_secret\",
    \"gcp_audience\": \"https://error-triage-production.jiratest.com\"
  }"
```

**Step 4: Wait and Validate**

```bash
# Wait for cache refresh
sleep 30

# Test webhook delivery in Vercel dashboard
# Click "Test Delivery" button on the Log Drain configuration
# Verify 202 Accepted response
```

Check CloudWatch logs for successful authentication:

```bash
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"webhook_authenticated" source=vercel' \
  --start-time $(date -u -d '1 minute ago' +%s)000
```

**Step 5: Rollback if Authentication Fails**

If 401 Unauthorized errors appear:

```bash
# Restore old secret in Vercel dashboard AND Secrets Manager
# Then retest delivery
```

Monitor authentication failure metrics:

```bash
# Check for auth failure spike
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name webhook_auth_failures_total \
  --dimensions Name=Environment,Value=production \
  --start-time $(date -u -d '5 minutes ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 60 \
  --statistics Sum
```

#### Procedure for GCP OIDC Audience

**Step 1: Review Current Configuration**

```bash
# Get current OIDC audience from Secrets Manager
aws secretsmanager get-secret-value \
  --secret-id jira/jiratest/production/webhook-secret \
  --query SecretString \
  --output text | jq -r '.gcp_audience'
```

**Step 2: Update OIDC Audience (if endpoint URL changed)**

```bash
# Update with new audience
aws secretsmanager update-secret \
  --secret-id jira/jiratest/production/webhook-secret \
  --secret-string "{
    \"vercel\": \"existing_secret\",
    \"gcp_audience\": \"https://new-error-triage-url.jiratest.com\"
  }"
```

**Step 3: Update GCP Pub/Sub Push Subscription**

```bash
# Update push subscription with new endpoint
gcloud pubsub subscriptions update error-events-push \
  --push-endpoint="https://new-error-triage-url.jiratest.com/events" \
  --push-auth-service-account="error-triage@PROJECT_ID.iam.gserviceaccount.com"
```

**Step 4: Validate GCP Webhook Delivery**

```bash
# Trigger test log entry in GCP
gcloud logging write test-log "Test error message" --severity=ERROR

# Check CloudWatch logs for successful GCP event
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"source": "gcp"' \
  --start-time $(date -u -d '1 minute ago' +%s)000
```

---

### 3. MongoDB Connection String Rotation

**Rotation Schedule:** Every 90 days (automatic via MongoDB Atlas)  
**Impact:** Minimal (automatic connection pool refresh)  
**Rollback Time:** < 1 minute

#### Automatic Rotation (MongoDB Atlas)

MongoDB Atlas automatically rotates database user passwords every 90 days if configured. The service handles connection pool refresh automatically on authentication errors.

#### Manual Rotation Procedure

**Step 1: Generate New MongoDB User Password**

1. Log in to MongoDB Atlas console
2. Navigate to Database Access
3. Edit the `error-triage-{env}` user
4. Click "Edit Password"
5. Generate new password or enter custom password
6. Click "Update User"

**Step 2: Update Connection String in Secrets Manager**

```bash
# Update connection string with new password
aws secretsmanager update-secret \
  --secret-id mongodb/jiratest/production/connection-string \
  --region us-east-1 \
  --secret-string "{
    \"uri\": \"mongodb+srv://error-triage-user:NEW_PASSWORD@cluster.mongodb.net/jiratest-production?retryWrites=true&w=majority\"
  }"
```

**Step 3: Monitor for Authentication Errors**

The service will automatically:
1. Detect authentication failures
2. Refresh the connection string from Secrets Manager
3. Reconnect with new credentials

Monitor CloudWatch logs:

```bash
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"mongodb_authentication_error"' \
  --start-time $(date -u -d '5 minutes ago' +%s)000
```

**Note:** MongoDB connectivity is optional (ENABLE_MONGO flag). If MongoDB is unavailable, the service continues processing webhooks without audit logging.

---

## Configuration Update Procedures

The Error Triage service uses YAML configuration files for business logic rules. These files control severity mappings, ownership assignments, and PII sanitization patterns.

### YAML Rule File Update Workflow

**Configuration Files:**
- `config/severity_rules.yaml` - Frequency-to-severity threshold mappings
- `config/ownership_rules.yaml` - Service/path/error patterns to assignee mappings
- `config/sanitization_patterns.yaml` - PII detection regex patterns

#### Standard Update Procedure (Recommended)

**Step 1: Clone Repository and Create Feature Branch**

```bash
git clone https://github.com/your-org/error-triage-service.git
cd error-triage-service
git checkout -b update-severity-rules
```

**Step 2: Edit Configuration Files**

```bash
# Example: Update severity thresholds for production
vi config/severity_rules.yaml
```

Example change:

```yaml
production:
  - threshold: 100  # Changed from 50
    priority: "Highest"
    severity: "SEV1"
  - threshold: 25   # Changed from 10
    priority: "High"
    severity: "SEV2"
```

**Step 3: Validate YAML Syntax**

```bash
# Install yamllint if not present
pip install yamllint

# Validate syntax
yamllint config/*.yaml

# Expected output: No errors
```

**Step 4: Test Rule Changes Locally**

```bash
# Start local development environment
docker-compose up -d

# Send test webhook
curl -X POST http://localhost:8080/events \
  -H "Content-Type: application/json" \
  -H "x-vercel-signature: test-signature" \
  -d @tests/fixtures/vercel-payload.json

# Review logs
docker-compose logs app | grep severity

# Stop environment
docker-compose down
```

**Step 5: Create Pull Request**

```bash
git add config/severity_rules.yaml
git commit -m "Update production severity thresholds

- Increase SEV1 threshold from 50 to 100 errors/5min
- Increase SEV2 threshold from 10 to 25 errors/5min
- Justification: Reduce noise from high-traffic services"

git push origin update-severity-rules
```

Create pull request in GitHub with:
- Clear description of changes
- Business justification
- Impact assessment (which services affected)

**Step 6: Peer Review and Approval**

- At least 1 team member must review
- Verify rule logic is correct
- Confirm no syntax errors
- Approve PR

**Step 7: Merge and Deploy to Staging**

```bash
# After approval, merge to main
git checkout main
git pull origin main

# CI/CD pipeline automatically:
# - Builds new Docker image with updated configs
# - Pushes to ECR with tag: latest-staging
# - Deploys to staging ECS service
```

**Step 8: Validate in Staging Environment**

```bash
# Check staging health
curl https://error-triage-staging.jiratest.com/healthz

# Send test events with varying frequencies
for i in {1..30}; do
  curl -X POST https://error-triage-staging.jiratest.com/events \
    -H "Content-Type: application/json" \
    -H "x-webhook-secret: $STAGING_SECRET" \
    -d @test-error-payload.json
  sleep 5
done

# Check Jira staging project for correct priority assignments
# Verify: 30 errors in 5 minutes → SEV2 with new threshold (was SEV1 before)
```

**Step 9: Deploy to Production**

```bash
# Trigger production deployment (requires approval in CI/CD)
# GitHub Actions workflow: deploy.yml

# Manual approval gate in GitHub UI
# After approval, deployment proceeds automatically
```

**Step 10: Post-Deployment Validation**

```bash
# Monitor CloudWatch logs for "Configuration loaded successfully"
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"Configuration loaded"' \
  --start-time $(date -u -d '5 minutes ago' +%s)000

# Monitor Jira issue creation for correct priorities
# Review first 10 issues created after deployment
```

---

### Hot-Reload Configuration (No Restart)

For urgent configuration updates without service restart:

**Step 1: Identify Running Container**

```bash
# List ECS tasks
aws ecs list-tasks \
  --cluster jiratest-production \
  --service-name error-triage \
  --desired-status RUNNING

# Get container instance
aws ecs describe-tasks \
  --cluster jiratest-production \
  --tasks TASK_ARN
```

**Step 2: Update Configuration File in Container**

```bash
# Connect to container (requires ECS Exec enabled)
aws ecs execute-command \
  --cluster jiratest-production \
  --task TASK_ARN \
  --container error-triage \
  --interactive \
  --command "/bin/sh"

# Inside container, edit file
vi /app/config/severity_rules.yaml

# Save and exit
```

**Step 3: Send SIGHUP Signal to Reload**

```bash
# Still inside container
kill -SIGHUP 1

# Exit container
exit
```

**Step 4: Verify Reload Success**

```bash
# Check CloudWatch logs for reload confirmation
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"Configuration reloaded successfully"' \
  --start-time $(date -u -d '1 minute ago' +%s)000
```

**IMPORTANT:** Hot-reload changes are **not persistent**. They will be lost on next deployment or container restart. Always follow up with a proper pull request to persist changes in git.

---

### Emergency Rule Override

For critical production issues requiring immediate rule changes:

```bash
# Example: Temporarily disable error processing for noisy service
aws ecs execute-command \
  --cluster jiratest-production \
  --task TASK_ARN \
  --container error-triage \
  --interactive \
  --command "/bin/sh"

# Add temporary filter rule
cat >> /app/config/ownership_rules.yaml <<EOF
  - service: "noisy-service"
    action: "ignore"  # Temporary: ignore errors from this service
EOF

# Reload configuration
kill -SIGHUP 1

# Exit and monitor
exit
```

**Follow-up Actions:**
1. Document the temporary change in incident log
2. Create proper PR within 24 hours to persist or revert change
3. Communicate temporary configuration to team

---

## Troubleshooting Common Issues

### Issue A: Webhook Authentication Failures (401 Unauthorized)

**Symptoms:**
- `webhook_auth_failures_total` metric increasing
- 401 response codes in CloudWatch logs
- Error: "Invalid signature" or "Invalid OIDC token"

**Diagnosis:**

```bash
# Check authentication failure rate
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name webhook_auth_failures_total \
  --dimensions Name=Environment,Value=production Name=Source,Value=vercel \
  --start-time $(date -u -d '15 minutes ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Sum

# Review CloudWatch logs for specific error messages
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"webhook_authentication_failed"' \
  --start-time $(date -u -d '15 minutes ago' +%s)000 \
  --limit 20
```

**Resolution for Vercel Signature Failures:**

1. Verify signature header is present:

```bash
# Check webhook delivery logs in Vercel dashboard
# Ensure "x-vercel-signature" header is included in requests
```

2. Compare secrets:

```bash
# Get secret from AWS Secrets Manager
aws secretsmanager get-secret-value \
  --secret-id jira/jiratest/production/webhook-secret \
  --query SecretString \
  --output text | jq -r '.vercel'

# Compare with Vercel Log Drain configuration
# If mismatch, update Vercel or rotate secret
```

3. Test HMAC computation manually:

```bash
# Create test script to verify signature
cat > test_signature.py <<EOF
import hmac
import hashlib

secret = "YOUR_SECRET_HERE"
payload = '{"test": "payload"}'

signature = hmac.new(
    secret.encode(),
    payload.encode(),
    hashlib.sha256
).hexdigest()

print(f"Expected signature: {signature}")
EOF

python test_signature.py
```

**Resolution for GCP OIDC Token Failures:**

1. Verify OIDC audience matches endpoint URL:

```bash
# Get configured audience
aws secretsmanager get-secret-value \
  --secret-id jira/jiratest/production/webhook-secret \
  --query SecretString \
  --output text | jq -r '.gcp_audience'

# Expected: https://error-triage-production.jiratest.com
```

2. Validate service account email claim:

```bash
# Check CloudWatch logs for token claims
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"gcp_oidc_token_invalid"' \
  --start-time $(date -u -d '5 minutes ago' +%s)000

# Verify email claim matches: error-triage@PROJECT_ID.iam.gserviceaccount.com
```

3. Check GCP service account permissions:

```bash
# Verify service account has required permissions
gcloud projects get-iam-policy PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:error-triage@PROJECT_ID.iam.gserviceaccount.com"

# Required role: roles/pubsub.publisher
```

**Prevention:**

- Set up CloudWatch alarm for `webhook_auth_failures_total > 20` in 5 minutes
- Schedule regular secret rotation
- Document secret rotation in calendar reminders

---

### Issue B: Jira Integration Errors

**Symptoms:**
- `jira_issues_created_total` not incrementing
- `jira_api_errors_total` increasing
- Error logs with Jira API exceptions

**Diagnosis:**

```bash
# Check Jira API error rate
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name jira_api_errors_total \
  --dimensions Name=Environment,Value=production \
  --start-time $(date -u -d '15 minutes ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Sum

# Check Atlassian status page
curl -s https://status.atlassian.com/api/v2/status.json | jq .

# Test Jira connectivity manually
JIRA_TOKEN=$(aws secretsmanager get-secret-value \
  --secret-id jira/jiratest/production/credentials \
  --query SecretString --output text | jq -r '.api_token')

curl -u "api-user@example.com:$JIRA_TOKEN" \
  https://your-org.atlassian.net/rest/api/3/serverInfo
```

**Resolution for 401 Authentication Errors:**

```bash
# Jira API token expired or invalid
# Follow secret rotation procedure to rotate token immediately

# Verify new token works
curl -u "api-user@example.com:$NEW_TOKEN" \
  https://your-org.atlassian.net/rest/api/3/myself
```

**Resolution for 429 Rate Limit Exceeded:**

```bash
# Check current request rate
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"jira_api_call"' \
  --start-time $(date -u -d '5 minutes ago' +%s)000 | \
  jq '.events | length'

# Jira Cloud rate limit: 100 requests/minute
# If exceeding, implement immediate mitigation:

# 1. Increase comment rate limit to reduce API calls
# Update config/severity_rules.yaml:
#   comment_rate_limit_minutes: 30  # Was 15

# 2. Review traffic patterns
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"jira_api_call"' \
  --start-time $(date -u -d '1 hour ago' +%s)000 | \
  jq -r '.events[].message' | jq -r '.operation' | sort | uniq -c

# 3. Consider severity threshold adjustments to reduce low-priority issue creation
```

**Resolution for 500 Internal Server Errors:**

```bash
# Jira internal server error
# Check Atlassian status page for incidents
curl -s https://status.atlassian.com/api/v2/incidents.json | jq '.incidents[0]'

# Implement retry with exponential backoff (built into service)
# Monitor CloudWatch logs for retry attempts
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"jira_retry_attempt"' \
  --start-time $(date -u -d '15 minutes ago' +%s)000

# If persistent, contact Atlassian support with:
# - Organization ID
# - Issue key (if specific issue affected)
# - Timestamp range
# - Request ID from response headers
```

**Resolution for 400 Bad Request:**

```bash
# Invalid JQL query or field values
# Review CloudWatch logs for request body
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"jira_bad_request"' \
  --start-time $(date -u -d '5 minutes ago' +%s)000 \
  --limit 1 | jq -r '.events[0].message' | jq -r '.request_body'

# Common issues:
# 1. Invalid custom field ID (customfield_10050 for severity)
#    Verify field ID in Jira project settings
# 2. Invalid priority name
#    Check allowed values: Highest, High, Medium, Low
# 3. Invalid component name in ownership rules
#    Verify component exists in Jira project
```

---

### Issue C: Redis Connectivity Issues

**Symptoms:**
- `redis_connection_errors_total` increasing
- `event_processing_duration_seconds` increasing significantly
- CloudWatch logs showing "Redis unavailable, using default frequency count"

**Diagnosis:**

```bash
# Check Redis cluster status
aws elasticache describe-cache-clusters \
  --cache-cluster-id jiratest-error-triage-redis-production \
  --show-cache-node-info

# Check security group rules
aws ec2 describe-security-groups \
  --group-ids sg-REDIS_SECURITY_GROUP \
  --query 'SecurityGroups[0].IpPermissions'

# Expected: Allow TCP 6379 from ECS task security group
```

**Resolution for Connection Timeouts:**

1. Verify VPC routing:

```bash
# Check route tables for private subnets
aws ec2 describe-route-tables \
  --filters "Name=tag:Name,Values=jiratest-production-private-rt" \
  --query 'RouteTables[0].Routes'

# Verify NAT Gateway route exists for 0.0.0.0/0
```

2. Check Network ACLs:

```bash
# Get subnet NACL
aws ec2 describe-network-acls \
  --filters "Name=association.subnet-id,Values=subnet-REDIS_SUBNET" \
  --query 'NetworkAcls[0].Entries'

# Verify rules allow TCP 6379 both inbound and outbound
```

3. Test connectivity from ECS task:

```bash
# Connect to running container
aws ecs execute-command \
  --cluster jiratest-production \
  --task TASK_ARN \
  --container error-triage \
  --interactive \
  --command "/bin/sh"

# Inside container, test Redis connection
telnet redis-endpoint.cache.amazonaws.com 6379

# Expected: Connected successfully
# Type PING, receive +PONG
```

**Resolution for Memory Pressure:**

```bash
# Check ElastiCache metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/ElastiCache \
  --metric-name Evictions \
  --dimensions Name=CacheClusterId,Value=jiratest-error-triage-redis-production \
  --start-time $(date -u -d '1 hour ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Sum

# If Evictions > 100 in last hour:
# Scale up node type:
aws elasticache modify-cache-cluster \
  --cache-cluster-id jiratest-error-triage-redis-production \
  --cache-node-type cache.t4g.medium \
  --apply-immediately

# OR reduce TTL values to expire keys faster:
# Update config (requires deployment):
# FREQUENCY_COUNTER_TTL=180  # Was 300 (5 minutes)
# DEDUPLICATION_TTL=1800  # Was 3600 (1 hour)
```

**Graceful Degradation Behavior:**

When Redis is unavailable, the service continues processing with degraded functionality:

- Frequency count defaults to 1 for all events
- All errors either create new issues or add comments (no deduplication)
- Comment rate limiting uses in-memory cache (resets on restart)

```bash
# Monitor for graceful degradation logs
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"graceful_degradation" "redis_unavailable"' \
  --start-time $(date -u -d '15 minutes ago' +%s)000
```

---

### Issue D: Performance Degradation

**Symptoms:**
- `/events` endpoint response time exceeding 200ms (p95 SLO)
- CloudWatch alarm firing: `event_processing_duration_seconds p95 > 0.2`
- User reports of slow webhook delivery

**Diagnosis:**

```bash
# Check overall latency distribution
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name event_processing_duration_seconds \
  --dimensions Name=Environment,Value=production \
  --start-time $(date -u -d '1 hour ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Average,p95,p99 \
  --extended-statistics p95,p99

# Review latency breakdown in CloudWatch logs
aws logs insights start-query \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --start-time $(date -u -d '1 hour ago' +%s) \
  --end-time $(date -u +%s) \
  --query-string '
fields @timestamp, duration_ms, redis_latency_ms, jira_latency_ms
| filter action = "event_processed"
| stats avg(duration_ms), max(duration_ms), avg(redis_latency_ms), avg(jira_latency_ms) by bin(5m)
'
```

**Resolution for Redis Latency:**

```bash
# Check Redis latency metric
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name redis_latency_seconds \
  --dimensions Name=Environment,Value=production Name=Operation,Value=incr \
  --start-time $(date -u -d '30 minutes ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --extended-statistics p99

# If p99 > 0.005 seconds (5ms):
# 1. Check Redis CPU utilization
aws cloudwatch get-metric-statistics \
  --namespace AWS/ElastiCache \
  --metric-name CPUUtilization \
  --dimensions Name=CacheClusterId,Value=jiratest-error-triage-redis-production \
  --start-time $(date -u -d '30 minutes ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Average

# If CPU > 80%, scale up node type
# If CPU < 50%, consider adding read replicas for read distribution
```

**Resolution for Jira API Latency:**

```bash
# Check Jira API latency
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name jira_api_latency_seconds \
  --dimensions Name=Environment,Value=production \
  --start-time $(date -u -d '30 minutes ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --extended-statistics p99

# If p99 > 3 seconds:
# This is external dependency latency (Atlassian infrastructure)
# Mitigation options:
# 1. Implement async background processing (future enhancement)
# 2. Increase timeout threshold to prevent premature failures
# 3. Contact Atlassian support if persistent
```

**Resolution for ECS Task CPU/Memory Pressure:**

```bash
# Check ECS Container Insights metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/ECS \
  --metric-name CPUUtilization \
  --dimensions Name=ServiceName,Value=error-triage Name=ClusterName,Value=jiratest-production \
  --start-time $(date -u -d '1 hour ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Average,Maximum

# If CPU > 80% sustained:
# Scale up task CPU allocation
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --task-definition jiratest-error-triage:REVISION \
  --force-new-deployment

# OR increase task count
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --desired-count 6  # Was 4
```

**Resolution for Application Load Balancer Connection Limits:**

```bash
# Check ALB connection count
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApplicationELB \
  --metric-name ActiveConnectionCount \
  --dimensions Name=LoadBalancer,Value=app/jiratest-error-triage/LOAD_BALANCER_ID \
  --start-time $(date -u -d '1 hour ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Sum,Maximum

# If connections saturated:
# Increase ALB capacity by adding second ALB (requires Terraform update)
# OR implement connection pooling optimization
```

---

### Issue E: MongoDB Connection Failures

**Symptoms:**
- MongoDB connection errors in CloudWatch logs
- Warning: "MongoDB unavailable, audit logging degraded"
- Runtime processing continues normally (MongoDB is non-critical)

**Diagnosis:**

```bash
# Check MongoDB Atlas cluster status
# Log in to MongoDB Atlas console: https://cloud.mongodb.com
# Navigate to: Clusters → jiratest-production → Metrics
# Verify: Cluster status is "Active"

# Test connectivity from ECS task
aws ecs execute-command \
  --cluster jiratest-production \
  --task TASK_ARN \
  --container error-triage \
  --interactive \
  --command "/bin/sh"

# Inside container, test MongoDB connection
mongo "$MONGODB_URI" --eval "db.adminCommand('ping')"
```

**Resolution:**

1. Verify MongoDB Atlas IP whitelist:

```bash
# Check NAT Gateway public IP
aws ec2 describe-nat-gateways \
  --filter "Name=tag:Name,Values=jiratest-production-nat" \
  --query 'NatGateways[0].NatGatewayAddresses[0].PublicIp'

# Ensure this IP is in MongoDB Atlas Network Access whitelist
# Atlas Console → Security → Network Access → IP Access List
```

2. Validate connection string:

```bash
# Get connection string from Secrets Manager
MONGODB_URI=$(aws secretsmanager get-secret-value \
  --secret-id mongodb/jiratest/production/connection-string \
  --query SecretString --output text | jq -r '.uri')

# Parse and verify components
echo $MONGODB_URI | sed 's/@/\n@/g'

# Expected format:
# mongodb+srv://username:password
# @cluster.mongodb.net/database?retryWrites=true&w=majority
```

3. Test with mongo client:

```bash
# Install mongo shell in container
apt-get update && apt-get install -y mongodb-clients

# Connect and test
mongo "$MONGODB_URI" --eval "db.error_events.countDocuments({})"

# Expected: Returns count of documents
```

**Impact Assessment:**

MongoDB failures are **non-critical** for runtime operations:
- Webhook processing continues normally
- Jira integration unaffected
- Only audit logging is impacted

Service automatically attempts reconnection every 60 seconds.

---

### Issue F: High Error Rates

**Symptoms:**
- `errors_total` counter spiking
- ERROR level logs increasing in CloudWatch
- Potential downstream impact on error tracking

**Diagnosis:**

```bash
# Check error rate by type
aws logs insights start-query \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --start-time $(date -u -d '30 minutes ago' +%s) \
  --end-time $(date -u +%s) \
  --query-string '
fields @timestamp, error_type, error_message
| filter level = "ERROR"
| stats count() by error_type
| sort count desc
'

# Get query ID from response
QUERY_ID="<query-id-from-response>"

# Wait for query to complete (5-10 seconds)
sleep 10

# Get results
aws logs insights get-query-results --query-id $QUERY_ID
```

**Resolution by Error Type:**

```bash
# Filter by specific error type
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"error_type": "validation_error"' \
  --start-time $(date -u -d '15 minutes ago' +%s)000 \
  --limit 5

# Common error types and resolutions:
# - validation_error: Invalid payload format from webhook source
#   → Coordinate with Vercel/GCP on schema changes
#   → Update payload adapter to handle new format
#
# - redis_error: Redis connectivity or operation failures
#   → See Issue C: Redis Connectivity Issues
#
# - jira_error: Jira API failures
#   → See Issue B: Jira Integration Errors
#
# - auth_error: Webhook authentication failures
#   → See Issue A: Webhook Authentication Failures
```

**Correlation with Recent Changes:**

```bash
# Check recent deployments
aws ecs describe-services \
  --cluster jiratest-production \
  --services error-triage \
  --query 'services[0].deployments'

# If error spike correlates with recent deployment:
# Rollback immediately (see Rollback Procedures section)
```

**Root Cause Investigation:**

```bash
# Get detailed error context
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"level": "ERROR"' \
  --start-time $(date -u -d '15 minutes ago' +%s)000 \
  --limit 10 | jq -r '.events[].message' | jq -r '{timestamp, error_type, error_message, stack_trace, event_id}'
```

---

## Rollback Procedures

### Immediate Service Rollback

**Use When:** Critical issues detected within 5 minutes of deployment

**Procedure:**

```bash
# Step 1: Identify previous task definition revision
aws ecs describe-task-definition \
  --task-definition jiratest-error-triage \
  --query 'taskDefinition.revision'

# Current revision (e.g., 25), rollback to previous (24)

# Step 2: Update service to use previous revision
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --task-definition jiratest-error-triage:24 \
  --force-new-deployment

# Step 3: Monitor service update status
watch aws ecs describe-services \
  --cluster jiratest-production \
  --services error-triage \
  --query 'services[0].deployments[*].{Status:status,Desired:desiredCount,Running:runningCount,TaskDefinition:taskDefinition}'

# Wait for:
# - Primary deployment: Status=PRIMARY, Running=Desired
# - Old deployment: Removed from list

# Step 4: Validate service health
curl -f https://error-triage-production.jiratest.com/healthz

# Step 5: Monitor metrics for 5 minutes
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name errors_total \
  --dimensions Name=Environment,Value=production \
  --start-time $(date -u -d '5 minutes ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 60 \
  --statistics Sum

# Expected: Error rate returns to baseline
```

---

### Configuration Rollback

**Use When:** Recent configuration changes causing issues

**Procedure:**

```bash
# Step 1: Identify problematic commit
git log --oneline config/ --since="1 day ago"

# Step 2: Revert configuration changes
git revert <commit-sha>
git push origin main

# This triggers CI/CD pipeline to:
# - Build new Docker image with reverted config
# - Deploy to staging first
# - After validation, deploy to production

# Step 3: Monitor deployment
# Check GitHub Actions workflow status

# Step 4: Validate configuration loaded correctly
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"Configuration loaded successfully"' \
  --start-time $(date -u -d '2 minutes ago' +%s)000
```

**Hot-Reload Rollback (Faster):**

```bash
# Copy previous YAML files from git history
git show <previous-commit>:config/severity_rules.yaml > severity_rules.yaml

# Upload to running container
aws ecs execute-command \
  --cluster jiratest-production \
  --task TASK_ARN \
  --container error-triage \
  --interactive \
  --command "/bin/sh"

# Inside container
cat > /app/config/severity_rules.yaml <<'EOF'
<paste previous config content>
EOF

# Reload configuration
kill -SIGHUP 1

# Verify reload
tail -f /proc/1/fd/1 | grep "Configuration reloaded"
```

---

### Terraform Infrastructure Rollback

**Use When:** Infrastructure changes causing service issues

**Procedure:**

```bash
# Step 1: Checkout previous Terraform state
cd deploy/terraform
git log --oneline --since="1 week ago"
git checkout <previous-commit> .

# Step 2: Review planned changes
terraform workspace select production
terraform plan -var-file=environments/production.tfvars -out=rollback.tfplan

# Step 3: Review plan carefully
terraform show rollback.tfplan

# Expected changes:
# - Revert recent resource modifications
# - No resource deletions (unless intended)

# Step 4: Apply rollback
terraform apply rollback.tfplan

# Step 5: Wait for infrastructure convergence (5-10 minutes)
# Monitor:
# - ECS service stabilization
# - Redis cluster status
# - Security group rule updates

# Step 6: Validate service health
curl -f https://error-triage-production.jiratest.com/healthz

# Step 7: Monitor application metrics
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name events_received_total \
  --dimensions Name=Environment,Value=production \
  --start-time $(date -u -d '10 minutes ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Sum
```

---

### Docker Image Rollback

**Use When:** New Docker image causing application errors

**Procedure:**

```bash
# Step 1: Identify previous Docker image tag
aws ecr describe-images \
  --repository-name jiratest/error-triage \
  --query 'sort_by(imageDetails,& imagePushedAt)[-5:]' \
  --output table

# Identify previous stable image tag (e.g., sha-abc123)

# Step 2: Get current task definition
aws ecs describe-task-definition \
  --task-definition jiratest-error-triage \
  --query 'taskDefinition' > task-def-current.json

# Step 3: Modify image tag in task definition
cat task-def-current.json | \
  jq '.containerDefinitions[0].image = "ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/jiratest/error-triage:sha-abc123"' | \
  jq 'del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities, .registeredAt, .registeredBy)' \
  > task-def-rollback.json

# Step 4: Register new task definition with old image
aws ecs register-task-definition \
  --cli-input-json file://task-def-rollback.json

# Step 5: Update service to use new task definition
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --task-definition jiratest-error-triage:<NEW_REVISION> \
  --force-new-deployment

# Step 6: Monitor deployment
watch aws ecs describe-services \
  --cluster jiratest-production \
  --services error-triage \
  --query 'services[0].deployments'

# Step 7: Validate rollback success
curl -f https://error-triage-production.jiratest.com/healthz
```

---

## Incident Response Playbooks

### Incident 1: Complete Service Outage

**Severity:** Critical  
**SLA:** < 15 minutes to mitigate

**Symptoms:**
- All ECS tasks failing health checks
- /healthz endpoint returning 503 or timing out
- PagerDuty alert: "Error Triage Service Down"

**Response Steps:**

```bash
# Step 1: Acknowledge alert in PagerDuty
# Immediate response required

# Step 2: Check ECS service events for failure reason
aws ecs describe-services \
  --cluster jiratest-production \
  --services error-triage \
  --query 'services[0].events[0:10]'

# Common failure reasons:
# - "unable to place task" → Resource constraints or subnet issues
# - "service unhealthy" → Tasks failing health checks
# - "task failed to start" → Container startup errors

# Step 3: Review CloudWatch logs for startup errors
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"level": "ERROR"' \
  --start-time $(date -u -d '10 minutes ago' +%s)000 \
  --limit 20

# Step 4: Identify root cause category
# A) Secrets Manager access denied
#    → Check IAM task role permissions
#    → Verify secret ARNs in task definition

# B) Redis unavailable
#    → Check ElastiCache cluster status
#    → Verify security group rules

# C) MongoDB unreachable
#    → Check MongoDB Atlas status page
#    → Verify IP whitelist includes NAT gateway IPs
#    → Note: MongoDB is non-critical; service should continue

# D) Application crash loop
#    → Check logs for Python exceptions
#    → Likely caused by recent deployment

# Step 5: If caused by recent deployment, rollback immediately
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --task-definition jiratest-error-triage:<PREVIOUS_REVISION> \
  --force-new-deployment

# Step 6: If infrastructure issue, verify dependencies
# Check VPC connectivity
aws ec2 describe-route-tables \
  --filters "Name=tag:Name,Values=jiratest-production-private-rt"

# Check security groups
aws ec2 describe-security-groups \
  --group-ids sg-ECS_TASK_SG sg-REDIS_SG

# Step 7: Temporary scale-up to compensate for failures
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --desired-count 8  # Double normal capacity

# Step 8: Communicate status to stakeholders
# Template message:
```

**Stakeholder Communication Template:**

```
Subject: [INCIDENT] Error Triage Service Outage - Investigating

Priority: HIGH
Status: Investigating
ETA: 30 minutes

Impact:
- Error Triage service is currently unavailable
- Webhook deliveries from Vercel and GCP may be delayed or dropped
- Jira issue creation/updates are paused
- No impact to production applications (errors still logged in source systems)

Current Actions:
- [Investigating root cause / Rolling back deployment / Restarting services]
- [Estimated restoration time: 15-30 minutes]

Next Update: In 15 minutes or when resolved

Incident Commander: [Your Name]
Incident Channel: #jiratest-incidents
```

**Post-Resolution:**

```bash
# After resolution, conduct postmortem within 48 hours

# Gather incident timeline
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --start-time <incident-start> \
  --end-time <incident-end> \
  > incident-logs.json

# Document:
# - Root cause analysis
# - Timeline of events
# - Actions taken
# - Prevention measures
# - Action items with owners
```

---

### Incident 2: Jira API Rate Limit Exceeded

**Severity:** High  
**SLA:** < 30 minutes to mitigate

**Symptoms:**
- `jira_api_errors_total{error_type="rate_limit"}` spiking
- 429 errors in CloudWatch logs
- Jira issues not being created or updated

**Response Steps:**

```bash
# Step 1: Confirm rate limit issue
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name jira_api_errors_total \
  --dimensions Name=Environment,Value=production Name=ErrorType,Value=rate_limit \
  --start-time $(date -u -d '15 minutes ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Sum

# Step 2: Calculate current request rate
aws logs insights start-query \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --start-time $(date -u -d '1 hour ago' +%s) \
  --end-time $(date -u +%s) \
  --query-string '
fields @timestamp
| filter action = "jira_api_call"
| stats count() by bin(1m) as requests_per_minute
| sort requests_per_minute desc
'

# Jira Cloud rate limit: 100 requests/minute

# Step 3: Immediate mitigation - Increase comment rate limiting
# Connect to container and update config
aws ecs execute-command \
  --cluster jiratest-production \
  --task TASK_ARN \
  --container error-triage \
  --interactive \
  --command "/bin/sh"

# Inside container, edit config
vi /app/config/severity_rules.yaml

# Add/update:
# comment_rate_limit_minutes: 30  # Increase from 15 to 30 minutes

# Reload configuration
kill -SIGHUP 1

# Step 4: Review error frequency patterns
aws logs insights start-query \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --start-time $(date -u -d '1 hour ago' +%s) \
  --end-time $(date -u +%s) \
  --query-string '
fields @timestamp, service, error_class
| filter action = "jira_issue_created" or action = "jira_comment_added"
| stats count() by service, error_class
| sort count desc
'

# Identify noisy services or error types

# Step 5: Consider temporary severity threshold adjustments
# For noisy low-priority errors, increase thresholds to reduce Jira traffic
# Example: Increase SEV4 threshold from 1 to 5 errors

vi /app/config/severity_rules.yaml

# Update thresholds for affected environments
kill -SIGHUP 1

# Step 6: Monitor rate limit resolution
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name jira_api_errors_total \
  --dimensions Name=Environment,Value=production Name=ErrorType,Value=rate_limit \
  --start-time $(date -u -d '5 minutes ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 60 \
  --statistics Sum

# Expected: Rate limit errors decrease to zero

# Step 7: Long-term mitigation planning
# - Implement request batching (future enhancement)
# - Queue Jira operations for rate-controlled processing
# - Review and optimize severity rules to reduce low-value issues
```

---

### Incident 3: High Authentication Failure Rate

**Severity:** High (Potential Security Incident)  
**SLA:** < 15 minutes to investigate

**Symptoms:**
- `webhook_auth_failures_total > 50` in 5 minutes
- Alert: "Possible unauthorized access attempts"

**Response Steps:**

```bash
# Step 1: Acknowledge security alert

# Step 2: Query CloudWatch logs for failure patterns
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"webhook_authentication_failed"' \
  --start-time $(date -u -d '15 minutes ago' +%s)000 \
  --limit 50 | jq -r '.events[].message' | \
  jq -r '{timestamp, source, source_ip, error}'

# Step 3: Analyze patterns
# - Same source IP with multiple failures → Malicious traffic
# - Multiple source IPs, same error → Legitimate webhook issue (secret mismatch)
# - Pattern of retries with exponential backoff → Legitimate webhook retries

# Step 4A: If malicious traffic detected
# Get offending IP ranges
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"webhook_authentication_failed"' \
  --start-time $(date -u -d '15 minutes ago' +%s)000 | \
  jq -r '.events[].message' | jq -r '.source_ip' | sort | uniq -c | sort -rn

# Block IP ranges at ALB level
aws wafv2 create-ip-set \
  --scope REGIONAL \
  --name error-triage-blocked-ips \
  --addresses 192.0.2.0/24 203.0.113.0/24 \
  --ip-address-version IPV4

# Associate IP set with WAF rule (requires existing WAF setup)

# Step 4B: If legitimate webhook issue (secret mismatch)
# Coordinate with webhook source (Vercel or GCP)
# Verify current secret and rotate if needed

# Step 5: Rotate webhook secrets as precaution
# Follow "Webhook Secret Rotation" procedure in Section 2

# Step 6: Notify security team
# Email: security@yourcompany.com
# Slack: #security-incidents

# Template:
```

**Security Notification Template:**

```
Subject: [SECURITY] High Authentication Failure Rate on Error Triage Service

Priority: HIGH
Classification: Potential Security Incident

Details:
- Service: Error Triage (error-triage-production.jiratest.com)
- Metric: webhook_auth_failures_total > 50 in 5 minutes
- Time Range: [Start Time] to [End Time]
- Source IPs: [List of top offending IPs]

Actions Taken:
- [Blocked IP ranges at WAF level / Rotated webhook secrets / Investigating]
- No evidence of successful unauthorized access
- Service continues operating normally

Request:
- Security team review for additional IOCs
- Investigate if part of broader attack pattern

Incident Commander: [Your Name]
CloudWatch Logs: [Link to filtered log query]
```

**Step 7: Document incident in security log**

```bash
# Create incident record
cat > security-incident-$(date +%Y%m%d-%H%M).md <<EOF
# Security Incident Report

**Date:** $(date --iso-8601=seconds)
**Type:** High Authentication Failure Rate
**Status:** Resolved

## Timeline
- [Time] Alert triggered: webhook_auth_failures_total exceeded threshold
- [Time] Investigation started
- [Time] Malicious IPs identified: [List]
- [Time] IPs blocked at WAF level
- [Time] Webhook secrets rotated
- [Time] Incident resolved

## Root Cause
[Description of what caused the authentication failures]

## Affected Systems
- Error Triage service (no successful unauthorized access)

## Prevention Measures
- [Actions to prevent recurrence]

## Follow-up Actions
- [ ] Review WAF rules for additional protection
- [ ] Implement rate limiting on /events endpoint
- [ ] Schedule webhook secret rotation reminder
EOF
```

---

## Capacity Planning and Scaling

### Baseline Capacity

**Current Configuration (Production):**

| Resource | Specification | Capacity |
|----------|---------------|----------|
| ECS Tasks | 4 tasks × 0.5 vCPU, 1 GB RAM | 100 req/s sustained |
| Redis | cache.t4g.medium (3.2 GB RAM) | 10,000 operations/second |
| Task Auto-scaling | Target 70% CPU | Scale 2-12 tasks |
| MongoDB | M10 (2 GB RAM, 10 GB storage) | 1,000 writes/second |

**Traffic Patterns:**

```bash
# Analyze historical traffic
aws logs insights start-query \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --start-time $(date -u -d '30 days ago' +%s) \
  --end-time $(date -u +%s) \
  --query-string '
fields @timestamp
| filter action = "event_received"
| stats count() as events by bin(1h) as hour
| sort hour
'

# Identify peak hours and growth trends
```

### Scaling Triggers

**ECS Auto-scaling Configuration:**

```bash
# Review current auto-scaling policy
aws application-autoscaling describe-scaling-policies \
  --service-namespace ecs \
  --resource-id service/jiratest-production/error-triage

# Current policy:
# - Metric: CPU Utilization
# - Target: 70%
# - Scale out: Add 2 tasks when CPU > 70% for 2 minutes
# - Scale in: Remove 1 task when CPU < 50% for 15 minutes
# - Min: 4 tasks
# - Max: 12 tasks
```

**Manual Scaling:**

```bash
# Scale up for anticipated traffic spike
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --desired-count 8

# Scale down after traffic subsides
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --desired-count 4
```

### Capacity Limits and Bottlenecks

**Redis Bottleneck:**

- Current: cache.t4g.medium supports ~10,000 ops/s
- With 12 ECS tasks: ~300 req/s = ~3,000 Redis ops/s (safe margin)
- **Recommendation:** Scale Redis to cache.r7g.large for > 500 req/s sustained

```bash
# Scale Redis cluster
aws elasticache modify-cache-cluster \
  --cache-cluster-id jiratest-error-triage-redis-production \
  --cache-node-type cache.r7g.large \
  --apply-immediately

# Enable Redis Cluster mode for horizontal scaling
# Requires Terraform changes and careful migration
```

**Jira API Rate Limit:**

- Jira Cloud: 100 requests/minute per API token
- Current mitigation: Comment rate limiting (15-30 min per issue)
- **Recommendation:** For > 200 errors/minute, implement request queuing

### Growth Planning

**Monthly Capacity Review:**

```bash
# Calculate average events per day
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name events_received_total \
  --dimensions Name=Environment,Value=production \
  --start-time $(date -u -d '30 days ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 86400 \
  --statistics Sum

# Project growth
# Current: 10,000 events/day
# Growth rate: 20% month-over-month
# Projected (3 months): 17,280 events/day
# Required capacity: Scale ECS from 4 to 6 tasks baseline
```

**Proactive Scaling Schedule:**

| Metric | Current | 1 Month | 3 Months | Action Required |
|--------|---------|---------|----------|-----------------|
| Events/day | 10,000 | 12,000 | 17,280 | Increase ECS tasks to 6 |
| Redis memory | 40% | 48% | 69% | Scale Redis to r7g.large by month 3 |
| MongoDB storage | 2 GB | 2.4 GB | 3.5 GB | Scale to M20 cluster by month 4 |

### Cost Optimization

**Scheduled Scaling for Off-Hours:**

```bash
# Create scheduled scaling policy for nights/weekends
# Requires EventBridge + Lambda or Application Auto-scaling schedules

# Scale down at 10 PM EST (low traffic period)
aws application-autoscaling put-scheduled-action \
  --service-namespace ecs \
  --resource-id service/jiratest-production/error-triage \
  --scheduled-action-name scale-down-night \
  --schedule "cron(0 2 * * ? *)" \
  --scalable-target-action MinCapacity=2,MaxCapacity=6

# Scale up at 6 AM EST (traffic resumes)
aws application-autoscaling put-scheduled-action \
  --service-namespace ecs \
  --resource-id service/jiratest-production/error-triage \
  --scheduled-action-name scale-up-morning \
  --schedule "cron(0 10 * * ? *)" \
  --scalable-target-action MinCapacity=4,MaxCapacity=12
```

**Right-Sizing Recommendations:**

```bash
# Review CPU and memory utilization over 30 days
aws cloudwatch get-metric-statistics \
  --namespace AWS/ECS \
  --metric-name CPUUtilization \
  --dimensions Name=ServiceName,Value=error-triage Name=ClusterName,Value=jiratest-production \
  --start-time $(date -u -d '30 days ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 86400 \
  --statistics Average,Maximum

# If CPU consistently < 30%, consider reducing vCPU allocation
# If Memory consistently < 50%, consider reducing memory allocation
```

---

## Maintenance Windows

### Routine Maintenance Schedule

**Maintenance Window:** Second Tuesday of each month, 2:00 AM - 4:00 AM EST

**Rationale:**
- Lowest traffic period (historical analysis)
- Weekday for engineer availability
- Consistent schedule for stakeholder planning

### Pre-Maintenance Checklist

**48 Hours Before Maintenance:**

```bash
# 1. Notify stakeholders
# Email template in Section 11: Contact Information

# 2. Verify rollback plan
# Ensure previous task definition available
aws ecs describe-task-definition \
  --task-definition jiratest-error-triage \
  --query 'taskDefinition.revision'

# 3. Backup current configuration
git clone https://github.com/your-org/error-triage-service.git maintenance-backup-$(date +%Y%m%d)
cd maintenance-backup-$(date +%Y%m%d)
git log -n 1 > BACKUP_COMMIT.txt

# 4. Review pending changes
git log --oneline --since="1 month ago"

# 5. Prepare maintenance runbook with specific tasks
cat > maintenance-plan-$(date +%Y%m%d).md <<EOF
# Maintenance Plan - $(date +%Y-%m-%d)

## Tasks
- [ ] Apply OS security patches (automatic via new task definition)
- [ ] Rotate Jira API token (if 90 days since last rotation)
- [ ] Update Python dependencies in requirements.txt
- [ ] Review and prune old CloudWatch logs
- [ ] Scale up tasks during maintenance for redundancy
- [ ] Validate service health post-maintenance

## Rollback Plan
- Previous task definition: jiratest-error-triage:REVISION
- Previous config commit: $(git rev-parse HEAD)
EOF
```

### Maintenance Tasks

**During Maintenance Window:**

```bash
# 1. Scale up tasks for redundancy during maintenance
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --desired-count 6

# 2. Apply OS security patches
# Rebuild Docker image with latest base image
docker build --no-cache -t jiratest/error-triage:maintenance-$(date +%Y%m%d) .
docker push ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/jiratest/error-triage:maintenance-$(date +%Y%m%d)

# Register new task definition
# (CI/CD pipeline handles this automatically)

# 3. Rotate secrets (if due)
# Follow secret rotation procedures in Section 2

# 4. Update dependencies
# Review Dependabot PRs and merge approved updates
# Update requirements.txt with new versions
# Test locally before deploying

# 5. Prune old CloudWatch logs
aws logs delete-log-stream \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --log-stream-name <old-stream-name>

# Or configure retention policy
aws logs put-retention-policy \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --retention-in-days 30

# 6. Deploy updates
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --task-definition jiratest-error-triage:NEW_REVISION \
  --force-new-deployment

# 7. Monitor deployment
watch aws ecs describe-services \
  --cluster jiratest-production \
  --services error-triage \
  --query 'services[0].deployments'
```

### Post-Maintenance Validation

```bash
# 1. Run smoke tests
curl -f https://error-triage-production.jiratest.com/healthz

# Expected response: {"status": "healthy", "checks": {...}}

# 2. Send test webhook
curl -X POST https://error-triage-production.jiratest.com/events \
  -H "Content-Type: application/json" \
  -H "x-webhook-secret: $PROD_SECRET" \
  -d '{
    "source": "test",
    "message": "Maintenance validation test",
    "environment": "production"
  }'

# Expected: 202 Accepted

# 3. Verify metrics collection
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name events_received_total \
  --dimensions Name=Environment,Value=production \
  --start-time $(date -u -d '5 minutes ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Sum

# 4. Confirm webhook processing resumed
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --filter-pattern '"event_processed"' \
  --start-time $(date -u -d '5 minutes ago' +%s)000 \
  --limit 5

# 5. Scale back to normal capacity
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --desired-count 4

# 6. Document maintenance completion
cat >> maintenance-plan-$(date +%Y%m%d).md <<EOF

## Completion Summary
- Maintenance completed at: $(date --iso-8601=seconds)
- All tasks completed successfully: YES
- Service health: HEALTHY
- Rollback required: NO
- Issues encountered: NONE

## Post-Maintenance Metrics
- Events processed (5 min): [COUNT]
- Average latency: [MS]
- Error rate: [COUNT]

Signed: [Your Name]
EOF
```

### Emergency Maintenance

**For Critical Security Patches:**

Emergency maintenance may be required during business hours for critical security vulnerabilities.

**Procedure:**

```bash
# 1. Assess severity
# CVE score > 9.0 or active exploitation: IMMEDIATE
# CVE score 7.0-9.0: Within 24 hours
# CVE score < 7.0: Next scheduled maintenance

# 2. Notify stakeholders with shortened notice
# Minimum 2 hours advance notice for emergency patching

# 3. Expedited deployment process
# Skip staging validation for critical patches
# Deploy directly to production with increased monitoring

# 4. Coordinate with platform team
# Ensure backup on-call engineer available during emergency deployment

# 5. Post-deployment monitoring
# Monitor service health continuously for 1 hour post-deployment
# Have rollback plan ready for immediate execution
```

---

## On-Call Responsibilities

### Primary On-Call Engineer

**Responsibilities:**

1. **Alert Response**
   - Respond to CRITICAL alerts within 15 minutes
   - Respond to HIGH alerts within 30 minutes
   - Acknowledge alerts in PagerDuty immediately

2. **Incident Management**
   - Investigate and mitigate incidents
   - Follow incident response playbooks
   - Escalate to senior engineer if unable to resolve within 1 hour
   - Document all actions in incident log

3. **Communication**
   - Update stakeholders every 30 minutes during active incidents
   - Post status updates in #jiratest-incidents Slack channel
   - Send incident resolution summary after mitigation

4. **Monitoring**
   - Review CloudWatch dashboards at start of shift
   - Check for any degraded components
   - Address non-critical alerts during business hours

### Secondary On-Call Engineer

**Responsibilities:**

1. **Backup Response**
   - Available for escalation from primary on-call
   - Respond within 30 minutes when escalated

2. **Daily Health Checks**
   - Review monitoring dashboards daily
   - Address HIGH priority alerts within 1 hour
   - Investigate anomalies in metrics

3. **Proactive Monitoring**
   - Review trend analysis for capacity planning
   - Identify potential issues before they become incidents

### On-Call Handoff Process

**Weekly Handoff Checklist:**

```bash
# 1. Document ongoing issues
cat > oncall-handoff-$(date +%Y%m%d).md <<EOF
# On-Call Handoff - $(date +%Y-%m-%d)

## Outgoing Engineer: [Name]
## Incoming Engineer: [Name]

## Active Incidents
- [None / List of active incidents with status]

## Recent Changes
- [Deployments in last 7 days]
- [Configuration updates]
- [Infrastructure changes]

## Known Issues
- [Any degraded components or monitoring gaps]

## Upcoming Maintenance
- [Scheduled maintenance windows]
- [Planned deployments]

## Notes
- [Any additional context or concerns]
EOF

# 2. Review recent incidents
git log --oneline docs/incidents/ --since="1 week ago"

# 3. Check for pending alerts
# Review PagerDuty dashboard for any suppressed or snoozed alerts

# 4. Brief incoming engineer on current state
# Schedule 15-minute handoff call to discuss any concerns

# 5. Transfer on-call rotation in PagerDuty
# Verify incoming engineer is listed as primary on-call
```

### Escalation Procedures

**When to Escalate:**

1. **Unable to resolve within 1 hour**
2. **Root cause unclear**
3. **Requires infrastructure changes beyond service scope**
4. **Security incident requiring security team involvement**

**Escalation Contacts:**

```bash
# Senior Engineer (Platform Team Lead)
# Slack: @platform-lead
# Phone: +1-XXX-XXX-XXXX

# Platform Team On-Call
# PagerDuty: "Platform Team" escalation policy

# Security Team (for security incidents)
# Slack: #security-incidents
# Email: security@yourcompany.com
```

---

## Regular Operational Tasks

### Daily Tasks

**Morning Health Check (10 minutes):**

```bash
# 1. Review CloudWatch dashboard
# URL: https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=ErrorTriage-Production

# Check key metrics:
# - events_received_total: Should match expected traffic patterns
# - errors_total: Should be < 1% of events_received_total
# - jira_issues_created_total + jira_comments_added_total: Should be > 0
# - event_processing_duration_seconds p95: Should be < 200ms

# 2. Check for anomalies
aws cloudwatch get-metric-statistics \
  --namespace Jiratest/ErrorTriage \
  --metric-name errors_total \
  --dimensions Name=Environment,Value=production \
  --start-time $(date -u -d '24 hours ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 3600 \
  --statistics Sum

# Compare to baseline (typical: < 10 errors/hour)

# 3. Review overnight alerts
# Check PagerDuty for any triggered alerts
# Investigate and document any incidents

# 4. Verify service health
curl -f https://error-triage-production.jiratest.com/healthz
curl -f https://error-triage-staging.jiratest.com/healthz
```

### Weekly Tasks

**Monday Morning Review (30 minutes):**

```bash
# 1. Review Jira issues created by service
# Navigate to Jira project ET (Error Triage)
# Filter: Created in last 7 days, Labels contains "errfp:"
# Validate:
# - Priority assignments are appropriate
# - Severity levels match frequency patterns
# - Assignees are correct per ownership rules

# 2. Gather feedback from development teams
# Post in #jiratest-dev Slack channel:
```

**Weekly Feedback Request Template:**

```
:wave: Hey team! Weekly Error Triage check-in:

Are the Jira issues created by Error Triage service helping you catch and prioritize bugs effectively?

Please let us know if:
- Any issues have incorrect priority/severity
- Ownership routing needs adjustment
- Too much noise (too many low-priority issues)
- Missing errors that should be tracked

Feedback: Thread below or DM @platform-team
```

```bash
# 3. Review error trends
aws logs insights start-query \
  --log-group-name /aws/ecs/jiratest-error-triage-production \
  --start-time $(date -u -d '7 days ago' +%s) \
  --end-time $(date -u +%s) \
  --query-string '
fields @timestamp, service, error_class
| filter action = "jira_issue_created"
| stats count() as issue_count by service, error_class
| sort issue_count desc
| limit 10
'

# Identify top error patterns and services

# 4. Check for configuration drift
git diff origin/main config/

# If local changes exist (hot-reload modifications), create PR to persist them

# 5. Review pending Dependabot PRs
# GitHub: https://github.com/your-org/error-triage-service/pulls
# Approve and merge security updates
# Schedule other updates for next maintenance window
```

### Monthly Tasks

**First Monday of Month (1 hour):**

```bash
# 1. Secret rotation check
# Verify rotation schedule compliance

# Jira API token (every 90 days)
# Check last rotation date in AWS Secrets Manager
aws secretsmanager describe-secret \
  --secret-id jira/jiratest/production/credentials \
  --query 'LastRotatedDate'

# Calculate days since rotation
# If > 80 days, schedule rotation before 90-day deadline

# Webhook secrets (every 180 days)
aws secretsmanager describe-secret \
  --secret-id jira/jiratest/production/webhook-secret \
  --query 'LastRotatedDate'

# 2. Review alert thresholds
# Analyze false positive rate
aws cloudwatch describe-alarms \
  --alarm-names "ErrorTriage-Production-HighErrorRate" \
  --query 'MetricAlarms[0].StateTransitionedTimestamp'

# Review CloudWatch alarm history
aws cloudwatch describe-alarm-history \
  --alarm-name "ErrorTriage-Production-HighErrorRate" \
  --start-date $(date -u -d '30 days ago' --iso-8601=seconds) \
  --end-date $(date -u --iso-8601=seconds) \
  --max-records 50

# Adjust thresholds if false positive rate > 20%

# 3. Analyze capacity trends
# Review growth metrics (see Section 7: Capacity Planning)

# 4. Update documentation
# Review and update runbook based on recent incidents
# Add new troubleshooting scenarios encountered
# Update contact information if team changes

# 5. Cost analysis
aws ce get-cost-and-usage \
  --time-period Start=$(date -u -d '30 days ago' +%Y-%m-%d),End=$(date -u +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --filter file://cost-filter.json

# cost-filter.json:
{
  "Tags": {
    "Key": "Service",
    "Values": ["error-triage"]
  }
}

# Review cost trends and optimization opportunities
```

### Quarterly Tasks

**First Week of Quarter (2 hours):**

```bash
# 1. Dependency updates
# Review all Python packages for major version updates

pip list --outdated

# Test major updates in staging before production
# Update requirements.txt with new versions

# 2. Security vulnerability scan
# Run Trivy scan on Docker image
trivy image ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/jiratest/error-triage:latest

# Address any HIGH or CRITICAL vulnerabilities

# 3. Disaster recovery test
# Simulate service failure and recovery

# Test 1: Complete service outage
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --desired-count 0

# Wait 5 minutes, verify monitoring alerts triggered

# Restore service
aws ecs update-service \
  --cluster jiratest-production \
  --service error-triage \
  --desired-count 4

# Measure recovery time

# Test 2: Redis cluster failure simulation
# (Coordinate with platform team for controlled test)

# Test 3: Restore from configuration backup
# Verify git repository backup process

# 4. Architecture review
# Assess current design against evolving requirements
# Identify technical debt and improvement opportunities
# Plan for upcoming quarters

# 5. Incident postmortem review
# Review all incidents from past quarter
# Identify patterns and systemic issues
# Create action items for prevention

ls docs/incidents/*.md
# Review each incident report
# Aggregate lessons learned
```

---

## Contact Information and Escalation

### On-Call Rotation

**PagerDuty Schedule:** https://yourcompany.pagerduty.com/schedules/ERROR_TRIAGE_SCHEDULE

**Current On-Call:**
- Primary: Check PagerDuty schedule
- Secondary: Check PagerDuty schedule

**Escalation Policy:**
- Primary on-call: 15 minutes
- Secondary on-call: 30 minutes
- Platform team lead: 45 minutes

### Team Contacts

**Platform Team:**

- **Slack Channel:** #jiratest-platform
- **Purpose:** Infrastructure issues, AWS resources, Terraform changes
- **Response Time:** < 4 hours during business hours

**Development Team:**

- **Slack Channel:** #jiratest-dev
- **Purpose:** Application logic issues, feature requests, severity rule feedback
- **Response Time:** < 8 hours during business hours

**Security Team:**

- **Slack Channel:** #security-incidents
- **Email:** security@yourcompany.com
- **Purpose:** Security incidents, authentication failures, potential breaches
- **Response Time:** < 1 hour for critical security incidents

### External Support

**Atlassian Support (Jira Cloud):**

- **Portal:** https://support.atlassian.com
- **Support Plan:** Premium (24/7 support)
- **When to Contact:**
  - Persistent Jira API errors (500, 503)
  - API rate limit adjustments
  - Custom field configuration issues
- **Information to Provide:**
  - Organization ID: [YOUR_ORG_ID]
  - Project Key: ET
  - Timestamp range of issue
  - Request IDs from response headers
  - Error messages from CloudWatch logs

**MongoDB Atlas Support:**

- **Portal:** https://cloud.mongodb.com (Support chat in console)
- **Support Plan:** M10 cluster includes standard support
- **When to Contact:**
  - Cluster connectivity issues
  - Performance degradation
  - Backup/restore operations
- **Information to Provide:**
  - Cluster name: jiratest-production
  - Database: jiratest-production
  - Timestamp of issue
  - Connection errors from application logs

**AWS Support:**

- **Portal:** https://console.aws.amazon.com/support/home
- **Support Plan:** Enterprise (24/7 support, < 15 min response for critical)
- **When to Contact:**
  - ECS service issues
  - ElastiCache cluster problems
  - VPC connectivity issues
  - Secrets Manager failures
- **Information to Provide:**
  - Account ID: [YOUR_AWS_ACCOUNT]
  - Region: us-east-1
  - Resource ARNs
  - CloudWatch logs
  - Reproduction steps

**Google Cloud Support (for GCP Pub/Sub):**

- **Portal:** https://cloud.google.com/support
- **Support Plan:** [YOUR_GCP_SUPPORT_TIER]
- **When to Contact:**
  - Pub/Sub push subscription delivery failures
  - OIDC token validation issues
  - Log sink configuration problems
- **Information to Provide:**
  - Project ID: [YOUR_GCP_PROJECT]
  - Subscription name: error-events-push
  - Message IDs of failed deliveries
  - Service account email

**Vercel Support:**

- **Portal:** https://vercel.com/support
- **Support Plan:** [YOUR_VERCEL_PLAN]
- **When to Contact:**
  - Log Drain delivery failures
  - Webhook signature validation issues
  - Payload format changes
- **Information to Provide:**
  - Vercel project name: [YOUR_PROJECT]
  - Log Drain ID
  - Timestamp of failures
  - Example webhook payloads

### Escalation Matrix

| Issue Type | First Contact | Escalation 1 | Escalation 2 | Escalation 3 |
|------------|---------------|--------------|--------------|--------------|
| Service Outage | Primary On-Call | Secondary On-Call | Platform Team Lead | Engineering Manager |
| Security Incident | Primary On-Call | Security Team | Security Lead | CISO |
| Jira Integration | Primary On-Call | Platform Team | Atlassian Support | Engineering Manager |
| Infrastructure | Primary On-Call | Platform Team Lead | AWS Support | Engineering Manager |
| Configuration | Primary On-Call | Development Team | Platform Team Lead | Engineering Manager |

### Emergency Contact Protocol

**For Critical Production Incidents (Service Down):**

1. **Acknowledge alert in PagerDuty** (< 5 minutes)
2. **Post in #jiratest-incidents Slack channel:**

```
:rotating_light: INCIDENT: Error Triage Service Outage
Status: Investigating
Impact: [Description]
Incident Commander: @your-name
Bridge: [Conference line if needed]
Updates: Every 15 minutes
```

3. **If unable to resolve in 15 minutes, escalate:**
   - Tag @platform-team-oncall in Slack
   - Call secondary on-call: PagerDuty escalation

4. **Update stakeholders every 15 minutes** until resolved

5. **Post resolution message:**

```
:white_check_mark: RESOLVED: Error Triage Service Restored
Duration: [XX minutes]
Root Cause: [Brief description]
Actions Taken: [Summary]
Postmortem: [Link when available]
```

### Calendar Reminders

**Set up recurring calendar events for:**

- Monthly: First Monday - Secret rotation check
- Monthly: Second Tuesday 2 AM EST - Maintenance window
- Quarterly: First week - Dependency updates and DR test
- Quarterly: Last week - Capacity planning review

---

## Appendix

### Useful CloudWatch Logs Insights Queries

**Query: Top Error Types (Last 24 Hours)**

```
fields @timestamp, error_type, error_message
| filter level = "ERROR"
| stats count() as error_count by error_type
| sort error_count desc
```

**Query: Event Processing Latency Distribution**

```
fields @timestamp, duration_ms
| filter action = "event_processed"
| stats avg(duration_ms) as avg_latency, 
        max(duration_ms) as max_latency, 
        pct(duration_ms, 95) as p95_latency, 
        pct(duration_ms, 99) as p99_latency 
        by bin(5m)
```

**Query: Jira Operations Summary**

```
fields @timestamp, action, jira_issue_key
| filter action in ["jira_issue_created", "jira_comment_added", "jira_priority_escalated"]
| stats count() as operation_count by action, bin(1h)
```

**Query: Authentication Failures by Source**

```
fields @timestamp, source, source_ip, error
| filter action = "webhook_authentication_failed"
| stats count() as failure_count by source, source_ip
| sort failure_count desc
```

### Common AWS CLI Commands Reference

```bash
# ECS Service Status
aws ecs describe-services --cluster jiratest-production --services error-triage

# ECS Task List
aws ecs list-tasks --cluster jiratest-production --service-name error-triage --desired-status RUNNING

# Force New Deployment
aws ecs update-service --cluster jiratest-production --service error-triage --force-new-deployment

# View Task Logs
aws logs tail /aws/ecs/jiratest-error-triage-production --follow

# Get Secret Value
aws secretsmanager get-secret-value --secret-id jira/jiratest/production/credentials --query SecretString --output text

# Check Redis Cluster Status
aws elasticache describe-cache-clusters --cache-cluster-id jiratest-error-triage-redis-production --show-cache-node-info

# CloudWatch Metrics
aws cloudwatch get-metric-statistics --namespace Jiratest/ErrorTriage --metric-name events_received_total --dimensions Name=Environment,Value=production --start-time $(date -u -d '1 hour ago' --iso-8601=seconds) --end-time $(date -u --iso-8601=seconds) --period 300 --statistics Sum
```

---

## Document Change Log

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-01-15 | Platform Team | Initial runbook creation |

---

**End of Runbook**

For questions or feedback on this runbook, contact the Platform Team in #jiratest-platform.
