"""The endpoints behind the header's panels, all through the `app.LiveContext` the context comes
from, so a change shows in the very next answer; without one, every endpoint is a 404. Every change
answers with the status as it then stands (`/api/status`'s shape).

- `GET /api/saves`: the saves under the game's `SaveGames` folder, by session, and which is read.
- `PUT /api/saves/selection`: which save to read — by a path `GET /api/saves` listed, never any
  other (see `LiveContext.choose_save`).
- `GET /api/server`: the dedicated server asked, how it last answered, and whether there's a token
  — never the token itself, here or in any other response.
- `POST /api/server/test`: whether a server answers (`HealthCheck`), and takes the token.
- `PUT /api/server`: ask this server from now on — with a token, or the admin password to get one
  with (the password is used once and not kept), or neither, keeping the token in use.
- `DELETE /api/server`: ask none; the save alone says what's built.
- `GET /api/recipes`: every recipe, with what it makes and in what, whether the save has it
  unlocked (and if not, what unlocks it) and whether plans may use it.
- `PUT /api/recipes/enabled`: turn recipes on or off for plans, in the save's session.
- `POST /api/recipes/reset`: back to the recipes the save has unlocked.
- `GET /api/tech`: the HUB's tiers and milestones, the MAM's trees and the hard drives, each
  technology with what it costs, what it unlocks, and whether the save has it.
- `GET /api/map`: the player's factories — where, what they take in and put out, which resource
  nodes feed them — every resource node, taken or free, and the map picture to draw them on.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pioneer.app import LiveContext
from pioneer.common import readable_id

StatusBody = Callable[[], dict[str, Any]]


class ServerRequest(BaseModel):
    host: str = Field(max_length=253, pattern=r"^\s*([A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])\s*$")
    """A host name, an IPv4 address or a bracketed IPv6 one: nothing that would make the URL the
    server is asked at point anywhere else."""
    port: int = Field(default=7777, ge=1, le=65535)
    token: str | None = Field(default=None, max_length=4096)
    password: str | None = Field(default=None, max_length=1024)


class RecipeChanges(BaseModel):
    changes: dict[str, bool] = Field(max_length=2000)
    """Recipe id -> whether plans may use it."""


class SaveSelectionRequest(BaseModel):
    mode: Literal["auto", "session", "file"]
    path: str | None = Field(default=None, max_length=4096)
    """A save as `GET /api/saves` listed it; none: back to `.env`'s folder (mode `auto`)."""


def source_routes(
    live: LiveContext | None,
    status: StatusBody,
    *,
    names: Mapping[str, str],
    icons: Mapping[str, str],
    map_picture: str | None = None,
    map_tiles: Mapping[str, Any] | None = None,
    world: tuple[float, float, float, float] | None = None,
) -> APIRouter:
    """`names` and `icons` are the knowledge base's in-game names and icon URLs, by id;
    `map_picture` the URL of the game's map, `map_tiles` the tiles it's cut into (as
    `map_presentation.MapTileSet.as_json` puts them), and `world` the part of the world it
    shows."""
    router = APIRouter()

    def named(class_id: str) -> dict[str, Any]:
        name = names.get(class_id) or readable_id(class_id)
        return {"id": class_id, "name": name, "icon": icons.get(class_id)}

    def sources() -> LiveContext:
        if live is None:
            raise HTTPException(status_code=404, detail="no live data sources")
        return live

    @router.get("/api/saves")
    def list_saves() -> dict[str, Any]:
        current = sources()
        save = current.sources().save
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for found in current.list_saves():
            key = (str(found.folder), found.session_name)
            group = groups.setdefault(
                key,
                {
                    "session": found.session_name,
                    "source": found.source,
                    "folder": str(found.folder),
                    "saves": [],
                },
            )
            group["saves"].append(
                {
                    "path": str(found.path),
                    "file": found.path.name,
                    "name": found.save_name,
                    "modified": found.modified,
                    "play_seconds": found.play_duration_seconds,
                    "size_bytes": found.size_bytes,
                }
            )
        return {
            "selection": asdict(current.save_selection()),
            "in_use": save.path,
            "loading": save.loading,
            "error": save.error,
            "groups": list(groups.values()),  # newest save first, so newest group first
        }

    @router.put("/api/saves/selection")
    def select_save(request: SaveSelectionRequest) -> dict[str, Any]:
        try:
            sources().choose_save(request.mode, request.path)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return status()

    @router.get("/api/recipes")
    def list_recipes() -> dict[str, Any]:
        current = sources()
        states = current.recipe_states()
        now = current.sources()
        return {
            "session": now.save.session,
            **asdict(now.recipes),
            "recipes": [
                {
                    "id": state.recipe.recipe_id,
                    "name": state.recipe.name,
                    "alternate": state.recipe.is_alternate,
                    "unlocked": state.unlocked,
                    "enabled": state.enabled,
                    "default": state.default,
                    "products": [
                        {**named(output.item_id), "per_minute": output.amount_per_minute}
                        for output in state.recipe.outputs
                    ],
                    "building": named(state.recipe.building_ids[0])
                    if state.recipe.building_ids
                    else None,
                    "unlock": {
                        "name": state.unlock_with.name,
                        "tier": state.unlock_with.tier,
                        "kind": state.unlock_with.kind,
                    }
                    if state.unlock_with is not None
                    else None,
                }
                for state in states
            ],
        }

    @router.put("/api/recipes/enabled")
    def change_recipes(request: RecipeChanges) -> dict[str, Any]:
        try:
            sources().set_recipes(request.changes)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return status()

    @router.post("/api/recipes/reset")
    def reset_recipes() -> dict[str, Any]:
        sources().reset_recipes()
        return status()

    @router.get("/api/tech")
    def tech() -> dict[str, Any]:
        current = sources()
        overview = current.tech_overview()
        now = current.sources()
        unlocked = overview.unlocked

        def has(technology_id: str) -> bool | None:
            return None if unlocked is None else technology_id in unlocked

        return {
            "known": unlocked is not None,
            "tier": now.tech.tier,
            "phase": now.server.phase,
            "tiers": [asdict(tier) for tier in overview.tiers],
            "sections": [
                {
                    "group": section.group,
                    "title": section.title,
                    "unlocked": None
                    if unlocked is None
                    else sum(t.technology_id in unlocked for t in section.technologies),
                    "total": len(section.technologies),
                    "technologies": [
                        {
                            "id": technology.technology_id,
                            "name": technology.name,
                            "tier": technology.tier,
                            "kind": technology.kind,
                            "unlocked": has(technology.technology_id),
                            "cost": [
                                {**named(cost.item_id), "amount": cost.amount}
                                for cost in technology.cost
                            ],
                            "unlocks": [
                                named(recipe_id)
                                for recipe_id in overview.unlocks.get(technology.technology_id, ())
                            ],
                        }
                        for technology in section.technologies
                    ],
                }
                for section in overview.sections
            ],
        }

    @router.get("/api/map")
    def world_map() -> dict[str, Any]:
        overview = sources().map_overview()

        def rates(balance: Mapping[str, float], sign: float) -> list[dict[str, Any]]:
            flows = [(item, rate * sign) for item, rate in balance.items() if rate * sign > 0.01]
            return [
                {**named(item), "per_minute": round(rate, 3)}
                for item, rate in sorted(flows, key=lambda flow: -flow[1])
            ]

        return {
            "image": map_picture,
            "tiles": map_tiles,
            "world": dict(zip(("min_x", "min_y", "max_x", "max_y"), world, strict=True))
            if world
            else None,
            "sites": [
                {
                    "id": entry.site.site_id,
                    "x": entry.site.position.x,
                    "y": entry.site.position.y,
                    "buildings": len(entry.site.placements),
                    "main": named(entry.main_product) if entry.main_product else None,
                    "inputs": rates(entry.balance, -1.0),
                    "outputs": rates(entry.balance, 1.0),
                    "feeds": [
                        {"node": feed.node_id, "approximate": feed.approximate}
                        for feed in entry.feeds
                    ],
                }
                for entry in overview.sites
            ],
            "nodes": [
                {
                    "id": node.node_id,
                    "item": named(node.item_id),
                    "purity": node.purity.value,
                    "x": node.position.x,
                    "y": node.position.y,
                    "occupied": node.node_id in overview.occupied,
                }
                for node in overview.nodes
            ],
        }

    @router.get("/api/server")
    def server_settings() -> dict[str, Any]:
        return asdict(sources().sources().server)

    @router.post("/api/server/test")
    def test_server(request: ServerRequest) -> dict[str, Any]:
        host = request.host.strip()
        return asdict(sources().check_server(host, request.port, request.token or None))

    @router.put("/api/server")
    def configure_server(request: ServerRequest) -> dict[str, Any]:
        try:
            note = sources().connect_server(
                request.host.strip(),
                request.port,
                token=request.token or None,
                password=request.password or None,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {**status(), "note": note}

    @router.delete("/api/server")
    def disconnect_server() -> dict[str, Any]:
        sources().configure_server(None)
        return status()

    return router
