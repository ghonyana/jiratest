"""
Severity Rule Data Models

This module defines dataclass structures for severity classification rules that map
error frequency thresholds to Jira priority levels and custom severity fields.

Rules are loaded from config/severity_rules.yaml and evaluated by the SeverityRulesEngine
to determine appropriate priority and severity based on error occurrence counts within
a rolling 5-minute window.

Expected YAML Structure:
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

Rule Evaluation Logic:
    Rules within each environment are evaluated in descending threshold order.
    The first rule where (error_count >= threshold) is matched and its priority/severity
    are returned. This enables progressive severity escalation as error frequency increases.

Example Usage:
    >>> rule = SeverityRule(threshold=10, priority="High", severity="SEV2")
    >>> rule.to_dict()
    {'threshold': 10, 'priority': 'High', 'severity': 'SEV2'}

    >>> condition = RuleCondition(
    ...     environment="production",
    ...     rules=[
    ...         SeverityRule(50, "Highest", "SEV1"),
    ...         SeverityRule(10, "High", "SEV2")
    ...     ]
    ... )
    >>> condition.evaluate(15)  # 15 errors in 5 minutes
    ('High', 'SEV2')
    >>> condition.evaluate(75)  # 75 errors in 5 minutes
    ('Highest', 'SEV1')
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


@dataclass(frozen=True)
class SeverityRule:
    """
    Represents a single severity classification rule mapping error frequency to priority.

    A rule defines the minimum error count threshold that must be met or exceeded for
    the associated priority and severity levels to be applied to a Jira issue.

    Attributes:
        threshold: Minimum error count within the rolling window (must be >= 0)
        priority: Jira priority level (e.g., "Highest", "High", "Medium", "Low")
        severity: Custom severity field value (e.g., "SEV1", "SEV2", "SEV3", "SEV4")

    Raises:
        ValueError: If threshold is negative
    """

    threshold: int
    priority: str
    severity: str

    def __post_init__(self) -> None:
        """
        Validate rule configuration after initialization.

        Ensures threshold is non-negative as negative error counts are nonsensical.

        Raises:
            ValueError: If threshold < 0
        """
        if self.threshold < 0:
            raise ValueError(
                f"Threshold must be non-negative, got {self.threshold}. " "Error frequency counts cannot be negative."
            )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert rule to dictionary representation for serialization.

        Useful for JSON serialization, logging, and debugging. Returns a shallow
        copy of the rule's fields as a plain dictionary.

        Returns:
            Dictionary with keys: threshold, priority, severity

        Example:
            >>> rule = SeverityRule(threshold=10, priority="High", severity="SEV2")
            >>> rule.to_dict()
            {'threshold': 10, 'priority': 'High', 'severity': 'SEV2'}
        """
        return {"threshold": self.threshold, "priority": self.priority, "severity": self.severity}


@dataclass
class RuleCondition:
    """
    Environment-specific collection of severity rules with evaluation logic.

    Groups multiple SeverityRule instances for a specific environment (production,
    staging, development) and provides evaluation logic to determine which rule
    matches a given error count.

    Rules are automatically sorted by threshold in descending order during initialization
    to enable efficient first-match evaluation: the highest applicable severity is
    returned for any given count.

    Attributes:
        environment: Target environment name (e.g., "production", "staging", "development")
        rules: Ordered list of severity rules, sorted descending by threshold after init

    Example:
        >>> condition = RuleCondition(
        ...     environment="production",
        ...     rules=[
        ...         SeverityRule(10, "High", "SEV2"),
        ...         SeverityRule(50, "Highest", "SEV1"),  # Will be reordered
        ...         SeverityRule(1, "Low", "SEV4")
        ...     ]
        ... )
        >>> condition.rules[0].threshold  # After __post_init__ sorting
        50
        >>> condition.evaluate(25)
        ('High', 'SEV2')
    """

    environment: str
    rules: List[SeverityRule] = field(default_factory=list)

    def __post_init__(self) -> None:
        """
        Sort rules by threshold in descending order for efficient evaluation.

        After sorting, the first rule where (count >= threshold) will be the rule
        with the highest applicable severity, enabling progressive escalation logic.

        Example:
            Input:  [Rule(10, "High"), Rule(50, "Highest"), Rule(1, "Low")]
            Output: [Rule(50, "Highest"), Rule(10, "High"), Rule(1, "Low")]
        """
        # Sort rules by threshold descending: highest threshold first
        # This ensures evaluate() returns the most severe applicable rule
        self.rules.sort(key=lambda rule: rule.threshold, reverse=True)

    def evaluate(self, count: int) -> Tuple[str, str]:
        """
        Determine priority and severity for a given error count.

        Evaluates rules in descending threshold order and returns the first matching
        rule where count >= threshold. This implements progressive severity escalation:
        as error frequency increases, higher thresholds are met and more severe
        classifications are applied.

        Args:
            count: Number of error occurrences in the rolling time window (typically 5 minutes)

        Returns:
            Tuple of (priority, severity) from the first matching rule.
            Returns ("Low", "SEV4") as default if no rules match (count < smallest threshold)

        Example:
            >>> condition = RuleCondition(
            ...     environment="production",
            ...     rules=[
            ...         SeverityRule(50, "Highest", "SEV1"),
            ...         SeverityRule(10, "High", "SEV2"),
            ...         SeverityRule(1, "Low", "SEV4")
            ...     ]
            ... )
            >>> condition.evaluate(5)   # Below all thresholds except 1
            ('Low', 'SEV4')
            >>> condition.evaluate(15)  # Meets 10 threshold
            ('High', 'SEV2')
            >>> condition.evaluate(60)  # Meets 50 threshold
            ('Highest', 'SEV1')
        """
        # Iterate through rules in descending threshold order (ensured by __post_init__)
        for rule in self.rules:
            if count >= rule.threshold:
                return (rule.priority, rule.severity)

        # Default fallback if no rules match (count is below all thresholds)
        # This should rarely occur if rules include a threshold=0 or threshold=1 catch-all
        return ("Low", "SEV4")
