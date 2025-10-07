"""
Normalized Error Event Data Model

This module defines the canonical internal format for error events ingested from
multiple sources (Vercel Log Drain, GCP Cloud Logging). The NormalizedErrorEvent
dataclass provides a unified schema that abstracts away source-specific payload
formats, enabling consistent error processing, fingerprinting, and Jira integration.

The dataclass includes comprehensive validation in __post_init__ to ensure data
integrity and proper normalization of environment values. All error events flowing
through the processing pipeline use this structure.

Usage Example:
    from models.error_event import NormalizedErrorEvent
    from datetime import datetime
    
    event = NormalizedErrorEvent(
        source='vercel',
        service='web-app',
        environment='production',
        error_class='TypeError',
        message='Cannot read property x of undefined',
        stack_trace='TypeError: Cannot read property...\n  at /app/pages/checkout.tsx:123:45',
        path='/api/checkout',
        url='https://my-app.vercel.app/api/checkout',
        release='dpl_xyz123',
        log_url='https://vercel.com/logs?traceId=abc123',
        event_id='vercel-xyz-123',
        occurred_at=datetime.now()
    )
    
    # Serialize for storage
    event_dict = event.to_dict()
    
    # Deserialize from storage
    restored_event = NormalizedErrorEvent.from_dict(event_dict)

Field Descriptions:
    source: Error source identifier, must be 'vercel' or 'gcp'
    service: Service name (e.g., 'web-app', 'api-service'), required non-empty
    environment: Deployment environment, normalized to 'prod', 'staging', or 'dev'
    error_class: Error type/class name (e.g., 'TypeError', 'RuntimeError')
    message: Error message text, required non-empty
    stack_trace: Full stack trace if available, can be None
    path: Request path that triggered error (e.g., '/api/checkout'), optional
    url: Full URL including domain if available, optional
    release: Release version or deployment ID, optional
    log_url: Deep link to log entry in source system (Vercel or GCP Log Explorer)
    event_id: Unique event identifier for deduplication (insertId for GCP)
    occurred_at: Timestamp when error occurred, must be datetime instance

Validation Rules:
    - source must be in ['vercel', 'gcp']
    - environment is normalized: production->prod, staging->stg, development->dev
    - Required string fields must be non-empty after stripping whitespace
    - occurred_at must be a datetime instance
    - event_id must be non-empty for deduplication tracking
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any


@dataclass
class NormalizedErrorEvent:
    """
    Canonical error event format for multi-source error ingestion system.
    
    This dataclass represents the unified schema for error events from Vercel
    and GCP sources. It serves as the primary data structure throughout the
    error processing pipeline, including fingerprinting, frequency tracking,
    severity evaluation, and Jira integration.
    
    Attributes:
        source: Error source identifier ('vercel' | 'gcp')
        service: Service name that generated the error
        environment: Deployment environment ('prod' | 'staging' | 'dev')
        error_class: Error type/class name
        message: Error message text
        stack_trace: Full stack trace (optional)
        path: Request path that triggered error (optional)
        url: Full URL including domain (optional)
        release: Release version or deployment ID (optional)
        log_url: Deep link to log entry in source system
        event_id: Unique event identifier for deduplication
        occurred_at: Timestamp when error occurred
    """
    
    # Required fields
    source: str  # 'vercel' or 'gcp'
    service: str  # Service name (e.g., 'web-app', 'api-service')
    environment: str  # 'prod', 'staging', or 'dev' (normalized)
    error_class: str  # Error type/class name
    message: str  # Error message text
    log_url: str  # Deep link to source system logs
    event_id: str  # Unique identifier for deduplication
    occurred_at: datetime  # Error occurrence timestamp
    
    # Optional fields
    stack_trace: Optional[str] = None  # Full stack trace if available
    path: Optional[str] = None  # Request path (e.g., '/api/checkout')
    url: Optional[str] = None  # Full URL including domain
    release: Optional[str] = None  # Release version or deployment ID
    
    def __post_init__(self) -> None:
        """
        Validate and normalize field values after dataclass initialization.
        
        This method enforces data integrity constraints and normalizes
        environment values to standard forms. It runs automatically after
        the dataclass __init__ method.
        
        Validation performed:
            - source must be 'vercel' or 'gcp'
            - environment normalized and validated
            - Required strings must be non-empty
            - occurred_at must be datetime instance
            - event_id must be non-empty
        
        Raises:
            ValueError: If any validation constraint is violated
        """
        # Validate source
        if self.source not in ['vercel', 'gcp']:
            raise ValueError(
                f"Invalid source '{self.source}'. Must be 'vercel' or 'gcp'."
            )
        
        # Normalize and validate environment
        environment_map = {
            'production': 'prod',
            'prod': 'prod',
            'staging': 'staging',
            'stg': 'staging',
            'stage': 'staging',
            'development': 'dev',
            'dev': 'dev',
        }
        
        normalized_env = environment_map.get(self.environment.lower())
        if normalized_env is None:
            raise ValueError(
                f"Invalid environment '{self.environment}'. Must be one of: "
                f"prod, production, staging, stg, stage, dev, development."
            )
        
        # Update environment to normalized value
        object.__setattr__(self, 'environment', normalized_env)
        
        # Validate required string fields are non-empty
        required_string_fields = {
            'service': self.service,
            'error_class': self.error_class,
            'message': self.message,
            'log_url': self.log_url,
            'event_id': self.event_id,
        }
        
        for field_name, field_value in required_string_fields.items():
            if not isinstance(field_value, str):
                raise ValueError(
                    f"Field '{field_name}' must be a string, got {type(field_value).__name__}."
                )
            
            stripped_value = field_value.strip()
            if not stripped_value:
                raise ValueError(
                    f"Field '{field_name}' cannot be empty or whitespace-only."
                )
            
            # Update field with stripped value to remove leading/trailing whitespace
            if field_value != stripped_value:
                object.__setattr__(self, field_name, stripped_value)
        
        # Validate occurred_at is datetime instance
        if not isinstance(self.occurred_at, datetime):
            raise ValueError(
                f"Field 'occurred_at' must be a datetime instance, "
                f"got {type(self.occurred_at).__name__}."
            )
        
        # Strip whitespace from optional string fields if they are provided
        optional_string_fields = ['stack_trace', 'path', 'url', 'release']
        for field_name in optional_string_fields:
            field_value = getattr(self, field_name)
            if field_value is not None and isinstance(field_value, str):
                stripped_value = field_value.strip()
                if stripped_value != field_value:
                    object.__setattr__(self, field_name, stripped_value)
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize the error event to a dictionary for JSON storage.
        
        Converts the dataclass to a dictionary format suitable for JSON
        serialization, MongoDB storage, and structured logging. The datetime
        field is converted to ISO 8601 format string.
        
        Returns:
            Dictionary containing all fields with datetime as ISO format string
            
        Example:
            >>> event.to_dict()
            {
                'source': 'vercel',
                'service': 'web-app',
                'environment': 'prod',
                'error_class': 'TypeError',
                'message': 'Cannot read property...',
                'stack_trace': 'TypeError: ...',
                'path': '/api/checkout',
                'url': 'https://my-app.vercel.app/api/checkout',
                'release': 'dpl_xyz123',
                'log_url': 'https://vercel.com/logs?traceId=abc123',
                'event_id': 'vercel-xyz-123',
                'occurred_at': '2025-01-15T10:30:45.123000'
            }
        """
        return {
            'source': self.source,
            'service': self.service,
            'environment': self.environment,
            'error_class': self.error_class,
            'message': self.message,
            'stack_trace': self.stack_trace,
            'path': self.path,
            'url': self.url,
            'release': self.release,
            'log_url': self.log_url,
            'event_id': self.event_id,
            'occurred_at': self.occurred_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'NormalizedErrorEvent':
        """
        Deserialize an error event from a dictionary.
        
        Reconstructs a NormalizedErrorEvent instance from a dictionary,
        typically loaded from MongoDB storage or JSON logs. The datetime
        string is parsed back to a datetime object using ISO 8601 format.
        
        Args:
            data: Dictionary containing error event fields
            
        Returns:
            NormalizedErrorEvent instance
            
        Raises:
            ValueError: If occurred_at is not a valid ISO format datetime string
            KeyError: If required fields are missing from the dictionary
            
        Example:
            >>> data = {
            ...     'source': 'vercel',
            ...     'service': 'web-app',
            ...     'environment': 'prod',
            ...     'error_class': 'TypeError',
            ...     'message': 'Cannot read property...',
            ...     'log_url': 'https://vercel.com/logs?traceId=abc123',
            ...     'event_id': 'vercel-xyz-123',
            ...     'occurred_at': '2025-01-15T10:30:45.123000'
            ... }
            >>> event = NormalizedErrorEvent.from_dict(data)
        """
        # Parse datetime string to datetime object
        occurred_at_str = data['occurred_at']
        if isinstance(occurred_at_str, str):
            occurred_at = datetime.fromisoformat(occurred_at_str)
        elif isinstance(occurred_at_str, datetime):
            occurred_at = occurred_at_str
        else:
            raise ValueError(
                f"Field 'occurred_at' must be ISO format string or datetime, "
                f"got {type(occurred_at_str).__name__}."
            )
        
        # Create instance with parsed datetime
        return cls(
            source=data['source'],
            service=data['service'],
            environment=data['environment'],
            error_class=data['error_class'],
            message=data['message'],
            stack_trace=data.get('stack_trace'),
            path=data.get('path'),
            url=data.get('url'),
            release=data.get('release'),
            log_url=data['log_url'],
            event_id=data['event_id'],
            occurred_at=occurred_at,
        )
    
    def __repr__(self) -> str:
        """
        Generate a concise string representation for debugging.
        
        Returns a string representation with truncated message for readability
        in logs and debugging output. The message is limited to 100 characters
        to prevent excessively long log entries.
        
        Returns:
            String representation of the error event
            
        Example:
            >>> repr(event)
            "NormalizedErrorEvent(source='vercel', service='web-app', env='prod', error_class='TypeError', message='Cannot read property x of undefined...', event_id='vercel-xyz-123')"
        """
        truncated_message = self.message[:100]
        if len(self.message) > 100:
            truncated_message += '...'
        
        return (
            f"NormalizedErrorEvent("
            f"source='{self.source}', "
            f"service='{self.service}', "
            f"env='{self.environment}', "
            f"error_class='{self.error_class}', "
            f"message='{truncated_message}', "
            f"event_id='{self.event_id}'"
            f")"
        )
