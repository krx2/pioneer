"""Anomaly Detector module (implementation.md Stage 10).

Scans a production state for gaps, power blackouts, and congestion. Depends only on
`pioneer.contracts`; tested against a deliberately-broken fixture production graph.
"""

from pioneer.anomaly_detector.detector import (
    detect_anomalies,
    detect_belt_overloads,
    detect_wiring_problems,
)

__all__ = ["detect_anomalies", "detect_belt_overloads", "detect_wiring_problems"]
