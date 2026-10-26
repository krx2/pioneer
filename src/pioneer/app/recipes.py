"""Which recipes plans may use: by default every recipe the save has unlocked, changed by the
player's pick for the save's session (`RecipeChoice`), kept as the difference from that default
-- so a recipe unlocked later in the game comes in ticked on its own."""

from __future__ import annotations

from collections.abc import Collection, Mapping

from pioneer.app.preferences import RECIPES, PreferencesStore, RecipeChoice
from pioneer.app.sources import RecipeState
from pioneer.knowledge_base import KnowledgeBase, easiest_unlock, recipe_is_unlocked


def recipe_states(
    kb: KnowledgeBase | None, unlocked: Collection[str] | None, choice: RecipeChoice
) -> tuple[RecipeState, ...]:
    """Every recipe: whether the save has it unlocked (`unlocked`: `None` when unknown), and
    whether plans may use it."""
    if kb is None:
        return ()
    states = []
    for recipe in kb.recipes:
        is_unlocked = recipe_is_unlocked(recipe, unlocked)
        default = is_unlocked is not False
        states.append(
            RecipeState(
                recipe=recipe,
                unlocked=is_unlocked,
                enabled=recipe.recipe_id in choice.enabled
                or (default and recipe.recipe_id not in choice.disabled),
                default=default,
                unlock_with=easiest_unlock(recipe.unlockable_by, kb.technologies)
                if is_unlocked is False
                else None,
            )
        )
    return tuple(states)


def enabled_recipe_ids(recipes: tuple[RecipeState, ...]) -> frozenset[str] | None:
    """The recipes plans may use; `None` while that's just the default."""
    if all(recipe.enabled == recipe.default for recipe in recipes):
        return None
    return frozenset(recipe.recipe.recipe_id for recipe in recipes if recipe.enabled)


def changed_choice(
    choice: RecipeChoice, recipes: tuple[RecipeState, ...], changes: Mapping[str, bool]
) -> RecipeChoice:
    """`choice` with the recipes `changes` maps to `True` let in, and those it maps to `False`
    left out. Raises `ValueError` for an id no recipe has."""
    by_id = {state.recipe.recipe_id: state for state in recipes}
    unknown = sorted(set(changes) - set(by_id))
    if unknown:
        raise ValueError(f"no recipe {unknown[0]!r}")
    enabled, disabled = set(choice.enabled), set(choice.disabled)
    for recipe_id, on in changes.items():
        enabled.discard(recipe_id)
        disabled.discard(recipe_id)
        default = by_id[recipe_id].default
        if on and not default:
            enabled.add(recipe_id)
        elif not on and default:
            disabled.add(recipe_id)
    return RecipeChoice(frozenset(enabled), frozenset(disabled))


def stored_choice(preferences: PreferencesStore, session: str) -> RecipeChoice:
    """The pick of `session` ("" with no save loaded), as the preferences keep it."""
    stored = preferences.get(RECIPES)
    picked = stored.get(session) if isinstance(stored, dict) else None
    return RecipeChoice.from_json(picked) or RecipeChoice()


def store_choice(preferences: PreferencesStore, session: str, choice: RecipeChoice) -> None:
    """Keeps `choice` as `session`'s pick, other sessions keeping theirs."""
    stored = preferences.get(RECIPES)
    sessions = stored if isinstance(stored, dict) else {}
    if choice.enabled or choice.disabled:
        sessions[session] = choice.to_json()
    else:
        sessions.pop(session, None)
    if sessions:
        preferences.put(RECIPES, sessions)
    else:
        preferences.remove(RECIPES)
