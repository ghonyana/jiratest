"""
Services Package Initialization

This module serves as the central export point for all service classes in the
Error Triage → Jira Upserter service. It provides a clean import interface that
enables simplified imports throughout the application following Python package
best practices.

Usage:
    from services import ErrorFingerprinter, JiraIntegrationService
    from services import PayloadAdapterFactory, FrequencyTracker

Architecture:
    This package contains all business logic services organized into distinct
    modules with single responsibilities per Section 0.7.2 architectural patterns:
    
    - Payload Adapters: Transform external webhook formats to canonical schema
    - Error Processing: Fingerprinting, sanitization, deduplication
    - Redis Services: Frequency tracking, rate limiting, deduplication cache
    - Jira Integration: Complete issue lifecycle management
    - Configuration Services: Rule evaluation engines for severity and ownership
    - Utility Services: Deep link construction for external log platforms

Dependencies:
    All service classes depend on configuration-driven behavior via YAML files
    and maintain stateless design with dependency injection for testability.
"""

# Payload transformation adapters for normalizing Vercel and GCP webhook formats
from .payload_adapters import (
    PayloadAdapterFactory,
    VercelPayloadAdapter,
    GCPPayloadAdapter,
)

# Error processing services for fingerprinting, sanitization, and deduplication
from .fingerprinter import ErrorFingerprinter
from .sanitizer import PIISanitizer
from .deduplication import DeduplicationService

# Redis-backed services for frequency tracking and rate limiting
from .frequency_tracker import FrequencyTracker
from .comment_rate_limiter import CommentRateLimiter

# Configuration-driven rule engines for severity classification and ownership routing
from .severity_engine import SeverityRulesEngine
from .ownership_resolver import OwnershipResolver

# Jira API integration service for complete issue lifecycle management
from .jira_integration import JiraIntegrationService

# Utility service for constructing deep links to external log platforms
from .log_link_builder import LogLinkBuilder

# Explicit export list for clarity and API documentation
__all__ = [
    # Payload Adapters
    "PayloadAdapterFactory",
    "VercelPayloadAdapter",
    "GCPPayloadAdapter",
    # Error Processing
    "ErrorFingerprinter",
    "PIISanitizer",
    "DeduplicationService",
    # Redis Services
    "FrequencyTracker",
    "CommentRateLimiter",
    # Configuration-Driven Services
    "SeverityRulesEngine",
    "OwnershipResolver",
    # Jira Integration
    "JiraIntegrationService",
    # Utilities
    "LogLinkBuilder",
]
