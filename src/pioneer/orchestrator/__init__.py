"""LLM Orchestrator — final integration layer (implementation.md Stage 16).

The only piece of the system allowed to depend on every other module. Classifies player intent,
calls the relevant module(s) as tools, runs results through the Verifier and the verification/
feedback module, and composes the final `ResponseArtifact`. Left empty until every other module
(Stages 1-15) is independently complete — see docs/implementation.md.
"""
