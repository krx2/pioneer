"""Expansion Advisor module (implementation.md Stage 8).

Given a new target and an existing production state, computes the minimal delta (extend/add)
instead of planning from scratch. Depends only on `pioneer.contracts`; tested against hand-built
"existing" vs. "from scratch" graph fixtures.
"""
