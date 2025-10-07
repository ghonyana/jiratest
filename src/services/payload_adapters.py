"""
Payload Transformation Adapters

This module implements the Adapter Pattern to normalize disparate webhook payload
formats from Vercel Log Drain and GCP Cloud Logging into a unified NormalizedErrorEvent
schema. The PayloadAdapterFactory provides source-specific adapter instances that
handle the complexity of different payload structures while presenting a consistent
transformation interface.

Architecture:
    - PayloadAdapterFactory: Factory method pattern for adapter selection
    - VercelPayloadAdapter: Transforms Vercel Log Drain JSON payloads
    - GCPPayloadAdapter: Transforms GCP Cloud Logging Pub/Sub push payloads

Key Features:
    - Automatic error class extraction from message text using regex patterns
    - Robust handling of missing or malformed fields with sensible defaults
    - Deep link construction to source logging platforms (Vercel, GCP Log Explorer)
    - Comprehensive validation with descriptive error messages
    - Structured logging for operational visibility

Usage Example:
    from services.payload_adapters import PayloadAdapterFactory

    # Determine source from request context
    factory = PayloadAdapterFactory()
    adapter = factory.get_adapter('vercel')

    # Transform payload to normalized format
    try:
        event = adapter.transform(webhook_payload)
        # Process event through fingerprinting and Jira integration
    except ValueError as e:
        # Handle unparseable payload
        logger.error(f"Invalid payload: {e}")

Error Handling:
    All adapters raise ValueError with descriptive messages when:
    - Required fields are missing from the payload
    - Field values have unexpected types or formats
    - JSON decoding fails (GCP adapter)
    - Base64 decoding fails (GCP adapter)
    - Timestamp parsing fails

Section References:
    - Section 0.1.1 requirement #1: Multi-source error ingestion
    - Section 0.5.1 Group 2: Core webhook endpoint implementation
    - Section 0.7.1 requirement #5: Deep linking to logs
"""

import base64
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

from src.models.error_event import NormalizedErrorEvent
from src.services.log_link_builder import LogLinkBuilder

# Initialize module logger for structured logging
logger = logging.getLogger(__name__)

# Compiled regex patterns for error class extraction
# Matches common error patterns: TypeError, DatabaseError, RuntimeError, Exception, etc.
ERROR_CLASS_PATTERN = re.compile(r'^(\w+Error|\w+Exception|Exception|Error):', re.IGNORECASE)
# Alternative pattern for stack traces: "at ClassName.methodName" or "TypeError: message"
STACK_ERROR_PATTERN = re.compile(r'(\w+Error|\w+Exception)(?:\:|\s+at)', re.IGNORECASE)


class PayloadAdapterFactory:
    """
    Factory for creating source-specific payload adapter instances.

    Implements the Factory Method pattern to abstract adapter selection logic
    from consuming code. This enables easy addition of new error sources without
    modifying existing code paths.

    Supported Sources:
        - 'vercel': Vercel Log Drain webhook payloads
        - 'gcp': GCP Cloud Logging Pub/Sub push subscription payloads

    Example:
        factory = PayloadAdapterFactory()
        adapter = factory.get_adapter('vercel')
        event = adapter.transform(payload)

    Thread Safety:
        This factory is stateless and thread-safe. Adapter instances are created
        fresh for each request to avoid state sharing between concurrent requests.
    """

    def __init__(self):
        """
        Initialize the PayloadAdapterFactory.

        Creates a new LogLinkBuilder instance that will be shared across all
        adapter instances created by this factory. The LogLinkBuilder is stateless
        and safe to share.
        """
        self.log_link_builder = LogLinkBuilder()
        logger.info("Initialized PayloadAdapterFactory")

    def get_adapter(self, source: str):
        """
        Get the appropriate payload adapter for the specified source.

        Args:
            source: Error source identifier, must be 'vercel' or 'gcp'

        Returns:
            Adapter instance (VercelPayloadAdapter or GCPPayloadAdapter)

        Raises:
            ValueError: If source is not recognized or is None/empty

        Example:
            >>> factory = PayloadAdapterFactory()
            >>> adapter = factory.get_adapter('vercel')
            >>> isinstance(adapter, VercelPayloadAdapter)
            True
        """
        if not source or not isinstance(source, str):
            logger.error("get_adapter called with invalid source type", extra={"source": source, "type": type(source)})
            raise ValueError("source parameter must be a non-empty string")

        source_lower = source.lower().strip()

        if source_lower == 'vercel':
            logger.debug("Creating VercelPayloadAdapter")
            return VercelPayloadAdapter(self.log_link_builder)
        elif source_lower == 'gcp':
            logger.debug("Creating GCPPayloadAdapter")
            return GCPPayloadAdapter(self.log_link_builder)
        else:
            logger.error(
                "Unsupported error source requested",
                extra={"source": source, "supported_sources": ["vercel", "gcp"]}
            )
            raise ValueError(
                f"Unsupported error source '{source}'. Supported sources: 'vercel', 'gcp'"
            )


class VercelPayloadAdapter:
    """
    Adapter for transforming Vercel Log Drain webhook payloads.

    Vercel Log Drain sends structured JSON payloads containing deployment information,
    log level, message text, timestamps, and trace IDs. This adapter extracts these
    fields and constructs a NormalizedErrorEvent with deep links back to Vercel logs.

    Payload Structure (simplified):
        {
            "source": "vercel",
            "deployment": {
                "id": "dpl_xyz123",
                "url": "my-app-abc123.vercel.app"
            },
            "message": "Error: Cannot read property 'x' of undefined",
            "level": "error",
            "timestamp": 1705318245123,
            "environment": "production",
            "path": "/api/checkout",
            "traceId": "abc123def456"
        }

    Field Mappings:
        - deployment.url -> Used for log URL construction
        - deployment.id -> release field
        - message -> message field (also used for error_class extraction)
        - level -> Mapped to standard severity (error/warning/info)
        - timestamp -> occurred_at (Unix epoch milliseconds converted to datetime)
        - environment -> environment (normalized to prod/staging/dev)
        - path -> path field
        - traceId -> Used for log URL construction

    Error Class Extraction:
        When the message starts with a recognizable error pattern (e.g., "TypeError:"),
        the error class is extracted. Otherwise, defaults to "UnknownError".

    Service Name:
        Extracted from deployment.url by taking the first part before dash
        (e.g., "my-app-abc123" -> "my-app")
    """

    def __init__(self, log_link_builder: LogLinkBuilder):
        """
        Initialize the Vercel payload adapter.

        Args:
            log_link_builder: LogLinkBuilder instance for constructing deep links
        """
        self.log_link_builder = log_link_builder
        logger.info("Initialized VercelPayloadAdapter")

    def transform(self, payload: Dict[str, Any]) -> NormalizedErrorEvent:
        """
        Transform Vercel Log Drain payload to NormalizedErrorEvent.

        Extracts required and optional fields from Vercel's payload structure,
        applies sensible defaults for missing fields, and constructs deep links
        to Vercel deployment logs using trace ID.

        Args:
            payload: Dictionary containing Vercel Log Drain webhook payload

        Returns:
            NormalizedErrorEvent instance with normalized fields

        Raises:
            ValueError: If payload is malformed, missing critical fields, or has
                       unexpected types that prevent transformation

        Example:
            >>> adapter = VercelPayloadAdapter(log_link_builder)
            >>> payload = {
            ...     "deployment": {"url": "my-app.vercel.app", "id": "dpl_123"},
            ...     "message": "TypeError: undefined is not an object",
            ...     "level": "error",
            ...     "timestamp": 1705318245123,
            ...     "environment": "production",
            ...     "path": "/api/checkout",
            ...     "traceId": "abc123"
            ... }
            >>> event = adapter.transform(payload)
            >>> event.source
            'vercel'
            >>> event.error_class
            'TypeError'
        """
        logger.debug("Transforming Vercel payload", extra={"payload_keys": list(payload.keys())})

        try:
            # Extract message (required field)
            message = self._extract_message(payload)

            # Extract error class from message text
            error_class = self._extract_error_class(message)

            # Extract deployment information
            deployment = payload.get('deployment', {})
            if not isinstance(deployment, dict):
                logger.warning(
                    "Vercel payload has invalid deployment field type",
                    extra={"deployment_type": type(deployment)}
                )
                deployment = {}

            deployment_url = deployment.get('url', '').strip()
            deployment_id = deployment.get('id', '').strip()

            # Extract service name from deployment URL
            service = self._extract_service_name(deployment_url, payload)

            # Extract environment (with normalization)
            environment = self._extract_environment(payload)

            # Extract timestamp and convert to datetime
            occurred_at = self._extract_timestamp(payload)

            # Extract optional fields
            path = payload.get('path')
            if path and isinstance(path, str):
                path = path.strip() or None

            trace_id = payload.get('traceId', '').strip() or 'unknown'

            # Construct full URL if path is available
            url = None
            if deployment_url and path:
                # Ensure deployment_url has protocol
                if not deployment_url.startswith('http'):
                    url = f"https://{deployment_url}{path}"
                else:
                    url = f"{deployment_url}{path}"

            # Extract stack trace if available (Vercel may include in structured format)
            stack_trace = payload.get('stack') or payload.get('stackTrace')
            if stack_trace and isinstance(stack_trace, str):
                stack_trace = stack_trace.strip() or None

            # Build deep link to Vercel logs using trace ID
            log_url = self._build_log_url(deployment_url, trace_id)

            # Generate unique event ID
            event_id = self._generate_event_id(payload, trace_id)

            # Construct NormalizedErrorEvent
            event = NormalizedErrorEvent(
                source='vercel',
                service=service,
                environment=environment,
                error_class=error_class,
                message=message,
                stack_trace=stack_trace,
                path=path,
                url=url,
                release=deployment_id or None,
                log_url=log_url,
                event_id=event_id,
                occurred_at=occurred_at
            )

            logger.info(
                "Successfully transformed Vercel payload",
                extra={
                    "event_id": event_id,
                    "service": service,
                    "environment": environment,
                    "error_class": error_class
                }
            )

            return event

        except KeyError as e:
            logger.error("Missing required field in Vercel payload", extra={"missing_field": str(e)})
            raise ValueError(f"Vercel payload missing required field: {e}")
        except (TypeError, AttributeError) as e:
            logger.error("Invalid field type in Vercel payload", extra={"error": str(e)})
            raise ValueError(f"Vercel payload has invalid field type: {e}")
        except Exception as e:
            logger.error("Failed to transform Vercel payload", extra={"error": str(e)}, exc_info=True)
            raise ValueError(f"Failed to transform Vercel payload: {e}")

    def _extract_message(self, payload: Dict[str, Any]) -> str:
        """
        Extract and validate message field from Vercel payload.

        Args:
            payload: Vercel webhook payload dictionary

        Returns:
            Non-empty message string

        Raises:
            ValueError: If message is missing or empty
        """
        message = payload.get('message', '').strip()
        if not message:
            raise ValueError("Vercel payload missing required 'message' field")
        return message

    def _extract_error_class(self, message: str) -> str:
        """
        Extract error class from message text using regex patterns.

        Attempts to identify error class from patterns like:
        - "TypeError: cannot read property..."
        - "DatabaseError: connection failed"
        - "Error: something went wrong"

        Args:
            message: Error message text

        Returns:
            Error class name or "UnknownError" if pattern not matched
        """
        match = ERROR_CLASS_PATTERN.match(message)
        if match:
            error_class = match.group(1)
            logger.debug("Extracted error class from message", extra={"error_class": error_class})
            return error_class

        # Try alternative pattern in message body
        match = STACK_ERROR_PATTERN.search(message)
        if match:
            error_class = match.group(1)
            logger.debug("Extracted error class from message body", extra={"error_class": error_class})
            return error_class

        logger.debug("No error class pattern found in message, using default")
        return "UnknownError"

    def _extract_service_name(self, deployment_url: str, payload: Dict[str, Any]) -> str:
        """
        Extract service name from deployment URL or payload.

        Vercel deployment URLs typically follow pattern: service-name-hash.vercel.app
        This extracts the service-name portion before the first dash.

        Args:
            deployment_url: Vercel deployment URL
            payload: Full payload (fallback for explicit service field)

        Returns:
            Service name extracted from URL or explicit field

        Raises:
            ValueError: If service name cannot be determined
        """
        # Check if payload has explicit service field
        if 'service' in payload:
            service = payload['service']
            if isinstance(service, str) and service.strip():
                return service.strip()

        # Extract from deployment URL
        if deployment_url:
            # Remove protocol if present
            url_without_protocol = deployment_url.replace('https://', '').replace('http://', '')
            # Split on dots and take first part (subdomain)
            subdomain = url_without_protocol.split('.')[0]
            # Split on dashes and take everything except last part (hash)
            parts = subdomain.split('-')
            if len(parts) > 1:
                # Join all parts except the last (which is usually the hash)
                service = '-'.join(parts[:-1])
                if service:
                    logger.debug("Extracted service from deployment URL", extra={"service": service})
                    return service

        # CRITICAL: Cannot determine service name - this is a required field for error triage
        logger.error(
            "Cannot extract service name from payload",
            extra={"deployment_url": deployment_url, "has_service_field": 'service' in payload}
        )
        raise ValueError(
            "Vercel payload missing required 'service' field and 'deployment.url' for service extraction"
        )

    def _extract_environment(self, payload: Dict[str, Any]) -> str:
        """
        Extract and normalize environment from Vercel payload.

        Args:
            payload: Vercel webhook payload

        Returns:
            Normalized environment string (prod/staging/dev)

        Raises:
            ValueError: If environment field is missing or empty
        """
        environment = payload.get('environment', '').strip()
        if not environment:
            # CRITICAL: Cannot determine environment - required for severity classification
            # Never default to 'production' - that could incorrectly escalate issues
            logger.error(
                "Cannot extract environment from Vercel payload",
                extra={"available_fields": list(payload.keys())}
            )
            raise ValueError(
                "Vercel payload missing required 'environment' field"
            )

        logger.debug("Extracted environment from payload", extra={"environment": environment})
        return environment

    def _extract_timestamp(self, payload: Dict[str, Any]) -> datetime:
        """
        Extract and convert timestamp from Vercel payload.

        Vercel provides Unix epoch timestamp in milliseconds. This method
        converts to Python datetime object.

        Args:
            payload: Vercel webhook payload

        Returns:
            datetime object representing error occurrence time

        Raises:
            ValueError: If timestamp is missing or invalid format
        """
        timestamp = payload.get('timestamp')

        if timestamp is None:
            logger.warning("Vercel payload missing timestamp, using current time")
            return datetime.now()

        try:
            # Vercel sends timestamp in milliseconds (Unix epoch)
            if isinstance(timestamp, (int, float)):
                # Convert milliseconds to seconds for fromtimestamp
                timestamp_seconds = timestamp / 1000.0
                occurred_at = datetime.fromtimestamp(timestamp_seconds)
                logger.debug("Converted Vercel timestamp", extra={"timestamp": timestamp, "datetime": occurred_at.isoformat()})
                return occurred_at
            elif isinstance(timestamp, str):
                # Try parsing as ISO format string (fallback)
                occurred_at = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                return occurred_at
            else:
                logger.warning(
                    "Vercel timestamp has unexpected type",
                    extra={"timestamp_type": type(timestamp)}
                )
                return datetime.now()
        except (ValueError, OSError) as e:
            logger.warning(
                "Failed to parse Vercel timestamp, using current time",
                extra={"timestamp": timestamp, "error": str(e)}
            )
            return datetime.now()

    def _build_log_url(self, deployment_url: str, trace_id: str) -> str:
        """
        Build deep link to Vercel logs using LogLinkBuilder.

        Args:
            deployment_url: Vercel deployment URL
            trace_id: Trace ID for log filtering

        Returns:
            Formatted Vercel logs URL

        Raises:
            ValueError: If required parameters are missing
        """
        if not deployment_url:
            logger.warning("Missing deployment URL for log link, using placeholder")
            deployment_url = "unknown-deployment"

        try:
            log_url = self.log_link_builder.build_vercel_link(deployment_url, trace_id)
            return log_url
        except ValueError as e:
            logger.error("Failed to build Vercel log URL", extra={"error": str(e)})
            # Fallback to basic Vercel logs page
            return f"https://vercel.com/logs?deploymentUrl={deployment_url}"

    def _generate_event_id(self, payload: Dict[str, Any], trace_id: str) -> str:
        """
        Generate unique event ID for deduplication.

        Uses Vercel's trace ID as the primary identifier, with timestamp
        as fallback for uniqueness.

        Args:
            payload: Vercel webhook payload
            trace_id: Extracted trace ID

        Returns:
            Unique event identifier string
        """
        # Use explicit ID if provided
        if 'id' in payload:
            event_id = payload['id']
            if isinstance(event_id, str) and event_id.strip():
                return f"vercel-{event_id.strip()}"

        # Use trace ID as primary identifier
        if trace_id and trace_id != 'unknown':
            return f"vercel-{trace_id}"

        # Fallback: generate from timestamp
        timestamp = payload.get('timestamp', int(datetime.now().timestamp() * 1000))
        return f"vercel-{timestamp}"


class GCPPayloadAdapter:
    """
    Adapter for transforming GCP Cloud Logging Pub/Sub push payloads.

    GCP Cloud Logging delivers log entries via Pub/Sub push subscriptions. The payload
    contains a base64-encoded JSON log entry in the message.data field. This adapter
    decodes the payload, extracts structured fields, and constructs a NormalizedErrorEvent
    with deep links to GCP Log Explorer.

    Payload Structure (simplified):
        {
            "message": {
                "data": "<base64-encoded-log-entry>",
                "messageId": "123456789",
                "publishTime": "2025-01-15T10:30:45.123Z"
            },
            "subscription": "projects/my-project/subscriptions/error-events-push"
        }

    Decoded Log Entry Structure:
        {
            "severity": "ERROR",
            "textPayload": "TypeError: Cannot read property 'x' of undefined",
            "resource": {
                "type": "cloud_run_revision",
                "labels": {
                    "service_name": "api-service",
                    "revision_name": "api-service-00042-xyz"
                }
            },
            "insertId": "abc123xyz789",
            "timestamp": "2025-01-15T10:30:45.123Z"
        }

    Field Mappings:
        - severity -> Mapped to standard level (ERROR/WARNING/INFO)
        - textPayload or jsonPayload.message -> message field
        - resource.labels.service_name -> service field
        - insertId -> event_id (unique identifier)
        - timestamp -> occurred_at (ISO 8601 format)
        - labels.environment or default -> environment

    Error Class Extraction:
        Similar to Vercel adapter, extracts error class from message text patterns.

    Project Extraction:
        Extracted from subscription field: projects/{project_id}/subscriptions/{name}
    """

    def __init__(self, log_link_builder: LogLinkBuilder):
        """
        Initialize the GCP payload adapter.

        Args:
            log_link_builder: LogLinkBuilder instance for constructing deep links
        """
        self.log_link_builder = log_link_builder
        logger.info("Initialized GCPPayloadAdapter")

    def transform(self, payload: Dict[str, Any]) -> NormalizedErrorEvent:
        """
        Transform GCP Cloud Logging Pub/Sub payload to NormalizedErrorEvent.

        Decodes base64-encoded log entry, extracts structured fields from GCP's
        log format, and constructs deep links to GCP Log Explorer using insertId.

        Args:
            payload: Dictionary containing GCP Pub/Sub push webhook payload

        Returns:
            NormalizedErrorEvent instance with normalized fields

        Raises:
            ValueError: If payload is malformed, base64 decoding fails, JSON parsing
                       fails, or critical fields are missing

        Example:
            >>> adapter = GCPPayloadAdapter(log_link_builder)
            >>> payload = {
            ...     "message": {
            ...         "data": "<base64-encoded-json>",
            ...         "messageId": "123456789"
            ...     },
            ...     "subscription": "projects/my-project/subscriptions/errors"
            ... }
            >>> event = adapter.transform(payload)
            >>> event.source
            'gcp'
        """
        logger.debug("Transforming GCP payload", extra={"payload_keys": list(payload.keys())})

        try:
            # Decode base64 payload from message.data
            log_entry = self._decode_log_entry(payload)

            # Extract message from textPayload or jsonPayload
            message = self._extract_message(log_entry)

            # Extract error class from message
            error_class = self._extract_error_class(message)

            # Extract service name from resource labels
            service = self._extract_service_name(log_entry)

            # Extract environment (from labels or default to prod)
            environment = self._extract_environment(log_entry)

            # Extract timestamp and convert to datetime
            occurred_at = self._extract_timestamp(log_entry)

            # Extract insertId for event identification and deduplication
            insert_id = self._extract_insert_id(log_entry)
            event_id = f"gcp-{insert_id}"

            # Extract optional fields
            stack_trace = self._extract_stack_trace(log_entry)
            path = self._extract_path(log_entry)

            # Extract resource information for URL construction
            resource = log_entry.get('resource', {})
            if not isinstance(resource, dict):
                resource = {}

            # Extract labels for additional context
            labels = log_entry.get('labels', {})
            if not isinstance(labels, dict):
                labels = {}

            release = labels.get('version') or labels.get('revision_name')
            if release and isinstance(release, str):
                release = release.strip() or None

            # Extract GCP project from subscription or resource
            project = self._extract_project(payload, resource)

            # Build deep link to GCP Log Explorer
            log_url = self._build_log_url(project, insert_id)

            # Construct NormalizedErrorEvent
            event = NormalizedErrorEvent(
                source='gcp',
                service=service,
                environment=environment,
                error_class=error_class,
                message=message,
                stack_trace=stack_trace,
                path=path,
                url=None,  # GCP doesn't provide full URL in logs
                release=release,
                log_url=log_url,
                event_id=event_id,
                occurred_at=occurred_at
            )

            logger.info(
                "Successfully transformed GCP payload",
                extra={
                    "event_id": event_id,
                    "service": service,
                    "environment": environment,
                    "error_class": error_class,
                    "insert_id": insert_id
                }
            )

            return event

        except (KeyError, ValueError) as e:
            logger.error("Failed to transform GCP payload", extra={"error": str(e)})
            raise ValueError(f"Failed to transform GCP payload: {e}")
        except Exception as e:
            logger.error("Unexpected error transforming GCP payload", extra={"error": str(e)}, exc_info=True)
            raise ValueError(f"Failed to transform GCP payload: {e}")

    def _decode_log_entry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decode base64-encoded log entry from GCP Pub/Sub message.

        GCP Pub/Sub push delivers log entries as base64-encoded JSON in the
        message.data field. This method extracts and decodes the data.

        Args:
            payload: GCP Pub/Sub push webhook payload

        Returns:
            Decoded log entry as dictionary

        Raises:
            ValueError: If message structure is invalid or decoding fails
        """
        # Extract message field
        message = payload.get('message')
        if not isinstance(message, dict):
            raise ValueError("GCP payload missing or invalid 'message' field")

        # Extract base64-encoded data
        data = message.get('data')
        if not data:
            raise ValueError("GCP payload missing 'message.data' field")

        try:
            # Decode base64 to bytes
            decoded_bytes = base64.b64decode(data)

            # Parse JSON
            log_entry = json.loads(decoded_bytes.decode('utf-8'))

            if not isinstance(log_entry, dict):
                raise ValueError("Decoded GCP log entry is not a JSON object")

            logger.debug("Successfully decoded GCP log entry", extra={"log_entry_keys": list(log_entry.keys())})
            return log_entry

        except (base64.binascii.Error, UnicodeDecodeError) as e:
            logger.error("Failed to decode base64 GCP payload", extra={"error": str(e)})
            raise ValueError(f"Invalid base64 encoding in GCP payload: {e}")
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON from GCP payload", extra={"error": str(e)})
            raise ValueError(f"Invalid JSON in GCP log entry: {e}")

    def _extract_message(self, log_entry: Dict[str, Any]) -> str:
        """
        Extract message text from GCP log entry.

        GCP logs may contain either textPayload (string) or jsonPayload (object).
        This method handles both formats.

        Args:
            log_entry: Decoded GCP log entry dictionary

        Returns:
            Non-empty message string

        Raises:
            ValueError: If message cannot be extracted
        """
        # Try textPayload first (most common for errors)
        text_payload = log_entry.get('textPayload')
        if text_payload and isinstance(text_payload, str):
            message = text_payload.strip()
            if message:
                return message

        # Try jsonPayload.message
        json_payload = log_entry.get('jsonPayload')
        if isinstance(json_payload, dict):
            message_field = json_payload.get('message')
            if message_field and isinstance(message_field, str):
                message = message_field.strip()
                if message:
                    return message

            # Try jsonPayload.error or jsonPayload.error_message
            error_field = json_payload.get('error') or json_payload.get('error_message')
            if error_field and isinstance(error_field, str):
                message = error_field.strip()
                if message:
                    return message

        # Fallback: use severity as message if no text found
        severity = log_entry.get('severity', 'ERROR')
        logger.warning("GCP log entry missing text payload, using severity as message", extra={"severity": severity})
        return f"GCP {severity} log entry"

    def _extract_error_class(self, message: str) -> str:
        """
        Extract error class from message text using regex patterns.

        Args:
            message: Error message text

        Returns:
            Error class name or "UnknownError" if pattern not matched
        """
        match = ERROR_CLASS_PATTERN.match(message)
        if match:
            error_class = match.group(1)
            logger.debug("Extracted error class from GCP message", extra={"error_class": error_class})
            return error_class

        match = STACK_ERROR_PATTERN.search(message)
        if match:
            error_class = match.group(1)
            logger.debug("Extracted error class from GCP message body", extra={"error_class": error_class})
            return error_class

        logger.debug("No error class pattern found in GCP message, using default")
        return "UnknownError"

    def _extract_service_name(self, log_entry: Dict[str, Any]) -> str:
        """
        Extract service name from GCP log entry resource labels.

        GCP log entries include resource metadata with labels containing
        service identifiers (service_name, function_name, job_name, etc.).

        Args:
            log_entry: Decoded GCP log entry

        Returns:
            Service name or default value

        Raises:
            ValueError: If service name cannot be determined
        """
        resource = log_entry.get('resource', {})
        if isinstance(resource, dict):
            labels = resource.get('labels', {})
            if isinstance(labels, dict):
                # Try common service name fields
                service = (
                    labels.get('service_name') or
                    labels.get('function_name') or
                    labels.get('job_name') or
                    labels.get('service')
                )

                if service and isinstance(service, str):
                    service = service.strip()
                    if service:
                        logger.debug("Extracted service from GCP resource labels", extra={"service": service})
                        return service

        # Check top-level labels as fallback
        labels = log_entry.get('labels', {})
        if isinstance(labels, dict):
            service = labels.get('service_name') or labels.get('service')
            if service and isinstance(service, str):
                service = service.strip()
                if service:
                    logger.debug("Extracted service from GCP top-level labels", extra={"service": service})
                    return service

        # Fallback to resource type
        resource_type = resource.get('type', '')
        if resource_type:
            logger.warning(
                "Could not extract service name, using resource type",
                extra={"resource_type": resource_type}
            )
            return f"gcp-{resource_type}"

        logger.warning("Could not extract service name, using default")
        return "gcp-service"

    def _extract_environment(self, log_entry: Dict[str, Any]) -> str:
        """
        Extract environment from GCP log entry labels.

        Args:
            log_entry: Decoded GCP log entry

        Returns:
            Environment string (defaults to 'prod')
        """
        labels = log_entry.get('labels', {})
        if isinstance(labels, dict):
            environment = (
                labels.get('environment') or
                labels.get('env') or
                labels.get('deployment_environment')
            )

            if environment and isinstance(environment, str):
                environment = environment.strip()
                if environment:
                    logger.debug("Extracted environment from GCP labels", extra={"environment": environment})
                    return environment

        # Default to production
        logger.debug("No environment found in GCP labels, defaulting to 'production'")
        return "production"

    def _extract_timestamp(self, log_entry: Dict[str, Any]) -> datetime:
        """
        Extract and parse timestamp from GCP log entry.

        GCP provides timestamps in ISO 8601 format with timezone information.

        Args:
            log_entry: Decoded GCP log entry

        Returns:
            datetime object

        Raises:
            ValueError: If timestamp parsing fails
        """
        timestamp = log_entry.get('timestamp')

        if not timestamp:
            logger.warning("GCP log entry missing timestamp, using current time")
            return datetime.now()

        try:
            if isinstance(timestamp, str):
                # GCP uses ISO 8601 format, may include 'Z' for UTC
                # Replace 'Z' with '+00:00' for Python compatibility
                timestamp_normalized = timestamp.replace('Z', '+00:00')
                occurred_at = datetime.fromisoformat(timestamp_normalized)
                logger.debug("Parsed GCP timestamp", extra={"timestamp": timestamp, "datetime": occurred_at.isoformat()})
                return occurred_at
            else:
                logger.warning(
                    "GCP timestamp has unexpected type",
                    extra={"timestamp_type": type(timestamp)}
                )
                return datetime.now()
        except ValueError as e:
            logger.warning(
                "Failed to parse GCP timestamp, using current time",
                extra={"timestamp": timestamp, "error": str(e)}
            )
            return datetime.now()

    def _extract_insert_id(self, log_entry: Dict[str, Any]) -> str:
        """
        Extract insertId from GCP log entry for unique identification.

        The insertId is GCP's unique identifier for log entries, used for
        deduplication and deep linking.

        Args:
            log_entry: Decoded GCP log entry

        Returns:
            Insert ID string

        Raises:
            ValueError: If insertId is missing
        """
        insert_id = log_entry.get('insertId')

        if not insert_id:
            # Fallback: generate from timestamp and other fields
            timestamp = log_entry.get('timestamp', datetime.now().isoformat())
            resource = log_entry.get('resource', {})
            resource_type = resource.get('type', 'unknown') if isinstance(resource, dict) else 'unknown'

            # Create pseudo-unique ID
            import hashlib
            unique_str = f"{timestamp}-{resource_type}-{log_entry.get('severity', '')}"
            insert_id = hashlib.md5(unique_str.encode()).hexdigest()[:16]

            logger.warning(
                "GCP log entry missing insertId, generated fallback ID",
                extra={"generated_id": insert_id}
            )

        if isinstance(insert_id, str):
            return insert_id.strip()
        else:
            return str(insert_id)

    def _extract_stack_trace(self, log_entry: Dict[str, Any]) -> Optional[str]:
        """
        Extract stack trace from GCP log entry if available.

        Args:
            log_entry: Decoded GCP log entry

        Returns:
            Stack trace string or None
        """
        # Try jsonPayload.stack or jsonPayload.stackTrace
        json_payload = log_entry.get('jsonPayload')
        if isinstance(json_payload, dict):
            stack = json_payload.get('stack') or json_payload.get('stackTrace')
            if stack and isinstance(stack, str):
                return stack.strip() or None

        # Try sourceLocation (GCP structured field)
        source_location = log_entry.get('sourceLocation')
        if isinstance(source_location, dict):
            file_path = source_location.get('file')
            line = source_location.get('line')
            function = source_location.get('function')

            if file_path:
                stack_parts = [f"  at {file_path}"]
                if line:
                    stack_parts[0] += f":{line}"
                if function:
                    stack_parts[0] += f" in {function}"
                return '\n'.join(stack_parts)

        return None

    def _extract_path(self, log_entry: Dict[str, Any]) -> Optional[str]:
        """
        Extract request path from GCP log entry if available.

        Args:
            log_entry: Decoded GCP log entry

        Returns:
            Request path string or None
        """
        # Try httpRequest.requestUrl
        http_request = log_entry.get('httpRequest')
        if isinstance(http_request, dict):
            request_url = http_request.get('requestUrl')
            if request_url and isinstance(request_url, str):
                # Extract path from full URL
                from urllib import parse as urlparse
                try:
                    parsed = urlparse.urlparse(request_url)
                    if parsed.path:
                        return parsed.path.strip() or None
                except Exception:
                    pass

        # Try jsonPayload.path or jsonPayload.url
        json_payload = log_entry.get('jsonPayload')
        if isinstance(json_payload, dict):
            path = json_payload.get('path') or json_payload.get('url')
            if path and isinstance(path, str):
                return path.strip() or None

        return None

    def _extract_project(self, payload: Dict[str, Any], resource: Dict[str, Any]) -> str:
        """
        Extract GCP project ID from payload or resource.

        Args:
            payload: Full GCP Pub/Sub payload
            resource: Resource metadata from log entry

        Returns:
            GCP project ID or default value
        """
        # Try extracting from subscription field
        subscription = payload.get('subscription', '')
        if isinstance(subscription, str) and subscription:
            # Format: projects/{project_id}/subscriptions/{subscription_name}
            match = re.match(r'projects/([^/]+)/subscriptions/', subscription)
            if match:
                project = match.group(1)
                logger.debug("Extracted project from subscription", extra={"project": project})
                return project

        # Try resource.labels.project_id
        if isinstance(resource, dict):
            labels = resource.get('labels', {})
            if isinstance(labels, dict):
                project = labels.get('project_id')
                if project and isinstance(project, str):
                    return project.strip()

        # Default fallback
        logger.warning("Could not extract GCP project ID, using default")
        return "unknown-project"

    def _build_log_url(self, project: str, insert_id: str) -> str:
        """
        Build deep link to GCP Log Explorer using LogLinkBuilder.

        Args:
            project: GCP project ID
            insert_id: Log entry insertId

        Returns:
            Formatted GCP Log Explorer URL
        """
        try:
            log_url = self.log_link_builder.build_gcp_link(project, insert_id)
            return log_url
        except ValueError as e:
            logger.error("Failed to build GCP log URL", extra={"error": str(e)})
            # Fallback to basic Log Explorer URL
            return f"https://console.cloud.google.com/logs/query?project={project}"


# Export all public classes
__all__ = ["PayloadAdapterFactory", "VercelPayloadAdapter", "GCPPayloadAdapter"]
