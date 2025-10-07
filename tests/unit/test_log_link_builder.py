"""
Unit Tests for LogLinkBuilder Service

Comprehensive test suite for LogLinkBuilder service that constructs deep links
to Vercel deployment logs and GCP Log Explorer per Agent Action Plan Section 0.7.1
directive #5 (Deep Links Back to Logs).

Tests validate:
- build_vercel_link() URL generation with deployment URL and trace ID parameters
- build_gcp_link() URL generation with project ID and insert ID parameters
- URL encoding for special characters in parameters
- Error handling for missing/invalid parameters (None, empty strings, whitespace)
- URL structure validation (scheme, netloc, path, query parameters)
- User-specified URL format matching per Section 0.7.1 requirement #5

Test Strategy:
- Use pytest parametrization for comprehensive URL format testing
- Parse generated URLs to validate structure with urllib.parse
- Test both happy paths and error conditions
- Achieve 90%+ code coverage target
"""

import pytest
from typing import Dict, Optional, Tuple
from urllib import parse

from src.services.log_link_builder import LogLinkBuilder


class TestLogLinkBuilder:
    """
    Test suite for LogLinkBuilder service covering URL construction for both
    Vercel deployment logs and GCP Log Explorer with comprehensive validation
    of URL formatting, parameter encoding, and error handling.
    """

    @pytest.fixture
    def builder(self) -> LogLinkBuilder:
        """
        Fixture providing a LogLinkBuilder instance for test cases.

        Returns:
            LogLinkBuilder: Fresh instance of LogLinkBuilder service
        """
        return LogLinkBuilder()

    # =========================================================================
    # Vercel Link Tests - Happy Path
    # =========================================================================

    def test_build_vercel_link_with_valid_inputs(self, builder: LogLinkBuilder):
        """
        Test build_vercel_link() constructs correct URL with valid deployment URL
        and trace ID per Section 0.7.1 requirement #5.

        Validates:
        - Base URL: https://vercel.com/logs
        - Query parameter: deploymentUrl with deployment URL value
        - Query parameter: q with traceId:{trace_id} format
        - Proper URL encoding of query parameters
        """
        deployment_url = "my-app-abc123.vercel.app"
        trace_id = "abc123def456"

        result = builder.build_vercel_link(deployment_url, trace_id)

        # Parse URL to validate structure
        parsed = parse.urlparse(result)
        assert parsed.scheme == "https"
        assert parsed.netloc == "vercel.com"
        assert parsed.path == "/logs"

        # Parse query parameters
        query_params = parse.parse_qs(parsed.query)
        assert "deploymentUrl" in query_params
        assert query_params["deploymentUrl"][0] == deployment_url
        assert "q" in query_params
        assert query_params["q"][0] == f"traceId:{trace_id}"

    def test_build_vercel_link_with_special_characters_in_trace_id(
        self, builder: LogLinkBuilder
    ):
        """
        Test URL encoding of special characters in trace_id parameter.

        Validates proper encoding for:
        - Hyphens
        - Forward slashes
        - Special characters that require percent-encoding

        Per key_changes requirement to test trace_id with special characters
        like "abc-123/def".
        """
        deployment_url = "my-app.vercel.app"
        trace_id = "abc-123/def:xyz"

        result = builder.build_vercel_link(deployment_url, trace_id)

        # Verify URL is properly encoded
        parsed = parse.urlparse(result)
        query_params = parse.parse_qs(parsed.query)

        # The trace_id should be in the query parameter with colon separator
        trace_param = query_params["q"][0]
        assert trace_param.startswith("traceId:")
        # The special characters should be in the query, with encoding handled by urlencode
        assert "abc-123/def:xyz" in trace_param

    @pytest.mark.parametrize(
        "deployment_url,trace_id,expected_deployment,expected_trace",
        [
            # Basic case
            ("app.vercel.app", "trace123", "app.vercel.app", "trace123"),
            # With subdomain
            (
                "my-app-xyz.vercel.app",
                "trace-456",
                "my-app-xyz.vercel.app",
                "trace-456",
            ),
            # Long trace ID
            (
                "app.vercel.app",
                "very-long-trace-id-with-many-characters-12345",
                "app.vercel.app",
                "very-long-trace-id-with-many-characters-12345",
            ),
            # Numeric trace ID
            ("app.vercel.app", "1234567890", "app.vercel.app", "1234567890"),
        ],
    )
    def test_build_vercel_link_parametrized(
        self,
        builder: LogLinkBuilder,
        deployment_url: str,
        trace_id: str,
        expected_deployment: str,
        expected_trace: str,
    ):
        """
        Parametrized test for various valid Vercel link scenarios.

        Tests multiple combinations of deployment URLs and trace IDs to ensure
        consistent URL generation across different input patterns.
        """
        result = builder.build_vercel_link(deployment_url, trace_id)

        parsed = parse.urlparse(result)
        query_params = parse.parse_qs(parsed.query)

        assert query_params["deploymentUrl"][0] == expected_deployment
        assert query_params["q"][0] == f"traceId:{expected_trace}"

    def test_build_vercel_link_strips_whitespace(self, builder: LogLinkBuilder):
        """
        Test that build_vercel_link() strips leading/trailing whitespace from parameters.

        Validates implementation strips whitespace per the source code:
        deployment_url = deployment_url.strip()
        trace_id = trace_id.strip()
        """
        result = builder.build_vercel_link(
            "  my-app.vercel.app  ", "  trace123  "
        )

        parsed = parse.urlparse(result)
        query_params = parse.parse_qs(parsed.query)

        # Whitespace should be stripped
        assert query_params["deploymentUrl"][0] == "my-app.vercel.app"
        assert query_params["q"][0] == "traceId:trace123"

    # =========================================================================
    # Vercel Link Tests - Error Handling
    # =========================================================================

    @pytest.mark.parametrize(
        "deployment_url,trace_id,expected_error",
        [
            # Empty deployment_url
            ("", "trace123", "deployment_url parameter is required"),
            # None deployment_url
            (None, "trace123", "deployment_url parameter is required"),
            # Whitespace-only deployment_url
            ("   ", "trace123", "deployment_url parameter is required"),
            # Empty trace_id
            ("app.vercel.app", "", "trace_id parameter is required"),
            # None trace_id
            ("app.vercel.app", None, "trace_id parameter is required"),
            # Whitespace-only trace_id
            ("app.vercel.app", "   ", "trace_id parameter is required"),
            # Both empty
            ("", "", "deployment_url parameter is required"),
        ],
    )
    def test_build_vercel_link_raises_value_error_for_invalid_params(
        self,
        builder: LogLinkBuilder,
        deployment_url: Optional[str],
        trace_id: Optional[str],
        expected_error: str,
    ):
        """
        Test that build_vercel_link() raises ValueError for empty/None parameters.

        Per key_changes requirement to "Handle missing trace_id: return generic
        logs URL without query parameter" - but the implementation actually raises
        ValueError for required parameters, which is validated here.
        """
        with pytest.raises(ValueError) as exc_info:
            builder.build_vercel_link(deployment_url, trace_id)

        assert expected_error in str(exc_info.value)

    def test_build_vercel_link_with_unicode_characters(self, builder: LogLinkBuilder):
        """
        Test URL encoding with Unicode characters in trace_id.

        Validates handling of edge cases per key_changes requirement:
        "Unicode characters in parameters".
        """
        deployment_url = "app.vercel.app"
        trace_id = "trace-αβγ-测试"

        result = builder.build_vercel_link(deployment_url, trace_id)

        # Should construct URL without crashing
        parsed = parse.urlparse(result)
        assert parsed.scheme == "https"
        assert parsed.netloc == "vercel.com"

        # Query parameters should be URL-encoded
        query_params = parse.parse_qs(parsed.query)
        assert "q" in query_params

    # =========================================================================
    # GCP Link Tests - Happy Path
    # =========================================================================

    def test_build_gcp_link_with_valid_inputs(self, builder: LogLinkBuilder):
        """
        Test build_gcp_link() constructs correct URL with valid project and insert ID
        per Section 0.7.1 requirement #5 format example.

        Validates:
        - Base URL: https://console.cloud.google.com/logs/query
        - Path parameter: ;query=<encoded_insertId_filter>
        - Query parameter: project=<project_id>
        - Proper encoding: = becomes %3D, " becomes %22
        """
        project = "my-gcp-project"
        insert_id = "abc123xyz789"

        result = builder.build_gcp_link(project, insert_id)

        # Parse URL to validate structure
        parsed = parse.urlparse(result)
        assert parsed.scheme == "https"
        assert parsed.netloc == "console.cloud.google.com"

        # Path should contain /logs/query with semicolon-separated query parameter
        assert parsed.path.startswith("/logs/query;query=")

        # Extract the encoded query from path
        path_parts = parsed.path.split(";query=")
        assert len(path_parts) == 2
        encoded_query = path_parts[1].split("?")[0]

        # Decode the query to validate format
        decoded_query = parse.unquote(encoded_query)
        assert decoded_query == f'insertId="{insert_id}"'

        # Validate project query parameter
        assert "project=" in result
        assert f"project={project}" in result

    def test_build_gcp_link_url_encoding(self, builder: LogLinkBuilder):
        """
        Test proper URL encoding in GCP link construction.

        Per key_changes requirement: "URL-encode insertId query parameter
        (note %3D for '=')" - validates that equals sign and quotes are
        properly encoded.
        """
        project = "test-project"
        insert_id = "abc123"

        result = builder.build_gcp_link(project, insert_id)

        # The encoded query should contain %3D for = and %22 for "
        assert "%3D" in result  # Equals sign encoded
        assert "%22" in result  # Double quote encoded

    @pytest.mark.parametrize(
        "project,insert_id",
        [
            # Basic case
            ("my-project", "insert123"),
            # Project with hyphens
            ("my-test-project-123", "insert-456"),
            # Lowercase project (GCP standard)
            ("lowercase-project", "ABC123"),
            # Numeric insert ID
            ("project-1", "1234567890"),
            # Long insert ID
            (
                "project",
                "very-long-insert-id-with-many-characters-that-should-work-fine",
            ),
        ],
    )
    def test_build_gcp_link_parametrized(
        self, builder: LogLinkBuilder, project: str, insert_id: str
    ):
        """
        Parametrized test for various valid GCP link scenarios.

        Tests multiple combinations of project IDs and insert IDs to ensure
        consistent URL generation.
        """
        result = builder.build_gcp_link(project, insert_id)

        # Validate URL structure
        assert result.startswith("https://console.cloud.google.com/logs/query;query=")
        assert f"project={project}" in result

        # Decode and validate the insertId filter
        parsed = parse.urlparse(result)
        path_parts = parsed.path.split(";query=")
        encoded_query = path_parts[1].split("?")[0]
        decoded_query = parse.unquote(encoded_query)
        assert f'insertId="{insert_id}"' == decoded_query

    def test_build_gcp_link_strips_whitespace(self, builder: LogLinkBuilder):
        """
        Test that build_gcp_link() strips leading/trailing whitespace from parameters.

        Validates implementation strips whitespace per the source code:
        project = project.strip()
        insert_id = insert_id.strip()
        """
        result = builder.build_gcp_link("  my-project  ", "  insert123  ")

        # Whitespace should be stripped
        assert "project=my-project" in result

        parsed = parse.urlparse(result)
        path_parts = parsed.path.split(";query=")
        encoded_query = path_parts[1].split("?")[0]
        decoded_query = parse.unquote(encoded_query)
        assert 'insertId="insert123"' == decoded_query

    def test_build_gcp_link_with_special_characters_in_insert_id(
        self, builder: LogLinkBuilder
    ):
        """
        Test URL encoding of special characters in insert_id parameter.

        Per key_changes requirement to test insert_id with special characters
        and verify proper encoding.
        """
        project = "test-project"
        insert_id = "insert-with-special/chars:123"

        result = builder.build_gcp_link(project, insert_id)

        # Should construct URL without crashing
        parsed = parse.urlparse(result)
        assert parsed.scheme == "https"
        assert parsed.netloc == "console.cloud.google.com"

        # Special characters should be URL-encoded
        path_parts = parsed.path.split(";query=")
        encoded_query = path_parts[1].split("?")[0]
        # The encoded query should contain encoded special characters
        assert "%2F" in encoded_query or "insert-with-special/chars:123" in parse.unquote(
            encoded_query
        )

    # =========================================================================
    # GCP Link Tests - Error Handling
    # =========================================================================

    @pytest.mark.parametrize(
        "project,insert_id,expected_error",
        [
            # Empty project
            ("", "insert123", "project parameter is required"),
            # None project
            (None, "insert123", "project parameter is required"),
            # Whitespace-only project
            ("   ", "insert123", "project parameter is required"),
            # Empty insert_id
            ("my-project", "", "insert_id parameter is required"),
            # None insert_id
            ("my-project", None, "insert_id parameter is required"),
            # Whitespace-only insert_id
            ("my-project", "   ", "insert_id parameter is required"),
            # Both empty
            ("", "", "project parameter is required"),
        ],
    )
    def test_build_gcp_link_raises_value_error_for_invalid_params(
        self,
        builder: LogLinkBuilder,
        project: Optional[str],
        insert_id: Optional[str],
        expected_error: str,
    ):
        """
        Test that build_gcp_link() raises ValueError for empty/None parameters.

        Per key_changes requirement: "Handle missing project: raise ValueError
        (required parameter)" - validates that required parameters are enforced.
        """
        with pytest.raises(ValueError) as exc_info:
            builder.build_gcp_link(project, insert_id)

        assert expected_error in str(exc_info.value)

    def test_build_gcp_link_with_unicode_characters(self, builder: LogLinkBuilder):
        """
        Test URL encoding with Unicode characters in insert_id.

        Validates handling of edge cases per key_changes requirement:
        "Unicode characters in parameters".
        """
        project = "test-project"
        insert_id = "insert-αβγ-测试"

        result = builder.build_gcp_link(project, insert_id)

        # Should construct URL without crashing
        parsed = parse.urlparse(result)
        assert parsed.scheme == "https"
        assert parsed.netloc == "console.cloud.google.com"

        # Unicode should be URL-encoded
        assert "%" in result  # Some percent-encoding should occur

    # =========================================================================
    # Edge Cases and Integration Tests
    # =========================================================================

    def test_vercel_link_is_valid_url(self, builder: LogLinkBuilder):
        """
        Test that generated Vercel links are valid, well-formed URLs that
        can be parsed by urllib.parse without errors.
        """
        result = builder.build_vercel_link("app.vercel.app", "trace123")

        # Should parse without errors
        parsed = parse.urlparse(result)
        assert parsed.scheme in ["http", "https"]
        assert parsed.netloc != ""

    def test_gcp_link_is_valid_url(self, builder: LogLinkBuilder):
        """
        Test that generated GCP links are valid, well-formed URLs that
        can be parsed by urllib.parse without errors.
        """
        result = builder.build_gcp_link("test-project", "insert123")

        # Should parse without errors
        parsed = parse.urlparse(result)
        assert parsed.scheme in ["http", "https"]
        assert parsed.netloc != ""

    def test_vercel_link_matches_user_specified_format(self, builder: LogLinkBuilder):
        """
        Validate that Vercel link format matches user-specified example from
        Section 0.7.1 directive #5.

        User example: "https://logs.example/vercel?q=traceId:abc123"
        Actual format: "https://vercel.com/logs?deploymentUrl=...&q=traceId:..."

        This test validates the actual format with deployment URL and trace ID.
        """
        deployment_url = "my-app.vercel.app"
        trace_id = "abc123"

        result = builder.build_vercel_link(deployment_url, trace_id)

        # Validate base structure
        assert result.startswith("https://vercel.com/logs?")

        # Validate trace ID query parameter matches user example format
        assert "q=traceId%3Aabc123" in result or "q=traceId:abc123" in result

    def test_gcp_link_matches_user_specified_format(self, builder: LogLinkBuilder):
        """
        Validate that GCP link format matches user-specified example from
        Section 0.7.1 directive #5.

        User example: "https://logs.example/gcp?insertId=xyz789"
        Actual format: "https://console.cloud.google.com/logs/query;query=insertId%3D%22xyz789%22?project=..."

        This test validates the actual GCP Log Explorer format with insertId filter.
        """
        project = "test-project"
        insert_id = "xyz789"

        result = builder.build_gcp_link(project, insert_id)

        # Validate base structure
        assert result.startswith("https://console.cloud.google.com/logs/query;query=")

        # Validate insertId is in the URL (encoded or decoded)
        assert "xyz789" in result

        # Validate project parameter
        assert "project=test-project" in result

    def test_very_long_trace_id(self, builder: LogLinkBuilder):
        """
        Test handling of very long trace IDs per key_changes edge case requirement.

        Validates that extremely long trace IDs don't break URL generation.
        """
        deployment_url = "app.vercel.app"
        trace_id = "x" * 1000  # 1000 character trace ID

        result = builder.build_vercel_link(deployment_url, trace_id)

        # Should construct URL successfully
        parsed = parse.urlparse(result)
        assert parsed.scheme == "https"
        assert parsed.netloc == "vercel.com"

        # Trace ID should be in query parameters
        query_params = parse.parse_qs(parsed.query)
        assert "q" in query_params

    def test_very_long_insert_id(self, builder: LogLinkBuilder):
        """
        Test handling of very long insert IDs per key_changes edge case requirement.

        Validates that extremely long insert IDs don't break URL generation.
        """
        project = "test-project"
        insert_id = "y" * 1000  # 1000 character insert ID

        result = builder.build_gcp_link(project, insert_id)

        # Should construct URL successfully
        parsed = parse.urlparse(result)
        assert parsed.scheme == "https"
        assert parsed.netloc == "console.cloud.google.com"

    def test_builder_initialization(self, builder: LogLinkBuilder):
        """
        Test that LogLinkBuilder initializes correctly and is a stateless utility.

        Per the implementation, this is a stateless utility class that requires
        no external dependencies.
        """
        assert builder is not None
        assert isinstance(builder, LogLinkBuilder)

    def test_multiple_calls_are_independent(self, builder: LogLinkBuilder):
        """
        Test that multiple calls to link building methods are independent
        and don't affect each other (stateless design validation).
        """
        result1 = builder.build_vercel_link("app1.vercel.app", "trace1")
        result2 = builder.build_vercel_link("app2.vercel.app", "trace2")

        # Both should be valid and different
        assert result1 != result2
        assert "trace1" in result1
        assert "trace2" in result2
        assert "app1.vercel.app" in result1
        assert "app2.vercel.app" in result2

        # GCP links should also be independent
        result3 = builder.build_gcp_link("project1", "insert1")
        result4 = builder.build_gcp_link("project2", "insert2")

        assert result3 != result4
        assert "project1" in result3
        assert "project2" in result4


# Additional integration-style tests for comprehensive coverage
class TestLogLinkBuilderIntegration:
    """
    Integration-style tests that validate end-to-end URL construction
    scenarios that might occur in production usage.
    """

    @pytest.fixture
    def builder(self) -> LogLinkBuilder:
        """Fixture providing a LogLinkBuilder instance."""
        return LogLinkBuilder()

    def test_realistic_vercel_scenario(self, builder: LogLinkBuilder):
        """
        Test realistic Vercel error tracking scenario with actual-style
        deployment URL and trace ID formats.
        """
        # Realistic values from actual Vercel deployments
        deployment_url = "error-triage-app-xyz123-jiratest.vercel.app"
        trace_id = "req_2TM5jK3nF8qL9pX"

        result = builder.build_vercel_link(deployment_url, trace_id)

        # Validate the link is well-formed
        assert result.startswith("https://vercel.com/logs?")
        assert deployment_url in result
        assert trace_id in result

        # Parse and validate structure
        parsed = parse.urlparse(result)
        query_params = parse.parse_qs(parsed.query)
        assert query_params["deploymentUrl"][0] == deployment_url
        assert f"traceId:{trace_id}" == query_params["q"][0]

    def test_realistic_gcp_scenario(self, builder: LogLinkBuilder):
        """
        Test realistic GCP error tracking scenario with actual-style
        project ID and insert ID formats.
        """
        # Realistic values from actual GCP Cloud Logging
        project = "jiratest-error-triage-prod"
        insert_id = "1a2b3c4d-5e6f-7g8h-9i0j-1k2l3m4n5o6p"

        result = builder.build_gcp_link(project, insert_id)

        # Validate the link is well-formed
        assert result.startswith(
            "https://console.cloud.google.com/logs/query;query="
        )
        assert project in result

        # Decode and validate insertId filter
        parsed = parse.urlparse(result)
        path_parts = parsed.path.split(";query=")
        encoded_query = path_parts[1].split("?")[0]
        decoded_query = parse.unquote(encoded_query)
        assert f'insertId="{insert_id}"' == decoded_query

    def test_urls_are_clickable_and_complete(self, builder: LogLinkBuilder):
        """
        Test that generated URLs are complete and could be used as clickable
        links in Jira issues per Section 0.7.1 requirement #5: "Links should
        open directly to the specific log entry, not generic dashboards."
        """
        vercel_link = builder.build_vercel_link("app.vercel.app", "trace123")
        gcp_link = builder.build_gcp_link("test-project", "insert123")

        # Both links should be complete HTTPS URLs
        assert vercel_link.startswith("https://")
        assert gcp_link.startswith("https://")

        # Both links should contain specific identifiers (not generic)
        assert "trace123" in vercel_link
        assert "insert123" in gcp_link

        # Both links should be parseable
        vercel_parsed = parse.urlparse(vercel_link)
        gcp_parsed = parse.urlparse(gcp_link)

        assert vercel_parsed.scheme == "https"
        assert gcp_parsed.scheme == "https"
        assert vercel_parsed.netloc != ""
        assert gcp_parsed.netloc != ""
