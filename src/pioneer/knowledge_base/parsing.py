"""Parsers for the Satisfactory `Docs.json` export's stringified Unreal Engine struct syntax.

Fields like `mIngredients` or `mProducedIn` are not JSON — they're UE property strings such as
`((ItemClass="/Script/.../Desc_IronIngot.Desc_IronIngot_C'",Amount=3))` or
`("/Game/.../Build_ConstructorMk1.Build_ConstructorMk1_C","/Script/FactoryGame.Foo")`. These
functions turn that text into plain Python values. Pure string -> value; no file I/O here.
"""

from __future__ import annotations

import re

_ITEM_AMOUNT_RE = re.compile(r'ItemClass="([^"]+)"\s*,\s*Amount=(-?\d+(?:\.\d+)?)')
_QUOTED_RE = re.compile(r'"([^"]+)"')


def class_name_from_path(path: str) -> str:
    """Extract the bare class name from a UE object path.

    `/Script/Engine.BlueprintGeneratedClass'/Game/.../Desc_IronIngot.Desc_IronIngot_C'`
    -> `Desc_IronIngot_C`. Also handles paths with no quote wrapper, e.g.
    `/Script/FactoryGame.FGBuildableAutomatedWorkBench` -> `FGBuildableAutomatedWorkBench`.
    """
    return path.rsplit(".", 1)[-1].rstrip("'")


def parse_item_amounts(raw: str) -> tuple[tuple[str, float], ...]:
    """Parse an `mIngredients`/`mProduct`-style string into `(item_class_name, amount)` pairs."""
    return tuple(
        (class_name_from_path(item_class), float(amount))
        for item_class, amount in _ITEM_AMOUNT_RE.findall(raw)
    )


def parse_quoted_class_list(raw: str) -> tuple[str, ...]:
    """Parse an `mProducedIn`/`mRecipes`-style quoted-path list into bare class names."""
    return tuple(class_name_from_path(path) for path in _QUOTED_RE.findall(raw))
