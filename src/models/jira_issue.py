"""
Jira Issue Data Model

This module defines the JiraIssueData dataclass, which represents the structure
of Jira issues for the Error Triage service. It provides a typed interface for
creating, updating, and retrieving Jira Bug issues with error tracking metadata.

The dataclass handles bidirectional transformation between:
- Internal Python representation (JiraIssueData)
- Jira REST API format (compatible with jira library v3.10+)

Usage in Jira Integration Service:
    from models.jira_issue import JiraIssueData
    
    # Creating a new issue
    issue_data = JiraIssueData(
        issue_key=None,  # Will be set after creation
        summary="[prod:web-app] TypeError - Cannot read property 'x' of undefined",
        description="## Error Details\\n\\nStack trace excerpt...",
        labels=["source:vercel", "env:prod", "service:web-app", "errfp:abc123"],
        priority="High",
        severity="SEV2",
        assignee="5f8e9a1b2c3d4e5f6a7b8c9d",
        component=None
    )
    
    # Convert to Jira API format
    jira_fields = issue_data.to_jira_fields(project_key="ET")
    created_issue = jira_client.create_issue(fields=jira_fields)
    
    # Parse existing Jira issue
    issue_data = JiraIssueData.from_jira_issue(jira_issue)

Field Descriptions:
- issue_key: Jira issue identifier (e.g., "ET-1234"), None for new issues
- summary: Issue title with format "[env:service] error_class - message" (max 255 chars)
- description: Markdown-formatted error context, stack trace, and log links
- labels: List of classification labels including fingerprint, source, env, service
- priority: Jira priority (Highest, High, Medium, Low)
- severity: Custom severity field (SEV1, SEV2, SEV3, SEV4)
- assignee: Atlassian account ID or None for project default
- component: Jira component name for routing or None
- status: Current issue status (Open, In Progress, Done, etc.)
- created_at: Issue creation timestamp
- updated_at: Last modification timestamp

Custom Fields:
- customfield_10050: Severity field (SEV1-SEV4) configured in Jira project
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


# Allowed values for validation
ALLOWED_PRIORITIES = ["Highest", "High", "Medium", "Low"]
ALLOWED_SEVERITIES = ["SEV1", "SEV2", "SEV3", "SEV4"]


@dataclass
class JiraIssueData:
    """
    Dataclass representing Jira issue structure for error tracking.
    
    Provides validation, serialization, and deserialization for Jira Bug issues
    created by the Error Triage service. Ensures data integrity and API compatibility.
    
    Attributes:
        issue_key: Jira issue key (e.g., "ET-1234"), None for new issues
        summary: Issue title following format "[env:service] error_class - message"
        description: Markdown-formatted error details with stack trace and context
        labels: Classification labels for filtering and grouping
        priority: Jira priority level (Highest, High, Medium, Low)
        severity: Custom severity field value (SEV1, SEV2, SEV3, SEV4)
        assignee: Atlassian account ID or None for default assignment
        component: Jira component name for issue routing
        status: Current issue status (e.g., "Open", "In Progress")
        created_at: Timestamp when issue was created in Jira
        updated_at: Timestamp when issue was last modified
    """
    
    summary: str
    description: str
    labels: List[str]
    priority: str
    severity: str
    issue_key: Optional[str] = None
    assignee: Optional[str] = None
    component: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def __post_init__(self) -> None:
        """
        Validate dataclass fields after initialization.
        
        Ensures data integrity by checking:
        - Summary is non-empty and within Jira length limits
        - Priority and severity are valid values
        - Labels list is not empty
        
        Raises:
            ValueError: If any validation check fails with descriptive message
        """
        # Validate summary
        if not self.summary or not self.summary.strip():
            raise ValueError("Summary cannot be empty")
        
        if len(self.summary) > 255:
            raise ValueError(
                f"Summary exceeds maximum length of 255 characters: {len(self.summary)}"
            )
        
        # Validate priority
        if self.priority not in ALLOWED_PRIORITIES:
            raise ValueError(
                f"Priority must be one of {ALLOWED_PRIORITIES}, got: {self.priority}"
            )
        
        # Validate severity
        if self.severity not in ALLOWED_SEVERITIES:
            raise ValueError(
                f"Severity must be one of {ALLOWED_SEVERITIES}, got: {self.severity}"
            )
        
        # Validate labels
        if not self.labels:
            raise ValueError("Labels list cannot be empty")
        
        # Trim whitespace from summary and description
        self.summary = self.summary.strip()
        self.description = self.description.strip()
    
    def to_jira_fields(self, project_key: str, issue_type: str = "Bug") -> Dict[str, Any]:
        """
        Convert dataclass to Jira REST API field dictionary format.
        
        Maps internal representation to structure required by jira library's
        create_issue() and update() methods. Handles optional fields gracefully.
        
        Args:
            project_key: Jira project key (e.g., "ET" for Error Triage project)
            issue_type: Jira issue type name, defaults to "Bug"
        
        Returns:
            Dictionary compatible with jira.create_issue(fields=...) containing:
            - project: Project identifier
            - issuetype: Issue type configuration
            - summary: Issue title
            - description: Issue body
            - labels: Classification labels
            - priority: Priority configuration
            - customfield_10050: Severity custom field
            - assignee: Assignee configuration (if specified)
            - components: Component list (if specified)
        
        Example:
            >>> issue_data = JiraIssueData(
            ...     summary="[prod:api] Error",
            ...     description="Details",
            ...     labels=["env:prod"],
            ...     priority="High",
            ...     severity="SEV2"
            ... )
            >>> fields = issue_data.to_jira_fields("ET")
            >>> created = jira_client.create_issue(fields=fields)
        """
        fields: Dict[str, Any] = {
            "project": {"key": project_key},
            "issuetype": {"name": issue_type},
            "summary": self.summary,
            "description": self.description,
            "labels": self.labels,
            "priority": {"name": self.priority},
            # Custom severity field (must be configured in Jira project)
            "customfield_10050": {"value": self.severity},
        }
        
        # Add optional assignee (Atlassian account ID)
        if self.assignee:
            fields["assignee"] = {"accountId": self.assignee}
        
        # Add optional component
        if self.component:
            fields["components"] = [{"name": self.component}]
        
        return fields
    
    @classmethod
    def from_jira_issue(cls, jira_issue: Any) -> "JiraIssueData":
        """
        Create JiraIssueData instance from Jira API response object.
        
        Parses issue data returned by jira.search_issues() or jira.issue() to
        populate dataclass fields. Handles missing or None fields gracefully.
        
        Args:
            jira_issue: Issue object from jira library (jira.resources.Issue)
        
        Returns:
            JiraIssueData instance populated with issue field values
        
        Raises:
            ValueError: If required fields are missing from Jira issue
            AttributeError: If jira_issue object structure is invalid
        
        Example:
            >>> issues = jira_client.search_issues("project = ET")
            >>> issue_data = JiraIssueData.from_jira_issue(issues[0])
            >>> print(issue_data.issue_key)  # "ET-1234"
        """
        fields = jira_issue.fields
        
        # Extract issue key
        issue_key = jira_issue.key
        
        # Extract basic required fields
        summary = fields.summary
        description = getattr(fields, "description", "") or ""
        labels = getattr(fields, "labels", [])
        
        # Extract priority with fallback
        priority_obj = getattr(fields, "priority", None)
        priority = priority_obj.name if priority_obj else "Low"
        
        # Extract custom severity field with fallback
        severity_obj = getattr(fields, "customfield_10050", None)
        if severity_obj and hasattr(severity_obj, "value"):
            severity = severity_obj.value
        else:
            # Default to SEV4 if custom field not populated
            severity = "SEV4"
        
        # Extract optional assignee
        assignee_obj = getattr(fields, "assignee", None)
        assignee = assignee_obj.accountId if assignee_obj else None
        
        # Extract optional component
        components = getattr(fields, "components", [])
        component = components[0].name if components else None
        
        # Extract status
        status_obj = getattr(fields, "status", None)
        status = status_obj.name if status_obj else None
        
        # Extract timestamps
        created_str = getattr(fields, "created", None)
        updated_str = getattr(fields, "updated", None)
        
        # Parse ISO datetime strings
        created_at = None
        if created_str:
            try:
                # Jira returns ISO format with timezone (e.g., 2025-01-15T10:30:45.123+0000)
                # Python's fromisoformat handles this in 3.11+
                created_at = datetime.fromisoformat(created_str.replace("+0000", "+00:00"))
            except (ValueError, AttributeError):
                # If parsing fails, leave as None
                pass
        
        updated_at = None
        if updated_str:
            try:
                updated_at = datetime.fromisoformat(updated_str.replace("+0000", "+00:00"))
            except (ValueError, AttributeError):
                pass
        
        return cls(
            issue_key=issue_key,
            summary=summary,
            description=description,
            labels=labels,
            priority=priority,
            severity=severity,
            assignee=assignee,
            component=component,
            status=status,
            created_at=created_at,
            updated_at=updated_at,
        )
    
    def __repr__(self) -> str:
        """
        Return string representation for debugging.
        
        Returns:
            Human-readable string with issue key and truncated summary
        """
        summary_preview = self.summary[:60] + "..." if len(self.summary) > 60 else self.summary
        return f"JiraIssueData(issue_key={self.issue_key!r}, summary={summary_preview!r}, priority={self.priority}, severity={self.severity})"
