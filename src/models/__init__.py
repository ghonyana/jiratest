"""
Models Package for Error Triage Service

This package provides data models and domain entities for the Error Triage → Jira
Upserter service. It defines the core data structures used throughout the application
for representing error events, Jira issues, and severity classification rules.

Package Contents:
    - NormalizedErrorEvent: Canonical error event format for multi-source ingestion
    - JiraIssueData: Jira issue representation with bidirectional API transformation
    - SeverityRule: Individual severity classification rule with threshold mapping
    - RuleCondition: Environment-specific collection of rules with evaluation logic

Usage:
    Import models directly from this package for use in service layer and route handlers:

    >>> from models import NormalizedErrorEvent, JiraIssueData
    >>> from models import SeverityRule, RuleCondition
    
    >>> # Create a normalized error event
    >>> event = NormalizedErrorEvent(
    ...     source='vercel',
    ...     service='web-app',
    ...     environment='production',
    ...     error_class='TypeError',
    ...     message='Cannot read property x of undefined',
    ...     log_url='https://vercel.com/logs?traceId=abc123',
    ...     event_id='vercel-xyz-123',
    ...     occurred_at=datetime.now()
    ... )
    
    >>> # Create Jira issue data
    >>> issue = JiraIssueData(
    ...     summary='[prod:web-app] TypeError - Cannot read property x',
    ...     description='## Error Details...',
    ...     labels=['source:vercel', 'env:prod', 'errfp:abc123'],
    ...     priority='High',
    ...     severity='SEV2'
    ... )
    
    >>> # Define severity rules
    >>> rule = SeverityRule(threshold=10, priority='High', severity='SEV2')
    >>> condition = RuleCondition(environment='production', rules=[rule])
    >>> priority, severity = condition.evaluate(count=15)

Architecture:
    This package serves as the single source of truth for domain entity definitions,
    ensuring consistent data structures across the error processing pipeline:
    
    1. Payload Adapters → NormalizedErrorEvent
    2. Service Layer → NormalizedErrorEvent, JiraIssueData
    3. Severity Engine → SeverityRule, RuleCondition
    4. Jira Integration → JiraIssueData
    
    All dataclasses include comprehensive validation in __post_init__ methods and
    provide serialization/deserialization utilities for JSON storage and API interaction.

Version: 1.0.0
"""

# Import all model classes from submodules using relative imports
from .error_event import NormalizedErrorEvent
from .jira_issue import JiraIssueData
from .severity_rule import RuleCondition, SeverityRule

# Package version
__version__ = "1.0.0"

# Explicit public API declaration
# Controls what is exported with 'from models import *'
__all__ = [
    "NormalizedErrorEvent",
    "JiraIssueData",
    "SeverityRule",
    "RuleCondition",
]
