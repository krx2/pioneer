"""Expansion Advisor module (implementation.md Stage 8).

Given a new target and an existing production state, computes the minimal delta (extend/add)
instead of planning from scratch -- and, given two versions of the player's plan, what it takes to
go from one to the other (`plan_changes`). Depends only on `pioneer.contracts`; tested against
hand-built "existing" vs. "from scratch" graph fixtures.
"""

from pioneer.expansion_advisor.advisor import advise_expansion
from pioneer.expansion_advisor.revision import plan_changes

__all__ = ["advise_expansion", "plan_changes"]
