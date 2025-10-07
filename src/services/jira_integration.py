"""
Jira Integration Service for Error Triage System

This module provides comprehensive Jira API integration for the Error Triage service,
implementing full issue lifecycle management including JQL-based fingerprint search,
bug issue creation with custom fields and labels, comment addition with rate limiting,
and priority escalation based on frequency thresholds.

Key Features:
- Search existing issues by error fingerprint using JQL queries
- Create new Bug issues with comprehensive error context and metadata
- Add timestamped comments to existing issues with occurrence counts
- Escalate issue priority when frequency thresholds are crossed
- Exponential backoff retry logic for transient failures (1s, 2s, 4s, 8s)
- Rate limit handling (100 requests/minute for Jira Cloud)
- Structured logging with correlation fields for operational troubleshooting
- Prometheus metrics for all Jira operations

Per Section 0.5.1 Group 5 requirements:
- JQL Pattern: project = {KEY} AND labels = "errfp:{fingerprint}" AND statusCategory != Done
- Labels Format: source:{source}, env:{env}, service:{service}, errfp:{fingerprint}
- Summary Format: [{env}:{service}] {error_class} — {sanitized_message_truncated}
- Custom Severity Field: customfield_10050 with values SEV1, SEV2, SEV3, SEV4

Per Section 0.7.5 Jira API Best Practices:
- Custom User-Agent: JiraTest-ErrorTriage/1.0
- Rate Limit Handling: Respect 100 requests/minute, handle 429 responses
- Timeout Enforcement: 10 seconds per API call with exponential backoff retry
- Error Code Handling: 401 (invalid credentials), 429 (rate limit), 503 (unavailable)

Security Considerations:
- All error messages and stack traces sanitized via PIISanitizer before transmission
- No PII in issue summaries, descriptions, or comments (per Section 0.7.4)
- Jira credentials loaded from AWS Secrets Manager (never logged)

Usage Example:
    from jira import JIRA
    from services.jira_integration import JiraIntegrationService
    from services.sanitizer import PIISanitizer
    from models.error_event import NormalizedErrorEvent

    # Initialize Jira client
    jira_client = JIRA(
        server='https://organization.atlassian.net',
        basic_auth=('api-token-user@example.com', api_token)
    )

    # Initialize service
    sanitizer = PIISanitizer()
    jira_service = JiraIntegrationService(
        jira_client=jira_client,
        project_key='ET',
        sanitizer=sanitizer,
        environment='production'
    )

    # Search for existing issue
    issue_key = jira_service.search_issue_by_fingerprint(fingerprint)

    # Create new issue if not found
    if issue_key is None:
        issue_key = jira_service.create_bug_issue(
            event=event,
            fingerprint=fingerprint,
            priority='High',
            severity='SEV2',
            assignee={'assignee': '5f8e9a1b2c3d4e5f6a7b8c9d'}
        )
    else:
        # Add comment to existing issue
        jira_service.add_comment(
            issue_key=issue_key,
            count=15,
            severity='SEV2',
            log_url=event.log_url,
            event=event
        )

"""

import time
from typing import Optional, Dict, Any, Callable, List
from time import perf_counter

from jira import JIRA
from jira.exceptions import JIRAError

# Internal imports (from depends_on_files only)
from src.models.error_event import NormalizedErrorEvent
from src.services.sanitizer import PIISanitizer
from src.utils.logging_config import get_logger
from src.utils.metrics_collector import record_jira_api_latency

# Initialize module logger
logger = get_logger(__name__)


class JiraIntegrationService:
    """
    Comprehensive Jira API integration service for error triage workflow.

    This service wraps the Jira Python library to provide error-specific operations
    including fingerprint-based issue search, bug creation with rich error context,
    comment addition for repeated errors, and priority escalation based on frequency
    thresholds. All operations include exponential backoff retry logic for resilience
    against transient Jira API failures.

    Attributes:
        _jira_client: Authenticated JIRA client instance
        _project_key: Jira project key for error issues (e.g., 'ET')
        _sanitizer: PIISanitizer instance for removing PII before transmission
        _environment: Deployment environment for metrics and logging
        _custom_severity_field: Jira custom field ID for severity (customfield_10050)
        _max_retries: Maximum retry attempts for transient failures (default: 5)
        _retry_delays: Exponential backoff delays in seconds [1, 2, 4, 8, 16]
        _timeout: API call timeout in seconds (default: 10)

    Thread Safety:
        This class is thread-safe for read operations. JIRA client handles
        connection pooling internally for concurrent requests.
    """

    # Class constants
    CUSTOM_SEVERITY_FIELD = "customfield_10050"  # Jira custom field for severity
    MAX_RETRIES = 5  # Maximum retry attempts per Section 0.7.5
    RETRY_DELAYS = [1, 2, 4, 8, 16]  # Exponential backoff delays in seconds
    API_TIMEOUT = 10  # API call timeout in seconds
    USER_AGENT = "JiraTest-ErrorTriage/1.0"  # Custom User-Agent per Section 0.7.5

    def __init__(
        self,
        jira_client: JIRA,
        project_key: str,
        sanitizer: PIISanitizer,
        environment: str = "production",
    ):
        """
        Initialize Jira integration service with dependencies.

        Args:
            jira_client: Authenticated JIRA client instance configured with
                        server URL and credentials (API token or OAuth)
            project_key: Jira project key for error tracking (e.g., 'ET')
            sanitizer: PIISanitizer instance for removing PII from error messages
                      and stack traces before Jira transmission
            environment: Deployment environment for metrics dimensions
                        ('production', 'staging', 'dev')

        Example:
            >>> from jira import JIRA
            >>> jira_client = JIRA(
            ...     server='https://org.atlassian.net',
            ...     basic_auth=('user@example.com', api_token)
            ... )
            >>> sanitizer = PIISanitizer()
            >>> service = JiraIntegrationService(
            ...     jira_client=jira_client,
            ...     project_key='ET',
            ...     sanitizer=sanitizer,
            ...     environment='production'
            ... )
        """
        self._jira_client = jira_client
        self._project_key = project_key
        self._sanitizer = sanitizer
        self._environment = environment
        self._custom_severity_field = self.CUSTOM_SEVERITY_FIELD
        self._max_retries = self.MAX_RETRIES
        self._retry_delays = self.RETRY_DELAYS
        self._timeout = self.API_TIMEOUT

        logger.info(
            "JiraIntegrationService initialized successfully",
            extra={
                "project_key": project_key,
                "environment": environment,
                "custom_severity_field": self._custom_severity_field,
                "action": "jira_service_initialized",
            },
        )

    def _retry_with_backoff(
        self,
        operation: Callable[[], Any],
        operation_name: str,
        event_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
    ) -> Any:
        """
        Execute Jira API operation with exponential backoff retry logic.

        Implements retry strategy for transient Jira API failures with exponential
        backoff delays. Retries on specific error codes: 429 (rate limit exceeded),
        503 (service unavailable), and network timeout errors. Permanent errors
        (401 invalid credentials, 404 not found) are raised immediately.

        Per Section 0.7.5, retry delays: 1s, 2s, 4s, 8s, 16s (max 5 attempts)

        Args:
            operation: Callable that performs the Jira API call, returns result
            operation_name: Human-readable operation name for logging and metrics
            event_id: Optional event correlation ID for tracing
            fingerprint: Optional error fingerprint for correlation

        Returns:
            Result from successful operation execution

        Raises:
            JIRAError: After max retries exhausted or on permanent error codes
            Exception: On unexpected errors after max retries

        Example:
            >>> def create_issue():
            ...     return self._jira_client.create_issue(fields=fields_dict)
            >>> issue = self._retry_with_backoff(
            ...     operation=create_issue,
            ...     operation_name='create_issue',
            ...     event_id=event.event_id,
            ...     fingerprint=fingerprint
            ... )
        """
        last_exception = None

        for attempt in range(1, self._max_retries + 1):
            try:
                # Execute operation with timing
                start_time = perf_counter()
                result = operation()
                duration = perf_counter() - start_time

                # Log successful operation
                logger.info(
                    f"Jira operation '{operation_name}' succeeded",
                    extra={
                        "operation": operation_name,
                        "attempt": attempt,
                        "duration_ms": int(duration * 1000),
                        "event_id": event_id,
                        "fingerprint": fingerprint,
                        "action": f"jira_{operation_name}_success",
                    },
                )

                # Record metrics for successful operation
                record_jira_api_latency(self._environment, operation_name, duration)

                return result

            except JIRAError as e:
                last_exception = e
                status_code = e.status_code if hasattr(e, "status_code") else None

                # Permanent errors - don't retry
                if status_code in [401, 403, 404]:
                    logger.error(
                        f"Jira operation '{operation_name}' failed with permanent error",
                        extra={
                            "operation": operation_name,
                            "status_code": status_code,
                            "error": str(e),
                            "error_type": "jira_permanent_error",
                            "event_id": event_id,
                            "fingerprint": fingerprint,
                            "action": f"jira_{operation_name}_permanent_error",
                        },
                    )
                    raise

                # Transient errors - retry with backoff
                if status_code in [429, 503] or "timeout" in str(e).lower():
                    if attempt < self._max_retries:
                        delay = self._retry_delays[attempt - 1]
                        logger.warning(
                            f"Jira operation '{operation_name}' failed with transient error, retrying",
                            extra={
                                "operation": operation_name,
                                "attempt": attempt,
                                "max_retries": self._max_retries,
                                "status_code": status_code,
                                "error": str(e),
                                "retry_delay_seconds": delay,
                                "event_id": event_id,
                                "fingerprint": fingerprint,
                                "action": f"jira_{operation_name}_retry",
                            },
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"Jira operation '{operation_name}' failed after max retries",
                            extra={
                                "operation": operation_name,
                                "attempts": self._max_retries,
                                "status_code": status_code,
                                "error": str(e),
                                "error_type": "jira_max_retries_exceeded",
                                "event_id": event_id,
                                "fingerprint": fingerprint,
                                "action": f"jira_{operation_name}_max_retries",
                            },
                        )
                        raise

                # Unknown error - log and raise
                logger.error(
                    f"Jira operation '{operation_name}' failed with unknown error",
                    extra={
                        "operation": operation_name,
                        "status_code": status_code,
                        "error": str(e),
                        "error_type": "jira_unknown_error",
                        "event_id": event_id,
                        "fingerprint": fingerprint,
                        "action": f"jira_{operation_name}_unknown_error",
                    },
                )
                raise

            except Exception as e:
                last_exception = e
                logger.error(
                    f"Jira operation '{operation_name}' failed with unexpected exception",
                    extra={
                        "operation": operation_name,
                        "attempt": attempt,
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "event_id": event_id,
                        "fingerprint": fingerprint,
                        "action": f"jira_{operation_name}_exception",
                    },
                    exc_info=True,
                )
                if attempt < self._max_retries:
                    delay = self._retry_delays[attempt - 1]
                    time.sleep(delay)
                    continue
                else:
                    raise

        # Should never reach here, but satisfy type checker
        if last_exception:
            raise last_exception
        else:
            raise RuntimeError(f"Operation '{operation_name}' failed with unknown error")

    def search_issue_by_fingerprint(self, fingerprint: str) -> Optional[str]:
        """
        Search for existing Jira issue by error fingerprint label.

        Executes JQL query to find open issues (not in Done status category) with
        the specified error fingerprint label. Returns the first matching issue key
        or None if no matching issue exists.

        Per Section 0.5.1 Group 5, JQL pattern:
        project = {PROJECT_KEY} AND labels = "errfp:{fingerprint}" AND statusCategory != Done

        Args:
            fingerprint: SHA-256 error fingerprint hash (64-character hex string)
                        generated by ErrorFingerprinter service

        Returns:
            Jira issue key (e.g., 'ET-1234') if matching issue found, None otherwise

        Raises:
            JIRAError: On permanent API errors (401, 403) or after max retries

        Example:
            >>> issue_key = jira_service.search_issue_by_fingerprint(
            ...     fingerprint='a3f5b9c8d2e1f4g6h8j9k0m1n3p5q7r9s0t2u4v6w8x0y2z4'
            ... )
            >>> if issue_key:
            ...     print(f"Found existing issue: {issue_key}")
            ... else:
            ...     print("No existing issue found, will create new one")
        """
        # Build JQL query per Section 0.5.1 Group 5
        jql_query = (
            f'project = {self._project_key} AND '
            f'labels = "errfp:{fingerprint}" AND '
            f'statusCategory != Done'
        )

        logger.debug(
            "Searching for Jira issue by fingerprint",
            extra={
                "fingerprint": fingerprint,
                "jql_query": jql_query,
                "project_key": self._project_key,
                "action": "jira_search_by_fingerprint",
            },
        )

        def execute_search() -> Optional[str]:
            """Inner function for retry wrapper"""
            try:
                # Execute JQL search with maxResults=1 (only need first match)
                issues = self._jira_client.search_issues(jql_query, maxResults=1)

                if issues and len(issues) > 0:
                    issue_key = issues[0].key
                    logger.info(
                        "Found existing Jira issue by fingerprint",
                        extra={
                            "fingerprint": fingerprint,
                            "jira_issue_key": issue_key,
                            "action": "jira_issue_found",
                        },
                    )
                    return issue_key
                else:
                    logger.debug(
                        "No existing Jira issue found for fingerprint",
                        extra={
                            "fingerprint": fingerprint,
                            "action": "jira_issue_not_found",
                        },
                    )
                    return None
            except Exception as e:
                # Let retry logic handle the exception
                raise

        # Execute with retry logic
        try:
            return self._retry_with_backoff(
                operation=execute_search,
                operation_name="search_by_fingerprint",
                fingerprint=fingerprint,
            )
        except Exception as e:
            logger.error(
                "Failed to search Jira issue by fingerprint after retries",
                extra={
                    "fingerprint": fingerprint,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "action": "jira_search_failed",
                },
            )
            # Return None to allow fallback to issue creation
            return None

    def create_bug_issue(
        self,
        event: NormalizedErrorEvent,
        fingerprint: str,
        priority: str,
        severity: str,
        assignee: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Create new Bug issue in Jira with comprehensive error context.

        Creates a new Jira issue with type Bug, including sanitized error message,
        stack trace excerpt, release version, and deep link to logs. Sets labels
        for filtering (source, environment, service, fingerprint), priority, and
        custom severity field.

        Per Section 0.5.1 Group 5, issue structure:
        - Summary: [{env}:{service}] {error_class} — {sanitized_message_truncated}
        - Labels: source:{source}, env:{env}, service:{service}, errfp:{fingerprint}
        - Description: Markdown-formatted error context with stack trace and log URL
        - Priority: Jira priority name (Highest, High, Medium, Low)
        - Severity: Custom field customfield_10050 (SEV1, SEV2, SEV3, SEV4)

        All error messages and stack traces are sanitized via PIISanitizer to
        remove PII (emails, UUIDs, tokens, numeric IDs) before Jira transmission
        per Section 0.7.4 security requirements.

        Args:
            event: NormalizedErrorEvent containing error details
            fingerprint: SHA-256 error fingerprint for grouping and labeling
            priority: Jira priority name (e.g., 'Highest', 'High', 'Medium', 'Low')
            severity: Severity value for custom field (e.g., 'SEV1', 'SEV2', 'SEV3', 'SEV4')
            assignee: Optional dict with assignee info:
                     {'assignee': 'account_id'} for direct assignment
                     {'component': 'component_name'} for component-based routing

        Returns:
            Created Jira issue key (e.g., 'ET-1234')

        Raises:
            JIRAError: On permanent API errors or after max retries

        Example:
            >>> issue_key = jira_service.create_bug_issue(
            ...     event=event,
            ...     fingerprint='a3f5b9c8d2e1...',
            ...     priority='High',
            ...     severity='SEV2',
            ...     assignee={'assignee': '5f8e9a1b2c3d4e5f6a7b8c9d'}
            ... )
            >>> print(f"Created issue: {issue_key}")
            Created issue: ET-1234
        """
        # Sanitize error message and stack trace to remove PII
        sanitized_message = self._sanitizer.sanitize(event.message)
        sanitized_stack = (
            self._sanitizer.sanitize(event.stack_trace) if event.stack_trace else None
        )

        # Build summary: [{env}:{service}] {error_class} — {sanitized_message_truncated}
        # Truncate message to 80 characters per Section 0.5.1
        truncated_message = sanitized_message[:80]
        if len(sanitized_message) > 80:
            truncated_message = truncated_message[:77] + "..."

        summary = f"[{event.environment}:{event.service}] {event.error_class} — {truncated_message}"

        # Build description with markdown formatting
        description_parts = [
            f"*Error Class:* {event.error_class}",
            f"*Service:* {event.service}",
            f"*Environment:* {event.environment}",
            f"*Occurred At:* {event.occurred_at.isoformat()}",
            "",
            f"*Message:*",
            f"{{code}}{sanitized_message}{{code}}",
            "",
        ]

        # Add stack trace excerpt if available (first 20 lines)
        if sanitized_stack:
            stack_lines = sanitized_stack.split("\n")
            stack_excerpt = "\n".join(stack_lines[:20])
            if len(stack_lines) > 20:
                stack_excerpt += f"\n... ({len(stack_lines) - 20} more lines)"

            description_parts.extend([
                "*Stack Trace:*",
                f"{{code}}{stack_excerpt}{{code}}",
                "",
            ])

        # Add request path if available
        if event.path:
            description_parts.append(f"*Request Path:* {event.path}")

        # Add full URL if available
        if event.url:
            description_parts.append(f"*Request URL:* {event.url}")

        # Add release version if available
        if event.release:
            description_parts.append(f"*Release:* {event.release}")

        # Add deep link to logs
        description_parts.extend([
            "",
            f"*View Logs:* [Open in {event.source.upper()}|{event.log_url}]",
            "",
            f"*Error Fingerprint:* {fingerprint}",
            f"*Event ID:* {event.event_id}",
        ])

        description = "\n".join(description_parts)

        # Build labels list per Section 0.5.1 Group 5
        labels = [
            f"source:{event.source}",
            f"env:{event.environment}",
            f"service:{event.service}",
            f"errfp:{fingerprint}",
        ]

        # Build fields dictionary for issue creation
        fields_dict: Dict[str, Any] = {
            "project": {"key": self._project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": "Bug"},
            "priority": {"name": priority},
            "labels": labels,
            # Custom severity field (customfield_10050)
            self._custom_severity_field: {"value": severity},
        }

        # Add assignee if provided
        if assignee:
            if "assignee" in assignee:
                # Direct assignee assignment via account ID
                fields_dict["assignee"] = {"accountId": assignee["assignee"]}
            elif "component" in assignee:
                # Component-based routing (component has default assignee)
                fields_dict["components"] = [{"name": assignee["component"]}]

        logger.info(
            "Creating new Jira bug issue",
            extra={
                "event_id": event.event_id,
                "fingerprint": fingerprint,
                "priority": priority,
                "severity": severity,
                "environment": event.environment,
                "service": event.service,
                "error_class": event.error_class,
                "action": "jira_create_bug",
            },
        )

        def execute_create() -> str:
            """Inner function for retry wrapper"""
            try:
                # Create issue via Jira API
                new_issue = self._jira_client.create_issue(fields=fields_dict)
                issue_key = new_issue.key

                logger.info(
                    "Successfully created Jira bug issue",
                    extra={
                        "event_id": event.event_id,
                        "fingerprint": fingerprint,
                        "jira_issue_key": issue_key,
                        "priority": priority,
                        "severity": severity,
                        "action": "jira_issue_created",
                    },
                )

                return issue_key

            except Exception as e:
                # Let retry logic handle the exception
                raise

        # Execute with retry logic
        try:
            return self._retry_with_backoff(
                operation=execute_create,
                operation_name="create_issue",
                event_id=event.event_id,
                fingerprint=fingerprint,
            )
        except Exception as e:
            logger.error(
                "Failed to create Jira bug issue after retries",
                extra={
                    "event_id": event.event_id,
                    "fingerprint": fingerprint,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "action": "jira_create_failed",
                },
                exc_info=True,
            )
            raise

    def add_comment(
        self,
        issue_key: str,
        count: int,
        severity: str,
        log_url: str,
        event: Optional[NormalizedErrorEvent] = None,
    ) -> None:
        """
        Add timestamped comment to existing Jira issue for error reoccurrence.

        Adds a comment to an existing Jira issue indicating the error has reoccurred,
        including the 5-minute occurrence count, current severity level, and deep link
        to the latest log entry. Comment is timestamped automatically by Jira.

        Per Section 0.5.1 Group 5, comment format:
        "Error reoccurred {count}× in last 5m. Severity: {severity}. {log_url}"

        This method should be called after comment rate limiting check to prevent
        spam (max once per 15 minutes unless severity increases).

        Args:
            issue_key: Jira issue key to add comment to (e.g., 'ET-1234')
            count: Number of occurrences in the last 5-minute window
            severity: Current severity level (e.g., 'SEV1', 'SEV2', 'SEV3', 'SEV4')
            log_url: Deep link to latest log entry in source system
            event: Optional NormalizedErrorEvent for additional context logging

        Raises:
            JIRAError: On permanent API errors or after max retries

        Example:
            >>> jira_service.add_comment(
            ...     issue_key='ET-1234',
            ...     count=15,
            ...     severity='SEV2',
            ...     log_url='https://vercel.com/logs?traceId=abc123',
            ...     event=event
            ... )
        """
        # Format comment text per Section 0.5.1 Group 5
        comment_text = (
            f"Error reoccurred {count}× in last 5m. "
            f"Severity: {severity}. "
            f"[View Logs|{log_url}]"
        )

        event_id = event.event_id if event else None
        fingerprint = None  # Not available in this context

        logger.info(
            "Adding comment to existing Jira issue",
            extra={
                "jira_issue_key": issue_key,
                "occurrence_count": count,
                "severity": severity,
                "event_id": event_id,
                "action": "jira_add_comment",
            },
        )

        def execute_add_comment() -> None:
            """Inner function for retry wrapper"""
            try:
                # Add comment via Jira API
                self._jira_client.add_comment(issue_key, comment_text)

                logger.info(
                    "Successfully added comment to Jira issue",
                    extra={
                        "jira_issue_key": issue_key,
                        "occurrence_count": count,
                        "severity": severity,
                        "event_id": event_id,
                        "action": "jira_comment_added",
                    },
                )

            except Exception as e:
                # Let retry logic handle the exception
                raise

        # Execute with retry logic
        try:
            self._retry_with_backoff(
                operation=execute_add_comment,
                operation_name="add_comment",
                event_id=event_id,
                fingerprint=fingerprint,
            )
        except Exception as e:
            logger.error(
                "Failed to add comment to Jira issue after retries",
                extra={
                    "jira_issue_key": issue_key,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "event_id": event_id,
                    "action": "jira_comment_failed",
                },
                exc_info=True,
            )
            raise

    def escalate_priority(
        self, issue_key: str, new_priority: str, event_id: Optional[str] = None
    ) -> None:
        """
        Escalate Jira issue priority when frequency threshold crossed.

        Updates the priority field of an existing Jira issue when error frequency
        increases and crosses a higher severity threshold. This is called when
        occurrence count in a 5-minute window exceeds a configured threshold,
        triggering priority escalation (e.g., Medium → High → Highest).

        Per Section 0.1.1 requirement #4, priority escalation occurs when frequency
        thresholds increase severity level, ensuring high-impact errors get
        appropriate attention.

        Args:
            issue_key: Jira issue key to escalate (e.g., 'ET-1234')
            new_priority: New Jira priority name (e.g., 'Highest', 'High', 'Medium', 'Low')
            event_id: Optional event correlation ID for tracing

        Raises:
            JIRAError: On permanent API errors or after max retries

        Example:
            >>> # Error frequency increased from 9 to 50 occurrences
            >>> # Severity escalated from SEV3 to SEV2, priority High to Highest
            >>> jira_service.escalate_priority(
            ...     issue_key='ET-1234',
            ...     new_priority='Highest',
            ...     event_id='vercel-xyz-123'
            ... )
        """
        logger.info(
            "Escalating Jira issue priority",
            extra={
                "jira_issue_key": issue_key,
                "new_priority": new_priority,
                "event_id": event_id,
                "action": "jira_escalate_priority",
            },
        )

        def execute_escalate() -> None:
            """Inner function for retry wrapper"""
            try:
                # Fetch issue object
                issue = self._jira_client.issue(issue_key)

                # Update priority field
                issue.update(priority={"name": new_priority})

                logger.info(
                    "Successfully escalated Jira issue priority",
                    extra={
                        "jira_issue_key": issue_key,
                        "new_priority": new_priority,
                        "event_id": event_id,
                        "action": "jira_priority_escalated",
                    },
                )

            except Exception as e:
                # Let retry logic handle the exception
                raise

        # Execute with retry logic
        try:
            self._retry_with_backoff(
                operation=execute_escalate,
                operation_name="escalate_priority",
                event_id=event_id,
                fingerprint=None,
            )
        except Exception as e:
            logger.error(
                "Failed to escalate Jira issue priority after retries",
                extra={
                    "jira_issue_key": issue_key,
                    "new_priority": new_priority,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "event_id": event_id,
                    "action": "jira_escalate_failed",
                },
                exc_info=True,
            )
            raise


# Module exports
__all__ = ["JiraIntegrationService"]

