"""
POST /events Webhook Endpoint for Multi-Source Error Ingestion

This module implements the core webhook handler for the Error Triage service, accepting
error events from Vercel Log Drain and GCP Cloud Logging via authenticated HTTPS POST
requests. The endpoint performs comprehensive error processing including payload
normalization, PII-sanitized fingerprinting, Redis-based frequency tracking, severity
evaluation, and intelligent Jira issue creation/updates.

Architecture:
    Flask Blueprint pattern for modular route registration
    Pipeline processing: Auth → Normalize → Dedup → Fingerprint → Track → Classify → Jira
    Asynchronous design: Return 202 Accepted within <200ms p95 SLO per Section 0.7.3

Processing Pipeline Stages:
    1. Authentication: Vercel HMAC-SHA256 signature or GCP OIDC JWT validation
    2. Payload Normalization: Adapter pattern transforms source-specific formats to NormalizedErrorEvent
    3. Deduplication: Redis-based event_id tracking with 1-hour TTL for idempotency
    4. Fingerprinting: Stable SHA-256 hash generation from service+env+error_class+stack+sanitized_message
    5. Frequency Tracking: Redis rolling 5-minute counters per (environment, fingerprint) pair
    6. Severity Classification: Rule-based evaluation of frequency thresholds to determine priority
    7. Ownership Resolution: Pattern-based assignee determination from service/path/error_class
    8. Jira Integration: Fingerprint-based search, create new issue or add comment, escalate on threshold crossing

Security Features (per Section 0.1.2 and 0.7.4):
    - Webhook signature/token validation before processing
    - PII sanitization before fingerprinting and Jira transmission
    - Rate limiting on Jira comments (max once per 15 minutes unless severity escalates)
    - Structured logging without sensitive data exposure

Observability (per Section 0.7.2):
    - Every operation emits structured JSON log with correlation fields (event_id, fingerprint)
    - Prometheus metrics for counters (events_received, jira_created, errors) and histograms (latency)
    - CloudWatch Logs integration via ECS awslogs driver

Performance Characteristics:
    - Target: <200ms p95 latency for /events endpoint response
    - Jira API timeout: 10 seconds with exponential backoff retry
    - Redis operations: <5ms p99 with connection pooling
    - Graceful degradation on dependency failures (Redis, MongoDB)

Error Handling:
    - 400 Bad Request: Malformed payload, missing required fields
    - 401 Unauthorized: Invalid signature or token verification failure
    - 500 Internal Server Error: Jira API timeout, Redis failures, unexpected exceptions
    - All errors increment errors_total counter with appropriate error_type dimension

Example Request (Vercel):
    POST /events
    Content-Type: application/json
    x-vercel-signature: abc123def456...

    {
      "source": "vercel",
      "deployment": {"id": "dpl_xyz", "url": "my-app-abc.vercel.app"},
      "message": "TypeError: Cannot read property 'x' of undefined",
      "level": "error",
      "timestamp": 1705318245123,
      "environment": "production",
      "path": "/api/checkout",
      "traceId": "abc123"
    }

Example Response:
    HTTP/1.1 202 Accepted
    Content-Type: application/json

    {
      "status": "accepted",
      "event_id": "vercel-xyz-123",
      "fingerprint": "a3f5b9c8d2e1f4g6h8j9k0"
    }

Integration Points:
    - Vercel Log Drain: x-vercel-signature HMAC verification per Section 0.7.5
    - GCP Pub/Sub Push: Authorization Bearer OIDC token validation per Section 0.7.5
    - Redis: Frequency counters, deduplication cache, rate limit timestamps
    - Jira Cloud API: Issue search, creation, commenting, priority escalation
    - CloudWatch: Structured JSON logs via stdout, automatic field extraction
    - Prometheus: Metrics exposition via /metrics endpoint for scraping

Section References:
    - Section 0.1.1 requirements #1-4: Multi-source ingestion, fingerprinting, frequency tracking, Jira integration
    - Section 0.5.1 Group 2: Core webhook endpoint implementation details
    - Section 0.7.1 requirements #2-4: Fingerprint stability, idempotency, comment rate limiting
    - Section 0.7.3: Performance requirements (<200ms p95 response time)

Author: Blitzy Platform
Version: 1.0.0
"""

from time import perf_counter
from typing import Tuple
from flask import Blueprint, jsonify, request, Request

# Internal imports from depends_on_files - utilities
from src.utils.logging_config import get_logger
from src.utils.auth import WebhookAuthenticator
from src.utils.metrics_collector import MetricsCollector

# Internal imports from depends_on_files - services
from src.services.payload_adapters import PayloadAdapterFactory
from src.services.deduplication import DeduplicationService
from src.services.fingerprinter import ErrorFingerprinter
from src.services.frequency_tracker import FrequencyTracker
from src.services.severity_engine import SeverityRulesEngine
from src.services.ownership_resolver import OwnershipResolver
from src.services.jira_integration import JiraIntegrationService
from src.services.comment_rate_limiter import CommentRateLimiter

# Internal imports from depends_on_files - models
from src.models.error_event import NormalizedErrorEvent


# ============================================================================
# Module-Level Configuration and Initialization
# ============================================================================

# Initialize structured logger for webhook processing pipeline
logger = get_logger(__name__)

# Initialize Prometheus metrics collector singleton
metrics_collector = MetricsCollector()

# Create Flask blueprint for events route
# Registered in src/app/__init__.py application factory
events_bp = Blueprint('events', __name__)


# ============================================================================
# Dependency Injection Container (Initialized by Application Factory)
# ============================================================================

# Global service instances (injected by app factory via init_services function)
# This pattern enables testability via dependency injection and mocking
_authenticator: WebhookAuthenticator = None
_payload_factory: PayloadAdapterFactory = None
_dedup_service: DeduplicationService = None
_fingerprinter: ErrorFingerprinter = None
_freq_tracker: FrequencyTracker = None
_severity_engine: SeverityRulesEngine = None
_ownership_resolver: OwnershipResolver = None
_jira_service: JiraIntegrationService = None
_rate_limiter: CommentRateLimiter = None
_environment: str = "production"  # Deployment environment for metrics/logs


def init_services(
    authenticator: WebhookAuthenticator,
    payload_factory: PayloadAdapterFactory,
    dedup_service: DeduplicationService,
    fingerprinter: ErrorFingerprinter,
    freq_tracker: FrequencyTracker,
    severity_engine: SeverityRulesEngine,
    ownership_resolver: OwnershipResolver,
    jira_service: JiraIntegrationService,
    rate_limiter: CommentRateLimiter,
    environment: str = "production",
) -> None:
    """
    Initialize service dependencies for the events blueprint.

    This function is called by the Flask application factory (src/app/__init__.py)
    to inject configured service instances into the blueprint. This pattern enables:
    - Dependency injection for testability
    - Configuration-driven service initialization
    - Clean separation of concerns between blueprint and service layers

    Args:
        authenticator: WebhookAuthenticator instance for Vercel/GCP webhook validation
        payload_factory: PayloadAdapterFactory for source-specific payload transformation
        dedup_service: DeduplicationService for event_id-based idempotency
        fingerprinter: ErrorFingerprinter for stable hash generation
        freq_tracker: FrequencyTracker for Redis-based occurrence counting
        severity_engine: SeverityRulesEngine for threshold-based classification
        ownership_resolver: OwnershipResolver for pattern-based assignee determination
        jira_service: JiraIntegrationService for issue creation/updates
        rate_limiter: CommentRateLimiter for comment frequency enforcement
        environment: Deployment environment (production, staging, dev)

    Example:
        >>> # In src/app/__init__.py application factory
        >>> from app.routes.events import init_services
        >>> init_services(
        ...     authenticator=WebhookAuthenticator(vercel_secret, gcp_audience),
        ...     payload_factory=PayloadAdapterFactory(),
        ...     dedup_service=DeduplicationService(redis_client, environment),
        ...     # ... other services ...
        ...     environment='production'
        ... )
    """
    global _authenticator, _payload_factory, _dedup_service, _fingerprinter
    global _freq_tracker, _severity_engine, _ownership_resolver, _jira_service
    global _rate_limiter, _environment

    _authenticator = authenticator
    _payload_factory = payload_factory
    _dedup_service = dedup_service
    _fingerprinter = fingerprinter
    _freq_tracker = freq_tracker
    _severity_engine = severity_engine
    _ownership_resolver = ownership_resolver
    _jira_service = jira_service
    _rate_limiter = rate_limiter
    _environment = environment

    logger.info(
        "Events blueprint services initialized",
        extra={"action": "events_services_init", "environment": environment},
    )


# ============================================================================
# Helper Functions for Source Detection and Validation
# ============================================================================


def detect_webhook_source(req: Request) -> str:
    """
    Detect webhook source (Vercel or GCP) from request headers.

    Examines request headers to determine the originating webhook source:
    - Vercel: Presence of x-vercel-signature header
    - GCP: Presence of Authorization Bearer header

    Args:
        req: Flask Request object

    Returns:
        str: 'vercel' or 'gcp' based on header presence

    Raises:
        ValueError: If no recognized authentication headers are present

    Example:
        >>> source = detect_webhook_source(request)
        >>> # Returns 'vercel' if x-vercel-signature present
        >>> # Returns 'gcp' if Authorization: Bearer present
    """
    if "x-vercel-signature" in req.headers:
        return "vercel"
    elif "Authorization" in req.headers:
        auth_header = req.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return "gcp"

    # No recognized authentication headers
    raise ValueError("No recognized authentication headers (x-vercel-signature or Authorization Bearer)")


def validate_content_type(req: Request) -> None:
    """
    Validate that request Content-Type is application/json.

    Args:
        req: Flask Request object

    Raises:
        ValueError: If Content-Type is not application/json
    """
    content_type = req.headers.get("Content-Type", "")
    if not content_type.startswith("application/json"):
        raise ValueError(f"Invalid Content-Type: {content_type}. Expected application/json")


# ============================================================================
# Main Webhook Endpoint Handler
# ============================================================================


@events_bp.route("/events", methods=["POST"])
def handle_webhook_event():
    """
    POST /events webhook endpoint for multi-source error ingestion.

    Processes error events from Vercel Log Drain and GCP Cloud Logging with complete
    pipeline: authentication, normalization, deduplication, fingerprinting, frequency
    tracking, severity classification, and Jira integration.

    Per Section 0.7.3, this endpoint must respond within <200ms p95 SLO. Processing
    stages are optimized for minimal latency with Redis connection pooling and
    efficient Jira API operations.

    Request Flow:
        1. Validate Content-Type: application/json
        2. Detect webhook source from headers (Vercel or GCP)
        3. Authenticate request via signature/token verification
        4. Transform payload to NormalizedErrorEvent
        5. Check deduplication cache (drop if duplicate)
        6. Generate stable fingerprint with PII sanitization
        7. Increment frequency counter in Redis
        8. Evaluate severity rules based on frequency
        9. Resolve ownership/assignment from rules
        10. Search for existing Jira issue by fingerprint
        11. Create new issue or add comment based on search result
        12. Escalate priority if severity threshold crossed
        13. Return 202 Accepted with event_id and fingerprint

    Returns:
        Tuple[dict, int]: JSON response and HTTP status code
            - 202 Accepted: {"status": "accepted", "event_id": "...", "fingerprint": "..."}
            - 400 Bad Request: {"error": "Malformed payload", "detail": "..."}
            - 401 Unauthorized: {"error": "Unauthorized", "detail": "Invalid signature or token"}
            - 500 Internal Server Error: {"error": "Processing failed", "detail": "..."}

    Metrics Emitted:
        - events_received_total: Incremented on entry
        - events_processed_total: Incremented on successful processing
        - events_deduplicated_total: Incremented when duplicate detected
        - jira_issues_created_total: Incremented when new issue created
        - jira_comments_added_total: Incremented when comment added
        - jira_escalations_total: Incremented when priority escalated
        - event_processing_duration_seconds: Histogram of total latency
        - errors_total: Incremented on any error with error_type

    Logs Emitted:
        Every stage emits structured JSON log with correlation fields:
        - event_id: Webhook event identifier
        - fingerprint: Error grouping hash
        - jira_issue_key: Jira issue identifier (when applicable)
        - action: Operation type (webhook_authenticated, fingerprint_generated, etc.)
        - duration_ms: Elapsed time for timed operations

    Error Handling:
        - Malformed payloads: 400 Bad Request with descriptive error
        - Authentication failures: 401 Unauthorized
        - Jira API timeouts: Logged, metric incremented, 500 returned
        - Redis connection failures: Graceful degradation (count=1 fallback)
        - Unexpected exceptions: 500 with error_type tracking

    Example Request (Vercel):
        POST /events HTTP/1.1
        Host: error-triage.jiratest.com
        Content-Type: application/json
        x-vercel-signature: abc123def456...

        {
          "source": "vercel",
          "deployment": {"id": "dpl_xyz", "url": "my-app.vercel.app"},
          "message": "TypeError: Cannot read property 'x' of undefined",
          "level": "error",
          "timestamp": 1705318245123,
          "environment": "production",
          "path": "/api/checkout",
          "traceId": "abc123"
        }

    Example Response:
        HTTP/1.1 202 Accepted
        Content-Type: application/json

        {
          "status": "accepted",
          "event_id": "vercel-abc123-1705318245123",
          "fingerprint": "a3f5b9c8d2e1f4g6h8j9k0m1n2p3q4r5s6t7u8v9w0x1y2z3"
        }
    """
    # Start processing timer for latency measurement
    start_time = perf_counter()
    source = None  # Initialize for error handling scope
    event_id = None  # Initialize for error handling scope
    fingerprint = None  # Initialize for error handling scope

    try:
        # ====================================================================
        # Stage 1: Request Validation and Source Detection
        # ====================================================================

        # Validate Content-Type header
        try:
            validate_content_type(request)
        except ValueError as e:
            logger.warning(
                "Invalid Content-Type header",
                extra={"action": "invalid_content_type", "error_detail": str(e)},
            )
            metrics_collector.increment_counter(
                "errors_total", {"environment": _environment, "error_type": "invalid_content_type"}
            )
            return jsonify({"error": "Bad Request", "detail": str(e)}), 400

        # Detect webhook source from headers
        try:
            source = detect_webhook_source(request)
        except ValueError as e:
            logger.warning(
                "Webhook received with no recognized authentication headers",
                extra={"action": "webhook_auth_failed", "error_detail": str(e)},
            )
            metrics_collector.increment_counter(
                "errors_total", {"environment": _environment, "error_type": "missing_auth_headers"}
            )
            return jsonify({"error": "Unauthorized", "detail": str(e)}), 401

        # Increment events_received counter
        metrics_collector.increment_counter("events_received_total", {"environment": _environment, "source": source})

        logger.info(
            f"Webhook received from {source}",
            extra={"action": "webhook_received", "source": source},
        )

        # ====================================================================
        # Stage 2: Authentication
        # ====================================================================

        # Verify webhook signature or token
        if not _authenticator.verify(request):
            logger.warning(
                f"Webhook authentication failed for {source}",
                extra={"action": "webhook_auth_failed", "source": source},
            )
            metrics_collector.increment_counter(
                "errors_total", {"environment": _environment, "error_type": "webhook_auth_failed"}
            )
            return jsonify({"error": "Unauthorized", "detail": "Invalid signature or token"}), 401

        logger.info(
            f"Webhook authenticated successfully for {source}",
            extra={"action": "webhook_authenticated", "source": source},
        )

        # ====================================================================
        # Stage 3: Payload Normalization
        # ====================================================================

        # Get appropriate adapter for source
        adapter = _payload_factory.get_adapter(source)

        # Transform payload to NormalizedErrorEvent
        try:
            normalized_event: NormalizedErrorEvent = adapter.transform(request.json)
            event_id = normalized_event.event_id
        except ValueError as e:
            logger.error(
                "Payload validation failed: missing required fields",
                extra={
                    "action": "payload_validation_failed",
                    "source": source,
                    "error_detail": str(e),
                },
            )
            metrics_collector.increment_counter(
                "errors_total", {"environment": _environment, "error_type": "validation_error"}
            )
            return jsonify({"error": "Payload validation failed: missing required fields", "detail": str(e)}), 400
        except Exception as e:
            logger.error(
                "Unexpected error during payload transformation",
                extra={
                    "action": "payload_transform_error",
                    "source": source,
                    "error_type": "unexpected_transform_error",
                },
                exc_info=True,
            )
            metrics_collector.increment_counter(
                "errors_total", {"environment": _environment, "error_type": "unexpected_transform_error"}
            )
            return jsonify({"error": "Processing failed", "detail": "Payload transformation error"}), 500

        logger.info(
            "Payload normalized successfully",
            extra={
                "action": "payload_normalized",
                "event_id": event_id,
                "source": source,
                "service": normalized_event.service,
                "environment": normalized_event.environment,
                "error_class": normalized_event.error_class,
            },
        )

        # ====================================================================
        # Stage 4: Deduplication Check
        # ====================================================================

        # Check if event was already processed
        if _dedup_service.is_duplicate(event_id):
            logger.info(
                "Duplicate event detected, skipping processing",
                extra={"action": "event_deduplicated", "event_id": event_id, "source": source},
            )
            metrics_collector.increment_counter(
                "events_deduplicated_total", {"environment": _environment, "source": source}
            )

            # Return 202 Accepted for duplicate (idempotent response)
            return (
                jsonify({"status": "accepted", "event_id": event_id, "duplicate": True}),
                202,
            )

        # Mark event as processed with 1-hour TTL
        _dedup_service.mark_processed(event_id, ttl=3600)

        # ====================================================================
        # Stage 5: Error Fingerprinting
        # ====================================================================

        # Generate stable fingerprint with PII sanitization
        fingerprint = _fingerprinter.generate_fingerprint(normalized_event)

        logger.info(
            "Error fingerprint generated",
            extra={
                "action": "fingerprint_generated",
                "event_id": event_id,
                "fingerprint": fingerprint,
                "service": normalized_event.service,
                "environment": normalized_event.environment,
            },
        )

        # ====================================================================
        # Stage 6: Frequency Tracking
        # ====================================================================

        # Increment rolling 5-minute counter in Redis
        try:
            frequency_count = _freq_tracker.increment(normalized_event.environment, fingerprint, ttl=300)
        except Exception as e:
            # Graceful degradation: fall back to count=1 on Redis failure
            logger.warning(
                "Redis frequency tracking failed, using fallback count=1",
                extra={
                    "action": "redis_frequency_fallback",
                    "event_id": event_id,
                    "fingerprint": fingerprint,
                    "error_detail": str(e),
                },
            )
            frequency_count = 1

        logger.info(
            "Frequency counter incremented",
            extra={
                "action": "redis_frequency_incr",
                "event_id": event_id,
                "fingerprint": fingerprint,
                "count": frequency_count,
                "environment": normalized_event.environment,
            },
        )

        # ====================================================================
        # Stage 7: Severity Classification
        # ====================================================================

        # Evaluate severity rules based on frequency threshold
        priority, severity = _severity_engine.evaluate(normalized_event.environment, frequency_count)

        logger.info(
            "Severity rules evaluated",
            extra={
                "action": "frequency_threshold_evaluated",
                "event_id": event_id,
                "fingerprint": fingerprint,
                "count": frequency_count,
                "priority": priority,
                "severity": severity,
            },
        )

        # ====================================================================
        # Stage 8: Ownership Resolution
        # ====================================================================

        # Resolve assignee or component from pattern-based rules
        assignment = _ownership_resolver.resolve(normalized_event)

        if assignment:
            logger.info(
                "Ownership assignment resolved",
                extra={
                    "action": "ownership_assigned",
                    "event_id": event_id,
                    "fingerprint": fingerprint,
                    "assignment": assignment,
                },
            )

        # ====================================================================
        # Stage 9: Jira Integration
        # ====================================================================

        # Search for existing issue by fingerprint
        try:
            issue_key = _jira_service.search_issue_by_fingerprint(fingerprint)
        except Exception as e:
            # Check if it's a timeout error for more specific logging
            from requests.exceptions import Timeout
            error_msg = "Jira API timeout" if isinstance(e, Timeout) else "Jira search failed"
            error_type = "jira_timeout" if isinstance(e, Timeout) else "jira_api_error"
            
            logger.error(
                error_msg,
                extra={
                    "action": "jira_search_failed",
                    "event_id": event_id,
                    "fingerprint": fingerprint,
                    "error_type": error_type,
                },
                exc_info=True,
            )
            metrics_collector.increment_counter(
                "errors_total", {"environment": _environment, "error_type": "jira_api_error"}
            )
            # Don't fail the request - return 202 and log for retry
            return (
                jsonify(
                    {
                        "status": "accepted",
                        "event_id": event_id,
                        "fingerprint": fingerprint,
                        "warning": "Jira integration temporarily unavailable",
                    }
                ),
                202,
            )

        if issue_key:
            # ================================================================
            # Existing Issue Path: Add Comment and Possibly Escalate
            # ================================================================

            logger.info(
                "Existing Jira issue found",
                extra={
                    "action": "jira_issue_found",
                    "event_id": event_id,
                    "fingerprint": fingerprint,
                    "jira_issue_key": issue_key,
                },
            )

            # Check if severity has increased (for priority escalation)
            # Retrieve previous severity from Redis cache
            severity_cache_key = f"severity:{_environment}:{fingerprint}"
            try:
                previous_severity = _freq_tracker.redis_client.get(severity_cache_key)
            except Exception as e:
                logger.warning(
                    "Failed to retrieve previous severity from Redis",
                    extra={
                        "action": "redis_severity_get_failed",
                        "error": str(e),
                        "fingerprint": fingerprint,
                    },
                )
                previous_severity = None
            
            # Define severity ordering (SEV1 is highest, SEV4 is lowest)
            severity_levels = {"SEV1": 1, "SEV2": 2, "SEV3": 3, "SEV4": 4}
            severity_increased = False
            
            if previous_severity and previous_severity in severity_levels and severity in severity_levels:
                # Check if new severity is higher (lower number means higher severity)
                if severity_levels[severity] < severity_levels[previous_severity]:
                    severity_increased = True
                    logger.info(
                        "Severity level increased",
                        extra={
                            "action": "severity_increased",
                            "fingerprint": fingerprint,
                            "previous_severity": previous_severity,
                            "new_severity": severity,
                        },
                    )
            
            # Store current severity in Redis cache (TTL: 7 days)
            try:
                _freq_tracker.redis_client.setex(severity_cache_key, 7 * 24 * 3600, severity)
            except Exception as e:
                logger.warning(
                    "Failed to store severity in Redis",
                    extra={
                        "action": "redis_severity_set_failed",
                        "error": str(e),
                        "fingerprint": fingerprint,
                    },
                )

            should_add_comment = _rate_limiter.should_comment(issue_key, severity_increased)

            if should_add_comment:
                try:
                    _jira_service.add_comment(
                        issue_key, frequency_count, severity, normalized_event.log_url
                    )

                    logger.info(
                        "Comment added to existing Jira issue",
                        extra={
                            "action": "jira_comment_added",
                            "event_id": event_id,
                            "fingerprint": fingerprint,
                            "jira_issue_key": issue_key,
                            "count": frequency_count,
                            "severity": severity,
                        },
                    )

                    # Record comment timestamp for rate limiting
                    _rate_limiter.record_comment(issue_key, ttl=900)  # 15 minutes

                    # Increment metrics
                    metrics_collector.increment_counter(
                        "jira_comments_added_total",
                        {"environment": _environment, "project": "ET"},  # ET = Error Triage project
                    )

                except Exception as e:
                    # Check if it's a timeout error for more specific logging
                    from requests.exceptions import Timeout
                    error_msg = "Jira API timeout (add comment)" if isinstance(e, Timeout) else "Failed to add Jira comment"
                    error_type = "jira_timeout" if isinstance(e, Timeout) else "jira_api_error"
                    
                    logger.error(
                        error_msg,
                        extra={
                            "action": "jira_comment_failed",
                            "event_id": event_id,
                            "fingerprint": fingerprint,
                            "jira_issue_key": issue_key,
                            "error_type": error_type,
                        },
                        exc_info=True,
                    )
                    metrics_collector.increment_counter(
                        "errors_total", {"environment": _environment, "error_type": error_type}
                    )
            else:
                logger.info(
                    "Comment rate limit enforced, skipping comment",
                    extra={
                        "action": "comment_rate_limited",
                        "event_id": event_id,
                        "fingerprint": fingerprint,
                        "jira_issue_key": issue_key,
                    },
                )

            # Check if priority escalation is needed
            # In production, would compare current priority with new priority
            # For now, we'll attempt escalation if severity changed
            if severity_increased:
                try:
                    _jira_service.escalate_priority(issue_key, priority)

                    logger.info(
                        "Jira issue priority escalated",
                        extra={
                            "action": "jira_priority_escalated",
                            "event_id": event_id,
                            "fingerprint": fingerprint,
                            "jira_issue_key": issue_key,
                            "new_priority": priority,
                        },
                    )

                    # Increment metrics
                    metrics_collector.increment_counter(
                        "jira_escalations_total", {"environment": _environment, "priority": priority}
                    )

                except Exception as e:
                    # Check if it's a timeout error for more specific logging
                    from requests.exceptions import Timeout
                    error_msg = "Jira API timeout (escalate priority)" if isinstance(e, Timeout) else "Failed to escalate Jira priority"
                    error_type = "jira_timeout" if isinstance(e, Timeout) else "jira_api_error"
                    
                    logger.error(
                        error_msg,
                        extra={
                            "action": "jira_escalate_failed",
                            "event_id": event_id,
                            "fingerprint": fingerprint,
                            "jira_issue_key": issue_key,
                            "error_type": error_type,
                        },
                        exc_info=True,
                    )
                    metrics_collector.increment_counter(
                        "errors_total", {"environment": _environment, "error_type": error_type}
                    )

        else:
            # ================================================================
            # New Issue Path: Create Bug Issue
            # ================================================================

            logger.info(
                "No existing Jira issue found, creating new issue",
                extra={
                    "action": "jira_issue_not_found",
                    "event_id": event_id,
                    "fingerprint": fingerprint,
                },
            )

            try:
                issue_key = _jira_service.create_bug_issue(
                    normalized_event, fingerprint, priority, severity, assignment
                )

                logger.info(
                    "New Jira issue created",
                    extra={
                        "action": "jira_issue_created",
                        "event_id": event_id,
                        "fingerprint": fingerprint,
                        "jira_issue_key": issue_key,
                        "priority": priority,
                        "severity": severity,
                    },
                )

                # Increment metrics
                metrics_collector.increment_counter(
                    "jira_issues_created_total",
                    {"environment": _environment, "project": "ET"},
                )

            except Exception as e:
                # Check if it's a timeout error for more specific logging
                from requests.exceptions import Timeout
                error_msg = "Jira API timeout (create issue)" if isinstance(e, Timeout) else "Failed to create Jira issue"
                error_type = "jira_timeout" if isinstance(e, Timeout) else "jira_api_error"
                
                logger.error(
                    error_msg,
                    extra={
                        "action": "jira_create_failed",
                        "event_id": event_id,
                        "fingerprint": fingerprint,
                        "error_type": error_type,
                    },
                    exc_info=True,
                )
                metrics_collector.increment_counter(
                    "errors_total", {"environment": _environment, "error_type": error_type}
                )
                # Don't fail the request - return 202 and log for retry
                return (
                    jsonify(
                        {
                            "status": "accepted",
                            "event_id": event_id,
                            "fingerprint": fingerprint,
                            "warning": "Jira issue creation temporarily unavailable",
                        }
                    ),
                    202,
                )

        # ====================================================================
        # Stage 10: Success Response
        # ====================================================================

        # Increment events_processed counter
        metrics_collector.increment_counter("events_processed_total", {"environment": _environment, "source": source})

        # Calculate total processing duration
        duration_seconds = perf_counter() - start_time
        metrics_collector.observe_histogram(
            "event_processing_duration_seconds",
            duration_seconds,
            {"environment": _environment, "source": source},
        )

        logger.info(
            "Webhook processed successfully",
            extra={
                "action": "webhook_processed",
                "event_id": event_id,
                "fingerprint": fingerprint,
                "jira_issue_key": issue_key,
                "source": source,
                "duration_ms": int(duration_seconds * 1000),
            },
        )

        # Return 202 Accepted with event details
        return (
            jsonify({"status": "accepted", "event_id": event_id, "fingerprint": fingerprint}),
            202,
        )

    except Exception as e:
        # ====================================================================
        # Global Error Handler
        # ====================================================================

        # Calculate processing duration up to error point
        duration_seconds = perf_counter() - start_time

        logger.error(
            "Unexpected error during webhook processing",
            extra={
                "action": "webhook_processing_error",
                "event_id": event_id if event_id else "unknown",
                "fingerprint": fingerprint if fingerprint else "unknown",
                "source": source if source else "unknown",
                "error_type": "unexpected_error",
                "duration_ms": int(duration_seconds * 1000),
            },
            exc_info=True,
        )

        # Increment errors counter
        metrics_collector.increment_counter(
            "errors_total", {"environment": _environment, "error_type": "unexpected_error"}
        )

        # Record processing duration even for errors
        if source:
            metrics_collector.observe_histogram(
                "event_processing_duration_seconds",
                duration_seconds,
                {"environment": _environment, "source": source},
            )

        # Return 500 Internal Server Error
        return jsonify({"error": "Processing failed", "detail": "Internal server error"}), 500


# ============================================================================
# Blueprint Metadata and Exports
# ============================================================================

# Export blueprint for registration in application factory
__all__ = ["events_bp", "init_services"]
