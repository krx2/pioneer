"""Verifier module (implementation.md Stage 4).

The deterministic arithmetic core: machine counts, throughput balance, power balance, distance.
Depends only on `pioneer.contracts`; tested against hand-built fixture production graphs with
known-correct expected results. `progress` counts how far the save has built the player's plan.
"""

from pioneer.verifier.calculations import (
    PowerPlant,
    TransportNeed,
    added_machines,
    allocate_supply,
    balance,
    belt_loads,
    consumption,
    distance,
    extraction_rates,
    extractors_needed,
    generator_byproducts,
    generator_fuel_demand,
    generator_supplemental_demand,
    implied_flows,
    machine_count,
    minimal_machine_graph,
    placed_generation_by_building,
    placed_generation_capacity_mw,
    placed_power_consumption_by_building,
    placed_power_consumption_mw,
    power_balance,
    power_plants,
    transport_needs,
)
from pioneer.verifier.progress import buildings_by_recipe, built_by_recipe, plan_progress

__all__ = [
    "PowerPlant",
    "TransportNeed",
    "added_machines",
    "allocate_supply",
    "balance",
    "belt_loads",
    "buildings_by_recipe",
    "built_by_recipe",
    "consumption",
    "distance",
    "extraction_rates",
    "extractors_needed",
    "generator_byproducts",
    "generator_fuel_demand",
    "generator_supplemental_demand",
    "implied_flows",
    "machine_count",
    "minimal_machine_graph",
    "placed_generation_by_building",
    "placed_generation_capacity_mw",
    "placed_power_consumption_by_building",
    "placed_power_consumption_mw",
    "plan_progress",
    "power_balance",
    "power_plants",
    "transport_needs",
]
