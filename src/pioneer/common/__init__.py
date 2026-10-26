"""Helpers every module may share: pure functions over contract data, the file and clock
utilities the stores need, and the shell of an HTML page. Like `contracts`, this imports nothing
else of the project.
"""

from pioneer.common.html import html_document, static_path, static_text
from pioneer.common.names import class_name_from_path, readable_id
from pioneer.common.production import (
    machines_by_recipe,
    machines_needed,
    raw_resource_ids,
    recipes_for_output,
)
from pioneer.common.storage import utc_timestamp, write_text_atomic

__all__ = [
    "class_name_from_path",
    "html_document",
    "machines_by_recipe",
    "machines_needed",
    "raw_resource_ids",
    "readable_id",
    "recipes_for_output",
    "static_path",
    "static_text",
    "utc_timestamp",
    "write_text_atomic",
]
