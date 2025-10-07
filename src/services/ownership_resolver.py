"""
Ownership Resolution Service

This module implements pattern-based assignment rules for Jira issue routing,
determining the appropriate assignee or component based on error event attributes.
The OwnershipResolver evaluates configurable rules loaded from YAML to route issues
to the correct team or component based on service name, URL path patterns, and
error class names.

The service supports both direct assignee assignment (using Atlassian account IDs)
and component-based routing with default assignees. Rules are evaluated in priority
order: specific error_class patterns, then path regex matches, then service defaults.

Configuration File Format (config/ownership_rules.yaml):
    rules:
      - service: "web-app"
        path_regex: "/api/.*"
        assignee: "5f8e9a1b2c3d4e5f6a7b8c9d"  # Backend team lead
      - service: "web-app"
        error_class: "TypeError"
        component: "Frontend"
      - service: "api-service"
        assignee: "1a2b3c4d5e6f7a8b9c0d1e2f"  # API team lead

Usage Example:
    from services.ownership_resolver import OwnershipResolver
    from models.error_event import NormalizedErrorEvent
    
    # Initialize resolver with configuration file
    resolver = OwnershipResolver('config/ownership_rules.yaml')
    
    # Resolve ownership for an error event
    result = resolver.resolve(event)
    
    if result and 'assignee' in result:
        print(f"Assign to user: {result['assignee']}")
    elif result and 'component' in result:
        print(f"Route to component: {result['component']}")
    else:
        print("Use Jira project default assignment")

Rule Evaluation Priority:
    1. Exact error_class match with service match (highest priority)
    2. Path regex match with service match
    3. Service-only match (default for service)
    4. No match returns None (use Jira default)

Performance Considerations:
    - Regex patterns are compiled once at initialization for efficiency
    - Rules are evaluated in order until first match
    - Path regex compilation reduces per-request overhead per Section 0.5.1
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Pattern
import re
import yaml

from src.models.error_event import NormalizedErrorEvent


@dataclass
class OwnershipRule:
    """
    Represents a single ownership assignment rule from configuration.
    
    This dataclass stores the parsed rule definition including optional pattern
    matching criteria (service, path_regex, error_class) and routing destination
    (assignee account ID or component name). The compiled_pattern field caches
    the compiled regex for efficient repeated evaluation.
    
    Attributes:
        service: Service name to match (e.g., 'web-app', 'api-service')
        path_regex: Optional regex pattern for URL path matching (e.g., '/api/.*')
        error_class: Optional error class name pattern (e.g., 'TypeError')
        assignee: Optional Atlassian account ID for direct assignment
        component: Optional Jira component name for component-based routing
        compiled_pattern: Cached compiled regex Pattern object for path_regex
    
    Validation Rules:
        - At least one of service, path_regex, or error_class must be specified
        - Exactly one of assignee or component must be specified
        - If path_regex is provided, compiled_pattern is automatically created
    
    Example:
        >>> rule = OwnershipRule(
        ...     service='web-app',
        ...     path_regex='/api/.*',
        ...     error_class=None,
        ...     assignee='5f8e9a1b2c3d4e5f6a7b8c9d',
        ...     component=None,
        ...     compiled_pattern=re.compile('/api/.*')
        ... )
    """
    service: Optional[str] = None
    path_regex: Optional[str] = None
    error_class: Optional[str] = None
    assignee: Optional[str] = None
    component: Optional[str] = None
    compiled_pattern: Optional[Pattern] = None


class OwnershipResolver:
    """
    Pattern-based ownership resolution service for Jira issue routing.
    
    This service loads assignment rules from YAML configuration and evaluates them
    against error events to determine the appropriate Jira assignee or component.
    Rules are evaluated in priority order (error_class → path regex → service default)
    until a match is found.
    
    The resolver supports configuration-driven behavior per Section 0.7.2, allowing
    non-developer updates to assignment rules through YAML file changes without
    code modifications.
    
    Attributes:
        rules: List of OwnershipRule objects loaded from configuration
        config_path: Path to the YAML configuration file for reloading
    
    Thread Safety:
        This class is thread-safe for read operations after initialization.
        The rules list is populated once at initialization and not modified.
    
    Example:
        >>> resolver = OwnershipResolver('config/ownership_rules.yaml')
        >>> event = NormalizedErrorEvent(...)
        >>> result = resolver.resolve(event)
        >>> if result:
        ...     print(f"Route to: {result}")
    """
    
    def __init__(self, yaml_path: str):
        """
        Initialize the ownership resolver with rules from YAML configuration.
        
        Loads and parses the ownership rules file, compiling regex patterns for
        efficient evaluation. The configuration file is validated at initialization
        to fail fast if rules are malformed.
        
        Args:
            yaml_path: Path to YAML configuration file containing ownership rules
        
        Raises:
            FileNotFoundError: If the configuration file does not exist
            ValueError: If the configuration file is malformed or contains invalid rules
            yaml.YAMLError: If the YAML syntax is invalid
        
        Example:
            >>> resolver = OwnershipResolver('config/ownership_rules.yaml')
        """
        self.config_path = yaml_path
        self.rules = self.load_rules(yaml_path)
    
    def load_rules(self, yaml_path: str) -> List[OwnershipRule]:
        """
        Load and parse ownership rules from YAML configuration file.
        
        Reads the YAML file, validates the structure, and creates OwnershipRule
        objects with compiled regex patterns. Rules are returned in the order
        defined in the configuration file, which determines evaluation priority.
        
        Expected YAML Structure:
            rules:
              - service: "web-app"
                path_regex: "/api/.*"
                assignee: "account_id_123"
              - service: "web-app"
                error_class: "TypeError"
                component: "Frontend"
        
        Args:
            yaml_path: Path to YAML configuration file (absolute or relative)
        
        Returns:
            List of OwnershipRule objects in configuration order
        
        Raises:
            FileNotFoundError: If yaml_path does not exist
            ValueError: If rules are malformed or missing required fields
            yaml.YAMLError: If YAML syntax is invalid
        
        Validation Performed:
            - File exists and is readable
            - YAML contains 'rules' key with list value
            - Each rule has at least one matching criterion (service, path_regex, error_class)
            - Each rule has exactly one routing target (assignee XOR component)
            - Path regex patterns compile successfully
        
        Performance:
            - Regex patterns are compiled once during loading
            - Compiled patterns are cached in OwnershipRule.compiled_pattern
            - Per Section 0.5.1: "compile path regex patterns for efficiency"
        
        Example:
            >>> rules = resolver.load_rules('config/ownership_rules.yaml')
            >>> print(f"Loaded {len(rules)} ownership rules")
        """
        # Convert to Path object for cross-platform compatibility
        config_file = Path(yaml_path)
        
        # Validate file exists
        if not config_file.exists():
            raise FileNotFoundError(
                f"Ownership rules configuration file not found: {yaml_path}\n"
                f"Resolved absolute path: {config_file.resolve()}"
            )
        
        # Read and parse YAML file
        try:
            config_content = config_file.read_text(encoding='utf-8')
            config_data = yaml.safe_load(config_content)
        except yaml.YAMLError as e:
            raise ValueError(
                f"Invalid YAML syntax in ownership rules file {yaml_path}: {e}"
            ) from e
        except Exception as e:
            raise ValueError(
                f"Failed to read ownership rules file {yaml_path}: {e}"
            ) from e
        
        # Validate configuration structure
        if not isinstance(config_data, dict):
            raise ValueError(
                f"Ownership rules configuration must be a YAML dictionary, "
                f"got {type(config_data).__name__}"
            )
        
        if 'rules' not in config_data:
            raise ValueError(
                f"Ownership rules configuration missing required 'rules' key in {yaml_path}"
            )
        
        rules_list = config_data['rules']
        if not isinstance(rules_list, list):
            raise ValueError(
                f"Ownership rules 'rules' must be a list, got {type(rules_list).__name__}"
            )
        
        # Parse and validate each rule
        parsed_rules: List[OwnershipRule] = []
        for idx, rule_data in enumerate(rules_list):
            if not isinstance(rule_data, dict):
                raise ValueError(
                    f"Rule #{idx + 1} in {yaml_path} must be a dictionary, "
                    f"got {type(rule_data).__name__}"
                )
            
            # Extract rule fields with defaults
            service = rule_data.get('service')
            path_regex = rule_data.get('path_regex')
            error_class = rule_data.get('error_class')
            assignee = rule_data.get('assignee')
            component = rule_data.get('component')
            
            # Validate at least one matching criterion is specified
            if not any([service, path_regex, error_class]):
                raise ValueError(
                    f"Rule #{idx + 1} in {yaml_path} must specify at least one of: "
                    f"service, path_regex, error_class"
                )
            
            # Validate exactly one routing target is specified (assignee XOR component)
            if not assignee and not component:
                raise ValueError(
                    f"Rule #{idx + 1} in {yaml_path} must specify either 'assignee' or 'component'"
                )
            
            if assignee and component:
                raise ValueError(
                    f"Rule #{idx + 1} in {yaml_path} cannot specify both 'assignee' and 'component'. "
                    f"Choose one routing method."
                )
            
            # Compile regex pattern if path_regex is specified
            compiled_pattern: Optional[Pattern] = None
            if path_regex:
                try:
                    compiled_pattern = re.compile(path_regex)
                except re.error as e:
                    raise ValueError(
                        f"Rule #{idx + 1} in {yaml_path} has invalid regex pattern '{path_regex}': {e}"
                    ) from e
            
            # Create OwnershipRule object
            rule = OwnershipRule(
                service=service,
                path_regex=path_regex,
                error_class=error_class,
                assignee=assignee,
                component=component,
                compiled_pattern=compiled_pattern
            )
            parsed_rules.append(rule)
        
        return parsed_rules
    
    def resolve(self, event: NormalizedErrorEvent) -> Optional[Dict[str, str]]:
        """
        Determine Jira issue assignee or component for the given error event.
        
        Evaluates ownership rules in priority order against the error event attributes,
        returning the first matching rule's routing target. Rules are evaluated with
        the following precedence per Section 0.5.1:
        
        1. Error class match (with optional service match)
        2. Path regex match (with optional service match)
        3. Service-only match (default for service)
        
        If no rules match, returns None to indicate Jira should use project defaults.
        
        Args:
            event: NormalizedErrorEvent containing service, environment, error_class, path
        
        Returns:
            Dictionary with routing target, or None if no match:
            - {"assignee": "account_id"} for direct user assignment
            - {"component": "component_name"} for component-based routing
            - None for Jira project default assignment
        
        Rule Matching Logic:
            A rule matches if ALL specified criteria are satisfied:
            - If rule.service is set: must match event.service
            - If rule.error_class is set: must match event.error_class
            - If rule.path_regex is set: must match event.path (if path is not None)
        
        Edge Cases:
            - If event.path is None and rule has path_regex: rule does not match
            - If event.error_class is empty and rule has error_class: rule does not match
            - Empty service name never matches any rule
        
        Performance:
            - Rules evaluated sequentially until first match
            - Regex patterns pre-compiled for O(1) pattern retrieval
            - Average case: O(n) where n is number of rules evaluated before match
        
        Example:
            >>> event = NormalizedErrorEvent(
            ...     source='vercel',
            ...     service='web-app',
            ...     environment='prod',
            ...     error_class='TypeError',
            ...     path='/api/checkout',
            ...     # ... other required fields
            ... )
            >>> result = resolver.resolve(event)
            >>> print(result)
            {'assignee': '5f8e9a1b2c3d4e5f6a7b8c9d'}
        """
        # Iterate through rules in configuration order
        for rule in self.rules:
            # Check if this rule matches the event
            if self._rule_matches_event(rule, event):
                # Return routing target from first matching rule
                if rule.assignee:
                    return {"assignee": rule.assignee}
                elif rule.component:
                    return {"component": rule.component}
        
        # No matching rule found, use Jira default assignment
        return None
    
    def _rule_matches_event(self, rule: OwnershipRule, event: NormalizedErrorEvent) -> bool:
        """
        Check if a rule matches the given error event.
        
        A rule matches if ALL specified criteria (service, error_class, path_regex)
        are satisfied. Unspecified criteria are treated as wildcards (always match).
        
        This private helper method encapsulates the matching logic to keep the
        resolve() method focused on rule iteration and result construction.
        
        Args:
            rule: OwnershipRule to evaluate
            event: NormalizedErrorEvent to match against
        
        Returns:
            True if all rule criteria match the event, False otherwise
        
        Matching Criteria:
            - service: Exact string match (case-sensitive)
            - error_class: Exact string match (case-sensitive)
            - path_regex: Regex pattern match against event.path
        
        Special Cases:
            - If rule.service is set but event.service is empty/None: no match
            - If rule.path_regex is set but event.path is None: no match
            - If rule.error_class is set but event.error_class is empty/None: no match
        
        Example:
            >>> rule = OwnershipRule(service='web-app', path_regex='/api/.*', ...)
            >>> event = NormalizedErrorEvent(service='web-app', path='/api/checkout', ...)
            >>> resolver._rule_matches_event(rule, event)
            True
        """
        # Check service match if specified in rule
        if rule.service is not None:
            if not event.service or event.service != rule.service:
                return False
        
        # Check error_class match if specified in rule
        if rule.error_class is not None:
            if not event.error_class or event.error_class != rule.error_class:
                return False
        
        # Check path_regex match if specified in rule
        if rule.path_regex is not None and rule.compiled_pattern is not None:
            # Path regex requires event.path to be present
            if event.path is None:
                return False
            
            # Use compiled pattern for efficient matching
            if not rule.compiled_pattern.match(event.path):
                return False
        
        # All specified criteria matched
        return True
