"""Verifier module (implementation.md Stage 4).

The deterministic arithmetic core: machine counts, throughput balance, power balance, distance.
Depends only on `pioneer.contracts`; tested against hand-built fixture production graphs with
known-correct expected results.
"""

from pioneer.verifier.calculations import (
    balance,
    distance,
    extraction_rates,
    generator_fuel_demand,
    machine_count,
    placed_generation_capacity_mw,
    placed_power_consumption_mw,
    power_balance,
)

__all__ = [
    "balance",
    "distance",
    "extraction_rates",
    "generator_fuel_demand",
    "machine_count",
    "placed_generation_capacity_mw",
    "placed_power_consumption_mw",
    "power_balance",
]
