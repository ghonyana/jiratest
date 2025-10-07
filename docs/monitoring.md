# Monitoring and Alerting Guide

## Table of Contents

1. [Observability Architecture Overview](#1-observability-architecture-overview)
2. [CloudWatch Dashboard Configuration](#2-cloudwatch-dashboard-configuration)
3. [Alert Threshold Recommendations](#3-alert-threshold-recommendations)
4. [CloudWatch Logs Insights Query Examples](#4-cloudwatch-logs-insights-query-examples)
5. [Prometheus Metrics Catalog](#5-prometheus-metrics-catalog)
6. [Metric Interpretation Guide](#6-metric-interpretation-guide)
7. [Structured JSON Log Field Reference](#7-structured-json-log-field-reference)
8. [Integration with Existing Monitoring Infrastructure](#8-integration-with-existing-monitoring-infrastructure)
9. [Operational Runbook Cross-References](#9-operational-runbook-cross-references)
10. [Monitoring Best Practices](#10-monitoring-best-practices)

---

## 1. Observability Architecture Overview

The Error Triage → Jira Upserter service implements a comprehensive observability strategy based on three core pillars:

### 1.1 Three Pillars of Observability

#### **Structured JSON Logging → CloudWatch Logs**
- All application logs emitted as structured JSON to stdout
- Automatically streamed to CloudWatch log group `/aws/ecs/jiratest-error-triage-{env}`
- Every log entry includes correlation IDs (`event_id`, `fingerprint`) for cross-system tracing
- Queryable via CloudWatch Logs Insights for troubleshooting and analysis

#### **Prometheus Metrics via /metrics Endpoint**
- Standardized `/metrics` endpoint exposing Prometheus-format metrics
- Scraped every 15 seconds by Prometheus for real-time monitoring
- Counters track business events (errors received, Jira actions)
- Histograms measure latencies (processing time, API calls, Redis operations)

#### **Event Correlation**
- `event_id`: Unique identifier from webhook source (Vercel `traceId`, GCP `insertId`)
- `fingerprint`: Stable hash for error grouping across occurrences
- Enables correlation between logs, metrics, and external systems (Vercel, GCP, Jira)

### 1.2 Design Principles

**No Distributed Tracing (v1)**
- AWS X-Ray out of scope for initial release
- Correlation achieved through structured log fields
- Sufficient for single-service architecture

**CloudWatch as Primary Platform**
- Native AWS integration with ECS Container Insights
- Unified view of infrastructure and application metrics
- Integrated alarming and dashboard capabilities

**Prometheus for Visualization**
- Metrics scraped and stored in Prometheus TSDB
- Grafana dashboards for operational visibility
- Enables advanced queries and aggregations

### 1.3 Service Level Objectives (SLOs)

| SLO Metric | Target | Measurement Method |
|------------|--------|-------------------|
| `/events` endpoint response time | < 200ms (p95) | `event_processing_duration_seconds` histogram |
| Redis operation latency | < 5ms (p99) | `redis_latency_seconds` histogram |
| Service availability | 99.9% uptime | CloudWatch ECS `HealthyHostCount` |
| Webhook processing success rate | > 99% | `events_received_total` / (`events_received_total` + `errors_total{error_type="processing"}`) |

---

## 2. CloudWatch Dashboard Configuration

### 2.1 Dashboard Overview

**Dashboard Name:** `JiraTest-ErrorTriage-{Environment}`

Each environment (dev, staging, production) has a dedicated dashboard providing real-time visibility into service health, performance, and business metrics.

### 2.2 Dashboard Widgets

#### **Widget 1: ECS Service Metrics**
- **Type:** Line graph
- **Time Range:** 1 hour
- **Metrics:**
  - `CPUUtilization` (ECS Service metric)
  - `MemoryUtilization` (ECS Service metric)
  - `DesiredTaskCount` (ECS Service metric)
  - `RunningTaskCount` (ECS Service metric)
- **Purpose:** Monitor container resource utilization and task health
- **Threshold Lines:** CPU > 70% (scale trigger), Memory > 80% (warning)

#### **Widget 2: Application Load Balancer Metrics**
- **Type:** Line graph
- **Time Range:** 1 hour
- **Metrics:**
  - `TargetResponseTime` (ALB Target Group)
  - `RequestCount` (ALB Target Group)
  - `HTTPCode_Target_2XX_Count` (ALB Target Group)
  - `HTTPCode_Target_4XX_Count` (ALB Target Group)
  - `HTTPCode_Target_5XX_Count` (ALB Target Group)
- **Purpose:** Track request throughput and error rates at load balancer level
- **Alert Conditions:** 5XX > 10/5min, Response Time > 300ms

#### **Widget 3: Custom Application Metrics**
- **Type:** Number widgets showing rate over last 5 minutes
- **Metrics:**
  - `events_received_total` (counter) - Webhook events received
  - `jira_issues_created_total` (counter) - New Jira bugs created
  - `jira_comments_added_total` (counter) - Comments on existing issues
  - `errors_total` (counter) - Application errors by type
- **Purpose:** Business-level metrics for error processing pipeline
- **Refresh:** Every 60 seconds

#### **Widget 4: Latency Histograms**
- **Type:** Percentile line graph
- **Time Range:** 1 hour
- **Metrics and Percentiles:** (p50, p95, p99)
  - `event_processing_duration_seconds` - End-to-end webhook processing
  - `jira_api_latency_seconds` - Jira API call duration
  - `redis_latency_seconds` - Redis operation latency
- **Purpose:** Identify performance bottlenecks and SLO compliance
- **SLO Lines:** p95 = 200ms (events), p99 = 5ms (Redis)

#### **Widget 5: ElastiCache Redis Metrics**
- **Type:** Line graph
- **Time Range:** 1 hour
- **Metrics:**
  - `CPUUtilization` (ElastiCache)
  - `NetworkBytesIn` (ElastiCache)
  - `NetworkBytesOut` (ElastiCache)
  - `CurrConnections` (ElastiCache)
  - `Evictions` (ElastiCache)
- **Purpose:** Monitor Redis cluster health and capacity
- **Alert Conditions:** CPU > 80%, Evictions > 100/15min

#### **Widget 6: Recent Error Logs**
- **Type:** CloudWatch Logs Insights query widget
- **Query:**
```
fields @timestamp, level, action, error_message, jira_issue_key
| filter level in ["ERROR", "CRITICAL"]
| sort @timestamp desc
| limit 20
```
- **Time Range:** Last 15 minutes
- **Purpose:** Quick visibility into recent application errors
- **Refresh:** Every 60 seconds

### 2.3 Dashboard JSON Export

Complete dashboard configuration available at:
```
deploy/cloudwatch/dashboard-template.json
```

**Deployment via AWS CLI:**
```bash
aws cloudwatch put-dashboard \
  --dashboard-name "JiraTest-ErrorTriage-production" \
  --dashboard-body file://deploy/cloudwatch/dashboard-template.json
```

**Automated Deployment:**
Dashboard provisioned via Terraform module `deploy/terraform/modules/cloudwatch/dashboards.tf`

---

## 3. Alert Threshold Recommendations

### 3.1 Alert Severity Levels

| Severity | Response Time | Notification Method | Example Scenarios |
|----------|---------------|---------------------|-------------------|
| **CRITICAL** | 15 minutes | Page on-call engineer (PagerDuty) | Service outage, security incident |
| **HIGH** | 1 hour | Team Slack channel notification | Performance degradation, dependency issues |
| **MEDIUM** | 4 hours | Team Slack channel notification | Elevated error rates, capacity warnings |
| **LOW** | Next business day | Ticketing system | Non-critical degradation, audit failures |

### 3.2 CloudWatch Alarm Configurations

#### **CRITICAL ALERT: High Response Time**
```yaml
Alarm Name: JiraTest-ErrorTriage-{env}-HighResponseTime
Metric: event_processing_duration_seconds (p95)
Threshold: > 300ms
Evaluation Periods: 2 consecutive (5-minute periods)
Action: Page on-call engineer via SNS → PagerDuty
Rationale: SLO is <200ms p95; 300ms indicates degradation
Investigation Steps:
  - Check redis_latency_seconds for Redis slowness
  - Review jira_api_latency_seconds for Jira API issues
  - Verify ECS task CPU/memory utilization
  - See runbook.md "Performance Degradation Investigation"
```

#### **CRITICAL ALERT: Unhealthy Tasks**
```yaml
Alarm Name: JiraTest-ErrorTriage-{env}-UnhealthyTasks
Metric: HealthyHostCount (ECS Service)
Threshold: < DesiredCount for 5 minutes
Action: Page on-call engineer
Rationale: Tasks failing health checks indicates service unavailability
Investigation Steps:
  - Review CloudWatch logs for startup errors
  - Check /healthz dependency failures (Redis, Jira)
  - Verify Secrets Manager access permissions
  - See runbook.md "Complete Service Outage"
```

#### **CRITICAL ALERT: High 5XX Error Rate**
```yaml
Alarm Name: JiraTest-ErrorTriage-{env}-High5xxErrors
Metric: HTTPCode_Target_5XX_Count (ALB)
Threshold: > 10 errors in 5 minutes
Action: Page on-call engineer
Rationale: Internal server errors indicate application failures
Investigation Steps:
  - Query CloudWatch Logs for exceptions
  - Verify dependency connectivity (Redis, MongoDB, Jira)
  - Check recent deployments for correlation
  - Review errors_total{error_type} breakdown
```

#### **CRITICAL ALERT: Authentication Failures**
```yaml
Alarm Name: JiraTest-ErrorTriage-{env}-AuthFailures
Metric: webhook_auth_failures_total (custom metric)
Threshold: > 20 failures in 5 minutes
Action: Page on-call engineer + Security team notification
Rationale: Potential brute-force attack or misconfiguration
Investigation Steps:
  - Query logs for source IPs: filter level="ERROR" and @message like /authentication failed/
  - Verify webhook secrets in AWS Secrets Manager
  - Review ALB access logs for traffic patterns
  - Update security group rules if malicious
  - See runbook.md "Security Incident Response"
```

#### **HIGH ALERT: Jira API Latency**
```yaml
Alarm Name: JiraTest-ErrorTriage-{env}-HighJiraLatency
Metric: jira_api_latency_seconds (p99)
Threshold: > 5 seconds
Evaluation Periods: 3 consecutive (5-minute periods)
Action: Notify #jiratest-platform Slack channel
Rationale: Jira Cloud performance degradation affecting processing
Investigation Steps:
  - Check Atlassian status page: status.atlassian.com
  - Review jira_api_latency_seconds by operation (search, create, comment)
  - Consider implementing backoff strategy
  - Monitor for transient vs persistent issue
```

#### **HIGH ALERT: Redis High CPU**
```yaml
Alarm Name: JiraTest-ErrorTriage-{env}-RedisHighCPU
Metric: CPUUtilization (ElastiCache Redis)
Threshold: > 80% for 10 minutes
Action: Notify #jiratest-platform Slack channel
Rationale: Redis approaching capacity limits
Investigation Steps:
  - Review Redis commands with INFO commandstats
  - Check Evictions metric for memory pressure
  - Consider scaling up node type (t4g.small → t4g.medium)
  - Enable read replicas for horizontal scaling
```

#### **MEDIUM ALERT: Elevated Error Rate**
```yaml
Alarm Name: JiraTest-ErrorTriage-{env}-ElevatedErrors
Metric: errors_total (rate over 5 minutes)
Threshold: > 5 errors/minute
Action: Notify #jiratest-dev Slack channel
Rationale: Application logic errors requiring investigation
Investigation Steps:
  - Filter CloudWatch logs by error_type dimension
  - Review recent deployments or configuration changes
  - Check for patterns in affected services or error classes
  - Validate configuration file syntax
```

#### **MEDIUM ALERT: Redis Evictions**
```yaml
Alarm Name: JiraTest-ErrorTriage-{env}-RedisEvictions
Metric: Evictions (ElastiCache Redis)
Threshold: > 100 evictions in 15 minutes
Action: Notify #jiratest-platform Slack channel
Rationale: Redis memory pressure causing premature key expiration
Investigation Steps:
  - Review TTL values for freq:*, dedup:*, comment_limit:* keys
  - Check Redis memory usage with INFO memory
  - Consider scaling up node type for more memory
  - Evaluate reducing TTL values to expire keys faster
```

#### **LOW ALERT: MongoDB Connection Failures**
```yaml
Alarm Name: JiraTest-ErrorTriage-{env}-MongoDBDown
Metric: mongodb_connection_errors_total (custom metric)
Threshold: > 5 failures in 15 minutes
Condition: Only if ENABLE_MONGO=true
Action: Create ticket in JIRA platform project
Rationale: Audit logging degraded but not critical for runtime
Investigation Steps:
  - Check MongoDB Atlas cluster status
  - Verify MongoDB connection string in Secrets Manager
  - Review network connectivity from ECS to MongoDB Atlas
  - Non-blocking: Service continues processing webhooks
```

### 3.3 Composite Alarms

To reduce alert noise, configure composite alarms requiring multiple conditions:

**Example: Critical Service Degradation**
```yaml
Composite Alarm: JiraTest-ErrorTriage-{env}-CriticalDegradation
Conditions:
  - HighResponseTime AND (RedisHighCPU OR HighJiraLatency)
  OR
  - High5xxErrors AND UnhealthyTasks
Action: Page on-call engineer
Rationale: Multiple symptoms indicate systemic issue
```

---

## 4. CloudWatch Logs Insights Query Examples

### 4.1 Event Tracking Queries

#### **Query 1: Find All Events for Specific Fingerprint**
```
fields @timestamp, event_id, source, service, environment, action, jira_issue_key
| filter fingerprint = "abc123def456..."
| sort @timestamp desc
| limit 100
```
**Use Case:** Track all occurrences of a specific error pattern, verify Jira issue creation and comments

#### **Query 2: Calculate /events Endpoint p95 Latency**
```
fields duration_ms
| filter action = "webhook_received"
| stats percentile(duration_ms, 95) as p95_latency by bin(5m)
```
**Use Case:** SLO compliance validation, identify time periods with performance degradation

#### **Query 3: Count Jira Actions by Type**
```
fields action
| filter action in ["jira_issue_created", "jira_comment_added", "jira_priority_escalated"]
| stats count() by action
```
**Use Case:** Understand Jira integration activity, validate deduplication effectiveness

---

### 4.2 Troubleshooting Queries

#### **Query 4: Find Authentication Failures with Source Details**
```
fields @timestamp, source, @message
| filter level = "ERROR" and @message like /authentication failed/
| sort @timestamp desc
```
**Use Case:** Investigate webhook authentication issues, identify misconfigured sources or potential attacks

#### **Query 5: Track Error Frequency for Service and Environment**
```
fields @timestamp, fingerprint, frequency_count, severity
| filter service = "web-app" and environment = "production"
| sort frequency_count desc
| limit 20
```
**Use Case:** Identify highest-volume error patterns, prioritize investigation efforts

#### **Query 6: Analyze Jira API Errors**
```
fields @timestamp, jira_issue_key, error_message, duration_ms
| filter action like /jira/ and success = false
| stats count() by error_message
```
**Use Case:** Diagnose Jira integration failures, identify rate limiting or authentication issues

#### **Query 7: Monitor Redis Operation Latency**
```
fields @timestamp, duration_ms
| filter action like /redis/
| stats avg(duration_ms) as avg_latency, percentile(duration_ms, 99) as p99_latency by bin(5m)
```
**Use Case:** Identify Redis performance degradation, validate < 5ms SLO

#### **Query 8: Identify Duplicate Event Drops**
```
fields @timestamp, event_id, source, service
| filter action = "event_deduplicated"
| stats count() by source, service
```
**Use Case:** Understand deduplication patterns, validate webhook retry behavior

---

### 4.3 Business Intelligence Queries

#### **Query 9: Error Distribution by Service**
```
fields service
| filter action = "jira_issue_created"
| stats count() as error_count by service
| sort error_count desc
```
**Use Case:** Identify services with highest error rates, prioritize reliability improvements

#### **Query 10: Severity Escalation Analysis**
```
fields @timestamp, fingerprint, jira_issue_key, severity
| filter action = "jira_priority_escalated"
| stats count() by severity
```
**Use Case:** Track frequency of severity threshold crossings, adjust rules if needed

---

## 5. Prometheus Metrics Catalog

### 5.1 Scrape Configuration

**Prometheus Scrape Job:**
```yaml
scrape_configs:
  - job_name: 'jiratest-error-triage'
    scrape_interval: 15s
    ec2_sd_configs:
      - region: us-east-1
        port: 8080
        filters:
          - name: tag:Service
            values: ['jiratest-error-triage']
    relabel_configs:
      - source_labels: [__meta_ec2_tag_Environment]
        target_label: environment
```

### 5.2 Counter Metrics

#### **Metric 1: events_received_total**
```
# HELP events_received_total Total number of webhook events received
# TYPE events_received_total counter
events_received_total{environment="production",source="vercel"} 12543
events_received_total{environment="production",source="gcp"} 8921
```
**Labels:** `environment`, `source`  
**Use:** Calculate request rate, source distribution analysis  
**Query Examples:**
- Request rate: `rate(events_received_total[5m])`
- By source: `sum by (source) (events_received_total)`

#### **Metric 2: jira_issues_created_total**
```
# HELP jira_issues_created_total Total number of new Jira issues created
# TYPE jira_issues_created_total counter
jira_issues_created_total{environment="production",priority="Highest"} 43
jira_issues_created_total{environment="production",priority="High"} 127
jira_issues_created_total{environment="production",priority="Medium"} 289
```
**Labels:** `environment`, `priority`  
**Use:** Track error impact, priority distribution  
**Query Examples:**
- Creation rate: `rate(jira_issues_created_total[1h])`
- By priority: `sum by (priority) (jira_issues_created_total)`

#### **Metric 3: jira_comments_added_total**
```
# HELP jira_comments_added_total Total number of comments added to existing issues
# TYPE jira_comments_added_total counter
jira_comments_added_total{environment="production"} 1847
```
**Labels:** `environment`  
**Use:** Measure error recurrence patterns  
**Query Example:** `rate(jira_comments_added_total[1h])`

#### **Metric 4: jira_escalations_total**
```
# HELP jira_escalations_total Total number of priority escalations
# TYPE jira_escalations_total counter
jira_escalations_total{environment="production",from_priority="High",to_priority="Highest"} 17
jira_escalations_total{environment="production",from_priority="Medium",to_priority="High"} 54
```
**Labels:** `environment`, `from_priority`, `to_priority`  
**Use:** Track severity escalation frequency  
**Query Example:** `rate(jira_escalations_total[6h])`

#### **Metric 5: errors_total**
```
# HELP errors_total Total number of application errors by type
# TYPE errors_total counter
errors_total{environment="production",error_type="validation_error"} 23
errors_total{environment="production",error_type="redis_error"} 5
errors_total{environment="production",error_type="jira_error"} 12
errors_total{environment="production",error_type="auth_error"} 8
```
**Labels:** `environment`, `error_type`  
**Use:** Application health monitoring, error categorization  
**Query Examples:**
- Total error rate: `rate(errors_total[5m])`
- By error type: `sum by (error_type) (errors_total)`

---

### 5.3 Histogram Metrics

#### **Metric 6: event_processing_duration_seconds**
```
# HELP event_processing_duration_seconds Duration of event processing in seconds
# TYPE event_processing_duration_seconds histogram
event_processing_duration_seconds_bucket{environment="production",source="vercel",le="0.05"} 1024
event_processing_duration_seconds_bucket{environment="production",source="vercel",le="0.1"} 3215
event_processing_duration_seconds_bucket{environment="production",source="vercel",le="0.2"} 12087
event_processing_duration_seconds_bucket{environment="production",source="vercel",le="0.5"} 12450
event_processing_duration_seconds_bucket{environment="production",source="vercel",le="1.0"} 12543
event_processing_duration_seconds_bucket{environment="production",source="vercel",le="+Inf"} 12543
```
**Labels:** `environment`, `source`  
**Buckets:** [0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0]  
**Use:** SLO compliance monitoring (< 200ms p95)  
**Query Examples:**
- p95 latency: `histogram_quantile(0.95, rate(event_processing_duration_seconds_bucket[5m]))`
- SLO compliance: `histogram_quantile(0.95, rate(event_processing_duration_seconds_bucket[5m])) < 0.2`

#### **Metric 7: jira_api_latency_seconds**
```
# HELP jira_api_latency_seconds Jira API call duration in seconds by operation
# TYPE jira_api_latency_seconds histogram
jira_api_latency_seconds_bucket{environment="production",operation="search",le="0.5"} 543
jira_api_latency_seconds_bucket{environment="production",operation="create",le="1.0"} 127
jira_api_latency_seconds_bucket{environment="production",operation="comment",le="0.5"} 1847
```
**Labels:** `environment`, `operation` (search, create, comment, update)  
**Use:** Diagnose Jira API performance issues  
**Query Examples:**
- p99 latency by operation: `histogram_quantile(0.99, sum by (operation, le) (rate(jira_api_latency_seconds_bucket[5m])))`
- Slow operations: `histogram_quantile(0.99, rate(jira_api_latency_seconds_bucket[5m])) > 3`

#### **Metric 8: redis_latency_seconds**
```
# HELP redis_latency_seconds Redis operation duration in seconds by operation
# TYPE redis_latency_seconds histogram
redis_latency_seconds_bucket{environment="production",operation="incr",le="0.001"} 8234
redis_latency_seconds_bucket{environment="production",operation="get",le="0.001"} 12543
redis_latency_seconds_bucket{environment="production",operation="setex",le="0.001"} 1847
```
**Labels:** `environment`, `operation` (incr, get, setex)  
**Use:** Validate Redis SLO (< 5ms p99), identify bottlenecks  
**Query Examples:**
- p99 latency: `histogram_quantile(0.99, rate(redis_latency_seconds_bucket[5m]))`
- SLO breach: `histogram_quantile(0.99, rate(redis_latency_seconds_bucket[5m])) > 0.005`

---

## 6. Metric Interpretation Guide

### 6.1 Healthy System Indicators

#### **High events_received_total with Low jira_issues_created_total**
**Interpretation:** Effective deduplication and error grouping working as designed  
**Expected Ratio:** 10:1 to 20:1 (10-20 events per new issue)  
**Action:** None required; indicates good fingerprint stability

#### **Stable jira_comments_added_total Rate**
**Interpretation:** Consistent error recurrence patterns, no major new incidents  
**Expected Pattern:** Gradual increase during business hours, flat overnight  
**Action:** Monitor for sudden spikes indicating new error patterns

#### **event_processing_duration_seconds p95 < 150ms**
**Interpretation:** Processing comfortably within SLO, healthy buffer  
**Expected Pattern:** Stable over time with minor variance  
**Action:** Baseline for capacity planning

---

### 6.2 Warning Indicators

#### **Increasing jira_escalations_total**
**Interpretation:** Error situations worsening, frequency thresholds being crossed  
**Impact:** SEV1/SEV2 issues requiring urgent attention  
**Action:**
- Query Jira for escalated issues: `project = ET AND updated >= -1h AND priority changed`
- Prioritize investigation of root causes
- Review severity rules if escalations seem premature

#### **event_processing_duration_seconds p95 Approaching 200ms**
**Interpretation:** Processing time nearing SLO boundary, early warning signal  
**Impact:** Risk of SLO breach with traffic increases  
**Action:**
- Review slowest percentiles: check p99 and max latency
- Investigate component latencies: redis_latency_seconds, jira_api_latency_seconds
- Consider optimization: caching, scaling, async processing

#### **jira_api_latency_seconds p99 > 3 seconds**
**Interpretation:** Jira Cloud performance issue affecting responsiveness  
**Impact:** Delays in issue creation/comments, risk of timeout errors  
**Action:**
- Check Atlassian status page: status.atlassian.com
- Review jira_api_latency_seconds by operation to identify bottleneck (search vs create vs comment)
- Consider implementing backoff strategy or request batching
- Monitor for transient vs persistent degradation

#### **redis_latency_seconds p99 Approaching 5ms**
**Interpretation:** Redis nearing SLO threshold, potential resource constraints  
**Impact:** Frequency tracking slowness, risk of processing delays  
**Action:**
- Review ElastiCache Redis CPU and memory utilization metrics
- Check for memory pressure: Evictions metric
- Consider scaling: Upgrade node type (t4g.small → t4g.medium) or add replicas

---

### 6.3 Critical Indicators

#### **errors_total Spike**
**Interpretation:** Application logic errors requiring immediate investigation  
**Impact:** Service degradation, potential data loss or incorrect Jira operations  
**Action:**
- Filter errors_total by error_type dimension: `sum by (error_type) (rate(errors_total[5m]))`
- Query CloudWatch Logs for stack traces and error messages
- Correlate with deployment events: check recent releases or configuration changes
- Review affected traffic patterns: specific sources, services, or error classes

#### **events_received_total Drop to Zero**
**Interpretation:** No webhook traffic being received, potential upstream issue  
**Impact:** Errors not being tracked, blind to production issues  
**Action:**
- Verify ALB target health: check ECS service HealthyHostCount
- Test webhook endpoints manually: curl from Vercel/GCP to /events
- Check ALB access logs for request arrival and response codes
- Verify webhook configurations in Vercel and GCP

#### **jira_issues_created_total Stalled with High events_received_total**
**Interpretation:** Jira integration failure preventing issue creation  
**Impact:** Errors not being surfaced to development teams  
**Action:**
- Check errors_total{error_type="jira_error"} for failures
- Query CloudWatch Logs: `filter action like /jira/ and success = false`
- Test Jira connectivity: curl Atlassian API with credentials
- Verify Jira project exists and service account has permissions
- See runbook.md "Jira Integration Troubleshooting"

---

## 7. Structured JSON Log Field Reference

### 7.1 Core Fields (Always Present)

| Field | Type | Example | Description |
|-------|------|---------|-------------|
| `timestamp` | ISO 8601 string | `"2025-01-15T10:30:46.123Z"` | UTC timestamp when log entry was created |
| `level` | string | `"INFO"`, `"ERROR"` | Log severity level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `service` | string | `"error-triage-jira-upserter"` | Service identifier for multi-service aggregation |
| `environment` | string | `"production"` | Deployment environment (dev, staging, production) |
| `message` | string | `"Jira issue created successfully"` | Human-readable log message |

### 7.2 Event Context Fields

| Field | Type | Example | Description |
|-------|------|---------|-------------|
| `event_id` | string | `"vercel-xyz-123"` | Webhook event identifier from source (Vercel traceId, GCP insertId) |
| `fingerprint` | string | `"a3f5b9c8d2e1..."` | Error grouping hash for correlation across occurrences |
| `source` | string | `"vercel"`, `"gcp"` | Webhook source system |
| `service_name` | string | `"web-app"` | Originating application service that produced the error |
| `error_class` | string | `"TypeError"` | Error type or exception class name |

### 7.3 Action Fields

| Field | Type | Example | Description |
|-------|------|---------|-------------|
| `action` | string | `"jira_issue_created"` | Specific operation being logged (see Action Values below) |
| `jira_issue_key` | string | `"ET-1234"` | Jira issue identifier for cross-referencing |
| `duration_ms` | integer | `125` | Operation duration in milliseconds |

**Action Values:**
- `webhook_received` - Webhook POST request received
- `event_deduplicated` - Duplicate event dropped
- `fingerprint_generated` - Error fingerprint computed
- `jira_issue_created` - New Jira bug created
- `jira_comment_added` - Comment added to existing issue
- `jira_priority_escalated` - Issue priority increased
- `redis_operation` - Redis command executed
- `mongodb_write` - Audit log written

### 7.4 Status Fields

| Field | Type | Example | Description |
|-------|------|---------|-------------|
| `success` | boolean | `true`, `false` | Whether operation completed successfully |
| `error_message` | string | `"Jira API returned 429 Rate Limit"` | Detailed error description for failed operations |
| `error_type` | string | `"jira_error"` | Error category (validation_error, redis_error, jira_error, auth_error) |

### 7.5 Correlation Fields

| Field | Type | Example | Description |
|-------|------|---------|-------------|
| `trace_id` | string | `"abc123def456"` | Vercel trace ID for cross-referencing with Vercel logs |
| `insert_id` | string | `"xyz789"` | GCP log insertId for cross-referencing with Cloud Logging |
| `frequency_count` | integer | `15` | Number of occurrences in 5-minute window |
| `severity` | string | `"SEV2"` | Severity classification (SEV1, SEV2, SEV3, SEV4) |
| `priority` | string | `"High"` | Jira priority (Highest, High, Medium, Low) |

### 7.6 Example Log Entries

#### **Successful Webhook Processing**
```json
{
  "timestamp": "2025-01-15T10:30:46.123Z",
  "level": "INFO",
  "service": "error-triage-jira-upserter",
  "environment": "production",
  "message": "Webhook event processed successfully",
  "action": "webhook_received",
  "event_id": "vercel-dpl-abc123-trace-xyz",
  "source": "vercel",
  "service_name": "web-app",
  "fingerprint": "a3f5b9c8d2e1f4a7b3c5d8e2f9a1b4c7",
  "duration_ms": 125,
  "success": true
}
```

#### **Jira Issue Creation**
```json
{
  "timestamp": "2025-01-15T10:30:46.500Z",
  "level": "INFO",
  "service": "error-triage-jira-upserter",
  "environment": "production",
  "message": "Created new Jira issue for error fingerprint",
  "action": "jira_issue_created",
  "event_id": "vercel-dpl-abc123-trace-xyz",
  "fingerprint": "a3f5b9c8d2e1f4a7b3c5d8e2f9a1b4c7",
  "jira_issue_key": "ET-1234",
  "priority": "High",
  "severity": "SEV2",
  "frequency_count": 12,
  "duration_ms": 847,
  "success": true
}
```

#### **Error Scenario**
```json
{
  "timestamp": "2025-01-15T10:32:15.789Z",
  "level": "ERROR",
  "service": "error-triage-jira-upserter",
  "environment": "production",
  "message": "Failed to create Jira issue",
  "action": "jira_issue_created",
  "event_id": "gcp-insertid-def789",
  "fingerprint": "b4c7d9e2f5a8b1c4d7e0f3a6b9c2d5e8",
  "error_type": "jira_error",
  "error_message": "Jira API returned 429 Too Many Requests: Rate limit exceeded",
  "duration_ms": 10234,
  "success": false
}
```

---

## 8. Integration with Existing Monitoring Infrastructure

### 8.1 CloudWatch Logs Integration

**Automatic Log Streaming**
- All stdout/stderr output from ECS tasks automatically streamed to CloudWatch
- Log group: `/aws/ecs/jiratest-error-triage-{env}`
- Retention: 30 days for dev, 90 days for staging, 365 days for production
- Encryption: AWS KMS with service-managed key

**Log Group Configuration (Terraform):**
```hcl
resource "aws_cloudwatch_log_group" "error_triage" {
  name              = "/aws/ecs/jiratest-error-triage-${var.environment}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn

  tags = {
    Service     = "error-triage-jira-upserter"
    Environment = var.environment
  }
}
```

**Access via AWS Console:**
1. Navigate to CloudWatch → Logs → Log groups
2. Select `/aws/ecs/jiratest-error-triage-production`
3. Use "Insights" tab for advanced queries

---

### 8.2 Prometheus Scraping Configuration

**ECS Service Discovery**
```yaml
scrape_configs:
  - job_name: 'jiratest-error-triage'
    scrape_interval: 15s
    scrape_timeout: 10s
    metrics_path: '/metrics'
    
    ec2_sd_configs:
      - region: us-east-1
        port: 8080
        filters:
          - name: tag:Service
            values: ['jiratest-error-triage']
          - name: tag:Environment
            values: ['production']
    
    relabel_configs:
      - source_labels: [__meta_ec2_tag_Environment]
        target_label: environment
      - source_labels: [__meta_ec2_private_ip]
        target_label: instance
      - source_labels: [__meta_ec2_availability_zone]
        target_label: availability_zone
```

**Alternative: Static Configuration (Staging/Dev)**
```yaml
scrape_configs:
  - job_name: 'error-triage-staging'
    static_configs:
      - targets: ['10.0.5.100:8080', '10.0.6.100:8080']
        labels:
          environment: 'staging'
          service: 'error-triage-jira-upserter'
```

**Validation:**
```bash
# Test metrics endpoint availability
curl http://10.0.5.100:8080/metrics

# Check Prometheus targets
curl http://prometheus-server:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job=="jiratest-error-triage")'
```

---

### 8.3 Grafana Dashboard Import

**Pre-Built Dashboard Location:**
```
deploy/grafana/error-triage-dashboard.json
```

**Import Steps:**
1. Open Grafana UI → Dashboards → Import
2. Upload `error-triage-dashboard.json`
3. Select Prometheus data source
4. Configure variables:
   - `$environment`: Environment filter (production, staging, dev)
   - `$source`: Event source filter (vercel, gcp, all)
5. Save dashboard

**Dashboard Panels:**
- Request rate by source (events_received_total)
- Processing latency percentiles (event_processing_duration_seconds)
- Jira action breakdown (issues created vs comments)
- Error rate by type (errors_total)
- Redis and Jira API latency
- ECS task health and resource utilization

---

### 8.4 PagerDuty Integration (Optional)

**SNS Topic for Critical Alarms:**
```hcl
resource "aws_sns_topic" "critical_alerts" {
  name = "jiratest-error-triage-${var.environment}-critical"
}

resource "aws_sns_topic_subscription" "pagerduty" {
  topic_arn = aws_sns_topic.critical_alerts.arn
  protocol  = "https"
  endpoint  = "https://events.pagerduty.com/integration/${var.pagerduty_integration_key}/enqueue"
}
```

**CloudWatch Alarm → SNS → PagerDuty Flow:**
1. CloudWatch alarm enters ALARM state
2. SNS topic publishes notification
3. PagerDuty receives event via integration key
4. Incident created and assigned to on-call engineer
5. Escalation policy triggered if not acknowledged within 15 minutes

---

### 8.5 Slack Notifications (Optional)

**Lambda Function for Slack Webhook:**
```python
# deploy/lambda/slack_notifier.py
import json
import urllib3

def lambda_handler(event, context):
    http = urllib3.PoolManager()
    
    message = event['Records'][0]['Sns']['Message']
    alarm_data = json.loads(message)
    
    slack_payload = {
        "text": f"⚠️ {alarm_data['AlarmName']} - {alarm_data['NewStateReason']}",
        "attachments": [{
            "color": "danger",
            "fields": [
                {"title": "Environment", "value": alarm_data['Trigger']['Dimensions'][0]['value'], "short": True},
                {"title": "Metric", "value": alarm_data['Trigger']['MetricName'], "short": True}
            ]
        }]
    }
    
    response = http.request('POST', SLACK_WEBHOOK_URL, body=json.dumps(slack_payload))
    return {'statusCode': 200}
```

**SNS Subscription:**
```hcl
resource "aws_sns_topic_subscription" "slack_notifications" {
  topic_arn = aws_sns_topic.high_alerts.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.slack_notifier.arn
}
```

---

## 9. Operational Runbook Cross-References

When alerts fire or issues are detected, refer to the operational runbook for detailed response procedures:

| Alert/Issue | Runbook Section | Quick Action |
|-------------|-----------------|--------------|
| **High Response Time Alert** | `runbook.md` → "Performance Degradation Investigation" | Check Redis latency, Jira API latency, ECS CPU/memory |
| **Authentication Failures Alert** | `runbook.md` → "Security Incident Response" | Review source IPs, verify secrets, check for brute force |
| **Jira API Errors** | `runbook.md` → "Jira Integration Troubleshooting" | Test API token, check Atlassian status, review rate limits |
| **Redis Connection Failures** | `runbook.md` → "Redis Connectivity Issues" | Verify ElastiCache status, check security groups, test telnet |
| **Service Outage** | `runbook.md` → "Complete Service Outage" | Check ECS events, review startup logs, verify dependencies |
| **Rate Limit Exceeded** | `runbook.md` → "Jira API Rate Limit Exceeded" | Enable comment rate limiting, adjust severity thresholds |

**Dashboard Access Configuration:**
- CloudWatch Dashboard: `deployment.md` → "Post-Deployment Monitoring Setup"
- Grafana Dashboard: `deployment.md` → "Monitoring Infrastructure Provisioning"
- Alert Configuration: `deployment.md` → "CloudWatch Alarms Setup"

**Configuration Updates:**
- Severity Rules: `configuration.md` → "Severity Rules YAML Format"
- Ownership Rules: `configuration.md` → "Ownership Rules YAML Format"
- Environment Variables: `configuration.md` → "Environment Variables Catalog"

---

## 10. Monitoring Best Practices

### 10.1 Daily Operations

**Morning Dashboard Review (5 minutes)**
- Check CloudWatch dashboard for overnight anomalies
- Review errors_total counter for unexpected spikes
- Verify all ECS tasks healthy (DesiredTaskCount = RunningTaskCount)
- Scan recent error logs widget for new error patterns

**Traffic Pattern Validation**
- Normal business hours: events_received_total rate 10-50 req/min
- Off-hours: events_received_total rate < 5 req/min
- Alert if rate drops to zero during business hours (upstream issue)

---

### 10.2 Alert Noise Reduction

**Use Composite Alarms**
- Combine multiple symptoms before paging: `HighResponseTime AND (RedisHighCPU OR HighJiraLatency)`
- Reduces false positives from transient spikes
- Ensures alerts represent real systemic issues

**Adjust Thresholds Based on Actual Traffic**
- Baseline normal behavior over 2-week period
- Set thresholds at 2-3 standard deviations from baseline
- Review and adjust monthly to account for traffic growth

**Implement Grace Periods**
- Allow 2-3 evaluation periods before alarming
- Filters transient issues that self-resolve
- Balance between noise reduction and detection speed

---

### 10.3 Regular Maintenance Tasks

**Weekly: Jira Issue Review (30 minutes)**
- Query Jira for issues created by service in past week
- Validate priority and severity assignments match actual impact
- Gather feedback from development teams on issue quality
- Identify opportunities to improve fingerprinting or ownership routing

**Monthly: Alert Threshold Review (1 hour)**
- Analyze false positive rate: alarms that resolved without action
- Review false negative scenarios: issues detected manually before alarms
- Adjust thresholds to maintain 95%+ true positive rate
- Document threshold changes in git commit messages

**Monthly: Capacity Planning (30 minutes)**
- Plot events_received_total trend over past 3 months
- Extrapolate to next 3 months: predict traffic growth
- Review ECS task count and Redis node size for adequacy
- Plan infrastructure scaling 1 month in advance

---

### 10.4 Runbook Linkage

**Every Alert Must Reference Runbook**
- Include runbook section in alarm description
- Provide direct link in PagerDuty incident details
- Ensure on-call engineers have immediate access to response procedures

**Example Alarm Description:**
```
CRITICAL: /events endpoint response time p95 > 300ms for 10 minutes.

This indicates performance degradation affecting webhook processing.

RUNBOOK: See docs/runbook.md section "Performance Degradation Investigation"
DASHBOARD: https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=JiraTest-ErrorTriage-production

Immediate Actions:
1. Check redis_latency_seconds for Redis slowness
2. Review jira_api_latency_seconds for Jira API issues
3. Verify ECS task CPU/memory utilization
```

---

### 10.5 Incident Response Protocol

**Hourly During Incidents**
- Review dashboard every 60 minutes minimum
- Track key metrics: response time, error rate, task health
- Document observations in incident timeline

**Post-Incident Review**
- Conduct postmortem within 48 hours of major incidents
- Analyze monitoring data for early warning signals missed
- Update alert thresholds or add new alerts to prevent recurrence
- Share learnings with broader team in incident review meeting

---

### 10.6 Continuous Improvement

**Quarterly: Metric Review (2 hours)**
- Analyze metric usage: which metrics are queried most frequently?
- Identify gaps: are there operational questions metrics don't answer?
- Add new metrics or labels to address gaps
- Deprecate unused metrics to reduce cardinality

**Quarterly: Dashboard Optimization**
- Review dashboard panel usage via Grafana analytics
- Remove or consolidate rarely viewed panels
- Add new panels based on recent incident investigations
- Ensure dashboard loads in < 5 seconds

**Annual: Monitoring Strategy Review**
- Evaluate overall observability maturity
- Consider advanced capabilities: distributed tracing, anomaly detection
- Review industry best practices and new tooling
- Plan monitoring infrastructure upgrades or migrations

---

## Appendix: Quick Reference

### Key Metrics at a Glance

| Metric | SLO/Threshold | Action on Breach |
|--------|---------------|------------------|
| `event_processing_duration_seconds` p95 | < 200ms | Investigate Redis/Jira latency |
| `redis_latency_seconds` p99 | < 5ms | Scale Redis node or add replicas |
| `jira_api_latency_seconds` p99 | < 5s | Check Atlassian status, implement backoff |
| ECS `HealthyHostCount` | = DesiredCount | Review health check failures, check dependencies |
| `errors_total` rate | < 1 error/min | Filter by error_type, review logs for root cause |
| `jira_escalations_total` | Low variance | Investigate escalated issues, adjust rules if needed |

### Essential CloudWatch Queries

```
# Find errors for fingerprint
fields @timestamp, event_id, jira_issue_key | filter fingerprint = "{fp}" | sort @timestamp desc

# Authentication failures
fields @timestamp, source, @message | filter level = "ERROR" and @message like /auth/ | sort @timestamp desc

# p95 latency
fields duration_ms | filter action = "webhook_received" | stats percentile(duration_ms, 95) by bin(5m)

# Error distribution by type
fields error_type | filter level = "ERROR" | stats count() by error_type
```

### Useful Prometheus Queries

```promql
# Request rate by source
rate(events_received_total[5m])

# Processing latency p95
histogram_quantile(0.95, rate(event_processing_duration_seconds_bucket[5m]))

# Error rate
rate(errors_total[5m])

# Jira action breakdown
sum by (action) (rate(jira_issues_created_total[1h])) + on() group_left sum by (action) (rate(jira_comments_added_total[1h]))
```

---

## Document Maintenance

**Last Updated:** 2025-01-15  
**Version:** 1.0  
**Owner:** JiraTest Platform Team  
**Review Frequency:** Quarterly

**Change History:**
- 2025-01-15: Initial version created for v1.0 service launch

**Related Documentation:**
- [Architecture Overview](architecture.md)
- [API Documentation](api.md)
- [Configuration Reference](configuration.md)
- [Deployment Guide](deployment.md)
- [Operational Runbook](runbook.md)

---

**For questions or updates to this monitoring guide, contact:**
- Slack: #jiratest-platform
- Email: jiratest-platform@example.com
- On-call: PagerDuty rotation "JiraTest Platform"
