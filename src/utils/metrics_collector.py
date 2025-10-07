"""
Prometheus Metrics Collector for Error Triage Service

This module defines all Prometheus metrics for comprehensive application observability,
including counter metrics for event tracking and Jira operations, and histogram metrics
for latency measurements. Metrics are exposed via the /metrics endpoint for Prometheus
scraping and integration with existing CloudWatch monitoring infrastructure.

Metrics Categories:
- Event counters: Track webhook events received, processed, and deduplicated
- Jira counters: Track issue creation, comments added, and priority escalations
- Error counters: Track application errors by type
- Latency histograms: Measure processing times for webhooks, Jira API calls, and Redis operations

Label Cardinality:
- environment: Fixed values (production, staging, dev)
- source: Fixed values (vercel, gcp)
- operation: Fixed values (search_by_fingerprint, create_issue, add_comment, escalate_priority, frequency_incr, dedup_check, rate_limit_check)
- error_type: Fixed values (jira_api_timeout, redis_connection_failure, mongodb_write_error, alarm_triggered)
- project: Jira project keys (ET)
- priority: Jira priority levels (Highest, High, Medium, Low)

SLO Targets:
- Event processing p95: < 200ms (per Section 0.7.3)
- Jira API calls: < 10s timeout (per Section 0.7.5)
- Redis operations p99: < 5ms (per Section 0.7.3)

Multi-Process Support:
- Supports Gunicorn multi-worker deployment via prometheus_client multiprocess mode
- Set PROMETHEUS_MULTIPROC_DIR environment variable to enable metric aggregation across workers
- Automatic detection and configuration based on environment

Usage Examples:
    # Increment counter
    increment_events_received('production', 'vercel')

    # Record histogram observation
    record_jira_api_latency('production', 'create_issue', 0.523)

    # Use timing context manager
    with measure_time(jira_api_latency_seconds, {'environment': 'prod', 'operation': 'search_by_fingerprint'}):
        jira_client.search_issues(jql)

    # Generate metrics for /metrics endpoint
    metrics_text = MetricsCollector().get_metrics()

Author: Blitzy Platform
Version: 1.0.0
"""

import os
from contextlib import contextmanager
from time import perf_counter
from typing import Dict, Optional, Iterator

from prometheus_client import Counter, Histogram, CollectorRegistry, generate_latest, REGISTRY


# ============================================================================
# Multi-Process Support Configuration
# ============================================================================

# Detect Gunicorn multi-worker mode and configure prometheus_client accordingly
_multiprocess_mode = os.getenv("PROMETHEUS_MULTIPROC_DIR")
if _multiprocess_mode:
    # Use multiprocess registry for aggregating metrics across Gunicorn workers
    from prometheus_client import multiprocess

    _registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(_registry)
else:
    # Use default registry for single-process mode
    _registry = REGISTRY


# ============================================================================
# Counter Metrics
# ============================================================================

# Events Received Counter
# Tracks total webhook events received from Vercel and GCP sources
# Incremented in /events endpoint for every incoming webhook
events_received_total = Counter(
    "events_received_total", "Total webhook events received", ["environment", "source"], registry=_registry
)

# Events Processed Counter
# Tracks successfully processed events after fingerprinting and Jira integration
# Incremented after successful end-to-end processing
events_processed_total = Counter(
    "events_processed_total", "Successfully processed events", ["environment", "source"], registry=_registry
)

# Events Deduplicated Counter
# Tracks duplicate events dropped via event_id deduplication cache
# Helps monitor webhook retry behavior and idempotency effectiveness
events_deduplicated_total = Counter(
    "events_deduplicated_total", "Duplicate events dropped", ["environment", "source"], registry=_registry
)

# Jira Issues Created Counter
# Tracks new Bug issues created for novel error fingerprints
# Incremented in JiraIntegrationService.create_bug_issue()
jira_issues_created_total = Counter(
    "jira_issues_created_total", "New Jira issues created", ["environment", "project"], registry=_registry
)

# Jira Comments Added Counter
# Tracks comments added to existing issues for repeated errors
# Incremented in JiraIntegrationService.add_comment()
jira_comments_added_total = Counter(
    "jira_comments_added_total", "Comments added to existing issues", ["environment", "project"], registry=_registry
)

# Jira Escalations Counter
# Tracks issues with escalated priority due to frequency threshold crossings
# Incremented in JiraIntegrationService.escalate_priority()
jira_escalations_total = Counter(
    "jira_escalations_total", "Issues with escalated priority", ["environment", "priority"], registry=_registry
)

# Errors Counter
# Tracks application errors by type for monitoring and alerting
# Incremented on any application error with appropriate error_type dimension
errors_total = Counter("errors_total", "Application errors by type", ["environment", "error_type"], registry=_registry)


# ============================================================================
# Histogram Metrics
# ============================================================================

# Event Processing Duration Histogram
# Measures /events endpoint end-to-end latency from request receipt to response
# Buckets tuned for < 200ms p95 SLO (Section 0.7.3)
# Buckets: 10ms, 50ms, 100ms, 200ms, 500ms, 1s, 2s
event_processing_duration_seconds = Histogram(
    "event_processing_duration_seconds",
    "Webhook processing latency in seconds",
    ["environment", "source"],
    buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0),
    registry=_registry,
)

# Jira API Latency Histogram
# Measures Jira API operation duration for all operations
# Buckets tuned for < 10s timeout enforcement (Section 0.7.5)
# Operations: search_by_fingerprint, create_issue, add_comment, escalate_priority
# Buckets: 100ms, 250ms, 500ms, 1s, 2.5s, 5s, 10s
jira_api_latency_seconds = Histogram(
    "jira_api_latency_seconds",
    "Jira API operation duration in seconds",
    ["environment", "operation"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=_registry,
)

# Redis Operation Latency Histogram
# Measures Redis operation duration for caching and counters
# Buckets tuned for < 5ms p99 SLO target (Section 0.7.3)
# Operations: frequency_incr, dedup_check, rate_limit_check
# Buckets: 1ms, 5ms, 10ms, 25ms, 50ms, 100ms
redis_operation_latency_seconds = Histogram(
    "redis_operation_latency_seconds",
    "Redis operation duration in seconds",
    ["environment", "operation"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1),
    registry=_registry,
)


# ============================================================================
# MetricsCollector Singleton Class
# ============================================================================


class MetricsCollector:
    """
    Singleton class for managing Prometheus metrics collection and exposition.

    Provides convenience methods for incrementing counters and observing histograms
    with proper label management. Returns formatted metrics text for /metrics endpoint.

    Thread-safe for use in multi-worker Gunicorn deployments when multiprocess mode
    is enabled via PROMETHEUS_MULTIPROC_DIR environment variable.

    Attributes:
        _instance: Singleton instance (class-level)
        registry: Prometheus registry (default or multiprocess)

    Methods:
        increment_counter: Increment a counter metric with labels
        observe_histogram: Record a histogram observation with labels
        get_metrics: Generate metrics text for Prometheus exposition
    """

    _instance: Optional["MetricsCollector"] = None

    def __new__(cls) -> "MetricsCollector":
        """Singleton pattern: ensure only one instance exists."""
        if cls._instance is None:
            cls._instance = super(MetricsCollector, cls).__new__(cls)
            cls._instance.registry = _registry
        return cls._instance

    def increment_counter(self, metric_name: str, labels: Dict[str, str]) -> None:
        """
        Increment a counter metric with specified labels.

        Args:
            metric_name: Name of the counter metric (e.g., 'events_received_total')
            labels: Dictionary of label key-value pairs (e.g., {'environment': 'prod', 'source': 'vercel'})

        Raises:
            AttributeError: If metric_name does not exist in module globals
            ValueError: If required labels are missing for the metric

        Example:
            collector = MetricsCollector()
            collector.increment_counter('events_received_total', {'environment': 'production', 'source': 'vercel'})
        """
        try:
            # Get metric object from module globals
            metric = globals().get(metric_name)
            if metric is None:
                raise AttributeError(f"Counter metric '{metric_name}' not found")

            # Increment with labels
            metric.labels(**labels).inc()
        except Exception:
            # Log error but don't crash (fail gracefully for metrics)
            # Note: Logging import deferred to avoid circular dependencies
            pass

    def observe_histogram(self, metric_name: str, value: float, labels: Dict[str, str]) -> None:
        """
        Record a histogram observation with specified labels.

        Args:
            metric_name: Name of the histogram metric (e.g., 'event_processing_duration_seconds')
            value: Observed value in seconds (e.g., 0.125 for 125ms)
            labels: Dictionary of label key-value pairs (e.g., {'environment': 'prod', 'operation': 'create_issue'})

        Raises:
            AttributeError: If metric_name does not exist in module globals
            ValueError: If required labels are missing for the metric

        Example:
            collector = MetricsCollector()
            collector.observe_histogram('jira_api_latency_seconds', 0.523, {'environment': 'production', 'operation': 'create_issue'})
        """
        try:
            # Get metric object from module globals
            metric = globals().get(metric_name)
            if metric is None:
                raise AttributeError(f"Histogram metric '{metric_name}' not found")

            # Observe value with labels
            metric.labels(**labels).observe(value)
        except Exception:
            # Log error but don't crash (fail gracefully for metrics)
            pass

    def get_metrics(self) -> str:
        """
        Generate Prometheus metrics text in exposition format.

        Returns formatted metrics text ready for /metrics endpoint response.
        Automatically aggregates metrics across Gunicorn workers if multiprocess
        mode is enabled.

        Returns:
            str: Metrics text in Prometheus exposition format (text/plain; version=0.0.4)

        Example:
            collector = MetricsCollector()
            metrics_text = collector.get_metrics()
            return Response(metrics_text, mimetype='text/plain; version=0.0.4; charset=utf-8')
        """
        try:
            return generate_latest(self.registry).decode("utf-8")
        except Exception:
            # Return empty metrics on error (fail gracefully)
            return ""


# ============================================================================
# Timing Context Manager
# ============================================================================


@contextmanager
def measure_time(histogram: Histogram, labels: Dict[str, str]) -> Iterator[None]:
    """
    Context manager for automatic duration measurement and histogram recording.

    Measures elapsed time of code block execution using monotonic clock (perf_counter)
    and records observation to specified histogram with labels. Handles exceptions
    gracefully - timing is still recorded even if code block raises an exception.

    Args:
        histogram: Histogram metric object to record duration
        labels: Dictionary of label key-value pairs for the observation

    Yields:
        None (context manager does not provide a value)

    Example:
        with measure_time(jira_api_latency_seconds, {'environment': 'prod', 'operation': 'search_by_fingerprint'}):
            results = jira_client.search_issues(jql)
            # Duration is automatically measured and recorded

    Example with exception handling:
        try:
            with measure_time(jira_api_latency_seconds, {'environment': 'prod', 'operation': 'create_issue'}):
                jira_client.create_issue(fields)
        except JIRAError as e:
            # Timing is still recorded even though exception was raised
            handle_error(e)
    """
    start_time = perf_counter()
    try:
        yield
    finally:
        # Always record timing, even if exception occurred
        duration = perf_counter() - start_time
        try:
            histogram.labels(**labels).observe(duration)
        except Exception:
            # Silently fail on metric recording errors
            pass


# ============================================================================
# Convenience Functions for Common Operations
# ============================================================================


def increment_events_received(environment: str, source: str) -> None:
    """
    Increment events_received_total counter.

    Args:
        environment: Deployment environment (production, staging, dev)
        source: Webhook source (vercel, gcp)

    Example:
        increment_events_received('production', 'vercel')
    """
    events_received_total.labels(environment=environment, source=source).inc()


def increment_events_processed(environment: str, source: str) -> None:
    """
    Increment events_processed_total counter.

    Args:
        environment: Deployment environment (production, staging, dev)
        source: Webhook source (vercel, gcp)

    Example:
        increment_events_processed('production', 'gcp')
    """
    events_processed_total.labels(environment=environment, source=source).inc()


def increment_events_deduplicated(environment: str, source: str) -> None:
    """
    Increment events_deduplicated_total counter.

    Args:
        environment: Deployment environment (production, staging, dev)
        source: Webhook source (vercel, gcp)

    Example:
        increment_events_deduplicated('production', 'vercel')
    """
    events_deduplicated_total.labels(environment=environment, source=source).inc()


def increment_jira_issue_created(environment: str, project: str) -> None:
    """
    Increment jira_issues_created_total counter.

    Args:
        environment: Deployment environment (production, staging, dev)
        project: Jira project key (e.g., 'ET')

    Example:
        increment_jira_issue_created('production', 'ET')
    """
    jira_issues_created_total.labels(environment=environment, project=project).inc()


def increment_jira_comment_added(environment: str, project: str) -> None:
    """
    Increment jira_comments_added_total counter.

    Args:
        environment: Deployment environment (production, staging, dev)
        project: Jira project key (e.g., 'ET')

    Example:
        increment_jira_comment_added('production', 'ET')
    """
    jira_comments_added_total.labels(environment=environment, project=project).inc()


def increment_jira_escalation(environment: str, priority: str) -> None:
    """
    Increment jira_escalations_total counter.

    Args:
        environment: Deployment environment (production, staging, dev)
        priority: New priority level (Highest, High, Medium, Low)

    Example:
        increment_jira_escalation('production', 'Highest')
    """
    jira_escalations_total.labels(environment=environment, priority=priority).inc()


def increment_error(environment: str, error_type: str) -> None:
    """
    Increment errors_total counter.

    Args:
        environment: Deployment environment (production, staging, dev)
        error_type: Error classification (jira_api_timeout, redis_connection_failure, mongodb_write_error, alarm_triggered)

    Example:
        increment_error('production', 'jira_api_timeout')
    """
    errors_total.labels(environment=environment, error_type=error_type).inc()


def record_event_processing_time(environment: str, source: str, duration: float) -> None:
    """
    Record observation to event_processing_duration_seconds histogram.

    Args:
        environment: Deployment environment (production, staging, dev)
        source: Webhook source (vercel, gcp)
        duration: Processing duration in seconds (e.g., 0.125 for 125ms)

    Example:
        start = perf_counter()
        process_webhook(event)
        record_event_processing_time('production', 'vercel', perf_counter() - start)
    """
    event_processing_duration_seconds.labels(environment=environment, source=source).observe(duration)


def record_jira_api_latency(environment: str, operation: str, duration: float) -> None:
    """
    Record observation to jira_api_latency_seconds histogram.

    Args:
        environment: Deployment environment (production, staging, dev)
        operation: Jira operation name (search_by_fingerprint, create_issue, add_comment, escalate_priority)
        duration: API call duration in seconds (e.g., 0.523 for 523ms)

    Example:
        start = perf_counter()
        jira_client.create_issue(fields)
        record_jira_api_latency('production', 'create_issue', perf_counter() - start)
    """
    jira_api_latency_seconds.labels(environment=environment, operation=operation).observe(duration)


def record_redis_latency(environment: str, operation: str, duration: float) -> None:
    """
    Record observation to redis_operation_latency_seconds histogram.

    Args:
        environment: Deployment environment (production, staging, dev)
        operation: Redis operation name (frequency_incr, dedup_check, rate_limit_check)
        duration: Operation duration in seconds (e.g., 0.002 for 2ms)

    Example:
        start = perf_counter()
        redis_client.incr(key)
        record_redis_latency('production', 'frequency_incr', perf_counter() - start)
    """
    redis_operation_latency_seconds.labels(environment=environment, operation=operation).observe(duration)


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    # Singleton class
    "MetricsCollector",
    # Counter metrics
    "events_received_total",
    "events_processed_total",
    "events_deduplicated_total",
    "jira_issues_created_total",
    "jira_comments_added_total",
    "jira_escalations_total",
    "errors_total",
    # Histogram metrics
    "event_processing_duration_seconds",
    "jira_api_latency_seconds",
    "redis_operation_latency_seconds",
    # Context manager
    "measure_time",
    # Convenience functions
    "increment_events_received",
    "increment_events_processed",
    "increment_events_deduplicated",
    "increment_jira_issue_created",
    "increment_jira_comment_added",
    "increment_jira_escalation",
    "increment_error",
    "record_event_processing_time",
    "record_jira_api_latency",
    "record_redis_latency",
]
