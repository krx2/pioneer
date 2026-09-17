"""Verifier module (implementation.md Stage 4).

The deterministic arithmetic core: machine counts, throughput balance, power balance, distance.
Depends only on `pioneer.contracts`; tested against hand-built fixture production graphs with
known-correct expected results.
"""

from pioneer.verifier.calculations import (
    added_machines,
    balance,
    consumption,
    distance,
    extraction_rates,
    generator_byproducts,
    generator_fuel_demand,
    generator_supplemental_demand,
    machine_count,
    minimal_machine_graph,
    placed_generation_capacity_mw,
    placed_power_consumption_mw,
    power_balance,
)

__all__ = [
    "added_machines",
    "balance",
    "consumption",
    "distance",
    "extraction_rates",
    "generator_byproducts",
    "generator_fuel_demand",
    "generator_supplemental_demand",
    "machine_count",
    "minimal_machine_graph",
    "placed_generation_capacity_mw",
    "placed_power_consumption_mw",
    "power_balance",
]
