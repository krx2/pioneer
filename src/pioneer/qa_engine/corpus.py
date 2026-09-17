"""Builds the Q&A Engine's corpus from Knowledge Base data (implementation.md Stage 11).

Two kinds of passage. First, the game's own description of every class it describes in words —
items, buildings, belts, equipment, schematics, around a thousand in `docs/en-US.json` — as its UI
shows them. Second, one generated line per factory recipe spelling out where it's made, its
per-machine rates and what unlocks it: the facts players ask about most, which the export only
holds as data. Every number in a recipe passage is Knowledge Base data, never model output; the
Q&A model only rephrases it. Ids are replaced by the names the game shows, so a question worded
the way a player words it finds them.

Depends only on `pioneer.contracts`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pioneer.contracts import Building, ClassDescription, Item, ItemAmount, Recipe, Technology
from pioneer.qa_engine.retrieval import Passage


def build_corpus(
    descriptions: Sequence[ClassDescription],
    recipes: Sequence[Recipe],
    items: Sequence[Item],
    buildings: Sequence[Building],
    technologies: Sequence[Technology],
) -> tuple[Passage, ...]:
    names = {item.item_id: item.name for item in items}
    names.update({building.building_id: building.name for building in buildings})
    names.update({technology.technology_id: technology.name for technology in technologies})

    def name(class_id: str) -> str:
        return names.get(class_id, class_id)

    passages = []
    seen: set[tuple[str, str]] = set()
    for description in descriptions:
        if (description.name, description.text) in seen:
            continue  # variants the export describes identically, e.g. three "Conveyor Wall x 1"s
        seen.add((description.name, description.text))
        passages.append(
            Passage(
                passage_id=description.class_id,
                text=f"{description.name}. {description.text}",
                source=description.name,
            )
        )
    passages += [_recipe_passage(recipe, name) for recipe in recipes]
    return tuple(passages)


def _recipe_passage(recipe: Recipe, name: Callable[[str], str]) -> Passage:
    kind = "an alternate recipe" if recipe.is_alternate else "a recipe"
    made_in = " or ".join(name(building_id) for building_id in recipe.building_ids)
    sentences = [
        f"{recipe.name} is {kind} made in {made_in}.",
        f"Per machine per minute it uses {_amounts(recipe.inputs, name)} "
        f"and makes {_amounts(recipe.outputs, name)}.",
    ]
    if recipe.unlocked_by is not None:
        sentences.append(f"It is unlocked by {name(recipe.unlocked_by)}.")
    return Passage(
        passage_id=f"recipe:{recipe.recipe_id}",
        text=" ".join(sentences),
        source=f"Recipe: {recipe.name}",
    )


def _amounts(amounts: Sequence[ItemAmount], name: Callable[[str], str]) -> str:
    return (
        ", ".join(f"{amount.amount_per_minute:g} {name(amount.item_id)}" for amount in amounts)
        or "nothing"
    )
