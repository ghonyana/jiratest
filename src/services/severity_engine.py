"""
Severity Rules Engine for Frequency-Based Error Classification

This module implements configuration-driven severity classification that maps error
frequency counts to Jira priority and custom severity field values. Rules are loaded
from YAML configuration and evaluated against environment-specific thresholds.

Core Functionality:
    - Load severity rules from config/severity_rules.yaml via yaml.safe_load()
    - Parse environment-specific threshold mappings (prod, staging, default)
    - Sort rules by threshold descending for efficient first-match evaluation
    - Cache loaded rules in memory for high-performance evaluation
    - Map error occurrence counts to (priority, severity) tuples

Expected Configuration Format (config/severity_rules.yaml):
    production:
      - threshold: 50
        priority: "Highest"
        severity: "SEV1"
      - threshold: 10
        priority: "High"
        severity: "SEV2"
    staging:
      - threshold: 20
        priority: "Medium"
        severity: "SEV3"
    default:
      - threshold: 1
        priority: "Low"
        severity: "SEV4"

Rule Evaluation Strategy:
    - For a given environment and error count, rules are evaluated in descending
      threshold order (highest threshold first)
    - The first rule where (count >= threshold) is matched
    - This enables progressive severity escalation as frequency increases
    - Falls back to "default" environment rules if specific environment not found
    - Returns ("Low", "SEV4") if no rules match

Example Usage:
    >>> engine = SeverityRulesEngine()
    >>> engine.load_rules("config/severity_rules.yaml")
    >>> 
    >>> # 15 errors in production triggers High/SEV2 (>= 10 threshold)
    >>> priority, severity = engine.evaluate("production", 15)
    >>> assert priority == "High" and severity == "SEV2"
    >>> 
    >>> # 60 errors in production triggers Highest/SEV1 (>= 50 threshold)
    >>> priority, severity = engine.evaluate("production", 60)
    >>> assert priority == "Highest" and severity == "SEV1"
    >>> 
    >>> # 5 errors with no matching rule returns default
    >>> priority, severity = engine.evaluate("unknown_env", 5)
    >>> assert priority == "Low" and severity == "SEV4"

Integration Points:
    - src/models/severity_rule.py: RuleCondition dataclass with evaluate() method
    - config/severity_rules.yaml: External configuration file (YAML format)
    - src/services/frequency_tracker.py: Provides error counts for evaluation
    - src/services/jira_integration.py: Consumes (priority, severity) outputs

Performance Considerations:
    - Rules loaded once at initialization and cached in memory
    - Evaluation is O(n) where n = rules per environment (typically < 10)
    - Descending sort ensures first-match optimization for common cases
    - No external I/O during evaluation for sub-millisecond performance

Error Handling:
    - File not found: Raises FileNotFoundError with descriptive message
    - Invalid YAML: Raises yaml.YAMLError with parse details
    - Missing required fields: Raises ValueError with validation error
    - Logs warnings for unknown environments, returns default rules

Security:
    - Uses yaml.safe_load() to prevent code injection attacks
    - No arbitrary code execution from configuration files
    - Validates all numeric thresholds to prevent overflow
"""

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml

from src.models.severity_rule import RuleCondition, SeverityRule

# Initialize module logger with structured context
logger = logging.getLogger(__name__)


class SeverityRulesEngine:
    """
    Configuration-driven engine for classifying error severity based on frequency.

    Loads environment-specific threshold rules from YAML configuration and evaluates
    error occurrence counts to determine appropriate Jira priority and custom severity
    field values. Implements caching and efficient first-match evaluation for
    high-throughput error processing.

    Attributes:
        _rules_by_env: Internal cache mapping environment names to RuleCondition objects.
                      Populated by load_rules() and queried by evaluate().
                      Format: {"production": RuleCondition(...), "staging": ...}

    Thread Safety:
        This class is thread-safe for read operations (evaluate) after initialization.
        load_rules() should only be called during startup or configuration reload,
        not concurrently during active request processing.

    Example:
        >>> engine = SeverityRulesEngine()
        >>> engine.load_rules("config/severity_rules.yaml")
        >>> priority, severity = engine.evaluate("production", 25)
        >>> print(f"Priority: {priority}, Severity: {severity}")
        Priority: High, Severity: SEV2
    """

    def __init__(self, yaml_path: Optional[str] = None) -> None:
        """
        Initialize the severity rules engine with optional rule loading.

        Args:
            yaml_path: Optional path to YAML configuration file. If provided,
                      rules are loaded immediately during initialization. If None,
                      load_rules() must be called explicitly before evaluate().

        Raises:
            FileNotFoundError: If yaml_path provided but file does not exist
            yaml.YAMLError: If yaml_path provided but file contains invalid YAML
            ValueError: If yaml_path provided but configuration structure is invalid

        Example:
            >>> # Deferred loading (for dependency injection testing)
            >>> engine = SeverityRulesEngine()
            >>> engine.load_rules("config/severity_rules.yaml")
            >>>
            >>> # Immediate loading (for production use)
            >>> engine = SeverityRulesEngine(yaml_path="config/severity_rules.yaml")
        """
        # Initialize empty rules cache
        # Dict[environment_name, RuleCondition]
        self._rules_by_env: Dict[str, RuleCondition] = {}

        logger.info(
            "Initializing SeverityRulesEngine",
            extra={
                "yaml_path": yaml_path,
                "deferred_loading": yaml_path is None,
            },
        )

        # Load rules immediately if path provided
        if yaml_path is not None:
            self.load_rules(yaml_path)

    def load_rules(self, yaml_path: str) -> None:
        """
        Load and parse severity classification rules from YAML configuration file.

        Reads the specified YAML file, validates structure, creates SeverityRule and
        RuleCondition objects, sorts rules by threshold descending, and caches them
        in memory for efficient evaluation.

        Expected YAML Structure:
            {
                "environment_name": [
                    {"threshold": int, "priority": str, "severity": str},
                    ...
                ],
                ...
            }

        Example YAML:
            production:
              - threshold: 50
                priority: "Highest"
                severity: "SEV1"
              - threshold: 10
                priority: "High"
                severity: "SEV2"
            staging:
              - threshold: 20
                priority: "Medium"
                severity: "SEV3"
            default:
              - threshold: 1
                priority: "Low"
                severity: "SEV4"

        Args:
            yaml_path: Path to YAML configuration file. Can be relative to current
                      working directory or absolute. Typically "config/severity_rules.yaml".

        Raises:
            FileNotFoundError: If the specified file does not exist. Includes resolved
                              absolute path in error message for debugging.
            yaml.YAMLError: If the file contains malformed YAML syntax. Includes line
                           number and column information from YAML parser.
            ValueError: If configuration structure is invalid (missing required fields,
                       invalid data types, negative thresholds). Includes specific
                       validation error details.

        Side Effects:
            - Replaces all previously cached rules in self._rules_by_env
            - Logs INFO message on successful load with rule counts per environment
            - Logs WARNING if no rules found for any environment
            - Logs ERROR with exception details on failure

        Example:
            >>> engine = SeverityRulesEngine()
            >>> engine.load_rules("config/severity_rules.yaml")
            >>> # Rules now cached and ready for evaluate() calls
        """
        # Convert to Path object for cross-platform compatibility
        config_path = Path(yaml_path)

        logger.info(
            "Loading severity rules from configuration",
            extra={
                "yaml_path": yaml_path,
                "resolved_path": str(config_path.resolve()),
            },
        )

        # Validate file exists before attempting to read
        if not config_path.exists():
            error_msg = (
                f"Severity rules configuration file not found: {yaml_path}. "
                f"Resolved absolute path: {config_path.resolve()}. "
                f"Ensure config/severity_rules.yaml exists and is readable."
            )
            logger.error(
                "Configuration file not found",
                extra={
                    "yaml_path": yaml_path,
                    "resolved_path": str(config_path.resolve()),
                    "error": "file_not_found",
                },
            )
            raise FileNotFoundError(error_msg)

        try:
            # Read file content as text
            yaml_content = config_path.read_text(encoding="utf-8")

            # Parse YAML using safe_load to prevent code injection
            # safe_load() only constructs simple Python objects (dict, list, str, int)
            # and never arbitrary Python objects or functions
            rules_config = yaml.safe_load(yaml_content)

            # Handle empty YAML file (None) as valid empty configuration
            if rules_config is None:
                rules_config = {}

            # Validate top-level structure is a dictionary
            if not isinstance(rules_config, dict):
                raise ValueError(
                    f"Invalid YAML structure: expected dictionary at root, got {type(rules_config).__name__}. "
                    f"Configuration must be organized by environment keys (production, staging, etc.)"
                )

            # Clear existing cached rules before loading new configuration
            # This enables hot-reload functionality via SIGHUP signal handler
            self._rules_by_env.clear()

            # Parse each environment's rules
            for env_name, env_rules_raw in rules_config.items():
                # Validate environment rules is a list
                if not isinstance(env_rules_raw, list):
                    logger.warning(
                        "Skipping invalid environment configuration",
                        extra={
                            "environment": env_name,
                            "expected_type": "list",
                            "actual_type": type(env_rules_raw).__name__,
                        },
                    )
                    continue

                # Parse each rule dictionary into SeverityRule dataclass
                severity_rules = []
                for rule_idx, rule_dict in enumerate(env_rules_raw):
                    try:
                        # Validate rule is a dictionary with required keys
                        if not isinstance(rule_dict, dict):
                            raise ValueError(f"Rule must be a dictionary, got {type(rule_dict).__name__}")

                        if "threshold" not in rule_dict:
                            raise ValueError("Rule missing required field: threshold")
                        if "priority" not in rule_dict:
                            raise ValueError("Rule missing required field: priority")
                        if "severity" not in rule_dict:
                            raise ValueError("Rule missing required field: severity")

                        # Extract and validate field types
                        threshold = rule_dict["threshold"]
                        priority = rule_dict["priority"]
                        severity = rule_dict["severity"]

                        if not isinstance(threshold, int):
                            raise ValueError(f"threshold must be integer, got {type(threshold).__name__}: {threshold}")

                        if not isinstance(priority, str):
                            raise ValueError(f"priority must be string, got {type(priority).__name__}: {priority}")

                        if not isinstance(severity, str):
                            raise ValueError(f"severity must be string, got {type(severity).__name__}: {severity}")

                        # Create SeverityRule (validates threshold >= 0 in __post_init__)
                        rule = SeverityRule(
                            threshold=threshold,
                            priority=priority,
                            severity=severity,
                        )

                        severity_rules.append(rule)

                        logger.debug(
                            "Parsed severity rule",
                            extra={
                                "environment": env_name,
                                "rule_index": rule_idx,
                                "threshold": threshold,
                                "priority": priority,
                                "severity": severity,
                            },
                        )

                    except (ValueError, KeyError) as e:
                        # Log validation error but continue processing other rules
                        logger.error(
                            "Failed to parse severity rule",
                            extra={
                                "environment": env_name,
                                "rule_index": rule_idx,
                                "rule_data": rule_dict,
                                "error": str(e),
                            },
                        )
                        raise ValueError(
                            f"Invalid rule in environment '{env_name}' at index {rule_idx}: {e}. "
                            f"Rule data: {rule_dict}"
                        ) from e

                # Create RuleCondition for environment
                # RuleCondition.__post_init__ automatically sorts rules by threshold descending
                rule_condition = RuleCondition(environment=env_name, rules=severity_rules)

                # Cache in memory for fast lookup during evaluate()
                self._rules_by_env[env_name] = rule_condition

                logger.info(
                    "Loaded severity rules for environment",
                    extra={
                        "environment": env_name,
                        "rule_count": len(severity_rules),
                        "thresholds": [rule.threshold for rule in severity_rules],
                    },
                )

            # Validate at least one environment loaded
            if not self._rules_by_env:
                logger.warning(
                    "No valid severity rules loaded from configuration",
                    extra={
                        "yaml_path": yaml_path,
                        "environments_found": list(rules_config.keys()) if rules_config else [],
                    },
                )

            logger.info(
                "Successfully loaded severity rules configuration",
                extra={
                    "yaml_path": yaml_path,
                    "environments": list(self._rules_by_env.keys()),
                    "total_environments": len(self._rules_by_env),
                },
            )

        except yaml.YAMLError as e:
            # YAML parsing error (malformed syntax)
            logger.error(
                "Failed to parse YAML configuration",
                extra={
                    "yaml_path": yaml_path,
                    "error": str(e),
                    "error_type": "yaml_parse_error",
                },
            )
            raise yaml.YAMLError(f"Invalid YAML syntax in {yaml_path}: {e}") from e

        except (OSError, IOError) as e:
            # File I/O error (permissions, disk full, etc.)
            logger.error(
                "Failed to read configuration file",
                extra={
                    "yaml_path": yaml_path,
                    "error": str(e),
                    "error_type": "io_error",
                },
            )
            raise IOError(f"Could not read severity rules configuration from {yaml_path}: {e}") from e

    def evaluate(self, env: str, count: int) -> Tuple[str, str]:
        """
        Determine Jira priority and severity for an error based on frequency and environment.

        Looks up the rule set for the specified environment, evaluates rules in descending
        threshold order, and returns the first matching (priority, severity) tuple where
        count >= threshold. Falls back to "default" environment rules if specific
        environment not found. Returns ("Low", "SEV4") if no rules match.

        Rule Matching Logic:
            1. Look up environment-specific RuleCondition (e.g., "production")
            2. If not found, fall back to "default" RuleCondition
            3. Delegate to RuleCondition.evaluate(count) which:
               - Iterates rules in descending threshold order
               - Returns (priority, severity) for first rule where count >= threshold
               - Returns ("Low", "SEV4") if no rule matches

        Progressive Severity Escalation Example (production environment):
            count=5   -> ("Low", "SEV4")      # Below all thresholds, use default
            count=15  -> ("High", "SEV2")     # Meets 10 threshold
            count=60  -> ("Highest", "SEV1")  # Meets 50 threshold

        Args:
            env: Environment name (e.g., "production", "staging", "development").
                Case-sensitive. If environment not found in loaded rules, falls back
                to "default" environment rules.

            count: Number of error occurrences in the rolling time window (typically
                  5 minutes). Must be >= 0. Negative counts are treated as 0.

        Returns:
            Tuple[str, str]: (priority, severity) pair where:
                - priority: Jira priority name ("Highest", "High", "Medium", "Low")
                - severity: Custom severity field value ("SEV1", "SEV2", "SEV3", "SEV4")

        Raises:
            RuntimeError: If load_rules() has not been called (no rules cached).
                         Indicates improper initialization.

        Side Effects:
            - Logs DEBUG message with evaluation details (env, count, result)
            - Logs WARNING if environment not found, falling back to default

        Example:
            >>> engine = SeverityRulesEngine(yaml_path="config/severity_rules.yaml")
            >>> 
            >>> # Production error with moderate frequency
            >>> priority, severity = engine.evaluate("production", 15)
            >>> assert priority == "High" and severity == "SEV2"
            >>> 
            >>> # Production error with critical frequency
            >>> priority, severity = engine.evaluate("production", 75)
            >>> assert priority == "Highest" and severity == "SEV1"
            >>> 
            >>> # Unknown environment falls back to default
            >>> priority, severity = engine.evaluate("unknown", 5)
            >>> assert priority == "Low" and severity == "SEV4"
        """
        # Validate rules have been loaded
        if not self._rules_by_env:
            error_msg = (
                "Cannot evaluate severity: no rules loaded. "
                "Call load_rules() with a valid configuration file path before evaluate()."
            )
            logger.error(
                "Attempted to evaluate without loaded rules",
                extra={
                    "environment": env,
                    "count": count,
                    "error": "no_rules_loaded",
                },
            )
            raise RuntimeError(error_msg)

        # Normalize count to non-negative (defensive programming)
        if count < 0:
            logger.warning(
                "Negative error count provided, normalizing to 0",
                extra={
                    "environment": env,
                    "original_count": count,
                    "normalized_count": 0,
                },
            )
            count = 0

        # Look up environment-specific rule condition
        rule_condition = self._rules_by_env.get(env)

        # Fall back to "default" environment if specific environment not found
        if rule_condition is None:
            logger.debug(
                "Environment not found in loaded rules, falling back to default",
                extra={
                    "requested_environment": env,
                    "fallback_environment": "default",
                    "available_environments": list(self._rules_by_env.keys()),
                },
            )

            rule_condition = self._rules_by_env.get("default")

            # If no default rules either, return hardcoded fallback
            if rule_condition is None:
                logger.warning(
                    "No default rules found, returning hardcoded fallback",
                    extra={
                        "environment": env,
                        "count": count,
                        "fallback_priority": "Low",
                        "fallback_severity": "SEV4",
                    },
                )
                return ("Low", "SEV4")

        # Delegate to RuleCondition.evaluate() for rule matching
        # RuleCondition handles descending threshold iteration and first-match logic
        priority, severity = rule_condition.evaluate(count)

        logger.debug(
            "Evaluated severity rule",
            extra={
                "environment": env,
                "error_count": count,
                "priority": priority,
                "severity": severity,
                "matched_environment": rule_condition.environment,
            },
        )

        return (priority, severity)
