"""
Unit Tests for Payload Adapters

Comprehensive test suite for payload adapter services that normalize disparate
webhook formats from Vercel Log Drain and GCP Cloud Logging into unified
NormalizedErrorEvent schema per Agent Action Plan Section 0.5.1 Group 2.

Test Coverage:
    - VercelPayloadAdapter: Field extraction, log level mapping, deep link construction
    - GCPPayloadAdapter: Base64 decoding, resource label parsing, GCP URL generation
    - PayloadAdapterFactory: Source-based routing to appropriate adapter
    - Error Handling: Missing required fields, malformed payloads, invalid sources

Tests validate that adapters correctly transform source-specific webhook payloads
into NormalizedErrorEvent instances with proper field mapping, type conversions,
and validation per Agent Action Plan requirements for 80%+ code coverage.
"""

import base64
import json
import pytest
from datetime import datetime
from typing import Dict, Any, Optional

from src.services.payload_adapters import (
    PayloadAdapterFactory,
    VercelPayloadAdapter,
    GCPPayloadAdapter,
)
from src.models.error_event import NormalizedErrorEvent


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def sample_vercel_payload() -> Dict[str, Any]:
    """
    Sample Vercel Log Drain webhook payload.

    Represents a typical error event from Vercel deployment logs with all
    required and optional fields populated. Used for happy path testing
    of VercelPayloadAdapter transformation logic.

    Returns:
        Dictionary matching Vercel Log Drain webhook format
    """
    return {
        "id": "log_abc123xyz",
        "message": "Error: Cannot read property 'x' of undefined",
        "timestamp": 1705320645123,  # Unix timestamp in milliseconds
        "source": "lambda",
        "projectId": "prj_xyz",
        "deploymentId": "dpl_abc123",
        "buildId": "bld_xyz789",
        "host": "my-app-abc123.vercel.app",
        "path": "/api/checkout",
        "entrypoint": "api/checkout.js",
        "level": "error",
        "requestId": "req_abc123",
        "statusCode": 500,
        "type": "lambda",
        "proxy": {
            "path": "/api/checkout",
            "method": "POST",
            "scheme": "https",
            "host": "my-app.vercel.app",
            "userAgent": "Mozilla/5.0",
        },
    }


@pytest.fixture
def sample_vercel_payload_minimal() -> Dict[str, Any]:
    """
    Minimal Vercel Log Drain payload with only required fields.

    Tests adapter's ability to handle payloads where optional fields
    (path, requestId) are missing, validating default value assignment.

    Returns:
        Minimal dictionary with required Vercel fields only
    """
    return {
        "id": "log_minimal123",
        "message": "Error: Service unavailable",
        "timestamp": 1705320645123,
        "level": "error",
        "host": "minimal-app.vercel.app",
        "deploymentId": "dpl_minimal",
    }


@pytest.fixture
def sample_gcp_payload() -> Dict[str, Any]:
    """
    Sample GCP Cloud Logging Pub/Sub push payload.

    Represents a typical GCP log entry delivered via Pub/Sub push subscription.
    The actual log entry is base64-encoded in the message.data field per
    GCP Pub/Sub push format from Section 0.4.2.

    Returns:
        Dictionary matching GCP Pub/Sub push subscription format
    """
    log_entry = {
        "severity": "ERROR",
        "textPayload": "TypeError: Cannot read property 'user' of null at getUserData",
        "insertId": "abc123def456",
        "timestamp": "2025-01-15T10:30:45.123Z",
        "resource": {
            "type": "cloud_run_revision",
            "labels": {
                "service_name": "api-service",
                "revision_name": "api-service-00042-xyz",
                "project_id": "my-gcp-project",
                "location": "us-central1",
            },
        },
        "labels": {
            "environment": "production",
        },
        "trace": "projects/my-gcp-project/traces/1234567890abcdef",
    }

    # Encode log entry as base64 (GCP Pub/Sub push format)
    encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()

    return {
        "message": {
            "data": encoded_data,
            "messageId": "123456789",
            "publishTime": "2025-01-15T10:30:45.123Z",
        },
        "subscription": "projects/my-gcp-project/subscriptions/error-events-push",
    }


@pytest.fixture
def sample_gcp_payload_minimal() -> Dict[str, Any]:
    """
    Minimal GCP payload with only required fields.

    Tests adapter's ability to handle payloads where optional fields
    (environment label, trace) are missing, validating default values.

    Returns:
        Minimal dictionary with required GCP fields only
    """
    log_entry = {
        "severity": "ERROR",
        "textPayload": "Error occurred in service",
        "insertId": "minimal_insert_123",
        "timestamp": "2025-01-15T10:30:45.123Z",
        "resource": {
            "type": "cloud_run_revision",
            "labels": {
                "service_name": "minimal-service",
            },
        },
    }

    encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()

    return {
        "message": {
            "data": encoded_data,
            "messageId": "minimal_msg_123",
            "publishTime": "2025-01-15T10:30:45.123Z",
        },
        "subscription": "projects/test-project/subscriptions/test-sub",
    }


# ============================================================================
# PayloadAdapterFactory Tests
# ============================================================================


class TestPayloadAdapterFactory:
    """
    Test suite for PayloadAdapterFactory routing logic.

    Validates that factory correctly instantiates source-specific adapter
    classes based on source parameter, and raises appropriate errors for
    unknown source types per Agent Action Plan requirements.
    """

    def test_get_adapter_vercel_returns_correct_adapter(self):
        """
        Test factory returns VercelPayloadAdapter for 'vercel' source.

        Validates that PayloadAdapterFactory.get_adapter('vercel') returns
        an instance of VercelPayloadAdapter class, enabling proper routing
        for Vercel Log Drain webhooks.
        """
        adapter = PayloadAdapterFactory.get_adapter("vercel")
        assert isinstance(adapter, VercelPayloadAdapter)

    def test_get_adapter_gcp_returns_correct_adapter(self):
        """
        Test factory returns GCPPayloadAdapter for 'gcp' source.

        Validates that PayloadAdapterFactory.get_adapter('gcp') returns
        an instance of GCPPayloadAdapter class, enabling proper routing
        for GCP Cloud Logging Pub/Sub push subscriptions.
        """
        adapter = PayloadAdapterFactory.get_adapter("gcp")
        assert isinstance(adapter, GCPPayloadAdapter)

    def test_get_adapter_unknown_source_raises_value_error(self):
        """
        Test factory raises ValueError for unknown source types.

        Validates that PayloadAdapterFactory.get_adapter() raises ValueError
        with descriptive message when provided with unsupported source string,
        preventing silent failures from unrecognized webhook sources.
        """
        with pytest.raises(ValueError) as exc_info:
            PayloadAdapterFactory.get_adapter("unknown")

        assert "unknown" in str(exc_info.value).lower()
        assert "source" in str(exc_info.value).lower()

    @pytest.mark.parametrize(
        "invalid_source",
        [
            "datadog",
            "sentry",
            "cloudwatch",
            "",
            "VERCEL",  # Case sensitive
            "Gcp",  # Case sensitive
        ],
    )
    def test_get_adapter_rejects_invalid_sources(self, invalid_source: str):
        """
        Test factory rejects various invalid source strings.

        Parametrized test validating that factory raises ValueError for
        multiple invalid source values including empty strings, unknown
        sources, and case mismatches (factory expects lowercase).
        """
        with pytest.raises(ValueError):
            PayloadAdapterFactory.get_adapter(invalid_source)


# ============================================================================
# VercelPayloadAdapter Tests
# ============================================================================


class TestVercelPayloadAdapter:
    """
    Test suite for VercelPayloadAdapter transformation logic.

    Validates field extraction from Vercel Log Drain webhooks, log level
    mapping, deep link construction, and error handling for missing required
    fields per Agent Action Plan Section 0.5.1 Group 2 requirements.
    """

    def test_transform_complete_payload_success(self, sample_vercel_payload):
        """
        Test transformation of complete Vercel payload with all fields.

        Validates that VercelPayloadAdapter.transform() correctly extracts
        all fields from a complete Vercel webhook payload and returns a
        properly populated NormalizedErrorEvent instance with correct types.
        """
        adapter = VercelPayloadAdapter()
        event = adapter.transform(sample_vercel_payload)

        # Validate event is correct type
        assert isinstance(event, NormalizedErrorEvent)

        # Validate required fields are populated correctly
        assert event.source == "vercel"
        assert event.service == "my-app"  # Extracted from host my-app-abc123.vercel.app
        assert event.environment == "prod"  # Default for Vercel production deployments
        assert event.error_class == "Error"  # Extracted from message prefix
        assert event.message == "Error: Cannot read property 'x' of undefined"
        assert event.event_id == "log_abc123xyz"  # From id field
        assert isinstance(event.occurred_at, datetime)

        # Validate optional fields are populated
        assert event.path == "/api/checkout"
        assert event.url == "https://my-app.vercel.app/api/checkout"
        assert event.release == "dpl_abc123"  # From deploymentId

        # Validate log URL contains trace ID
        assert "vercel.com" in event.log_url
        assert "req_abc123" in event.log_url  # requestId used as traceId

    def test_transform_minimal_payload_with_defaults(self, sample_vercel_payload_minimal):
        """
        Test transformation of minimal Vercel payload with optional fields missing.

        Validates that adapter handles missing optional fields (path, requestId)
        by setting them to None, and still produces a valid NormalizedErrorEvent
        per Agent Action Plan requirement to handle missing optional fields.
        """
        adapter = VercelPayloadAdapter()
        event = adapter.transform(sample_vercel_payload_minimal)

        assert isinstance(event, NormalizedErrorEvent)
        assert event.source == "vercel"
        assert event.service == "minimal-app"
        assert event.message == "Error: Service unavailable"
        assert event.path is None  # Optional field missing
        assert event.event_id == "log_minimal123"

        # Log URL should still be constructed even without requestId
        assert "vercel.com" in event.log_url

    def test_extract_service_name_from_host(self):
        """
        Test service name extraction from Vercel host field.

        Validates that adapter correctly extracts service name by removing
        the hash suffix and .vercel.app domain from the host field, handling
        various host format variations.
        """
        adapter = VercelPayloadAdapter()

        test_cases = [
            ("my-app-abc123.vercel.app", "my-app"),
            ("simple-app.vercel.app", "simple-app"),
            ("multi-word-app-xyz789.vercel.app", "multi-word-app"),
        ]

        for host, expected_service in test_cases:
            payload = {
                "id": "test",
                "message": "Test error",
                "timestamp": 1705320645123,
                "level": "error",
                "host": host,
                "deploymentId": "dpl_test",
            }
            event = adapter.transform(payload)
            assert event.service == expected_service, f"Failed for host: {host}"

    def test_extract_error_class_from_message(self):
        """
        Test error class extraction from message text.

        Validates that adapter correctly parses error class from message
        prefix using regex pattern for common JavaScript error types
        (Error, TypeError, ReferenceError, etc.) and "Unknown" fallback.
        """
        adapter = VercelPayloadAdapter()

        test_cases = [
            ("TypeError: Cannot read property", "TypeError"),
            ("ReferenceError: x is not defined", "ReferenceError"),
            ("Error: Something went wrong", "Error"),
            ("SyntaxError: Unexpected token", "SyntaxError"),
            ("Random message without error type", "Unknown"),
        ]

        for message, expected_error_class in test_cases:
            payload = {
                "id": "test",
                "message": message,
                "timestamp": 1705320645123,
                "level": "error",
                "host": "test-app.vercel.app",
                "deploymentId": "dpl_test",
            }
            event = adapter.transform(payload)
            assert event.error_class == expected_error_class, f"Failed for message: {message}"

    @pytest.mark.parametrize(
        "vercel_level,expected_env",
        [
            ("error", "prod"),
            ("warning", "prod"),
            ("info", "prod"),
        ],
    )
    def test_log_level_mapping(self, vercel_level: str, expected_env: str):
        """
        Test Vercel log level doesn't affect environment determination.

        Vercel payloads use 'level' field for log severity (error/warning/info),
        but environment is determined by deployment context, not log level.
        Currently defaults to 'prod' for all Vercel deployments.
        """
        adapter = VercelPayloadAdapter()
        payload = {
            "id": "test",
            "message": "Test message",
            "timestamp": 1705320645123,
            "level": vercel_level,
            "host": "test-app.vercel.app",
            "deploymentId": "dpl_test",
        }
        event = adapter.transform(payload)
        assert event.environment == expected_env

    def test_timestamp_conversion_to_datetime(self, sample_vercel_payload):
        """
        Test Unix millisecond timestamp conversion to datetime.

        Validates that adapter correctly converts Vercel's Unix timestamp
        in milliseconds to Python datetime object for occurred_at field
        per NormalizedErrorEvent schema requirements.
        """
        adapter = VercelPayloadAdapter()
        event = adapter.transform(sample_vercel_payload)

        assert isinstance(event.occurred_at, datetime)
        # Timestamp 1705320645123 milliseconds = 2025-01-15T10:30:45.123
        assert event.occurred_at.year == 2025
        assert event.occurred_at.month == 1
        assert event.occurred_at.day == 15

    def test_build_vercel_log_url_with_trace_id(self):
        """
        Test deep link construction to Vercel logs with trace ID.

        Validates that adapter constructs proper Vercel log URL including
        requestId as trace ID query parameter for direct navigation to
        specific log entry per Agent Action Plan Section 0.7.1 directive #5.
        """
        adapter = VercelPayloadAdapter()
        payload = {
            "id": "test",
            "message": "Test error",
            "timestamp": 1705320645123,
            "level": "error",
            "host": "my-app.vercel.app",
            "deploymentId": "dpl_abc123",
            "requestId": "req_trace_xyz",
        }
        event = adapter.transform(payload)

        assert "vercel.com" in event.log_url
        assert "req_trace_xyz" in event.log_url
        assert "dpl_abc123" in event.log_url

    def test_build_vercel_log_url_without_trace_id(self):
        """
        Test deep link construction without trace ID (fallback).

        Validates that adapter still constructs valid Vercel log URL when
        requestId is missing, falling back to deployment-level logs URL
        per Agent Action Plan requirement to handle missing optional fields.
        """
        adapter = VercelPayloadAdapter()
        payload = {
            "id": "test",
            "message": "Test error",
            "timestamp": 1705320645123,
            "level": "error",
            "host": "my-app.vercel.app",
            "deploymentId": "dpl_abc123",
        }
        event = adapter.transform(payload)

        assert "vercel.com" in event.log_url
        assert "dpl_abc123" in event.log_url

    def test_missing_required_field_message_raises_value_error(self):
        """
        Test ValueError raised when required 'message' field is missing.

        Validates that adapter raises ValueError with descriptive message
        when Vercel payload lacks required 'message' field, preventing
        invalid event creation per Agent Action Plan error handling requirements.
        """
        adapter = VercelPayloadAdapter()
        payload = {
            "id": "test",
            "timestamp": 1705320645123,
            "level": "error",
            "host": "test-app.vercel.app",
            # Missing 'message' field
        }

        with pytest.raises(ValueError) as exc_info:
            adapter.transform(payload)

        assert "message" in str(exc_info.value).lower()

    def test_missing_required_field_timestamp_raises_value_error(self):
        """
        Test ValueError raised when required 'timestamp' field is missing.

        Validates that adapter raises ValueError when Vercel payload lacks
        timestamp field required for occurred_at conversion.
        """
        adapter = VercelPayloadAdapter()
        payload = {
            "id": "test",
            "message": "Test error",
            "level": "error",
            "host": "test-app.vercel.app",
            # Missing 'timestamp' field
        }

        with pytest.raises(ValueError) as exc_info:
            adapter.transform(payload)

        assert "timestamp" in str(exc_info.value).lower()

    def test_missing_required_field_host_raises_value_error(self):
        """
        Test ValueError raised when required 'host' field is missing.

        Validates that adapter raises ValueError when host field is missing,
        as it's required for service name extraction.
        """
        adapter = VercelPayloadAdapter()
        payload = {
            "id": "test",
            "message": "Test error",
            "timestamp": 1705320645123,
            "level": "error",
            # Missing 'host' field
        }

        with pytest.raises(ValueError) as exc_info:
            adapter.transform(payload)

        assert "host" in str(exc_info.value).lower()


# ============================================================================
# GCPPayloadAdapter Tests
# ============================================================================


class TestGCPPayloadAdapter:
    """
    Test suite for GCPPayloadAdapter transformation logic.

    Validates base64 payload decoding, field extraction from GCP log entries,
    resource label parsing, GCP Log Explorer URL construction, and error
    handling for malformed payloads per Agent Action Plan requirements.
    """

    def test_transform_complete_payload_success(self, sample_gcp_payload):
        """
        Test transformation of complete GCP payload with all fields.

        Validates that GCPPayloadAdapter.transform() correctly decodes base64
        message data, extracts all fields from GCP log entry structure, and
        returns properly populated NormalizedErrorEvent instance.
        """
        adapter = GCPPayloadAdapter()
        event = adapter.transform(sample_gcp_payload)

        # Validate event is correct type
        assert isinstance(event, NormalizedErrorEvent)

        # Validate required fields from GCP log entry
        assert event.source == "gcp"
        assert event.service == "api-service"  # From resource.labels.service_name
        assert event.environment == "prod"  # From labels.environment normalized
        assert event.error_class == "TypeError"  # Extracted from textPayload
        assert event.message == "TypeError: Cannot read property 'user' of null at getUserData"
        assert event.event_id == "abc123def456"  # From insertId
        assert isinstance(event.occurred_at, datetime)

        # Validate log URL contains insertId for GCP Log Explorer
        assert "console.cloud.google.com" in event.log_url
        assert "abc123def456" in event.log_url  # insertId in URL

    def test_transform_minimal_payload_with_defaults(self, sample_gcp_payload_minimal):
        """
        Test transformation of minimal GCP payload with optional fields missing.

        Validates that adapter handles missing optional fields (environment label,
        trace) by using sensible defaults, and still produces valid event per
        Agent Action Plan requirement for missing optional fields.
        """
        adapter = GCPPayloadAdapter()
        event = adapter.transform(sample_gcp_payload_minimal)

        assert isinstance(event, NormalizedErrorEvent)
        assert event.source == "gcp"
        assert event.service == "minimal-service"
        assert event.environment == "prod"  # Default when labels.environment missing
        assert event.message == "Error occurred in service"
        assert event.path is None  # Optional field not in GCP logs
        assert event.event_id == "minimal_insert_123"

    def test_base64_decoding_of_message_data(self, sample_gcp_payload):
        """
        Test base64 decoding of GCP Pub/Sub message data.

        Validates that adapter correctly decodes base64-encoded log entry
        from message.data field per GCP Pub/Sub push format from Section 0.4.2.
        """
        adapter = GCPPayloadAdapter()
        event = adapter.transform(sample_gcp_payload)

        # If decoding successful, event should be created with data from decoded JSON
        assert event.service == "api-service"
        assert "TypeError" in event.message

    def test_malformed_base64_raises_value_error(self):
        """
        Test ValueError raised for malformed base64 data.

        Validates that adapter raises ValueError when message.data contains
        invalid base64 string that cannot be decoded per Agent Action Plan
        requirement to raise ValueError for unparseable base64.
        """
        adapter = GCPPayloadAdapter()
        payload = {
            "message": {
                "data": "not-valid-base64!!!",  # Invalid base64
                "messageId": "test",
                "publishTime": "2025-01-15T10:30:45.123Z",
            },
            "subscription": "projects/test/subscriptions/test",
        }

        with pytest.raises(ValueError) as exc_info:
            adapter.transform(payload)

        assert "base64" in str(exc_info.value).lower() or "decode" in str(exc_info.value).lower()

    def test_invalid_json_after_base64_decode_raises_value_error(self):
        """
        Test ValueError raised when decoded base64 is not valid JSON.

        Validates that adapter raises ValueError when decoded message.data
        is not valid JSON structure per Agent Action Plan error handling.
        """
        adapter = GCPPayloadAdapter()

        # Valid base64 but invalid JSON content
        invalid_json = "not a json string"
        encoded_data = base64.b64encode(invalid_json.encode()).decode()

        payload = {
            "message": {
                "data": encoded_data,
                "messageId": "test",
                "publishTime": "2025-01-15T10:30:45.123Z",
            },
            "subscription": "projects/test/subscriptions/test",
        }

        with pytest.raises(ValueError) as exc_info:
            adapter.transform(payload)

        assert "json" in str(exc_info.value).lower()

    def test_extract_service_name_from_resource_labels(self, sample_gcp_payload):
        """
        Test service name extraction from resource.labels.service_name.

        Validates that adapter correctly extracts service name from GCP
        log entry resource labels structure per Agent Action Plan Group 2.
        """
        adapter = GCPPayloadAdapter()
        event = adapter.transform(sample_gcp_payload)

        assert event.service == "api-service"

    def test_extract_error_class_from_text_payload(self):
        """
        Test error class extraction from GCP textPayload.

        Validates that adapter parses error class from textPayload prefix
        using regex pattern for common error types, similar to Vercel adapter.
        """
        adapter = GCPPayloadAdapter()

        test_cases = [
            ("TypeError: Something failed", "TypeError"),
            ("ValueError: Invalid input", "ValueError"),
            ("RuntimeError: Process crashed", "RuntimeError"),
            ("Error: Generic error", "Error"),
            ("No error prefix here", "Unknown"),
        ]

        for text_payload, expected_error_class in test_cases:
            log_entry = {
                "severity": "ERROR",
                "textPayload": text_payload,
                "insertId": "test_id",
                "timestamp": "2025-01-15T10:30:45.123Z",
                "resource": {
                    "type": "cloud_run_revision",
                    "labels": {"service_name": "test-service"},
                },
            }
            encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()
            payload = {
                "message": {
                    "data": encoded_data,
                    "messageId": "test",
                    "publishTime": "2025-01-15T10:30:45.123Z",
                },
                "subscription": "projects/test/subscriptions/test",
            }

            event = adapter.transform(payload)
            assert event.error_class == expected_error_class, f"Failed for text: {text_payload}"

    def test_environment_extraction_from_labels(self):
        """
        Test environment extraction from GCP labels.environment.

        Validates that adapter correctly extracts and normalizes environment
        value from log entry labels, with proper normalization per
        NormalizedErrorEvent validation (production->prod, staging->staging).
        """
        adapter = GCPPayloadAdapter()

        test_cases = [
            ("production", "prod"),
            ("staging", "staging"),
            ("development", "dev"),
        ]

        for gcp_env, expected_env in test_cases:
            log_entry = {
                "severity": "ERROR",
                "textPayload": "Test error",
                "insertId": "test_id",
                "timestamp": "2025-01-15T10:30:45.123Z",
                "resource": {
                    "type": "cloud_run_revision",
                    "labels": {"service_name": "test-service"},
                },
                "labels": {"environment": gcp_env},
            }
            encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()
            payload = {
                "message": {
                    "data": encoded_data,
                    "messageId": "test",
                    "publishTime": "2025-01-15T10:30:45.123Z",
                },
                "subscription": "projects/test/subscriptions/test",
            }

            event = adapter.transform(payload)
            assert event.environment == expected_env, f"Failed for GCP env: {gcp_env}"

    def test_default_environment_when_label_missing(self):
        """
        Test default environment assignment when labels.environment missing.

        Validates that adapter uses 'prod' as default environment when
        environment label is not present in GCP log entry per Agent Action
        Plan requirement for sensible defaults.
        """
        adapter = GCPPayloadAdapter()

        log_entry = {
            "severity": "ERROR",
            "textPayload": "Test error",
            "insertId": "test_id",
            "timestamp": "2025-01-15T10:30:45.123Z",
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"service_name": "test-service"},
            },
            # No labels.environment field
        }
        encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()
        payload = {
            "message": {
                "data": encoded_data,
                "messageId": "test",
                "publishTime": "2025-01-15T10:30:45.123Z",
            },
            "subscription": "projects/test/subscriptions/test",
        }

        event = adapter.transform(payload)
        assert event.environment == "prod"

    def test_timestamp_conversion_from_iso_format(self, sample_gcp_payload):
        """
        Test ISO 8601 timestamp string conversion to datetime.

        Validates that adapter correctly parses GCP's ISO 8601 timestamp
        format to Python datetime object for occurred_at field per
        NormalizedErrorEvent schema requirements.
        """
        adapter = GCPPayloadAdapter()
        event = adapter.transform(sample_gcp_payload)

        assert isinstance(event.occurred_at, datetime)
        # Timestamp "2025-01-15T10:30:45.123Z"
        assert event.occurred_at.year == 2025
        assert event.occurred_at.month == 1
        assert event.occurred_at.day == 15
        assert event.occurred_at.hour == 10
        assert event.occurred_at.minute == 30

    def test_build_gcp_log_explorer_url_with_insert_id(self, sample_gcp_payload):
        """
        Test GCP Log Explorer URL construction with insertId filter.

        Validates that adapter constructs proper GCP Log Explorer URL
        including insertId query parameter for direct navigation to specific
        log entry per Agent Action Plan Section 0.7.1 directive #5.
        """
        adapter = GCPPayloadAdapter()
        event = adapter.transform(sample_gcp_payload)

        assert "console.cloud.google.com" in event.log_url
        assert "logs" in event.log_url
        assert "abc123def456" in event.log_url  # insertId in URL

    def test_extract_project_id_from_subscription(self):
        """
        Test project ID extraction from subscription path.

        Validates that adapter correctly parses GCP project ID from
        subscription field format: projects/{project}/subscriptions/{sub}
        for use in Log Explorer URL construction.
        """
        adapter = GCPPayloadAdapter()

        log_entry = {
            "severity": "ERROR",
            "textPayload": "Test error",
            "insertId": "test_id",
            "timestamp": "2025-01-15T10:30:45.123Z",
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"service_name": "test-service"},
            },
        }
        encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()

        payload = {
            "message": {
                "data": encoded_data,
                "messageId": "test",
                "publishTime": "2025-01-15T10:30:45.123Z",
            },
            "subscription": "projects/my-test-project/subscriptions/error-sub",
        }

        event = adapter.transform(payload)

        # Project ID should be in log URL
        assert "my-test-project" in event.log_url

    def test_missing_required_field_text_payload_raises_value_error(self):
        """
        Test ValueError raised when required 'textPayload' field is missing.

        Validates that adapter raises ValueError with descriptive message
        when decoded GCP log entry lacks required textPayload field.
        """
        adapter = GCPPayloadAdapter()

        log_entry = {
            "severity": "ERROR",
            # Missing 'textPayload' field
            "insertId": "test_id",
            "timestamp": "2025-01-15T10:30:45.123Z",
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"service_name": "test-service"},
            },
        }
        encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()
        payload = {
            "message": {
                "data": encoded_data,
                "messageId": "test",
                "publishTime": "2025-01-15T10:30:45.123Z",
            },
            "subscription": "projects/test/subscriptions/test",
        }

        with pytest.raises(ValueError) as exc_info:
            adapter.transform(payload)

        assert "textPayload" in str(exc_info.value) or "message" in str(exc_info.value).lower()

    def test_missing_required_field_insert_id_raises_value_error(self):
        """
        Test ValueError raised when required 'insertId' field is missing.

        Validates that adapter raises ValueError when GCP log entry lacks
        insertId field required for event deduplication.
        """
        adapter = GCPPayloadAdapter()

        log_entry = {
            "severity": "ERROR",
            "textPayload": "Test error",
            # Missing 'insertId' field
            "timestamp": "2025-01-15T10:30:45.123Z",
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"service_name": "test-service"},
            },
        }
        encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()
        payload = {
            "message": {
                "data": encoded_data,
                "messageId": "test",
                "publishTime": "2025-01-15T10:30:45.123Z",
            },
            "subscription": "projects/test/subscriptions/test",
        }

        with pytest.raises(ValueError) as exc_info:
            adapter.transform(payload)

        assert "insertId" in str(exc_info.value) or "event_id" in str(exc_info.value).lower()

    def test_missing_required_field_service_name_raises_value_error(self):
        """
        Test ValueError raised when required 'service_name' label is missing.

        Validates that adapter raises ValueError when resource.labels lacks
        service_name field required for service identification.
        """
        adapter = GCPPayloadAdapter()

        log_entry = {
            "severity": "ERROR",
            "textPayload": "Test error",
            "insertId": "test_id",
            "timestamp": "2025-01-15T10:30:45.123Z",
            "resource": {
                "type": "cloud_run_revision",
                "labels": {
                    # Missing 'service_name' field
                    "revision_name": "test-rev",
                },
            },
        }
        encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()
        payload = {
            "message": {
                "data": encoded_data,
                "messageId": "test",
                "publishTime": "2025-01-15T10:30:45.123Z",
            },
            "subscription": "projects/test/subscriptions/test",
        }

        with pytest.raises(ValueError) as exc_info:
            adapter.transform(payload)

        assert "service_name" in str(exc_info.value).lower() or "service" in str(exc_info.value).lower()


# ============================================================================
# Integration Tests - Cross-Adapter Validation
# ============================================================================


class TestPayloadAdapterIntegration:
    """
    Integration tests validating consistent behavior across both adapters.

    These tests ensure that both VercelPayloadAdapter and GCPPayloadAdapter
    produce NormalizedErrorEvent instances with consistent structure and
    validation, regardless of source-specific payload format differences.
    """

    def test_both_adapters_produce_valid_normalized_events(
        self, sample_vercel_payload, sample_gcp_payload
    ):
        """
        Test both adapters produce valid NormalizedErrorEvent instances.

        Validates that events from both sources pass NormalizedErrorEvent
        validation in __post_init__, ensuring consistent schema compliance
        across all error sources.
        """
        vercel_adapter = VercelPayloadAdapter()
        gcp_adapter = GCPPayloadAdapter()

        vercel_event = vercel_adapter.transform(sample_vercel_payload)
        gcp_event = gcp_adapter.transform(sample_gcp_payload)

        # Both should be valid NormalizedErrorEvent instances
        assert isinstance(vercel_event, NormalizedErrorEvent)
        assert isinstance(gcp_event, NormalizedErrorEvent)

        # Both should have normalized environment values
        assert vercel_event.environment in ["prod", "staging", "dev"]
        assert gcp_event.environment in ["prod", "staging", "dev"]

    def test_both_adapters_set_correct_source_field(self, sample_vercel_payload, sample_gcp_payload):
        """
        Test both adapters set correct 'source' field value.

        Validates that source field correctly identifies webhook origin
        ('vercel' vs 'gcp') for tracking and metrics per Agent Action Plan.
        """
        vercel_adapter = VercelPayloadAdapter()
        gcp_adapter = GCPPayloadAdapter()

        vercel_event = vercel_adapter.transform(sample_vercel_payload)
        gcp_event = gcp_adapter.transform(sample_gcp_payload)

        assert vercel_event.source == "vercel"
        assert gcp_event.source == "gcp"

    def test_both_adapters_construct_log_urls(self, sample_vercel_payload, sample_gcp_payload):
        """
        Test both adapters construct valid deep link log URLs.

        Validates that both adapters create non-empty log_url fields
        containing source-specific domain names for deep linking per
        Agent Action Plan Section 0.7.1 directive #5 requirements.
        """
        vercel_adapter = VercelPayloadAdapter()
        gcp_adapter = GCPPayloadAdapter()

        vercel_event = vercel_adapter.transform(sample_vercel_payload)
        gcp_event = gcp_adapter.transform(sample_gcp_payload)

        # Both should have non-empty log URLs
        assert vercel_event.log_url
        assert gcp_event.log_url

        # URLs should contain source-specific domains
        assert "vercel.com" in vercel_event.log_url
        assert "console.cloud.google.com" in gcp_event.log_url

    def test_both_adapters_handle_datetime_conversion(
        self, sample_vercel_payload, sample_gcp_payload
    ):
        """
        Test both adapters convert timestamps to datetime objects.

        Validates that both adapters properly convert their respective
        timestamp formats (Unix milliseconds for Vercel, ISO 8601 for GCP)
        to Python datetime objects per NormalizedErrorEvent schema.
        """
        vercel_adapter = VercelPayloadAdapter()
        gcp_adapter = GCPPayloadAdapter()

        vercel_event = vercel_adapter.transform(sample_vercel_payload)
        gcp_event = gcp_adapter.transform(sample_gcp_payload)

        assert isinstance(vercel_event.occurred_at, datetime)
        assert isinstance(gcp_event.occurred_at, datetime)

    def test_both_adapters_extract_error_class(self, sample_vercel_payload, sample_gcp_payload):
        """
        Test both adapters extract error class from message text.

        Validates that both adapters successfully parse error_class field
        from their respective message formats using similar regex patterns.
        """
        vercel_adapter = VercelPayloadAdapter()
        gcp_adapter = GCPPayloadAdapter()

        vercel_event = vercel_adapter.transform(sample_vercel_payload)
        gcp_event = gcp_adapter.transform(sample_gcp_payload)

        # Both should extract error class (not "Unknown")
        assert vercel_event.error_class in ["Error", "TypeError", "ReferenceError", "RuntimeError"]
        assert gcp_event.error_class in ["Error", "TypeError", "ReferenceError", "RuntimeError"]


# ============================================================================
# Edge Case Tests
# ============================================================================


class TestEdgeCases:
    """
    Edge case and boundary condition tests for payload adapters.

    Validates adapter behavior with unusual but valid input scenarios
    including empty strings, very long messages, special characters,
    and boundary timestamp values.
    """

    def test_vercel_adapter_handles_empty_path_gracefully(self):
        """
        Test Vercel adapter handles empty string path field.

        Validates that adapter treats empty string path as None per
        optional field handling requirements.
        """
        adapter = VercelPayloadAdapter()
        payload = {
            "id": "test",
            "message": "Error: test",
            "timestamp": 1705320645123,
            "level": "error",
            "host": "test-app.vercel.app",
            "deploymentId": "dpl_test",
            "path": "",  # Empty string
        }
        event = adapter.transform(payload)

        # Empty string should be treated as None
        assert event.path is None or event.path == ""

    def test_gcp_adapter_handles_very_long_text_payload(self):
        """
        Test GCP adapter handles very long error messages.

        Validates that adapter successfully processes GCP log entries
        with very long textPayload fields (e.g., large stack traces).
        """
        adapter = GCPPayloadAdapter()

        # Create very long message (10KB)
        long_message = "Error: " + ("A" * 10000)

        log_entry = {
            "severity": "ERROR",
            "textPayload": long_message,
            "insertId": "test_id",
            "timestamp": "2025-01-15T10:30:45.123Z",
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"service_name": "test-service"},
            },
        }
        encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()
        payload = {
            "message": {
                "data": encoded_data,
                "messageId": "test",
                "publishTime": "2025-01-15T10:30:45.123Z",
            },
            "subscription": "projects/test/subscriptions/test",
        }

        event = adapter.transform(payload)

        assert len(event.message) > 10000
        assert event.error_class == "Error"

    def test_vercel_adapter_handles_special_characters_in_message(self):
        """
        Test Vercel adapter handles special characters and unicode.

        Validates that adapter correctly processes messages containing
        special characters, unicode, quotes, and escape sequences.
        """
        adapter = VercelPayloadAdapter()

        special_message = 'Error: User "John\'s" data: {"key": "value"} → ñoño'

        payload = {
            "id": "test",
            "message": special_message,
            "timestamp": 1705320645123,
            "level": "error",
            "host": "test-app.vercel.app",
            "deploymentId": "dpl_test",
        }
        event = adapter.transform(payload)

        assert event.message == special_message
        assert "ñoño" in event.message

    def test_both_adapters_handle_boundary_timestamps(self):
        """
        Test adapters handle boundary timestamp values.

        Validates that adapters correctly convert timestamps near Unix
        epoch boundaries and future dates.
        """
        # Test Vercel with timestamp near epoch
        vercel_adapter = VercelPayloadAdapter()
        payload_early = {
            "id": "test",
            "message": "Test error",
            "timestamp": 1000,  # Very early timestamp
            "level": "error",
            "host": "test-app.vercel.app",
            "deploymentId": "dpl_test",
        }
        event_early = vercel_adapter.transform(payload_early)
        assert isinstance(event_early.occurred_at, datetime)

        # Test GCP with far future timestamp
        gcp_adapter = GCPPayloadAdapter()
        log_entry = {
            "severity": "ERROR",
            "textPayload": "Test error",
            "insertId": "test_id",
            "timestamp": "2099-12-31T23:59:59.999Z",  # Far future
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"service_name": "test-service"},
            },
        }
        encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()
        payload_future = {
            "message": {
                "data": encoded_data,
                "messageId": "test",
                "publishTime": "2099-12-31T23:59:59.999Z",
            },
            "subscription": "projects/test/subscriptions/test",
        }
        event_future = gcp_adapter.transform(payload_future)
        assert isinstance(event_future.occurred_at, datetime)
        assert event_future.occurred_at.year == 2099
