"""Expansion Advisor: the minimal change (extend/add) an existing factory needs for a new target,
and what it takes to go from one version of the player's plan to the next (`plan_changes`).
"""

from pioneer.expansion_advisor.advisor import advise_expansion
from pioneer.expansion_advisor.revision import plan_changes

__all__ = ["advise_expansion", "plan_changes"]
