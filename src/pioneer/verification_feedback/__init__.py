"""Verification & feedback scoring module (implementation.md Stage 15).

Pure scoring functions over a `ResponseArtifact`: RAG-consistency, distance-from-optimum,
LLM-as-a-judge hooks, and feedback capture. Depends only on `pioneer.contracts`; tested against
hand-built fixture artifacts with known-correct expected scores.
"""
