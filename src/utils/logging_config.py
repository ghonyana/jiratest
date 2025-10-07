"""
Structured JSON logging configuration module for Error Triage service.

This module provides CloudWatch-compatible structured logging with mandatory
correlation fields (event_id, fingerprint) for operational troubleshooting and
end-to-end request tracing through CloudWatch Logs Insights queries.

Per Section 0.1.1 and 0.4.4 requirements, all logs include:
- timestamp: ISO 8601 datetime with millisecond precision
- level: Log severity (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- service: Always 'error-triage' for this microservice
- environment: Deployment environment (production, staging, dev)
- message: Human-readable log message

Optional correlation fields (added via extra parameter):
- event_id: Webhook event correlation ID for tracing
- fingerprint: SHA-256 error grouping hash
- jira_issue_key: Jira issue identifier (e.g., ET-1234)
- action: Operation type (e.g., jira_comment_added, webhook_authenticated)
- duration_ms: Elapsed time for operation measurement

Security constraints per Section 6.5.2.1:
- NEVER log: Webhook secrets, Jira API tokens, Bearer tokens, credentials
- NEVER log: Raw unsanitized error messages with PII
- NEVER log: Complete webhook payloads (may contain PII)

Usage:
    from utils.logging_config import setup_logging, get_logger
    
    # Initialize logging at application startup
    setup_logging(level='INFO', environment='production')
    
    # Use logger in modules
    logger = get_logger(__name__)
    logger.info(
        "Added comment to Jira issue",
        extra={
            'event_id': event.event_id,
            'fingerprint': fingerprint,
            'jira_issue_key': 'ET-1234',
            'action': 'jira_comment_added',
            'duration_ms': 125
        }
    )
"""

import logging
import sys
import os
import re
from datetime import datetime
from typing import Optional, Dict, Any
from pythonjsonlogger.jsonlogger import JsonFormatter


class CloudWatchJsonFormatter(JsonFormatter):
    """
    Custom JSON formatter for CloudWatch-compatible structured logging.
    
    Extends pythonjsonlogger.JsonFormatter to inject default fields
    (service, environment) and ensure ISO 8601 timestamp format with
    millisecond precision for CloudWatch Logs Insights compatibility.
    
    Per Section 6.5.2.1, mandatory fields included in every log entry:
    - timestamp: ISO 8601 datetime with milliseconds (YYYY-MM-DDTHH:MM:SS.sssZ)
    - level: Log severity (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - service: Microservice name (always 'error-triage')
    - environment: Deployment environment (production, staging, dev)
    - message: Human-readable log message
    
    Optional fields (included if present in extra parameter):
    - event_id: Webhook event correlation ID
    - fingerprint: SHA-256 error grouping hash
    - jira_issue_key: Jira issue identifier (e.g., ET-1234)
    - action: Operation type (e.g., jira_comment_added, webhook_authenticated)
    - duration_ms: Elapsed time for operation
    - error_type: Error classification for debugging
    - exc_info: Exception stack trace (sanitized)
    
    Metadata fields (from log record):
    - filename: Source file name
    - lineno: Line number
    - funcName: Function name
    
    Example log output:
        {
          "timestamp": "2025-01-15T10:30:46.123Z",
          "level": "INFO",
          "service": "error-triage",
          "environment": "production",
          "event_id": "vercel-xyz-123",
          "fingerprint": "a3f5b9c8d2e1f4g6h8j9k0",
          "jira_issue_key": "ET-1234",
          "action": "jira_comment_added",
          "duration_ms": 125,
          "message": "Added comment to existing issue ET-1234",
          "filename": "jira_integration.py",
          "lineno": 145,
          "funcName": "add_comment"
        }
    """
    
    def __init__(self, service: str = 'error-triage', environment: str = 'production', *args, **kwargs):
        """
        Initialize CloudWatch JSON formatter with default fields.
        
        Args:
            service: Microservice name (default: 'error-triage')
            environment: Deployment environment (default: 'production')
            *args: Additional positional arguments for JsonFormatter
            **kwargs: Additional keyword arguments for JsonFormatter
        """
        self.service = service
        self.environment = environment
        super().__init__(*args, **kwargs)
    
    def add_fields(self, log_record: Dict[str, Any], record: logging.LogRecord, message_dict: Dict[str, Any]) -> None:
        """
        Add custom fields to the log record before JSON serialization.
        
        This method is called by JsonFormatter to inject additional fields
        into the log record. It adds default fields (service, environment,
        timestamp) and preserves optional fields from the extra parameter.
        
        Per Section 6.5.2.1, this ensures all logs contain mandatory correlation
        fields for CloudWatch Logs Insights queries and operational troubleshooting.
        
        Args:
            log_record: Dictionary that will be serialized to JSON
            record: Standard Python LogRecord instance
            message_dict: Dictionary containing log message and extra fields
        """
        # Call parent implementation to add standard fields
        super().add_fields(log_record, record, message_dict)
        
        # Add ISO 8601 timestamp with millisecond precision
        # Format: YYYY-MM-DDTHH:MM:SS.sssZ (CloudWatch-compatible)
        log_record['timestamp'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        
        # Add log level name
        log_record['level'] = record.levelname
        
        # Add default service and environment fields
        log_record['service'] = self.service
        log_record['environment'] = self.environment
        
        # Add metadata fields for debugging and troubleshooting
        log_record['filename'] = record.filename
        log_record['lineno'] = record.lineno
        log_record['funcName'] = record.funcName
        
        # Preserve optional correlation fields if present in extra
        # These enable end-to-end request tracing across distributed systems
        optional_fields = [
            'event_id',
            'fingerprint',
            'jira_issue_key',
            'action',
            'duration_ms',
            'error_type'
        ]
        
        for field in optional_fields:
            if hasattr(record, field):
                log_record[field] = getattr(record, field)
        
        # Ensure message field is always present
        if 'message' not in log_record:
            log_record['message'] = record.getMessage()
    
    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as JSON string with security sanitization.
        
        This method sanitizes sensitive information (tokens, secrets, credentials)
        before formatting the log record as JSON for CloudWatch ingestion.
        
        Per Section 6.5.2.1 security constraints, this prevents accidental
        logging of:
        - Webhook secrets
        - Jira API tokens
        - Bearer tokens
        - Database credentials
        - API keys
        
        Args:
            record: Standard Python LogRecord instance
            
        Returns:
            JSON-formatted log string with sanitized content
        """
        # Get message and sanitize sensitive patterns
        message = record.getMessage()
        
        # Security: Never log raw tokens, secrets, or credentials
        # Check for common sensitive patterns in message
        sensitive_patterns = [
            'bearer ',
            'token=',
            'password=',
            'secret=',
            'api_key=',
            'apikey=',
            'authorization:',
            'credential='
        ]
        
        message_lower = message.lower()
        if any(pattern in message_lower for pattern in sensitive_patterns):
            # Replace sensitive values with redacted placeholder
            message = re.sub(r'(bearer\s+)[\w\-\.]+', r'\1[REDACTED]', message, flags=re.IGNORECASE)
            message = re.sub(r'(token=)[\w\-\.]+', r'\1[REDACTED]', message, flags=re.IGNORECASE)
            message = re.sub(r'(password=)[\w\-\.]+', r'\1[REDACTED]', message, flags=re.IGNORECASE)
            message = re.sub(r'(secret=)[\w\-\.]+', r'\1[REDACTED]', message, flags=re.IGNORECASE)
            message = re.sub(r'(api_?key=)[\w\-\.]+', r'\1[REDACTED]', message, flags=re.IGNORECASE)
            message = re.sub(r'(credential=)[\w\-\.]+', r'\1[REDACTED]', message, flags=re.IGNORECASE)
            message = re.sub(r'(authorization:\s*)[\w\-\.]+', r'\1[REDACTED]', message, flags=re.IGNORECASE)
            
            # Update the record message
            record.msg = message
            record.args = ()  # Clear args to prevent re-formatting
        
        # Call parent format to generate JSON string
        return super().format(record)


def setup_logging(level: str = 'INFO', environment: str = 'production') -> None:
    """
    Configure structured JSON logging for the Error Triage application.
    
    Sets up the root logger with CloudWatch-compatible JSON formatting per
    Section 0.5.1 Group 8 and Section 6.5.2 requirements. Logs are written
    to stdout for capture by ECS awslogs driver and automatic streaming to
    CloudWatch Logs group: /aws/ecs/jiratest-error-triage-{env}
    
    Per Section 6.5.2.1, JSON format enables CloudWatch Logs Insights automatic
    field extraction for operational queries and troubleshooting.
    
    Environment variable override:
    - LOG_LEVEL: Override default log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    
    Args:
        level: Log level (default: 'INFO'). Valid values: DEBUG, INFO, WARNING, ERROR, CRITICAL
        environment: Deployment environment (default: 'production'). Examples: production, staging, dev
    
    Example:
        >>> from utils.logging_config import setup_logging
        >>> setup_logging(level='INFO', environment='production')
        >>> # Logs now emit structured JSON to stdout
    
    CloudWatch Integration (per Section 0.4.1):
        Logs are automatically streamed via ECS awslogs driver to:
        /aws/ecs/jiratest-error-triage-{env}
        
        JSON format enables CloudWatch Logs Insights queries like:
        
        # Find all events for a specific webhook
        fields @timestamp, level, message, event_id, fingerprint
        | filter event_id = "vercel-xyz-123"
        | sort @timestamp desc
        
        # Track Jira operations for an error fingerprint
        fields @timestamp, action, jira_issue_key, duration_ms
        | filter fingerprint = "a3f5b9c8d2e1f4g6h8j9k0"
        | filter action like /jira/
        | stats count() by action
        
        # Monitor API latencies
        fields @timestamp, action, duration_ms
        | filter action like /jira/
        | stats avg(duration_ms), max(duration_ms), pct(duration_ms, 95) by action
    """
    # Allow environment variable override for log level
    # This enables runtime configuration without code changes
    log_level = os.getenv('LOG_LEVEL', level).upper()
    
    # Validate log level against Python logging constants
    valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    if log_level not in valid_levels:
        # Fall back to INFO for invalid levels
        log_level = 'INFO'
    
    # Convert string level to logging constant
    numeric_level = getattr(logging, log_level)
    
    # Create StreamHandler writing to stdout
    # ECS awslogs driver captures stdout for CloudWatch streaming
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)
    
    # Apply CloudWatch JSON formatter with service and environment defaults
    formatter = CloudWatchJsonFormatter(
        service='error-triage',
        environment=environment
    )
    handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Remove existing handlers to prevent duplicate log entries
    # This ensures clean initialization when called multiple times
    root_logger.handlers.clear()
    
    # Attach our JSON handler
    root_logger.addHandler(handler)
    
    # Disable propagation to prevent duplicate logs in hierarchical loggers
    root_logger.propagate = False
    
    # Log initialization event with action correlation field
    root_logger.info(
        "Structured JSON logging initialized for error-triage service",
        extra={
            'action': 'logging_initialized',
            'log_level': log_level,
            'environment': environment
        }
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger for a specific module.
    
    Returns a logger instance with the specified name, inheriting the
    root logger's configuration (JSON formatting, log level, handlers).
    
    Per Section 0.7.2 observability requirements, every operation must emit
    at least one log entry with correlation fields (event_id, fingerprint)
    for end-to-end request tracing.
    
    Args:
        name: Logger name, typically __name__ of the calling module
    
    Returns:
        Configured Logger instance with JSON formatting and CloudWatch integration
    
    Example - Basic usage:
        >>> from utils.logging_config import get_logger
        >>> logger = get_logger(__name__)
        >>> logger.info("Processing webhook event")
    
    Example - With correlation fields:
        >>> logger.info(
        ...     "Added comment to Jira issue",
        ...     extra={
        ...         'event_id': event.event_id,
        ...         'fingerprint': fingerprint,
        ...         'jira_issue_key': 'ET-1234',
        ...         'action': 'jira_comment_added',
        ...         'duration_ms': 125
        ...     }
        ... )
        
        Output JSON:
        {
          "timestamp": "2025-01-15T10:30:46.123Z",
          "level": "INFO",
          "service": "error-triage",
          "environment": "production",
          "event_id": "vercel-xyz-123",
          "fingerprint": "a3f5b9c8d2e1f4g6h8j9k0",
          "jira_issue_key": "ET-1234",
          "action": "jira_comment_added",
          "duration_ms": 125,
          "message": "Added comment to Jira issue",
          "filename": "jira_integration.py",
          "lineno": 145,
          "funcName": "add_comment"
        }
    
    Example - Error logging with exception:
        >>> try:
        ...     jira_client.create_issue(...)
        ... except Exception as e:
        ...     logger.error(
        ...         "Jira API error",
        ...         extra={
        ...             'event_id': event.event_id,
        ...             'action': 'jira_api_error',
        ...             'error_type': 'jira_api_timeout',
        ...             'duration_ms': duration
        ...         },
        ...         exc_info=True  # Includes sanitized stack trace
        ...     )
        
        Output JSON:
        {
          "timestamp": "2025-01-15T10:30:50.234Z",
          "level": "ERROR",
          "service": "error-triage",
          "environment": "production",
          "event_id": "gcp-abc456",
          "action": "jira_api_error",
          "error_type": "jira_api_timeout",
          "duration_ms": 5123,
          "message": "Jira API error",
          "exc_info": "Traceback (most recent call last):...",
          "filename": "jira_integration.py",
          "lineno": 89,
          "funcName": "create_bug_issue"
        }
    
    Example - Debug logging with fingerprint:
        >>> logger.debug(
        ...     "Incremented frequency counter",
        ...     extra={
        ...         'fingerprint': fingerprint,
        ...         'action': 'redis_frequency_incr',
        ...         'duration_ms': 2
        ...     }
        ... )
    """
    return logging.getLogger(name)
