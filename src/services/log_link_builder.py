"""
LogLinkBuilder Service

Utility service for constructing deep links to external log platforms (Vercel deployment
logs and GCP Cloud Logging Log Explorer) with query parameters for trace IDs and insert IDs,
enabling quick navigation from Jira issues to original error context.

This service handles URL encoding for special characters and validates required parameters
per Section 0.7.1 requirement #5.
"""

import logging
from typing import Optional
from urllib import parse

# Initialize module logger
logger = logging.getLogger(__name__)


class LogLinkBuilder:
    """
    Constructs deep links to Vercel deployment logs and GCP Log Explorer with trace IDs
    and insert IDs for direct navigation from Jira issues to original error context.
    
    This standalone utility has no internal service dependencies and serves as a foundational
    component for Jira integration service per Section 0.5.1 Group 6.
    
    Example Usage:
        builder = LogLinkBuilder()
        
        # Vercel link
        vercel_link = builder.build_vercel_link(
            deployment_url="my-app-abc123.vercel.app",
            trace_id="abc123def456"
        )
        # Returns: https://vercel.com/logs?deploymentUrl=my-app-abc123.vercel.app&q=traceId:abc123def456
        
        # GCP link
        gcp_link = builder.build_gcp_link(
            project="my-gcp-project",
            insert_id="abc123xyz789"
        )
        # Returns: https://console.cloud.google.com/logs/query;query=insertId%3D%22abc123xyz789%22?project=my-gcp-project
    """
    
    def __init__(self):
        """
        Initialize LogLinkBuilder service.
        
        This is a stateless utility class that requires no external dependencies.
        """
        logger.info("Initialized LogLinkBuilder service")
    
    def build_vercel_link(self, deployment_url: str, trace_id: str) -> str:
        """
        Construct deep link to Vercel deployment logs with trace ID query parameter.
        
        Builds a URL that opens directly to the specific log entry in Vercel's logs UI,
        not a generic dashboard. Per Section 0.7.1 requirement #5, links must enable
        one-click navigation to the exact error context.
        
        Args:
            deployment_url: Vercel deployment URL (e.g., "my-app-abc123.vercel.app")
                           or deployment ID for constructing the logs link
            trace_id: Vercel trace ID from the error event for filtering logs
        
        Returns:
            str: Formatted Vercel logs URL with deployment and trace ID filters
                 Example: https://vercel.com/logs?deploymentUrl=my-app-abc123.vercel.app&q=traceId:abc123def456
        
        Raises:
            ValueError: If deployment_url or trace_id are empty or None
        
        Notes:
            - URL components are properly percent-encoded per RFC 3986
            - Trace ID is included in query parameter for log filtering
            - Links open directly to specific log entries, not generic dashboards
        """
        # Validate required parameters
        if not deployment_url or not isinstance(deployment_url, str) or not deployment_url.strip():
            logger.error("build_vercel_link called with empty or invalid deployment_url")
            raise ValueError("deployment_url parameter is required and must be a non-empty string")
        
        if not trace_id or not isinstance(trace_id, str) or not trace_id.strip():
            logger.error("build_vercel_link called with empty or invalid trace_id",
                        extra={"deployment_url": deployment_url})
            raise ValueError("trace_id parameter is required and must be a non-empty string")
        
        deployment_url = deployment_url.strip()
        trace_id = trace_id.strip()
        
        # Construct Vercel logs URL with deployment URL and trace ID query
        # Vercel logs UI uses query parameter format: q=traceId:{trace_id}
        base_url = "https://vercel.com/logs"
        
        # Build query parameters with proper encoding
        # Include deployment URL for context and trace ID for filtering
        query_params = {
            "deploymentUrl": deployment_url,
            "q": f"traceId:{trace_id}"
        }
        
        # Use urlencode for proper query parameter encoding
        encoded_query = parse.urlencode(query_params)
        full_url = f"{base_url}?{encoded_query}"
        
        logger.info("Built Vercel log link",
                   extra={
                       "deployment_url": deployment_url,
                       "trace_id": trace_id,
                       "link": full_url
                   })
        
        return full_url
    
    def build_gcp_link(self, project: str, insert_id: str) -> str:
        """
        Construct deep link to GCP Log Explorer with insertId filter.
        
        Builds a URL that opens GCP Cloud Logging Log Explorer with a pre-populated
        query filtering to the specific log entry by insertId. Per Section 0.7.1
        requirement #5, links must open directly to specific log entries.
        
        The query format uses GCP's Log Explorer query syntax with insertId filter,
        properly encoded for URL transmission.
        
        Args:
            project: GCP project ID containing the logs (e.g., "my-gcp-project")
            insert_id: GCP log entry insertId for filtering to specific entry
        
        Returns:
            str: Formatted GCP Log Explorer URL with insertId query filter
                 Example: https://console.cloud.google.com/logs/query;query=insertId%3D%22abc123%22?project=my-gcp-project
        
        Raises:
            ValueError: If project or insert_id are empty or None
        
        Notes:
            - Uses GCP Log Explorer query syntax: insertId="<insert_id>"
            - Query parameter is percent-encoded (%3D for =, %22 for ")
            - Project is passed as query parameter for proper context
            - Links open directly to specific log entry in Log Explorer
        """
        # Validate required parameters
        if not project or not isinstance(project, str) or not project.strip():
            logger.error("build_gcp_link called with empty or invalid project")
            raise ValueError("project parameter is required and must be a non-empty string")
        
        if not insert_id or not isinstance(insert_id, str) or not insert_id.strip():
            logger.error("build_gcp_link called with empty or invalid insert_id",
                        extra={"project": project})
            raise ValueError("insert_id parameter is required and must be a non-empty string")
        
        project = project.strip()
        insert_id = insert_id.strip()
        
        # Construct GCP Log Explorer URL with insertId query filter
        # Format per Section 0.5.1: https://console.cloud.google.com/logs/query;query=insertId%3D{insert_id}?project={project}
        # GCP Log Explorer query syntax: insertId="<insert_id>"
        base_url = "https://console.cloud.google.com/logs/query"
        
        # Build the query filter for insertId
        # GCP expects: insertId="<value>"
        query_filter = f'insertId="{insert_id}"'
        
        # URL encode the query filter for the query path parameter
        # The semicolon syntax is GCP-specific: /logs/query;query=<encoded_query>
        encoded_query = parse.quote(query_filter, safe='')
        
        # Construct the full URL with encoded query in path and project in query params
        # Format: /logs/query;query=<encoded>&?project=<project_id>
        full_url = f"{base_url};query={encoded_query}?project={project}"
        
        logger.info("Built GCP log link",
                   extra={
                       "project": project,
                       "insert_id": insert_id,
                       "link": full_url
                   })
        
        return full_url


# Export the LogLinkBuilder class for use by other services
__all__ = ["LogLinkBuilder"]
