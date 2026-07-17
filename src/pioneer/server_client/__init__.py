"""Dedicated server API client module (implementation.md Stage 6).

Pulls live game state: progress, phase, session info. Depends only on `pioneer.contracts`;
tested against mocked HTTP responses, including the "server unreachable" case, which must
surface as a typed result, never a raw exception.
"""
