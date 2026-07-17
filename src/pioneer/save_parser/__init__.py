"""Save file (.sav) parser module (implementation.md Stage 5).

Reads a Satisfactory save file into `ProductionGraph` + `PlacementRecord` shapes. Read-only —
never writes to the save file. Depends only on `pioneer.contracts`; tested against fixture save
files, resolving recipe IDs against a fixture recipe list rather than a live Knowledge Base call.
"""
