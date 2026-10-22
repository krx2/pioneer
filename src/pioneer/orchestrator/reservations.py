"""What a plan extending the player's factory takes of their factories' spare output, factory by
factory: only what it uses. A megafactory making far more of an item than the plan needs gives the
plan just that much — the rest stays spare, for the player and for other plans.

**Each factory's spare.** A factory (`FactorySite`) has some of an item to spare when it makes
more of it than it uses (`site_balance`). The whole factory may have less to spare than its
factories add up to — one's output can be another's input — so each one's spare is scaled down in
proportion to come to no more than the whole's.

**Others' reservations aren't spare.** What the plans of the player's other conversations reserved
(`OrchestratorContext.reserved_supply`) is taken off the whole factory's spare, and off the
factory it was reserved from — found again by where it stands, as factory ids follow their sizes
and change from save to save.

**Who gives what.** A plan's draw of an item comes first from the factories it took it from
before — a revision keeps its reservations where they were — then from the one nearest where it's
used, then from those with the most to spare.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from pioneer.contracts import Coordinates, FactorySite, SupplyReservation
from pioneer.orchestrator.base import OrchestratorContext
from pioneer.orchestrator.existing_factory import site_balance
from pioneer.verifier import distance

_SAME_FACTORY = 50_000.0
"""500 m: how far a factory's middle may have moved since a reservation was made from it."""
_EPSILON = 1e-6


def unreserved(balance: Mapping[str, float], context: OrchestratorContext) -> dict[str, float]:
    """The whole factory's net per-item rate, less what other plans reserved of it."""
    left = dict(balance)
    for reservation in context.reserved_supply:
        left[reservation.item_id] = left.get(reservation.item_id, 0.0) - reservation.rate_per_minute
    return left


def factory_of(reservation: SupplyReservation, sites: Sequence[FactorySite]) -> FactorySite | None:
    """The factory a reservation was made from, as the save now has it: the one standing nearest
    where it stood, if near enough."""
    nearest = min(
        sites, key=lambda site: distance(site.position, reservation.position), default=None
    )
    if nearest is None or distance(nearest.position, reservation.position) > _SAME_FACTORY:
        return None
    return nearest


def spare_by_factory(
    context: OrchestratorContext, whole: Mapping[str, float]
) -> dict[str, dict[str, float]]:
    """What each factory has to spare a minute, by site id and item: its own surplus, scaled to
    come to no more than the whole factory has, less what others reserved of it — and in all no
    more than `whole`, the whole factory's spare less others' reservations."""
    surplus = {
        site.site_id: {item: rate for item, rate in site_balance(site, context).items() if rate > 0}
        for site in context.factory_sites
    }
    made: dict[str, float] = defaultdict(float)
    for rates in surplus.values():
        for item, rate in rates.items():
            made[item] += rate
    taken: dict[str, float] = defaultdict(float)
    for reservation in context.reserved_supply:
        taken[reservation.item_id] += reservation.rate_per_minute
    spare = {
        site_id: {
            item: rate * min(1.0, max(whole.get(item, 0.0) + taken[item], 0.0) / made[item])
            for item, rate in rates.items()
        }
        for site_id, rates in surplus.items()
    }
    for reservation in context.reserved_supply:
        site = factory_of(reservation, context.factory_sites)
        rates = spare.get(site.site_id, {}) if site is not None else {}
        if reservation.item_id in rates:
            rates[reservation.item_id] = max(
                0.0, rates[reservation.item_id] - reservation.rate_per_minute
            )
    for item in made:  # a reservation whose factory is gone is still taken: from all of them
        total = sum(rates.get(item, 0.0) for rates in spare.values())
        allowed = max(whole.get(item, 0.0), 0.0)
        if total > allowed + _EPSILON:
            for rates in spare.values():
                if item in rates:
                    rates[item] *= allowed / total
    return spare


def reserve(
    draws: Mapping[str, float],
    context: OrchestratorContext,
    whole: Mapping[str, float],
    *,
    near: Coordinates | None = None,
    kept: Sequence[SupplyReservation] = (),
) -> tuple[SupplyReservation, ...]:
    """What of `draws` (item -> per minute the plan takes of the factory's spare) to reserve from
    which factory — see the module docstring. `whole`: the factory's spare, less others'."""
    spare = spare_by_factory(context, whole)
    sites = {site.site_id: site for site in context.factory_sites}
    reservations: list[SupplyReservation] = []
    for item_id, rate in draws.items():
        left = rate
        # What the plan reserved before is its own, from where it was — spare in the save now or
        # not, for the player may have built what takes it.
        for reservation in kept:
            site = factory_of(reservation, context.factory_sites)
            if reservation.item_id != item_id or site is None or left <= _EPSILON:
                continue
            taken = min(left, reservation.rate_per_minute)
            reservations.append(SupplyReservation(site.site_id, site.position, item_id, taken))
            rates = spare.setdefault(site.site_id, {})
            rates[item_id] = max(0.0, rates.get(item_id, 0.0) - taken)
            left -= taken
        givers = sorted(
            (site_id for site_id, rates in spare.items() if rates.get(item_id, 0.0) > _EPSILON),
            key=lambda site_id: (
                distance(sites[site_id].position, near) if near is not None else 0.0,
                -spare[site_id][item_id],
                site_id,
            ),
        )
        for site_id in givers:
            if left <= _EPSILON:
                break
            taken = min(left, spare[site_id][item_id])
            reservations.append(SupplyReservation(site_id, sites[site_id].position, item_id, taken))
            spare[site_id][item_id] -= taken
            left -= taken
    return tuple(_merged(reservations))


def _merged(reservations: Sequence[SupplyReservation]) -> list[SupplyReservation]:
    """One reservation per factory and item."""
    merged: dict[tuple[str, str], SupplyReservation] = {}
    for reservation in reservations:
        key = (reservation.site_id, reservation.item_id)
        if key in merged:
            rate = merged[key].rate_per_minute + reservation.rate_per_minute
            merged[key] = SupplyReservation(key[0], reservation.position, key[1], rate)
        else:
            merged[key] = reservation
    return list(merged.values())


def reservation_summary(
    reservations: Sequence[SupplyReservation],
    context: OrchestratorContext,
    whole: Mapping[str, float],
) -> list[dict[str, object]]:
    """What the model is told of `reservations`: each with what its factory has to spare of the
    item, so that taking 100 of a megafactory's 2000 a minute reads as just that."""
    spare = spare_by_factory(context, whole)
    return [
        {
            "site_id": reservation.site_id,
            "item_id": reservation.item_id,
            "per_minute": round(reservation.rate_per_minute, 1),
            "site_spare_per_minute": round(
                spare.get(reservation.site_id, {}).get(reservation.item_id, 0.0), 1
            ),
        }
        for reservation in reservations
    ]
