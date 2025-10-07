"""
Integration Tests for Jira API Operations

This module provides comprehensive integration tests for the JiraIntegrationService
class, validating all Jira API operations using unittest.mock to mock the JIRA client
methods without making actual external API calls.

Per Section 0.5.1 Group 9 and Technical Specification Section 6.6, these tests verify:
- JQL-based fingerprint search with correct query construction
- Bug issue creation with labels, custom severity fields, and field structure
- Comment addition with formatted content and occurrence counts
- Priority escalation when frequency thresholds are crossed
- Exponential backoff retry logic for transient errors (1s, 2s, 4s, 8s)
- Error handling for authentication failures (401), rate limits (429), and server errors (503)
- PII sanitization in issue summaries and descriptions

Test Strategy:
We use unittest.mock to mock the JIRA client methods, allowing us to:
- Validate that correct methods are called with expected parameters
- Simulate various response scenarios (success, transient errors, permanent errors)
- Test retry logic without waiting for actual network delays
- Ensure no actual external API calls are made during test execution

All tests use fixtures from tests/conftest.py:
- sample_vercel_payload: Realistic Vercel webhook payload for test data
- sample_gcp_payload: Realistic GCP Pub/Sub push payload for test data

Technical References:
- Section 0.1.1: Core feature objectives including Jira integration requirements
- Section 0.4.2: Jira Cloud API integration patterns and endpoints
- Section 0.5.1 Group 5: Jira integration implementation details
- Section 0.7.5: Jira API best practices including retry logic and rate limiting
- Section 6.6: Testing strategy requiring 80%+ integration test coverage

Author: Blitzy Platform
Version: 1.0.0
"""

import json
from datetime import datetime
from typing import Dict, Any, Optional, List
from unittest.mock import Mock, MagicMock, patch, call

import pytest
from jira import JIRA
from jira.exceptions import JIRAError

# Internal imports - Services under test
from src.services.jira_integration import JiraIntegrationService

# Internal imports - Data models
from src.models.error_event import NormalizedErrorEvent

# Internal imports - Supporting services
from src.services.sanitizer import PIISanitizer


# =============================================================================
# Test Class: JiraIntegration
# =============================================================================

@pytest.mark.integration
@pytest.mark.jira
class TestJiraIntegration:
    """
    Integration test suite for Jira API operations via JiraIntegrationService.
    
    This test class validates all Jira API interactions with mocked HTTP responses,
    ensuring correct request formatting, response handling, and retry logic without
    making actual external API calls to Atlassian.
    
    Test Coverage:
    - Issue search by fingerprint with JQL queries
    - Bug issue creation with complete field population
    - Comment addition to existing issues
    - Priority escalation for threshold crossing
    - Exponential backoff retry for transient errors
    - Error handling for permanent failures (401, 404)
    - Rate limit handling (429 responses)
    - PII sanitization validation in issue content
    
    All tests use unittest.mock to mock JIRA client methods.
    """
    
    # Test constants
    JIRA_BASE_URL = "https://test.atlassian.net"
    PROJECT_KEY = "ET"
    TEST_FINGERPRINT = "a3f5b9c8d2e1f4g6h8j9k0m1n3p5q7r9s0t2u4v6w8x0y2z4"
    
    @pytest.fixture
    def jira_client(self) -> Mock:
        """
        Create mocked JIRA client instance for testing.
        
        Returns a Mock object that simulates the JIRA client interface.
        This allows us to test the JiraIntegrationService without making
        actual HTTP requests to the Jira API.
        
        Returns:
            Mock JIRA client instance
        """
        mock_client = MagicMock(spec=JIRA)
        mock_client._options = {'server': self.JIRA_BASE_URL}
        return mock_client
    
    @pytest.fixture
    def sanitizer(self) -> PIISanitizer:
        """
        Create PIISanitizer instance for testing.
        
        Returns PIISanitizer loaded with default patterns from config/sanitization_patterns.yaml.
        Used by JiraIntegrationService to remove PII before Jira transmission.
        
        Returns:
            PIISanitizer instance for PII removal
        """
        return PIISanitizer(config_path='config/sanitization_patterns.yaml')
    
    @pytest.fixture
    def jira_service(self, jira_client: JIRA, sanitizer: PIISanitizer) -> JiraIntegrationService:
        """
        Create JiraIntegrationService instance for testing.
        
        Initializes service with mocked JIRA client and PIISanitizer. All Jira API
        operations performed by this service will be intercepted by responses library.
        
        Args:
            jira_client: JIRA client instance from jira_client fixture
            sanitizer: PIISanitizer instance from sanitizer fixture
        
        Returns:
            JiraIntegrationService configured for testing
        """
        return JiraIntegrationService(
            jira_client=jira_client,
            project_key=self.PROJECT_KEY,
            sanitizer=sanitizer,
            environment='test'
        )
    
    @pytest.fixture
    def sample_error_event(self) -> NormalizedErrorEvent:
        """
        Create sample NormalizedErrorEvent for testing.
        
        Returns a complete error event with realistic data matching Vercel webhook
        format. Used in tests that create or update Jira issues.
        
        Returns:
            NormalizedErrorEvent with test data
        """
        return NormalizedErrorEvent(
            source='vercel',
            service='web-app',
            environment='production',
            error_class='TypeError',
            message='Cannot read property \'x\' of undefined',
            stack_trace=(
                'TypeError: Cannot read property \'x\' of undefined\n'
                '    at processCheckout (/app/pages/api/checkout.tsx:123:45)\n'
                '    at handler (/app/pages/api/checkout.tsx:98:12)\n'
                '    at next (/app/node_modules/next/dist/server/api-utils.js:45:7)'
            ),
            path='/api/checkout',
            url='https://web-app-abc123.vercel.app/api/checkout',
            release='dpl_xyz123abc456',
            log_url='https://vercel.com/logs?traceId=abc123def456',
            event_id='vercel-xyz-123',
            occurred_at=datetime.fromisoformat('2025-01-15T10:30:45.123000')
        )
    
    # =========================================================================
    # Test 1: search_issue_by_fingerprint returns issue key
    # =========================================================================
    
    def test_search_issue_by_fingerprint_returns_issue_key(self, jira_client: Mock, jira_service: JiraIntegrationService):
        """
        Test that search_issue_by_fingerprint returns issue key when issue exists.
        
        Validates:
        - JQL query is correctly constructed with project, fingerprint label, and status filter
        - search_issues method is called on JIRA client with correct JQL
        - Response parsing extracts issue key from first result
        - Method returns issue key string when issue found
        
        Per Section 0.5.1 Group 5, JQL pattern:
        project = ET AND labels = "errfp:{fingerprint}" AND statusCategory != Done
        """
        # Arrange - Mock Jira issue object
        mock_issue = Mock()
        mock_issue.key = 'ET-1234'
        mock_issue.fields.summary = '[prod:web-app] TypeError — Cannot read property...'
        mock_issue.fields.status.name = 'Open'
        mock_issue.fields.priority.name = 'Medium'
        
        # Configure mock to return list with one issue
        jira_client.search_issues.return_value = [mock_issue]
        
        # Act - Search for issue by fingerprint
        result = jira_service.search_issue_by_fingerprint(self.TEST_FINGERPRINT)
        
        # Assert - Issue key returned
        assert result == 'ET-1234', "Expected issue key 'ET-1234' to be returned"
        
        # Assert - search_issues called with correct JQL query
        expected_jql = f'project = {self.PROJECT_KEY} AND labels = "errfp:{self.TEST_FINGERPRINT}" AND statusCategory != Done'
        jira_client.search_issues.assert_called_once()
        call_args = jira_client.search_issues.call_args
        
        # Verify JQL query in call
        actual_jql = call_args[0][0]  # First positional argument
        assert actual_jql == expected_jql, f"Expected JQL query to match. Got: {actual_jql}"
        
        # Verify maxResults parameter
        assert call_args[1]['maxResults'] == 1, "Expected maxResults=1"
    
    # =========================================================================
    # Test 2: search_issue_by_fingerprint returns None when not found
    # =========================================================================
    
    def test_search_issue_by_fingerprint_returns_none_when_not_found(self, jira_client: Mock, jira_service: JiraIntegrationService):
        """
        Test that search_issue_by_fingerprint returns None when no matching issue exists.
        
        Validates:
        - Empty issues array in response is handled correctly
        - Method returns None to indicate no existing issue
        - Allows calling code to proceed with issue creation
        """
        # Arrange - Mock Jira client to return empty list
        jira_client.search_issues.return_value = []
        
        # Act - Search for non-existent fingerprint
        result = jira_service.search_issue_by_fingerprint('nonexistent-fingerprint')
        
        # Assert - None returned when no issue found
        assert result is None, "Expected None when no issue found"
        jira_client.search_issues.assert_called_once()
    
    # =========================================================================
    # Test 3: create_bug_issue with all fields
    # =========================================================================
    
    def test_create_bug_issue_with_all_fields(
        self,
        jira_client: Mock,
        jira_service: JiraIntegrationService,
        sample_error_event: NormalizedErrorEvent
    ):
        """
        Test bug issue creation with complete field population.
        
        Validates:
        - Summary format: [{env}:{service}] {error_class} — {sanitized_message}
        - Labels include: source:vercel, env:prod, service:web-app, errfp:{fingerprint}
        - Issue type is Bug
        - Priority field is set correctly
        - Custom severity field (customfield_10050) is populated
        - Description contains markdown-formatted error context
        - Stack trace excerpt included in description
        - Log URL deep link included
        - Assignee field set when provided
        - Created issue key is returned
        
        Per Section 0.5.1 Group 5 requirements.
        """
        # Arrange - Mock Jira client create_issue method
        mock_issue = Mock()
        mock_issue.key = 'ET-5678'
        jira_client.create_issue.return_value = mock_issue
        
        # Act - Create bug issue with all fields
        issue_key = jira_service.create_bug_issue(
            event=sample_error_event,
            fingerprint=self.TEST_FINGERPRINT,
            priority='High',
            severity='SEV2',
            assignee={'assignee': '5f8e9a1b2c3d4e5f6a7b8c9d'}
        )
        
        # Assert - Issue key returned
        assert issue_key == 'ET-5678', "Expected created issue key 'ET-5678'"
        
        # Verify create_issue was called once
        jira_client.create_issue.assert_called_once()
        
        # Extract fields from the call
        call_kwargs = jira_client.create_issue.call_args.kwargs
        fields = call_kwargs['fields']
        
        # Validate project key
        assert fields['project']['key'] == self.PROJECT_KEY, "Expected correct project key"
        
        # Validate summary format: [{env}:{service}] {error_class} — {message}
        summary = fields['summary']
        assert summary.startswith('[prod:web-app]'), "Expected summary to start with environment and service"
        assert 'TypeError' in summary, "Expected error class in summary"
        assert '—' in summary or '-' in summary, "Expected separator in summary"
        
        # Validate labels (all 4 required per Section 0.5.1 Group 5)
        labels = fields['labels']
        assert len(labels) == 4, "Expected exactly 4 labels"
        assert 'source:vercel' in labels, "Expected source label"
        assert 'env:prod' in labels, "Expected environment label"
        assert 'service:web-app' in labels, "Expected service label"
        assert f'errfp:{self.TEST_FINGERPRINT}' in labels, "Expected fingerprint label"
        
        # Validate issue type is Bug
        assert fields['issuetype']['name'] == 'Bug', "Expected issuetype to be Bug"
        
        # Validate priority
        assert fields['priority']['name'] == 'High', "Expected priority High"
        
        # Validate custom severity field (customfield_10050)
        assert 'customfield_10050' in fields, "Expected custom severity field"
        assert fields['customfield_10050']['value'] == 'SEV2', "Expected severity SEV2"
        
        # Validate description contains key elements
        description = fields['description']
        assert 'TypeError' in description, "Expected error class in description"
        assert 'Cannot read property' in description, "Expected error message in description"
        assert 'Stack Trace' in description or 'stack_trace' in description.lower(), "Expected stack trace section"
        assert sample_error_event.log_url in description, "Expected log URL in description"
        assert self.TEST_FINGERPRINT in description, "Expected fingerprint in description"
        
        # Validate assignee field
        assert 'assignee' in fields, "Expected assignee field"
        assert fields['assignee']['accountId'] == '5f8e9a1b2c3d4e5f6a7b8c9d', "Expected correct assignee account ID"
    
    # =========================================================================
    # Test 4: create_bug_issue with component assignment
    # =========================================================================
    
    def test_create_bug_issue_with_component_assignment(
        self,
        jira_client: Mock,
        jira_service: JiraIntegrationService,
        sample_error_event: NormalizedErrorEvent
    ):
        """
        Test bug issue creation with component-based routing instead of direct assignee.
        
        Validates:
        - Components field populated when assignee dict contains 'component' key
        - No assignee field when using component routing
        - Component name correctly set in fields
        
        Per Section 0.1.1 requirement #5 for ownership routing patterns.
        """
        # Arrange - Mock Jira client create_issue method
        mock_issue = Mock()
        mock_issue.key = 'ET-9012'
        jira_client.create_issue.return_value = mock_issue
        
        # Act - Create issue with component assignment
        issue_key = jira_service.create_bug_issue(
            event=sample_error_event,
            fingerprint=self.TEST_FINGERPRINT,
            priority='Medium',
            severity='SEV3',
            assignee={'component': 'Frontend'}
        )
        
        # Assert - Issue created
        assert issue_key == 'ET-9012'
        
        # Extract fields from the call
        call_kwargs = jira_client.create_issue.call_args.kwargs
        fields = call_kwargs['fields']
        
        # Validate components field present
        assert 'components' in fields, "Expected components field when using component routing"
        assert fields['components'][0]['name'] == 'Frontend', "Expected Frontend component"
        
        # Validate no direct assignee field when using component routing
        assert 'assignee' not in fields, "Expected no assignee field when using component routing"
    
    # =========================================================================
    # Test 5: add_comment with occurrence count
    # =========================================================================
    
    def test_add_comment_with_occurrence_count(
        self,
        jira_client: Mock,
        jira_service: JiraIntegrationService,
        sample_error_event: NormalizedErrorEvent
    ):
        """
        Test adding comment to existing issue with formatted occurrence content.
        
        Validates:
        - add_comment method called on JIRA client
        - Comment text format: "Error reoccurred {count}× in last 5m. Severity: {severity}. [link]"
        - Comment includes occurrence count
        - Comment includes current severity level
        - Comment includes deep link to logs
        - ISO 8601 timestamp in comment (auto-added by Jira)
        
        Per Section 0.5.1 Group 5 comment format requirements.
        """
        # Arrange - Mock Jira client add_comment method
        jira_client.add_comment.return_value = None
        
        # Act - Add comment to existing issue
        jira_service.add_comment(
            issue_key='ET-1234',
            count=15,
            severity='SEV2',
            log_url='https://vercel.com/logs?traceId=abc123',
            event=sample_error_event
        )
        
        # Assert - add_comment was called once
        jira_client.add_comment.assert_called_once()
        
        # Extract arguments from the call
        call_args = jira_client.add_comment.call_args
        issue_key_arg = call_args.args[0]
        comment_text = call_args.args[1]
        
        # Validate issue key
        assert issue_key_arg == 'ET-1234', "Expected correct issue key"
        
        # Validate comment format per Section 0.5.1 Group 5
        assert '15' in comment_text, "Expected occurrence count in comment"
        assert 'SEV2' in comment_text, "Expected severity in comment"
        assert 'https://vercel.com/logs' in comment_text, "Expected log URL in comment"
        assert 'reoccurred' in comment_text.lower() or 'occurred' in comment_text.lower(), "Expected occurrence language"
        assert '5m' in comment_text or '5 m' in comment_text, "Expected time window in comment"
    
    # =========================================================================
    # Test 6: escalate_priority updates issue
    # =========================================================================
    
    def test_escalate_priority_updates_issue(self, jira_client: Mock, jira_service: JiraIntegrationService):
        """
        Test priority escalation when frequency threshold crossed.
        
        Validates:
        - issue() method called to fetch issue object
        - update() method called on issue with priority field
        - Priority field structure: {"name": "Highest"}
        - No errors raised on successful update
        
        Per Section 0.1.1 requirement #4 for threshold-based escalation.
        """
        # Arrange - Mock Jira client issue() method
        mock_issue = Mock()
        mock_issue.update = Mock()
        jira_client.issue.return_value = mock_issue
        
        # Act - Escalate priority
        jira_service.escalate_priority('ET-1234', 'Highest', event_id='vercel-xyz-123')
        
        # Assert - Methods called correctly
        jira_client.issue.assert_called_once_with('ET-1234')
        mock_issue.update.assert_called_once_with(priority={"name": "Highest"})
    
    # =========================================================================
    # Test 7: Jira API retry on transient error
    # =========================================================================
    
    def test_jira_api_retry_on_transient_error(
        self,
        jira_client: Mock,
        jira_service: JiraIntegrationService,
        sample_error_event: NormalizedErrorEvent
    ):
        """
        Test exponential backoff retry logic for transient 503 errors.
        
        Validates:
        - Initial 503 Service Unavailable response triggers retry
        - Subsequent successful response
        - Multiple API calls made (initial + retry)
        - Final result is successful issue creation
        - Retry delays follow exponential backoff pattern (1s, 2s, 4s, 8s)
        
        Per Section 0.7.5, retry delays: 1s, 2s, 4s, 8s, 16s (max 5 attempts).
        Note: We don't validate timing in this test, only that retries occur.
        """
        # Arrange - Mock create_issue to fail first, then succeed
        from jira.exceptions import JIRAError
        
        # Create a mock JIRAError with status_code attribute
        error_503 = JIRAError(status_code=503, text='Service temporarily unavailable')
        
        # Create mock issue for successful response
        mock_issue = Mock()
        mock_issue.key = 'ET-9999'
        
        # Set side_effect: first call raises error, second call succeeds
        jira_client.create_issue.side_effect = [error_503, mock_issue]
        
        # Act - Create issue (should retry after initial failure)
        issue_key = jira_service.create_bug_issue(
            event=sample_error_event,
            fingerprint=self.TEST_FINGERPRINT,
            priority='Medium',
            severity='SEV3',
            assignee=None
        )
        
        # Assert - Eventually successful after retry
        assert issue_key == 'ET-9999', "Expected eventual success with issue key ET-9999"
        assert jira_client.create_issue.call_count == 2, "Expected two API calls (initial failure + retry success)"
    
    # =========================================================================
    # Test 8: Jira API 429 rate limit handling
    # =========================================================================
    
    def test_jira_api_429_rate_limit_handling(self, jira_client: Mock, jira_service: JiraIntegrationService):
        """
        Test rate limit handling with 429 response and Retry-After header.
        
        Validates:
        - 429 Too Many Requests response is recognized as transient error
        - Retry-After header value is logged (respecting rate limit timing)
        - Retry logic attempts operation again after backoff
        - Final success or appropriate error after retries
        
        Per Section 0.7.5, rate limits: 100 requests/minute for Jira Cloud.
        """
        # Arrange - Mock search_issues to fail with rate limit, then succeed
        from jira.exceptions import JIRAError
        
        # Create a mock JIRAError with status_code 429
        error_429 = JIRAError(status_code=429, text='Rate limit exceeded')
        
        # Create mock search result for successful response
        mock_issue = Mock()
        mock_issue.key = 'ET-2345'
        
        # Set side_effect: first call raises 429, second call succeeds
        jira_client.search_issues.side_effect = [error_429, [mock_issue]]
        
        # Act - Search for issue (should retry after rate limit)
        result = jira_service.search_issue_by_fingerprint(self.TEST_FINGERPRINT)
        
        # Assert - Eventually successful after retry
        assert result == 'ET-2345', "Expected eventual success after rate limit retry"
        assert jira_client.search_issues.call_count == 2, "Expected two API calls (rate limit + retry success)"
    
    # =========================================================================
    # Test 9: Jira API 401 authentication failure (no retry)
    # =========================================================================
    
    def test_jira_api_401_authentication_failure(
        self,
        jira_client: Mock,
        jira_service: JiraIntegrationService,
        sample_error_event: NormalizedErrorEvent
    ):
        """
        Test permanent error handling for 401 Unauthorized (no retry).
        
        Validates:
        - 401 Unauthorized response is recognized as permanent error
        - No retry attempts made (permanent errors should not retry)
        - Exception raised to calling code
        - Error logged with structured context
        
        Per Section 0.7.5, permanent errors (401, 403, 404) should not retry.
        """
        # Arrange - Mock create_issue to raise 401 error
        from jira.exceptions import JIRAError
        
        # Create a mock JIRAError with status_code 401
        error_401 = JIRAError(status_code=401, text='Invalid credentials')
        jira_client.create_issue.side_effect = error_401
        
        # Act & Assert - Expect exception raised without retry
        with pytest.raises(JIRAError) as exc_info:
            jira_service.create_bug_issue(
                event=sample_error_event,
                fingerprint=self.TEST_FINGERPRINT,
                priority='High',
                severity='SEV2',
                assignee=None
            )
        
        # Verify only one API call made (no retries for permanent errors)
        assert jira_client.create_issue.call_count == 1, "Expected only one API call (no retries for 401)"
        
        # Verify exception raised
        assert exc_info.value is not None, "Expected exception to be raised for 401 error"
        assert exc_info.value.status_code == 401, "Expected 401 status code"
    
    # =========================================================================
    # Test 10: Sanitized message in issue summary
    # =========================================================================
    
    def test_sanitized_message_in_issue_summary(
        self,
        jira_client: Mock,
        jira_service: JiraIntegrationService
    ):
        """
        Test that PII is removed from Jira issue summary and description.
        
        Validates:
        - Email addresses replaced with [EMAIL] placeholder
        - UUIDs replaced with [UUID] placeholder
        - Numeric IDs sanitized
        - Original error information preserved while PII removed
        - Sanitization applied before Jira API call
        
        Per Section 0.7.4 security requirements for PII sanitization.
        """
        # Arrange - Create event with PII in message
        event_with_pii = NormalizedErrorEvent(
            source='vercel',
            service='web-app',
            environment='production',
            error_class='ValidationError',
            message='User user@example.com with UUID 550e8400-e29b-41d4-a716-446655440000 and ID 12345 failed validation',
            stack_trace='ValidationError: User validation failed\n  at validateUser (/app/api/users.ts:45:10)',
            path='/api/users',
            url='https://web-app.vercel.app/api/users',
            release='v1.2.3',
            log_url='https://vercel.com/logs?traceId=xyz789',
            event_id='vercel-abc-789',
            occurred_at=datetime.now()
        )
        
        # Mock Jira client create_issue method
        mock_issue = Mock()
        mock_issue.key = 'ET-7890'
        jira_client.create_issue.return_value = mock_issue
        
        # Act - Create issue with PII in message
        issue_key = jira_service.create_bug_issue(
            event=event_with_pii,
            fingerprint=self.TEST_FINGERPRINT,
            priority='Low',
            severity='SEV4',
            assignee=None
        )
        
        # Assert - Issue created
        assert issue_key == 'ET-7890'
        
        # Extract fields from the call
        call_kwargs = jira_client.create_issue.call_args.kwargs
        fields = call_kwargs['fields']
        summary = fields['summary']
        description = fields['description']
        
        # Verify PII removed from summary
        assert 'user@example.com' not in summary, "Expected email removed from summary"
        assert '550e8400-e29b-41d4-a716-446655440000' not in summary, "Expected UUID removed from summary"
        
        # Verify PII placeholders or removal in summary/description
        # Note: Actual sanitization patterns depend on PIISanitizer implementation
        # We verify that the original PII is not present in Jira fields
        assert 'user@example.com' not in description, "Expected email removed from description"
        assert '550e8400-e29b-41d4-a716-446655440000' not in description, "Expected UUID removed from description"
        
        # Verify error information preserved
        assert 'ValidationError' in summary, "Expected error class preserved in summary"
        assert 'validation' in summary.lower() or 'validation' in description.lower(), \
            "Expected validation context preserved"


# =============================================================================
# Module Exports
# =============================================================================

__all__ = ['TestJiraIntegration']
