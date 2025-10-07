"""
Unit Tests for SeverityRulesEngine

Comprehensive test suite for the SeverityRulesEngine class that validates frequency-based
severity classification logic. Tests cover threshold matching, environment-specific rules,
YAML configuration loading, rule precedence, and edge case handling.

Test Coverage:
    - SeverityRulesEngine.__init__() with immediate and deferred loading
    - SeverityRulesEngine.load_rules() with valid/invalid YAML configurations
    - SeverityRulesEngine.evaluate() with various count/environment combinations
    - RuleCondition.evaluate() threshold matching and default fallback
    - Configuration validation and error handling
    - Rule caching and sorting behavior

Expected Behavior per Agent Action Plan Section 0.5.1 Group 4:
    - Production: 50+ → (Highest, SEV1), 10+ → (High, SEV2)
    - Staging: 20+ → (Medium, SEV3)
    - Default: 1+ → (Low, SEV4)
    - Rules sorted by threshold descending for first-match evaluation
    - Fallback to default environment when specific environment not found
    - Hardcoded fallback ("Low", "SEV4") when no rules loaded

Test Execution:
    pytest tests/unit/test_severity_engine.py -v --cov=src/services/severity_engine
"""

from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml

from src.models.severity_rule import RuleCondition, SeverityRule
from src.services.severity_engine import SeverityRulesEngine


# =============================================================================
# Test Fixtures and Helper Data
# =============================================================================


@pytest.fixture
def sample_rules_config() -> Dict[str, List[Dict[str, Any]]]:
    """
    Sample severity rules configuration for testing.

    Provides a complete multi-environment configuration matching the format
    expected by config/severity_rules.yaml per Agent Action Plan Section 0.5.1.

    Returns:
        Dictionary with environment keys mapping to lists of rule dictionaries.
        Each rule contains threshold (int), priority (str), and severity (str).

    Structure:
        production:
          - threshold: 50, priority: "Highest", severity: "SEV1"
          - threshold: 10, priority: "High", severity: "SEV2"
        staging:
          - threshold: 20, priority: "Medium", severity: "SEV3"
        default:
          - threshold: 1, priority: "Low", severity: "SEV4"
    """
    return {
        "production": [
            {"threshold": 50, "priority": "Highest", "severity": "SEV1"},
            {"threshold": 10, "priority": "High", "severity": "SEV2"},
        ],
        "staging": [
            {"threshold": 20, "priority": "Medium", "severity": "SEV3"},
        ],
        "default": [
            {"threshold": 1, "priority": "Low", "severity": "SEV4"},
        ],
    }


@pytest.fixture
def valid_yaml_file(tmp_path: Path, sample_rules_config: Dict[str, List[Dict[str, Any]]]) -> Path:
    """
    Create a temporary YAML file with valid severity rules configuration.

    Uses pytest's tmp_path fixture to create a temporary file that is automatically
    cleaned up after the test completes. The file contains the sample_rules_config
    serialized as YAML.

    Args:
        tmp_path: Pytest-provided temporary directory path (unique per test)
        sample_rules_config: Fixture providing sample configuration dictionary

    Returns:
        Path object pointing to the created temporary YAML file

    Example Content:
        production:
          - threshold: 50
            priority: Highest
            severity: SEV1
    """
    yaml_file = tmp_path / "severity_rules.yaml"
    yaml_file.write_text(yaml.dump(sample_rules_config), encoding="utf-8")
    return yaml_file


@pytest.fixture
def empty_yaml_file(tmp_path: Path) -> Path:
    """
    Create a temporary YAML file with empty/null content.

    Tests configuration loading behavior when YAML file exists but contains no
    valid environment configurations.

    Args:
        tmp_path: Pytest-provided temporary directory path

    Returns:
        Path to YAML file containing null/empty content
    """
    yaml_file = tmp_path / "empty_rules.yaml"
    yaml_file.write_text("", encoding="utf-8")
    return yaml_file


@pytest.fixture
def malformed_yaml_file(tmp_path: Path) -> Path:
    """
    Create a temporary YAML file with invalid syntax.

    Used to test yaml.YAMLError handling when configuration file contains
    malformed YAML syntax (unclosed brackets, invalid indentation, etc.).

    Args:
        tmp_path: Pytest-provided temporary directory path

    Returns:
        Path to YAML file with syntax errors
    """
    yaml_file = tmp_path / "malformed_rules.yaml"
    # Invalid YAML: unclosed bracket
    yaml_file.write_text("production:\n  - threshold: [invalid syntax", encoding="utf-8")
    return yaml_file


# =============================================================================
# SeverityRulesEngine Initialization Tests
# =============================================================================


def test_init_deferred_loading() -> None:
    """
    Test SeverityRulesEngine initialization without immediate rule loading.

    Validates that engine can be instantiated without providing a yaml_path,
    enabling deferred loading for dependency injection and testing scenarios.

    Expected Behavior:
        - Engine initializes successfully
        - Internal _rules_by_env cache is empty
        - No FileNotFoundError or other exceptions raised
        - Subsequent load_rules() call required before evaluate()
    """
    engine = SeverityRulesEngine()

    assert engine is not None
    assert engine._rules_by_env == {}
    assert isinstance(engine._rules_by_env, dict)


def test_init_immediate_loading(valid_yaml_file: Path) -> None:
    """
    Test SeverityRulesEngine initialization with immediate rule loading.

    Validates that providing yaml_path during initialization automatically
    loads and caches rules, making engine ready for evaluate() calls.

    Args:
        valid_yaml_file: Fixture providing temporary YAML configuration file

    Expected Behavior:
        - Engine initializes successfully
        - Rules loaded and cached in _rules_by_env
        - Environments present: production, staging, default
        - No exceptions raised
    """
    engine = SeverityRulesEngine(yaml_path=str(valid_yaml_file))

    assert engine is not None
    assert len(engine._rules_by_env) > 0
    assert "production" in engine._rules_by_env
    assert "staging" in engine._rules_by_env
    assert "default" in engine._rules_by_env


def test_init_with_invalid_path() -> None:
    """
    Test SeverityRulesEngine initialization with non-existent file path.

    Validates that FileNotFoundError is raised during initialization when
    yaml_path points to a file that does not exist.

    Expected Behavior:
        - FileNotFoundError raised with descriptive message
        - Error message includes provided path and resolved absolute path
        - Engine initialization fails (does not return)
    """
    with pytest.raises(FileNotFoundError) as exc_info:
        SeverityRulesEngine(yaml_path="/nonexistent/path/rules.yaml")

    assert "not found" in str(exc_info.value).lower()
    assert "/nonexistent/path/rules.yaml" in str(exc_info.value)


# =============================================================================
# load_rules() Configuration Loading Tests
# =============================================================================


def test_load_rules_valid_configuration(valid_yaml_file: Path) -> None:
    """
    Test load_rules() with valid multi-environment YAML configuration.

    Validates successful parsing of properly formatted YAML with multiple
    environments, each containing properly structured rules with required fields.

    Args:
        valid_yaml_file: Fixture providing valid temporary YAML file

    Expected Behavior:
        - All environments loaded: production, staging, default
        - Each environment has RuleCondition with sorted rules
        - Production has 2 rules, staging has 1 rule, default has 1 rule
        - Rules sorted by threshold descending
        - No exceptions raised
    """
    engine = SeverityRulesEngine()
    engine.load_rules(str(valid_yaml_file))

    # Validate all environments loaded
    assert "production" in engine._rules_by_env
    assert "staging" in engine._rules_by_env
    assert "default" in engine._rules_by_env

    # Validate production rules
    prod_condition = engine._rules_by_env["production"]
    assert isinstance(prod_condition, RuleCondition)
    assert prod_condition.environment == "production"
    assert len(prod_condition.rules) == 2

    # Validate rules are sorted by threshold descending
    assert prod_condition.rules[0].threshold == 50  # Highest threshold first
    assert prod_condition.rules[0].priority == "Highest"
    assert prod_condition.rules[0].severity == "SEV1"
    assert prod_condition.rules[1].threshold == 10
    assert prod_condition.rules[1].priority == "High"
    assert prod_condition.rules[1].severity == "SEV2"

    # Validate staging rules
    staging_condition = engine._rules_by_env["staging"]
    assert len(staging_condition.rules) == 1
    assert staging_condition.rules[0].threshold == 20
    assert staging_condition.rules[0].priority == "Medium"
    assert staging_condition.rules[0].severity == "SEV3"

    # Validate default rules
    default_condition = engine._rules_by_env["default"]
    assert len(default_condition.rules) == 1
    assert default_condition.rules[0].threshold == 1
    assert default_condition.rules[0].priority == "Low"
    assert default_condition.rules[0].severity == "SEV4"


def test_load_rules_file_not_found() -> None:
    """
    Test load_rules() behavior when configuration file does not exist.

    Validates that FileNotFoundError is raised with descriptive message
    including both provided path and resolved absolute path.

    Expected Behavior:
        - FileNotFoundError raised
        - Error message contains file path
        - Error message mentions resolved absolute path
        - Engine remains in uninitialized state (no rules loaded)
    """
    engine = SeverityRulesEngine()

    with pytest.raises(FileNotFoundError) as exc_info:
        engine.load_rules("/nonexistent/directory/rules.yaml")

    error_message = str(exc_info.value)
    assert "not found" in error_message.lower()
    assert "/nonexistent/directory/rules.yaml" in error_message
    assert "resolved absolute path" in error_message.lower()

    # Verify engine has no rules loaded
    assert len(engine._rules_by_env) == 0


def test_load_rules_malformed_yaml(malformed_yaml_file: Path) -> None:
    """
    Test load_rules() with syntactically invalid YAML file.

    Validates that yaml.YAMLError is raised when configuration file contains
    malformed YAML syntax (unclosed brackets, invalid indentation, etc.).

    Args:
        malformed_yaml_file: Fixture providing YAML file with syntax errors

    Expected Behavior:
        - yaml.YAMLError raised
        - Error message references invalid YAML syntax
        - Error message includes file path
        - Engine remains in uninitialized state
    """
    engine = SeverityRulesEngine()

    with pytest.raises(yaml.YAMLError) as exc_info:
        engine.load_rules(str(malformed_yaml_file))

    error_message = str(exc_info.value)
    assert "invalid yaml syntax" in error_message.lower() or "yaml" in error_message.lower()
    assert str(malformed_yaml_file) in error_message


def test_load_rules_missing_required_field_threshold(tmp_path: Path) -> None:
    """
    Test load_rules() with configuration missing required 'threshold' field.

    Validates that ValueError is raised when a rule dictionary lacks the
    required 'threshold' field.

    Args:
        tmp_path: Pytest-provided temporary directory

    Expected Behavior:
        - ValueError raised with descriptive message
        - Error message mentions missing field: threshold
        - Error message includes environment and rule index
        - No rules loaded
    """
    # Create YAML with missing threshold field
    invalid_config = {
        "production": [
            {"priority": "High", "severity": "SEV2"},  # Missing threshold
        ]
    }
    yaml_file = tmp_path / "missing_threshold.yaml"
    yaml_file.write_text(yaml.dump(invalid_config), encoding="utf-8")

    engine = SeverityRulesEngine()

    with pytest.raises(ValueError) as exc_info:
        engine.load_rules(str(yaml_file))

    error_message = str(exc_info.value)
    assert "threshold" in error_message.lower()
    assert "missing" in error_message.lower() or "required" in error_message.lower()


def test_load_rules_missing_required_field_priority(tmp_path: Path) -> None:
    """
    Test load_rules() with configuration missing required 'priority' field.

    Args:
        tmp_path: Pytest-provided temporary directory

    Expected Behavior:
        - ValueError raised with descriptive message
        - Error message mentions missing field: priority
    """
    invalid_config = {
        "production": [
            {"threshold": 10, "severity": "SEV2"},  # Missing priority
        ]
    }
    yaml_file = tmp_path / "missing_priority.yaml"
    yaml_file.write_text(yaml.dump(invalid_config), encoding="utf-8")

    engine = SeverityRulesEngine()

    with pytest.raises(ValueError) as exc_info:
        engine.load_rules(str(yaml_file))

    error_message = str(exc_info.value)
    assert "priority" in error_message.lower()
    assert "missing" in error_message.lower() or "required" in error_message.lower()


def test_load_rules_missing_required_field_severity(tmp_path: Path) -> None:
    """
    Test load_rules() with configuration missing required 'severity' field.

    Args:
        tmp_path: Pytest-provided temporary directory

    Expected Behavior:
        - ValueError raised with descriptive message
        - Error message mentions missing field: severity
    """
    invalid_config = {
        "production": [
            {"threshold": 10, "priority": "High"},  # Missing severity
        ]
    }
    yaml_file = tmp_path / "missing_severity.yaml"
    yaml_file.write_text(yaml.dump(invalid_config), encoding="utf-8")

    engine = SeverityRulesEngine()

    with pytest.raises(ValueError) as exc_info:
        engine.load_rules(str(yaml_file))

    error_message = str(exc_info.value)
    assert "severity" in error_message.lower()
    assert "missing" in error_message.lower() or "required" in error_message.lower()


def test_load_rules_invalid_threshold_type(tmp_path: Path) -> None:
    """
    Test load_rules() with non-integer threshold value.

    Validates that ValueError is raised when threshold field contains
    non-integer value (string, float, etc.).

    Args:
        tmp_path: Pytest-provided temporary directory

    Expected Behavior:
        - ValueError raised
        - Error message mentions invalid threshold type
        - No rules loaded
    """
    invalid_config = {
        "production": [
            {"threshold": "not_an_integer", "priority": "High", "severity": "SEV2"},
        ]
    }
    yaml_file = tmp_path / "invalid_threshold_type.yaml"
    yaml_file.write_text(yaml.dump(invalid_config), encoding="utf-8")

    engine = SeverityRulesEngine()

    with pytest.raises(ValueError) as exc_info:
        engine.load_rules(str(yaml_file))

    error_message = str(exc_info.value)
    assert "threshold" in error_message.lower()
    assert "integer" in error_message.lower() or "int" in error_message.lower()


def test_load_rules_negative_threshold(tmp_path: Path) -> None:
    """
    Test load_rules() with negative threshold value.

    Validates that ValueError is raised when threshold is negative,
    as negative error counts are nonsensical.

    Args:
        tmp_path: Pytest-provided temporary directory

    Expected Behavior:
        - ValueError raised from SeverityRule.__post_init__
        - Error message mentions negative threshold not allowed
    """
    invalid_config = {
        "production": [
            {"threshold": -10, "priority": "High", "severity": "SEV2"},
        ]
    }
    yaml_file = tmp_path / "negative_threshold.yaml"
    yaml_file.write_text(yaml.dump(invalid_config), encoding="utf-8")

    engine = SeverityRulesEngine()

    with pytest.raises(ValueError) as exc_info:
        engine.load_rules(str(yaml_file))

    error_message = str(exc_info.value)
    assert "negative" in error_message.lower() or "-10" in error_message


def test_load_rules_empty_configuration(empty_yaml_file: Path) -> None:
    """
    Test load_rules() with empty YAML file (no environments defined).

    Validates behavior when YAML file exists but contains no environment
    configurations. Engine should load successfully but have empty rules cache.

    Args:
        empty_yaml_file: Fixture providing empty YAML file

    Expected Behavior:
        - No exception raised (empty config is technically valid)
        - _rules_by_env is empty dict
        - Subsequent evaluate() calls will use hardcoded fallback
    """
    engine = SeverityRulesEngine()
    engine.load_rules(str(empty_yaml_file))

    # Empty configuration is valid, just results in no rules loaded
    assert len(engine._rules_by_env) == 0
    assert isinstance(engine._rules_by_env, dict)


def test_load_rules_caching_behavior(tmp_path: Path, sample_rules_config: Dict[str, List[Dict[str, Any]]]) -> None:
    """
    Test that load_rules() replaces previously cached rules.

    Validates rule caching and hot-reload functionality: calling load_rules()
    multiple times should replace existing cached rules with new configuration.

    Args:
        tmp_path: Pytest-provided temporary directory
        sample_rules_config: Fixture providing sample configuration

    Expected Behavior:
        - First load populates cache
        - Second load with different config replaces cache entirely
        - Only rules from second configuration present after reload
    """
    engine = SeverityRulesEngine()

    # First configuration: production and staging
    first_config = {
        "production": [
            {"threshold": 50, "priority": "Highest", "severity": "SEV1"},
        ]
    }
    first_yaml = tmp_path / "first_rules.yaml"
    first_yaml.write_text(yaml.dump(first_config), encoding="utf-8")
    engine.load_rules(str(first_yaml))

    assert "production" in engine._rules_by_env
    assert "staging" not in engine._rules_by_env
    assert len(engine._rules_by_env["production"].rules) == 1

    # Second configuration: staging only (production should be removed)
    second_config = {
        "staging": [
            {"threshold": 20, "priority": "Medium", "severity": "SEV3"},
        ]
    }
    second_yaml = tmp_path / "second_rules.yaml"
    second_yaml.write_text(yaml.dump(second_config), encoding="utf-8")
    engine.load_rules(str(second_yaml))

    # Verify cache replaced (not merged)
    assert "production" not in engine._rules_by_env  # Removed from first config
    assert "staging" in engine._rules_by_env  # Added from second config
    assert len(engine._rules_by_env["staging"].rules) == 1


def test_load_rules_rule_sorting(tmp_path: Path) -> None:
    """
    Test that rules are sorted by threshold descending after loading.

    Validates that regardless of definition order in YAML, rules are sorted
    with highest threshold first for efficient first-match evaluation.

    Args:
        tmp_path: Pytest-provided temporary directory

    Expected Behavior:
        - Rules defined in ascending order are reordered to descending
        - After loading: rules[0].threshold > rules[1].threshold > rules[2].threshold
    """
    # Define rules in ascending order (opposite of desired evaluation order)
    unsorted_config = {
        "production": [
            {"threshold": 1, "priority": "Low", "severity": "SEV4"},
            {"threshold": 10, "priority": "High", "severity": "SEV2"},
            {"threshold": 50, "priority": "Highest", "severity": "SEV1"},
        ]
    }
    yaml_file = tmp_path / "unsorted_rules.yaml"
    yaml_file.write_text(yaml.dump(unsorted_config), encoding="utf-8")

    engine = SeverityRulesEngine()
    engine.load_rules(str(yaml_file))

    prod_rules = engine._rules_by_env["production"].rules

    # Verify sorted by threshold descending
    assert len(prod_rules) == 3
    assert prod_rules[0].threshold == 50  # Highest first
    assert prod_rules[1].threshold == 10
    assert prod_rules[2].threshold == 1  # Lowest last


# =============================================================================
# evaluate() Threshold Matching Tests
# =============================================================================


@pytest.mark.parametrize(
    "environment,count,expected_priority,expected_severity",
    [
        # Production environment threshold tests
        ("production", 0, "Low", "SEV4"),  # Below all thresholds, use default
        ("production", 5, "Low", "SEV4"),  # Below 10 threshold
        ("production", 10, "High", "SEV2"),  # Exact 10 threshold match
        ("production", 15, "High", "SEV2"),  # Between 10 and 50
        ("production", 49, "High", "SEV2"),  # Just below 50 threshold
        ("production", 50, "Highest", "SEV1"),  # Exact 50 threshold match
        ("production", 75, "Highest", "SEV1"),  # Above 50 threshold
        ("production", 100, "Highest", "SEV1"),  # Well above highest threshold
        # Staging environment threshold tests
        ("staging", 0, "Low", "SEV4"),  # Below threshold
        ("staging", 19, "Low", "SEV4"),  # Just below 20 threshold
        ("staging", 20, "Medium", "SEV3"),  # Exact 20 threshold match
        ("staging", 25, "Medium", "SEV3"),  # Above 20 threshold
        ("staging", 50, "Medium", "SEV3"),  # Well above threshold
        # Default environment tests
        ("default", 0, "Low", "SEV4"),  # Below threshold
        ("default", 1, "Low", "SEV4"),  # Exact threshold match
        ("default", 5, "Low", "SEV4"),  # Above threshold
    ],
)
def test_evaluate_threshold_matching(
    valid_yaml_file: Path,
    environment: str,
    count: int,
    expected_priority: str,
    expected_severity: str,
) -> None:
    """
    Test evaluate() with various error counts and environments.

    Parametrized test covering comprehensive threshold matching scenarios:
    - Exact threshold matches
    - Counts above and below thresholds
    - Counts between multiple thresholds
    - Edge cases (0, exact match, just below/above)

    Args:
        valid_yaml_file: Fixture providing valid YAML configuration
        environment: Target environment name (production, staging, default)
        count: Error occurrence count in rolling window
        expected_priority: Expected Jira priority result
        expected_severity: Expected custom severity field result

    Expected Behavior:
        - Returns correct (priority, severity) tuple for each combination
        - First-match logic: highest applicable severity returned
        - Progressive escalation as count increases
    """
    engine = SeverityRulesEngine(yaml_path=str(valid_yaml_file))

    priority, severity = engine.evaluate(environment, count)

    assert priority == expected_priority, f"Expected priority {expected_priority} for {environment} with count {count}, got {priority}"
    assert severity == expected_severity, f"Expected severity {expected_severity} for {environment} with count {count}, got {severity}"


def test_evaluate_unknown_environment_fallback(valid_yaml_file: Path) -> None:
    """
    Test evaluate() falls back to default environment for unknown environments.

    Validates that when an environment not defined in configuration is requested,
    the engine falls back to "default" environment rules if available.

    Args:
        valid_yaml_file: Fixture providing valid YAML with default environment

    Expected Behavior:
        - Unknown environment triggers fallback to "default"
        - Default rules applied (threshold=1 → Low/SEV4)
        - No exception raised
    """
    engine = SeverityRulesEngine(yaml_path=str(valid_yaml_file))

    # Request unknown environment with count that matches default threshold
    priority, severity = engine.evaluate("unknown_environment", 5)

    # Should use default environment rules (threshold=1 → Low/SEV4)
    assert priority == "Low"
    assert severity == "SEV4"


def test_evaluate_no_default_environment_hardcoded_fallback(tmp_path: Path) -> None:
    """
    Test evaluate() returns hardcoded fallback when no default environment exists.

    Validates behavior when requested environment not found AND no "default"
    environment defined in configuration. Engine should return hardcoded
    ("Low", "SEV4") fallback without raising exception.

    Args:
        tmp_path: Pytest-provided temporary directory

    Expected Behavior:
        - Unknown environment triggers fallback to "default"
        - No "default" environment in config triggers hardcoded fallback
        - Returns ("Low", "SEV4")
        - No exception raised
    """
    # Configuration without default environment
    config_without_default = {
        "production": [
            {"threshold": 50, "priority": "Highest", "severity": "SEV1"},
        ]
    }
    yaml_file = tmp_path / "no_default.yaml"
    yaml_file.write_text(yaml.dump(config_without_default), encoding="utf-8")

    engine = SeverityRulesEngine(yaml_path=str(yaml_file))

    # Request unknown environment with no default fallback available
    priority, severity = engine.evaluate("unknown_environment", 10)

    # Should return hardcoded fallback
    assert priority == "Low"
    assert severity == "SEV4"


def test_evaluate_count_zero(valid_yaml_file: Path) -> None:
    """
    Test evaluate() with error count of zero.

    Validates edge case handling when count=0 (no errors occurred). Should
    return lowest severity since no thresholds are met.

    Args:
        valid_yaml_file: Fixture providing valid YAML configuration

    Expected Behavior:
        - count=0 does not meet any positive thresholds
        - Returns default fallback ("Low", "SEV4")
        - No exception raised
    """
    engine = SeverityRulesEngine(yaml_path=str(valid_yaml_file))

    priority, severity = engine.evaluate("production", 0)

    # Zero errors should not trigger any severity escalation
    assert priority == "Low"
    assert severity == "SEV4"


def test_evaluate_negative_count_normalization(valid_yaml_file: Path) -> None:
    """
    Test evaluate() normalizes negative error counts to zero.

    Validates defensive programming: negative counts are nonsensical but
    should be handled gracefully by normalizing to 0 rather than raising
    exception.

    Args:
        valid_yaml_file: Fixture providing valid YAML configuration

    Expected Behavior:
        - Negative count normalized to 0
        - Evaluation proceeds with count=0
        - Returns default fallback ("Low", "SEV4")
        - Warning logged but no exception raised
    """
    engine = SeverityRulesEngine(yaml_path=str(valid_yaml_file))

    priority, severity = engine.evaluate("production", -5)

    # Negative count should be normalized to 0, returning default
    assert priority == "Low"
    assert severity == "SEV4"


def test_evaluate_without_loaded_rules() -> None:
    """
    Test evaluate() raises RuntimeError when no rules loaded.

    Validates that attempting to evaluate without calling load_rules() first
    raises descriptive RuntimeError indicating improper initialization.

    Expected Behavior:
        - RuntimeError raised
        - Error message indicates no rules loaded
        - Error message suggests calling load_rules() first
    """
    engine = SeverityRulesEngine()

    # Attempt to evaluate without loading rules
    with pytest.raises(RuntimeError) as exc_info:
        engine.evaluate("production", 10)

    error_message = str(exc_info.value)
    assert "no rules loaded" in error_message.lower()
    assert "load_rules" in error_message.lower()


def test_evaluate_rule_precedence(valid_yaml_file: Path) -> None:
    """
    Test evaluate() applies highest matching rule (first-match with descending sort).

    Validates that when multiple rules match (count >= threshold for multiple rules),
    the highest severity rule is returned due to descending threshold sort.

    Args:
        valid_yaml_file: Fixture providing valid YAML with multiple thresholds

    Expected Behavior:
        - count=60 matches both threshold=50 and threshold=10
        - Returns result from threshold=50 rule (higher severity)
        - Demonstrates first-match with descending sort strategy
    """
    engine = SeverityRulesEngine(yaml_path=str(valid_yaml_file))

    # Count of 60 matches both threshold 50 (SEV1) and threshold 10 (SEV2)
    priority, severity = engine.evaluate("production", 60)

    # Should return highest severity match (threshold 50)
    assert priority == "Highest"
    assert severity == "SEV1"


# =============================================================================
# RuleCondition.evaluate() Tests
# =============================================================================


def test_rule_condition_evaluate_threshold_match() -> None:
    """
    Test RuleCondition.evaluate() returns correct rule when threshold met.

    Validates direct RuleCondition evaluation logic with a simple rule set.
    Tests that count >= threshold triggers rule match.

    Expected Behavior:
        - count >= threshold returns rule's (priority, severity)
        - count < threshold continues to next rule
    """
    rules = [
        SeverityRule(threshold=50, priority="Highest", severity="SEV1"),
        SeverityRule(threshold=10, priority="High", severity="SEV2"),
    ]
    condition = RuleCondition(environment="production", rules=rules)

    # Count meets 10 threshold but not 50
    priority, severity = condition.evaluate(15)

    assert priority == "High"
    assert severity == "SEV2"


def test_rule_condition_evaluate_no_match_fallback() -> None:
    """
    Test RuleCondition.evaluate() returns default when no rules match.

    Validates fallback behavior when error count is below all defined
    thresholds. Should return hardcoded ("Low", "SEV4") default.

    Expected Behavior:
        - count < all thresholds returns ("Low", "SEV4")
        - No exception raised
    """
    rules = [
        SeverityRule(threshold=50, priority="Highest", severity="SEV1"),
        SeverityRule(threshold=10, priority="High", severity="SEV2"),
    ]
    condition = RuleCondition(environment="production", rules=rules)

    # Count below all thresholds
    priority, severity = condition.evaluate(5)

    assert priority == "Low"
    assert severity == "SEV4"


def test_rule_condition_evaluate_exact_threshold() -> None:
    """
    Test RuleCondition.evaluate() matches when count exactly equals threshold.

    Validates that threshold matching uses >= comparison, so exact matches
    trigger rule application.

    Expected Behavior:
        - count == threshold returns rule's (priority, severity)
        - Exact matches are considered valid triggers
    """
    rules = [
        SeverityRule(threshold=10, priority="High", severity="SEV2"),
    ]
    condition = RuleCondition(environment="production", rules=rules)

    # Exact threshold match
    priority, severity = condition.evaluate(10)

    assert priority == "High"
    assert severity == "SEV2"


def test_rule_condition_empty_rules_list() -> None:
    """
    Test RuleCondition.evaluate() with empty rules list.

    Validates behavior when RuleCondition has no rules defined. Should
    return default fallback without raising exception.

    Expected Behavior:
        - Empty rules list always returns ("Low", "SEV4")
        - No exception raised for any count value
    """
    condition = RuleCondition(environment="production", rules=[])

    # Any count should return default since no rules to match
    priority, severity = condition.evaluate(100)

    assert priority == "Low"
    assert severity == "SEV4"


def test_rule_condition_post_init_sorting() -> None:
    """
    Test RuleCondition.__post_init__() sorts rules by threshold descending.

    Validates that rules are automatically sorted during initialization
    regardless of definition order, ensuring efficient first-match evaluation.

    Expected Behavior:
        - Rules sorted by threshold descending after __post_init__
        - Highest threshold first, lowest threshold last
        - Sorting happens automatically without explicit call
    """
    # Define rules in ascending order
    rules = [
        SeverityRule(threshold=1, priority="Low", severity="SEV4"),
        SeverityRule(threshold=50, priority="Highest", severity="SEV1"),
        SeverityRule(threshold=10, priority="High", severity="SEV2"),
    ]

    condition = RuleCondition(environment="production", rules=rules)

    # Verify sorted by threshold descending
    assert condition.rules[0].threshold == 50
    assert condition.rules[1].threshold == 10
    assert condition.rules[2].threshold == 1


# =============================================================================
# Integration and Edge Case Tests
# =============================================================================


def test_end_to_end_production_severity_escalation(valid_yaml_file: Path) -> None:
    """
    Test complete production severity escalation flow.

    Simulates progressive error frequency increase in production environment,
    validating that severity escalates through SEV4 → SEV2 → SEV1 as count
    crosses thresholds per Agent Action Plan Section 0.5.1.

    Args:
        valid_yaml_file: Fixture providing valid YAML configuration

    Expected Behavior:
        - 5 errors: Low/SEV4 (below 10 threshold)
        - 15 errors: High/SEV2 (meets 10 threshold)
        - 60 errors: Highest/SEV1 (meets 50 threshold)
        - Demonstrates progressive escalation strategy
    """
    engine = SeverityRulesEngine(yaml_path=str(valid_yaml_file))

    # Initial low frequency: no escalation
    priority, severity = engine.evaluate("production", 5)
    assert priority == "Low" and severity == "SEV4"

    # Moderate frequency: escalate to SEV2
    priority, severity = engine.evaluate("production", 15)
    assert priority == "High" and severity == "SEV2"

    # Critical frequency: escalate to SEV1
    priority, severity = engine.evaluate("production", 60)
    assert priority == "Highest" and severity == "SEV1"


def test_end_to_end_staging_severity_classification(valid_yaml_file: Path) -> None:
    """
    Test staging environment severity classification.

    Validates that staging environment has different thresholds than production,
    with single threshold at 20 errors per Agent Action Plan Section 0.5.1.

    Args:
        valid_yaml_file: Fixture providing valid YAML configuration

    Expected Behavior:
        - Below 20: Low/SEV4
        - 20+: Medium/SEV3
        - No SEV1 or SEV2 thresholds in staging
    """
    engine = SeverityRulesEngine(yaml_path=str(valid_yaml_file))

    # Below staging threshold
    priority, severity = engine.evaluate("staging", 15)
    assert priority == "Low" and severity == "SEV4"

    # Meets staging threshold
    priority, severity = engine.evaluate("staging", 25)
    assert priority == "Medium" and severity == "SEV3"


def test_multiple_evaluations_with_same_engine(valid_yaml_file: Path) -> None:
    """
    Test that single engine instance can handle multiple evaluate() calls.

    Validates thread-safe read operations: engine can be reused for multiple
    evaluations without state corruption or performance degradation.

    Args:
        valid_yaml_file: Fixture providing valid YAML configuration

    Expected Behavior:
        - Multiple evaluate() calls return consistent results
        - No state mutation between calls
        - Rules cache remains stable
    """
    engine = SeverityRulesEngine(yaml_path=str(valid_yaml_file))

    # Perform multiple evaluations
    result1 = engine.evaluate("production", 15)
    result2 = engine.evaluate("staging", 25)
    result3 = engine.evaluate("production", 60)
    result4 = engine.evaluate("production", 15)  # Repeat first evaluation

    # Verify consistency
    assert result1 == ("High", "SEV2")
    assert result2 == ("Medium", "SEV3")
    assert result3 == ("Highest", "SEV1")
    assert result4 == ("High", "SEV2")  # Same as result1
    assert result1 == result4  # Consistency check


def test_configuration_reload(tmp_path: Path) -> None:
    """
    Test that load_rules() can be called multiple times to update configuration.

    Validates hot-reload functionality: engine can reload rules from a different
    configuration file without reinitialization, enabling runtime configuration
    updates via SIGHUP signal handler.

    Args:
        tmp_path: Pytest-provided temporary directory

    Expected Behavior:
        - First configuration loaded successfully
        - Second load_rules() replaces first configuration
        - Evaluate() uses rules from most recent load_rules() call
        - No residual state from first configuration
    """
    engine = SeverityRulesEngine()

    # Load first configuration
    first_config = {
        "production": [
            {"threshold": 100, "priority": "Critical", "severity": "SEV0"},
        ]
    }
    first_yaml = tmp_path / "first.yaml"
    first_yaml.write_text(yaml.dump(first_config), encoding="utf-8")
    engine.load_rules(str(first_yaml))

    priority1, severity1 = engine.evaluate("production", 100)
    assert priority1 == "Critical"
    assert severity1 == "SEV0"

    # Reload with second configuration
    second_config = {
        "production": [
            {"threshold": 50, "priority": "Highest", "severity": "SEV1"},
        ]
    }
    second_yaml = tmp_path / "second.yaml"
    second_yaml.write_text(yaml.dump(second_config), encoding="utf-8")
    engine.load_rules(str(second_yaml))

    # Verify new configuration active
    priority2, severity2 = engine.evaluate("production", 100)
    assert priority2 == "Highest"
    assert severity2 == "SEV1"

    # Old threshold (100 → Critical/SEV0) should no longer apply
    # New threshold (50 → Highest/SEV1) should be active
