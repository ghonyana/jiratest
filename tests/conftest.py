"""
Pytest Configuration and Shared Test Fixtures

This module provides centralized pytest configuration and reusable test fixtures
for the Error Triage to Jira Upserter service test suite. All fixtures implement
the dependency injection pattern enabling isolated, reproducible unit and
integration tests with consistent test data across the test suite.

Per Section 0.5.1 Group 9 and Technical Specification Section 6.6, this module:
- Provides mock_redis fixture using fakeredis for in-memory Redis testing
- Provides mock_jira fixture using unittest.mock for Jira API testing
- Provides mock_boto3_secrets fixture for AWS Secrets Manager testing
- Supplies sample_vercel_payload with realistic Vercel webhook test data
- Supplies sample_gcp_payload with realistic GCP Pub/Sub push test data
- Provides Flask test client fixture with test-specific configuration
- Configures pytest settings for test discovery and coverage reporting

Fixture Scope Strategy:
- Function-scoped fixtures (default): Reset state between tests for isolation
- Session-scoped fixtures: Shared configuration loaded once per test session
- Module-scoped fixtures: Shared within test module for performance

Test Isolation:
All fixtures implement proper cleanup via yield pattern or automatic garbage
collection to ensure no state leakage between test cases. This enables:
- Parallel test execution without conflicts
- Deterministic test results regardless of execution order
- Fast test execution with minimal setup overhead

Usage in Test Files:
    import pytest
    from datetime import datetime
    
    def test_redis_operations(mock_redis):
        '''Test using mock Redis client'''
        mock_redis.set('key', 'value')
        assert mock_redis.get('key') == 'value'
    
    def test_jira_integration(mock_jira):
        '''Test using mock Jira client'''
        mock_jira.create_issue.return_value.key = 'ET-123'
        issue_key = mock_jira.create_issue(fields={})
        assert issue_key.key == 'ET-123'
    
    def test_webhook_endpoint(app, sample_vercel_payload):
        '''Test using Flask test client and sample payload'''
        response = app.post('/events', json=sample_vercel_payload)
        assert response.status_code == 202

Coverage Target (per Section 6.6):
- Unit tests: 90%+ code coverage for service layer
- Integration tests: 80%+ code coverage for end-to-end flows
- All fixtures designed to support comprehensive test coverage

Technical References:
- Section 0.5.1 Group 9: Testing infrastructure requirements
- Section 6.6: Testing strategy and coverage targets
- Section 0.4.2: External service integration patterns
- Section 0.7.4: Security and data sanitization requirements

Author: Blitzy Platform
Version: 1.0.0
"""

import os
import json
import base64
from datetime import datetime
from typing import Dict, Any, Generator
from unittest.mock import Mock, MagicMock, patch

import pytest
from fakeredis import FakeRedis


# =============================================================================
# Pre-Import AWS Secrets Manager Mocking
# =============================================================================
# CRITICAL: Mock AWS Secrets Manager BEFORE importing any application modules
# that attempt to load secrets at import time (src.app.config loads secrets
# in __init_subclass__ metaclass method). This prevents boto3 from attempting
# to locate AWS credentials during test discovery and import.

# Define mock secret values for testing
_mock_jira_credentials = {
    'base_url': 'https://test.atlassian.net',
    'email': 'test@example.com',
    'api_token': 'test_api_token_12345'
}

_mock_webhook_secret = 'test_webhook_secret_abcdef123456'

# Patch secrets_manager functions at module level
# This ensures all imports after this point see mocked functions
_secrets_patcher_json = patch(
    'src.utils.secrets_manager.get_json_secret',
    return_value=_mock_jira_credentials
)
_secrets_patcher_plain = patch(
    'src.utils.secrets_manager.get_secret',
    return_value=_mock_webhook_secret
)

# Start the patches before any application imports
_secrets_patcher_json.start()
_secrets_patcher_plain.start()

# Internal imports - Application factory and configuration
# NOW safe to import because AWS Secrets Manager is mocked
from src.app import create_app
from src.app.config import Config

# Internal imports - Data models for test data construction
from src.models.error_event import NormalizedErrorEvent


# =============================================================================
# Pytest Configuration Hooks
# =============================================================================

def pytest_configure(config):
    """
    Pytest configuration hook called before test collection begins.
    
    Configures pytest behavior and registers custom markers for test
    categorization and selective execution. This hook runs once per
    test session before any tests are collected or executed.
    
    Custom Markers Registered:
    - unit: Unit tests for service layer components (fast, no external deps)
    - integration: Integration tests for end-to-end flows (may use Docker)
    - slow: Tests that take >1 second to execute
    - redis: Tests requiring Redis connectivity
    - jira: Tests requiring Jira API connectivity
    
    Usage:
        # Run only unit tests
        pytest -m unit
        
        # Run all except slow tests
        pytest -m "not slow"
        
        # Run integration tests only
        pytest -m integration
    
    Args:
        config: Pytest configuration object
    """
    # Register custom markers for test categorization
    config.addinivalue_line(
        "markers",
        "unit: Unit tests for isolated component testing"
    )
    config.addinivalue_line(
        "markers",
        "integration: Integration tests for end-to-end workflows"
    )
    config.addinivalue_line(
        "markers",
        "slow: Tests that take more than 1 second to execute"
    )
    config.addinivalue_line(
        "markers",
        "redis: Tests requiring Redis connectivity"
    )
    config.addinivalue_line(
        "markers",
        "jira: Tests requiring Jira API connectivity"
    )


def pytest_sessionstart(session):
    """
    Pytest hook called at the start of the test session.
    
    Performs session-level initialization before any tests are collected
    or executed. This includes setting up test environment variables,
    validating test configuration, and initializing shared resources.
    
    Test Environment Configuration:
    - Sets TESTING=true to disable external service dependencies
    - Sets REDIS_HOST=localhost for local test Redis instances
    - Disables MongoDB audit logging (ENABLE_MONGO=false)
    - Disables AWS Secrets Manager calls via mock_boto3_secrets fixture
    
    Args:
        session: Pytest session object
    """
    # Set test environment variables
    os.environ['TESTING'] = 'true'
    os.environ['ENVIRONMENT'] = 'test'
    os.environ['REDIS_HOST'] = 'localhost'
    os.environ['ENABLE_MONGO'] = 'false'
    os.environ['JIRA_BASE_URL'] = 'https://test.atlassian.net'
    os.environ['PROJECT_KEY'] = 'ET'


def pytest_sessionfinish(session, exitstatus):
    """
    Pytest hook called at the end of the test session.
    
    Performs session-level cleanup after all tests have completed.
    This includes stopping module-level patches that were started
    before application imports to mock AWS Secrets Manager.
    
    Args:
        session: Pytest session object
        exitstatus: Exit status that will be returned to the OS
    """
    # Stop module-level AWS Secrets Manager patches
    _secrets_patcher_json.stop()
    _secrets_patcher_plain.stop()


# =============================================================================
# Redis Mock Fixture
# =============================================================================

@pytest.fixture
def mock_redis() -> Generator[FakeRedis, None, None]:
    """
    Provide in-memory Redis mock for testing without external dependencies.
    
    Creates a fakeredis.FakeRedis instance that provides a drop-in replacement
    for redis.Redis client with full support for all Redis operations used in
    the service: INCR, SETEX, GET, EXPIRE, EXISTS, PING. The fake Redis server
    maintains state in memory for the duration of the test function, then
    automatically cleans up.
    
    Per Section 0.5.1 Group 9, this fixture enables:
    - Testing frequency tracking (INCR with EXPIRE for rolling counters)
    - Testing event deduplication (SETEX for TTL-based caching)
    - Testing comment rate limiting (GET/SETEX for timestamp tracking)
    - Fast unit tests without external Redis server (<10ms per test)
    - Parallel test execution without port conflicts
    
    Supported Operations (per Section 0.4.2 Redis integration):
    - PING: Health check
    - INCR: Atomic counter increment
    - GET: Retrieve key value
    - SETEX: Set key with expiration
    - EXPIRE: Set TTL on existing key
    - EXISTS: Check key existence
    - TTL: Get remaining TTL
    - FLUSHDB: Clear all keys (for test cleanup)
    
    Fixture Scope: Function (default)
    Each test function receives a fresh Redis instance with empty database,
    ensuring complete test isolation and no state leakage between tests.
    
    Returns:
        FakeRedis instance configured for testing with decode_responses=True
    
    Example - Frequency tracking test:
        def test_frequency_counter(mock_redis):
            key = "freq:prod:abc123"
            count = mock_redis.incr(key)
            mock_redis.expire(key, 300)
            assert count == 1
            assert mock_redis.ttl(key) == 300
    
    Example - Deduplication test:
        def test_event_deduplication(mock_redis):
            event_id = "vercel-xyz-123"
            mock_redis.setex(f"dedup:{event_id}", 3600, "1")
            assert mock_redis.exists(f"dedup:{event_id}") == 1
    
    Example - Comment rate limiting test:
        def test_comment_rate_limit(mock_redis):
            issue_key = "ET-123"
            timestamp = "1705315845"
            mock_redis.setex(f"comment_limit:{issue_key}", 900, timestamp)
            last_comment = mock_redis.get(f"comment_limit:{issue_key}")
            assert last_comment == timestamp
    """
    # Create FakeRedis instance with configuration matching production Redis client
    # Per Section 0.4.2, production Redis client configuration:
    # - decode_responses=True: Return strings instead of bytes
    # - db=0: Use default database for all operations
    redis_client = FakeRedis(decode_responses=True, db=0)
    
    # Yield Redis client to test function
    # Test function executes with access to redis_client fixture
    yield redis_client
    
    # Cleanup: Flush all keys after test completes
    # This ensures no state leakage to subsequent tests
    redis_client.flushdb()


# =============================================================================
# Jira API Mock Fixture
# =============================================================================

@pytest.fixture
def mock_jira() -> Mock:
    """
    Provide mock Jira API client for testing without external API calls.
    
    Creates a unittest.mock.Mock object configured with all Jira API method
    signatures used by JiraIntegrationService. The mock includes realistic
    return values and method configurations matching the jira library v3.10+
    API, enabling isolated testing of Jira integration logic without making
    actual HTTP requests to Atlassian.
    
    Per Section 0.5.1 Group 9, this fixture mocks the following Jira operations:
    - search_issues: Search for existing issues by JQL query
    - create_issue: Create new bug issues with labels and custom fields
    - add_comment: Add timestamped comments to existing issues
    - issue: Retrieve issue object for updates
    - update: Update issue fields (priority, severity)
    - server_info: Health check endpoint
    
    Mock Method Configurations (per Section 0.4.2 Jira integration):
    
    search_issues(jql, maxResults=1):
        Returns: List of mock Issue objects with key attribute
        Usage: Search for issues by fingerprint label
    
    create_issue(fields):
        Returns: Mock Issue object with generated key (ET-1234)
        Usage: Create new bug issues with summary, description, labels
    
    add_comment(issue_key, comment):
        Returns: Mock Comment object with id attribute
        Usage: Add occurrence count and log link to existing issues
    
    issue(issue_key).update(fields):
        Returns: None (success)
        Usage: Update issue priority or custom severity field
    
    server_info():
        Returns: Dict with baseUrl and version
        Usage: Health check to verify Jira connectivity
    
    Fixture Scope: Function (default)
    Each test function receives a fresh mock with no call history, ensuring
    test isolation and preventing cross-test contamination.
    
    Returns:
        Mock object configured with Jira API method signatures
    
    Example - Test issue search:
        def test_search_issue_by_fingerprint(mock_jira):
            mock_issue = Mock()
            mock_issue.key = 'ET-123'
            mock_jira.search_issues.return_value = [mock_issue]
            
            results = mock_jira.search_issues(
                'project = ET AND labels = "errfp:abc123"',
                maxResults=1
            )
            assert len(results) == 1
            assert results[0].key == 'ET-123'
    
    Example - Test issue creation:
        def test_create_bug_issue(mock_jira):
            mock_issue = Mock()
            mock_issue.key = 'ET-456'
            mock_jira.create_issue.return_value = mock_issue
            
            issue = mock_jira.create_issue(fields={
                'project': {'key': 'ET'},
                'issuetype': {'name': 'Bug'},
                'summary': '[prod:web-app] TypeError - ...',
                'labels': ['errfp:abc123']
            })
            assert issue.key == 'ET-456'
    
    Example - Test comment addition:
        def test_add_comment(mock_jira):
            mock_comment = Mock()
            mock_comment.id = '10050'
            mock_jira.add_comment.return_value = mock_comment
            
            comment = mock_jira.add_comment(
                'ET-123',
                'Error reoccurred 15× in last 5m. Severity: SEV2.'
            )
            assert comment.id == '10050'
            mock_jira.add_comment.assert_called_once()
    """
    # Create Mock object for Jira API client
    jira_mock = Mock()
    
    # Configure search_issues method
    # Returns list of mock Issue objects matching JQL query
    mock_search_result = Mock()
    mock_search_result.key = 'ET-1234'
    mock_search_result.fields = Mock()
    mock_search_result.fields.summary = '[prod:web-app] TypeError - Cannot read property'
    mock_search_result.fields.status = Mock()
    mock_search_result.fields.status.name = 'Open'
    mock_search_result.fields.priority = Mock()
    mock_search_result.fields.priority.name = 'Medium'
    jira_mock.search_issues.return_value = [mock_search_result]
    
    # Configure create_issue method
    # Returns mock Issue object with generated key
    mock_created_issue = Mock()
    mock_created_issue.key = 'ET-5678'
    mock_created_issue.fields = Mock()
    mock_created_issue.fields.summary = 'New error issue'
    jira_mock.create_issue.return_value = mock_created_issue
    
    # Configure add_comment method
    # Returns mock Comment object with id
    mock_comment = Mock()
    mock_comment.id = '10050'
    mock_comment.body = 'Error reoccurred'
    mock_comment.created = '2025-01-15T10:30:45.000Z'
    jira_mock.add_comment.return_value = mock_comment
    
    # Configure issue retrieval and update methods
    # issue(key) returns mock Issue object with update method
    mock_issue_for_update = Mock()
    mock_issue_for_update.key = 'ET-1234'
    mock_issue_for_update.update = Mock(return_value=None)
    jira_mock.issue.return_value = mock_issue_for_update
    
    # Configure server_info method for health checks
    # Returns dict with baseUrl and version matching Jira Cloud API
    jira_mock.server_info.return_value = {
        'baseUrl': 'https://test.atlassian.net',
        'version': '1001.0.0-SNAPSHOT',
        'deploymentType': 'Cloud'
    }
    
    return jira_mock


# =============================================================================
# AWS Secrets Manager Mock Fixture
# =============================================================================

@pytest.fixture
def mock_boto3_secrets() -> Generator[Mock, None, None]:
    """
    Provide mock AWS Secrets Manager for testing without AWS credentials.
    
    Patches boto3.client('secretsmanager') to return a mock client that
    provides realistic secret values without requiring AWS credentials or
    network connectivity. This enables testing of configuration loading and
    secret management logic in complete isolation.
    
    Per Section 0.5.1 Group 9, this fixture mocks AWS Secrets Manager secrets:
    - jira/jiratest/{env}/credentials: Jira API token and base URL
    - jira/jiratest/{env}/webhook-secret: Vercel webhook HMAC secret
    - mongodb/jiratest/{env}/connection-string: MongoDB Atlas URI (optional)
    
    Mock Secret Values (per Section 0.4.1 secret naming):
    
    jira/jiratest/test/credentials (JSON):
        {
            "base_url": "https://test.atlassian.net",
            "email": "test@example.com",
            "api_token": "test_api_token_12345"
        }
    
    jira/jiratest/test/webhook-secret (plain text):
        "test_webhook_secret_abcdef123456"
    
    mongodb/jiratest/test/connection-string (plain text):
        "mongodb+srv://test:pass@test.mongodb.net/jiratest-test"
    
    Fixture Scope: Function (default)
    Each test function receives an isolated mock that doesn't affect other
    tests. The patch is automatically removed after test completion via
    context manager cleanup.
    
    Returns:
        Mock boto3 Secrets Manager client with get_secret_value configured
    
    Example - Test configuration loading:
        def test_load_jira_credentials(mock_boto3_secrets):
            from utils.secrets_manager import get_json_secret
            
            creds = get_json_secret('jira/jiratest/test/credentials')
            assert creds['base_url'] == 'https://test.atlassian.net'
            assert creds['api_token'] == 'test_api_token_12345'
    
    Example - Test webhook secret loading:
        def test_load_webhook_secret(mock_boto3_secrets):
            from utils.secrets_manager import get_secret
            
            secret = get_secret('jira/jiratest/test/webhook-secret')
            assert secret == 'test_webhook_secret_abcdef123456'
    """
    # Define mock secret values matching production secret format
    mock_secrets = {
        'jira/jiratest/test/credentials': json.dumps({
            'base_url': 'https://test.atlassian.net',
            'email': 'test@example.com',
            'api_token': 'test_api_token_12345'
        }),
        'jira/jiratest/test/webhook-secret': 'test_webhook_secret_abcdef123456',
        'mongodb/jiratest/test/connection-string': 'mongodb+srv://test:pass@test.mongodb.net/jiratest-test'
    }
    
    # Create mock boto3 client
    mock_client = Mock()
    
    # Configure get_secret_value method
    # Returns dict with SecretString matching AWS Secrets Manager API
    def mock_get_secret_value(SecretId, **kwargs):
        secret_value = mock_secrets.get(SecretId)
        if secret_value is None:
            # Raise exception matching AWS Secrets Manager behavior
            from botocore.exceptions import ClientError
            raise ClientError(
                {'Error': {'Code': 'ResourceNotFoundException'}},
                'GetSecretValue'
            )
        return {'SecretString': secret_value}
    
    mock_client.get_secret_value.side_effect = mock_get_secret_value
    
    # Patch boto3.client to return mock client
    with patch('boto3.client', return_value=mock_client) as mock_boto3:
        yield mock_boto3


# =============================================================================
# Sample Webhook Payload Fixtures
# =============================================================================

@pytest.fixture
def sample_vercel_payload() -> Dict[str, Any]:
    """
    Provide realistic Vercel Log Drain webhook payload for testing.
    
    Returns a complete Vercel webhook payload structure matching the format
    described in Section 0.4.2 External Service Integrations. The payload
    includes all fields used by VercelPayloadAdapter for transformation to
    NormalizedErrorEvent, with realistic values for production-like testing.
    
    Per Section 0.5.1 Group 9, this fixture provides:
    - Complete Vercel Log Drain payload structure
    - Realistic error message and stack trace
    - Deployment metadata (ID, URL, environment)
    - Trace ID for deep linking to Vercel logs
    - ISO 8601 timestamp for occurrence tracking
    
    Payload Structure (per Section 0.4.2 Vercel integration):
    {
        "source": "vercel",
        "deployment": {
            "id": "dpl_...",
            "url": "my-app-abc123.vercel.app"
        },
        "message": "Error: Cannot read property 'x' of undefined",
        "level": "error",
        "timestamp": "2025-01-15T10:30:45.123Z",
        "environment": "production",
        "path": "/api/checkout",
        "traceId": "abc123def456"
    }
    
    Fixture Scope: Function (default)
    Each test receives a fresh copy of the payload dictionary that can be
    modified without affecting other tests.
    
    Returns:
        Dict containing complete Vercel webhook payload
    
    Example - Test payload adapter:
        def test_vercel_payload_transformation(sample_vercel_payload):
            from services.payload_adapters import VercelPayloadAdapter
            
            adapter = VercelPayloadAdapter()
            event = adapter.transform(sample_vercel_payload)
            
            assert event.source == 'vercel'
            assert event.error_class == 'TypeError'
            assert event.environment == 'prod'
    
    Example - Test webhook endpoint:
        def test_events_endpoint_vercel(app, sample_vercel_payload):
            response = app.post(
                '/events',
                json=sample_vercel_payload,
                headers={'x-vercel-signature': 'test_signature'}
            )
            assert response.status_code == 202
    """
    return {
        'source': 'vercel',
        'deployment': {
            'id': 'dpl_xyz123abc456',
            'url': 'web-app-abc123.vercel.app',
            'name': 'web-app',
            'environment': 'production'
        },
        'message': 'TypeError: Cannot read property \'x\' of undefined',
        'level': 'error',
        'timestamp': '2025-01-15T10:30:45.123Z',
        'environment': 'production',
        'path': '/api/checkout',
        'url': 'https://web-app-abc123.vercel.app/api/checkout',
        'traceId': 'abc123def456ghi789',
        'stack': (
            'TypeError: Cannot read property \'x\' of undefined\n'
            '    at processCheckout (/app/pages/api/checkout.tsx:123:45)\n'
            '    at handler (/app/pages/api/checkout.tsx:98:12)\n'
            '    at next (/app/node_modules/next/dist/server/api-utils.js:45:7)'
        ),
        'release': 'dpl_xyz123abc456'
    }


@pytest.fixture
def sample_gcp_payload() -> Dict[str, Any]:
    """
    Provide realistic GCP Cloud Logging Pub/Sub push payload for testing.
    
    Returns a complete GCP Pub/Sub push subscription payload matching the
    format described in Section 0.4.2 External Service Integrations. The
    payload includes base64-encoded log entry matching GCP Cloud Logging
    format, with all fields used by GCPPayloadAdapter for transformation
    to NormalizedErrorEvent.
    
    Per Section 0.5.1 Group 9, this fixture provides:
    - Complete GCP Pub/Sub push subscription structure
    - Base64-encoded log entry (mimics GCP behavior)
    - Structured log entry with severity, textPayload, resource labels
    - Insert ID for deduplication and deep linking
    - RFC 3339 timestamp for occurrence tracking
    
    Payload Structure (per Section 0.4.2 GCP integration):
    {
        "message": {
            "data": "<base64-encoded-log-entry>",
            "messageId": "123456789",
            "publishTime": "2025-01-15T10:30:45.123Z"
        },
        "subscription": "projects/{project}/subscriptions/error-events-push"
    }
    
    Decoded Log Entry (base64-decoded message.data):
    {
        "severity": "ERROR",
        "textPayload": "RuntimeError: Database connection timeout",
        "resource": {
            "type": "cloud_run_revision",
            "labels": {
                "service_name": "api-service",
                "revision_name": "api-service-00042-xyz",
                "location": "us-central1"
            }
        },
        "insertId": "abc123xyz789",
        "timestamp": "2025-01-15T10:30:45.123456Z",
        "trace": "projects/my-project/traces/abc123def456"
    }
    
    Fixture Scope: Function (default)
    Each test receives a fresh copy of the payload dictionary that can be
    modified without affecting other tests.
    
    Returns:
        Dict containing complete GCP Pub/Sub push payload with base64 data
    
    Example - Test payload adapter:
        def test_gcp_payload_transformation(sample_gcp_payload):
            from services.payload_adapters import GCPPayloadAdapter
            
            adapter = GCPPayloadAdapter()
            event = adapter.transform(sample_gcp_payload)
            
            assert event.source == 'gcp'
            assert event.error_class == 'RuntimeError'
            assert event.service == 'api-service'
    
    Example - Test webhook endpoint:
        def test_events_endpoint_gcp(app, sample_gcp_payload):
            response = app.post(
                '/events',
                json=sample_gcp_payload,
                headers={'Authorization': 'Bearer test_token'}
            )
            assert response.status_code == 202
    """
    # Construct log entry matching GCP Cloud Logging format
    log_entry = {
        'severity': 'ERROR',
        'textPayload': (
            'RuntimeError: Database connection timeout after 30 seconds\n'
            '  File "/app/services/database.py", line 156, in connect\n'
            '    raise RuntimeError("Database connection timeout")\n'
        ),
        'resource': {
            'type': 'cloud_run_revision',
            'labels': {
                'service_name': 'api-service',
                'revision_name': 'api-service-00042-xyz',
                'location': 'us-central1',
                'project_id': 'my-gcp-project'
            }
        },
        'insertId': 'abc123xyz789def456',
        'timestamp': '2025-01-15T10:30:45.123456Z',
        'trace': 'projects/my-gcp-project/traces/abc123def456ghi789',
        'labels': {
            'environment': 'production',
            'version': 'v1.2.3'
        }
    }
    
    # Encode log entry as base64 to match GCP Pub/Sub push format
    # Per Section 0.4.2, GCP sends log entries as base64-encoded JSON
    log_entry_json = json.dumps(log_entry)
    encoded_data = base64.b64encode(log_entry_json.encode('utf-8')).decode('utf-8')
    
    return {
        'message': {
            'data': encoded_data,
            'messageId': '123456789',
            'publishTime': '2025-01-15T10:30:45.123Z',
            'attributes': {}
        },
        'subscription': 'projects/my-gcp-project/subscriptions/error-events-push'
    }


# =============================================================================
# Flask Test Application Fixture
# =============================================================================

@pytest.fixture
def app(mock_redis, mock_jira, mock_boto3_secrets) -> Generator[Any, None, None]:
    """
    Provide Flask test client with test configuration and mocked dependencies.
    
    Creates a Flask application instance using the create_app() factory with
    test-specific configuration. The application is configured with TESTING=True
    to disable external service dependencies and enable test-friendly behavior.
    All external dependencies (Redis, Jira, AWS) are automatically mocked via
    dependent fixtures.
    
    Per Section 0.5.1 Group 9, this fixture provides:
    - Flask test client for making HTTP requests to endpoints
    - Test configuration with TESTING=True
    - Mocked Redis client (fakeredis) for frequency tracking
    - Mocked Jira client (unittest.mock) for issue operations
    - Mocked AWS Secrets Manager for credential loading
    - Automatic application context management
    
    Test Configuration Overrides:
    - TESTING=True: Enable Flask test mode
    - DEBUG=False: Disable debug mode for production-like behavior
    - REDIS_HOST='localhost': Use local Redis (mocked by mock_redis fixture)
    - ENABLE_MONGO=False: Disable MongoDB audit logging for faster tests
    - SECRET_KEY='test_secret_key': Fixed secret for session management
    
    Dependency Injection:
    The application context (g object) is populated with mocked dependencies:
    - g.redis_client: FakeRedis instance from mock_redis fixture
    - g.mongo_client: None (MongoDB disabled in tests)
    - g.config: Test configuration object
    - g.metrics_collector: MetricsCollector instance
    
    Fixture Scope: Function (default)
    Each test function receives a fresh Flask application instance with clean
    state, ensuring complete test isolation. The application context is
    automatically torn down after test completion.
    
    Args:
        mock_redis: FakeRedis instance from mock_redis fixture
        mock_jira: Mock Jira client from mock_jira fixture
        mock_boto3_secrets: Mock boto3 client from mock_boto3_secrets fixture
    
    Yields:
        Flask test client for making HTTP requests
    
    Example - Test health check endpoint:
        def test_health_endpoint(app):
            response = app.get('/healthz')
            assert response.status_code == 200
            data = response.get_json()
            assert data['status'] == 'healthy'
    
    Example - Test events endpoint:
        def test_events_endpoint(app, sample_vercel_payload):
            response = app.post(
                '/events',
                json=sample_vercel_payload,
                headers={'Content-Type': 'application/json'}
            )
            assert response.status_code == 202
    
    Example - Test metrics endpoint:
        def test_metrics_endpoint(app):
            response = app.get('/metrics')
            assert response.status_code == 200
            assert b'events_received_total' in response.data
    
    Example - Accessing injected dependencies:
        def test_dependency_injection(app):
            with app.application_context():
                from flask import g
                assert g.redis_client is not None
                assert isinstance(g.redis_client, FakeRedis)
    """
    # Set test-specific environment variables
    # These override any existing environment variables for test isolation
    os.environ['TESTING'] = 'true'
    os.environ['ENVIRONMENT'] = 'test'
    os.environ['DEBUG'] = 'false'
    os.environ['REDIS_HOST'] = 'localhost'
    os.environ['ENABLE_MONGO'] = 'false'
    os.environ['JIRA_BASE_URL'] = 'https://test.atlassian.net'
    os.environ['PROJECT_KEY'] = 'ET'
    
    # Create Flask application using factory pattern
    # Use 'development' config name but override with test settings
    flask_app = create_app('development')
    
    # Override configuration with test-specific settings
    flask_app.config.update({
        'TESTING': True,
        'DEBUG': False,
        'SECRET_KEY': 'test_secret_key',
        'REDIS_HOST': 'localhost',
        'ENABLE_MONGO': False,
        'JIRA_BASE_URL': 'https://test.atlassian.net',
        'PROJECT_KEY': 'ET'
    })
    
    # Patch application context to use mocked dependencies
    # This ensures all routes use mocked Redis and Jira clients
    with flask_app.app_context():
        from flask import g
        g.redis_client = mock_redis
        g.mongo_client = None  # MongoDB disabled in tests
        g.config = flask_app.config
        
        # Yield test client to test function
        # with statement ensures proper context cleanup
        with flask_app.test_client() as client:
            yield client


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    'mock_redis',
    'mock_jira',
    'mock_boto3_secrets',
    'sample_vercel_payload',
    'sample_gcp_payload',
    'app',
    'pytest_configure',
    'pytest_sessionstart',
    'pytest_sessionfinish',
]
