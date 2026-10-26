"""Class ids and UE object paths, as the game's data and saves write them."""

from __future__ import annotations

import re

_CLASS_ID_AFFIX = re.compile(r"^(?:Desc|Recipe|Build|BP|Schematic|Research)_|_C$")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def readable_id(class_id: str) -> str:
    """`Desc_IronPlate_C` -> `Iron Plate`: the label for an id the knowledge base can't name."""
    words = _CAMEL_BOUNDARY.sub(" ", _CLASS_ID_AFFIX.sub("", class_id)).replace("_", " ")
    return " ".join(words.split()) or class_id


def class_name_from_path(path: str) -> str:
    """`/Script/Engine.BlueprintGeneratedClass'/Game/.../Desc_IronIngot.Desc_IronIngot_C'` ->
    `Desc_IronIngot_C`; also without the quote wrapper."""
    return path.rsplit(".", 1)[-1].rstrip("'")
