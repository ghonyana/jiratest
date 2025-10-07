"""
Unit Tests Package

This package contains isolated component tests for the Error Triage → Jira Upserter service.
Unit tests mock all external dependencies (Redis, Jira, MongoDB) to test individual
components in isolation.

Test modules in this package:
- test_fingerprinter.py: Error fingerprinting logic tests
- test_sanitizer.py: PII sanitization tests
- test_severity_engine.py: Severity rule evaluation tests
- test_payload_adapters.py: Vercel and GCP payload transformation tests
- test_frequency_tracker.py: Redis counter operation tests
- test_ownership_resolver.py: Assignee determination tests
- test_deduplication.py: Event ID tracking tests
- test_comment_rate_limiter.py: Comment throttling tests

This __init__.py file serves as a package marker to enable pytest test discovery
and proper import resolution for unit test modules.
"""
