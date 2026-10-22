"""Tests for the header panels' endpoints, through FastAPI's TestClient over a real
`app.LiveContext` with fake loaders: no save parsing, no network, no model."""

import base64
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pioneer.app import LiveContext, PreferencesStore, ServerConfig
from pioneer.contracts import (
    Coordinates,
    Item,
    ItemAmount,
    ItemCount,
    PlacementRecord,
    ProductionGraph,
    Purity,
    Recipe,
    ResourceNode,
    ResponseArtifact,
    Technology,
    TransportLink,
)
from pioneer.knowledge_base import KnowledgeBase
from pioneer.map_image import MapTiles
from pioneer.orchestrator import OrchestratorContext
from pioneer.save_parser import SaveHeader, SaveState
from pioneer.server_client import TransportError
from pioneer.web import create_app


def _header(session: str) -> SaveHeader:
    return SaveHeader(14, 60, 1, "", "Persistent_Level", "", session, 3_725)


def _state(path: Path) -> SaveState:
    return SaveState(
        header=_header(path.stem.split("_")[0]),
        placements=(
            PlacementRecord(building_id="Build_SmelterMk1_C", position=Coordinates(x=0, y=0)),
        ),
        graph=ProductionGraph(nodes=(), flows=()),
    )


def _save(path: Path, mtime: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"save")
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def save_games(tmp_path: Path) -> Path:
    root = tmp_path / "SaveGames"
    _save(root / "76561198311247541" / "alfa_autosave_0.sav", 1_000)
    _save(root / "76561198311247541" / "alfa_autosave_1.sav", 2_000)
    _save(root / "76561198311247541" / "beta_manual.sav", 3_000)
    _save(root / "server" / "alfa_autosave_0.sav", 500)
    return root


def _client(save_games: Path, tmp_path: Path) -> TestClient:
    live = LiveContext(
        None,
        (),
        save_directory=str(save_games / "76561198311247541"),
        save_root=str(save_games),
        preferences=PreferencesStore(tmp_path / "settings.json"),
        load_save=_state,
        read_header=lambda path: _header(path.stem.split("_")[0]),
    )
    return TestClient(create_app(live=live, answer=lambda *args, **kwargs: None))


def test_the_saves_are_listed_by_folder_and_session_with_the_one_in_use(
    save_games, tmp_path
) -> None:
    body = _client(save_games, tmp_path).get("/api/saves").json()

    groups = [(g["source"], g["session"], [s["file"] for s in g["saves"]]) for g in body["groups"]]
    assert groups == [
        ("steam", "beta", ["beta_manual.sav"]),
        ("steam", "alfa", ["alfa_autosave_1.sav", "alfa_autosave_0.sav"]),
        ("server", "alfa", ["alfa_autosave_0.sav"]),
    ]
    assert body["in_use"] == str(save_games / "76561198311247541" / "beta_manual.sav")
    assert body["selection"] == {"mode": "auto", "directory": None, "session": None, "path": None}
    first = body["groups"][0]["saves"][0]
    assert (first["name"], first["play_seconds"], first["size_bytes"]) == ("beta_manual", 3725, 4)


def test_picking_a_save_reads_it_and_says_so(save_games, tmp_path) -> None:
    client = _client(save_games, tmp_path)
    pinned = str(save_games / "server" / "alfa_autosave_0.sav")

    status = client.put("/api/saves/selection", json={"mode": "file", "path": pinned}).json()

    assert status["sources"]["save"]["path"] == pinned
    assert status["sources"]["save"]["mode"] == "file"
    assert "save alfa_autosave_0.sav" in status["status"]
    assert client.get("/api/saves").json()["selection"]["path"] == pinned

    back = client.put("/api/saves/selection", json={"mode": "auto", "path": None}).json()
    assert back["sources"]["save"]["file"] == "beta_manual.sav"


def test_a_path_the_list_did_not_give_is_refused(save_games, tmp_path) -> None:
    client = _client(save_games, tmp_path)
    outside = _save(tmp_path / "elsewhere" / "secret.sav", 9_000)

    for request in (
        {"mode": "file", "path": str(outside)},
        {"mode": "file", "path": str(save_games / "76561198311247541" / "..")},
        {"mode": "session", "path": None},
        {"mode": "newest", "path": None},
    ):
        response = client.put("/api/saves/selection", json=request)
        assert response.status_code == 422, request
    assert client.get("/api/status").json()["sources"]["save"]["file"] == "beta_manual.sav"


def test_without_live_sources_the_panels_have_nothing_to_show() -> None:
    client = TestClient(
        create_app(context=lambda: (OrchestratorContext(), "status"), answer=lambda *a: None)
    )

    assert client.get("/api/saves").status_code == 404
    assert client.put("/api/saves/selection", json={"mode": "auto"}).status_code == 404


# --- The dedicated server ----------------------------------------------------------------------


def _api_token() -> str:
    payload = base64.b64encode(json.dumps({"pl": "APIToken"}).encode()).decode().rstrip("=")
    return f"{payload}.0a1b2c3d"


class _FakeServer:
    """A dedicated server answering the documented API functions; `password` logs in, and only
    the tokens it handed out (or `known`) are taken."""

    def __init__(self, password: str = "hunter2", reachable: bool = True) -> None:
        self.password = password
        self.reachable = reachable
        self.known = {"env-token"}
        self.calls: list[str] = []

    def __call__(self, url, body, headers):
        self.calls.append(body["function"])
        if not self.reachable:
            raise TransportError("connection refused")
        token = headers.get("Authorization", "").removeprefix("Bearer ")
        function = body["function"]
        if function == "HealthCheck":
            return 200, {"data": {"Health": "healthy", "ServerCustomData": ""}}
        if function == "PasswordLogin":
            if body["data"]["Password"] != self.password:
                return 401, {"errorCode": "wrong_password"}
            self.known.add("admin-login")
            return 200, {"data": {"AuthenticationToken": "admin-login"}}
        if token not in self.known:
            return 401, {"errorCode": "invalid_token"}
        if function == "RunCommand":
            self.known.add(_api_token())
            return 200, {"data": {"CommandResult": f"API Token: {_api_token()}"}}
        if function == "VerifyAuthenticationToken":
            return 204, None
        if function == "QueryServerState":
            state = {"GamePhase": "Phase_2", "TechTier": 6, "ActiveSessionName": "alfa"}
            return 200, {"data": {"ServerGameState": state}}
        raise AssertionError(function)


def _server_client(tmp_path: Path, fake: _FakeServer, env: ServerConfig | None = None):
    live = LiveContext(
        None,
        (),
        save_directory=None,
        server=env,
        preferences=PreferencesStore(tmp_path / "settings.json"),
        post=fake,
    )
    return TestClient(create_app(live=live, answer=lambda *args, **kwargs: None))


def test_the_server_from_env_is_shown_without_its_token(tmp_path) -> None:
    client = _server_client(
        tmp_path, _FakeServer(), ServerConfig("factory.local", 7777, "env-token")
    )

    shown = client.get("/api/server").json()

    assert shown == {
        "configured": True,
        "source": "env",
        "host": "factory.local",
        "port": 7777,
        "has_token": True,
        "phase": "Phase_2",
        "tech_tier": 6,
        "session": "alfa",
        "error": None,
    }
    assert "env-token" not in client.get("/").text
    assert "env-token" not in json.dumps(client.get("/api/status").json())


def test_testing_a_server_checks_it_answers_and_takes_the_token(tmp_path) -> None:
    fake = _FakeServer()
    client = _server_client(tmp_path, fake, ServerConfig("factory.local", 7777, "env-token"))

    saved = client.post("/api/server/test", json={"host": "10.0.0.2", "port": 7778}).json()
    wrong = client.post("/api/server/test", json={"host": "10.0.0.2", "token": "nope"}).json()
    fake.reachable = False
    down = client.post("/api/server/test", json={"host": "10.0.0.2"}).json()

    assert saved == {"reachable": True, "health": "healthy", "token_accepted": True, "error": None}
    assert wrong["token_accepted"] is False
    assert (down["reachable"], down["error"]) == (
        False,
        "could not reach dedicated server: connection refused",
    )
    assert client.get("/api/server").json()["host"] == "factory.local"  # nothing changed


def test_the_admin_password_is_traded_for_a_lasting_token_and_not_kept(tmp_path) -> None:
    fake = _FakeServer()
    client = _server_client(tmp_path, fake)

    answer = client.put(
        "/api/server", json={"host": " 10.0.0.2 ", "port": 7778, "password": "hunter2"}
    ).json()

    assert answer["note"] is None
    assert answer["sources"]["server"]["phase"] == "Phase_2"
    assert answer["sources"]["server"]["source"] == "page"
    settings = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert settings["server"] == {"host": "10.0.0.2", "port": 7778, "token": _api_token()}
    assert "hunter2" not in (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert _api_token() not in json.dumps(answer)


def test_a_wrong_password_changes_nothing(tmp_path) -> None:
    client = _server_client(
        tmp_path, _FakeServer(), ServerConfig("factory.local", 7777, "env-token")
    )

    response = client.put("/api/server", json={"host": "10.0.0.2", "password": "guess"})

    assert response.status_code == 422
    assert response.json()["detail"] == "could not log in: wrong password"
    assert client.get("/api/server").json()["source"] == "env"


def test_saving_without_a_token_keeps_the_one_in_use(tmp_path) -> None:
    client = _server_client(
        tmp_path, _FakeServer(), ServerConfig("factory.local", 7777, "env-token")
    )

    answer = client.put("/api/server", json={"host": "factory.local", "port": 7778}).json()

    assert answer["sources"]["server"]["port"] == 7778
    assert answer["sources"]["server"]["has_token"] is True
    assert answer["sources"]["server"]["error"] is None


def test_disconnecting_wins_over_env_until_a_server_is_set_again(tmp_path) -> None:
    fake = _FakeServer()
    client = _server_client(tmp_path, fake, ServerConfig("factory.local", 7777, "env-token"))

    status = client.delete("/api/server").json()

    assert status["sources"]["server"] == {
        "configured": False,
        "source": "page",
        "host": None,
        "port": None,
        "has_token": False,
        "phase": None,
        "tech_tier": None,
        "session": None,
        "error": None,
    }
    assert status["status"].endswith("no dedicated server configured")


def test_a_host_that_is_no_host_is_refused(tmp_path) -> None:
    client = _server_client(tmp_path, _FakeServer())

    for host in ("evil.com/path?x=", "a b", "", "user@host"):
        assert client.put("/api/server", json={"host": host}).status_code == 422, host
        assert client.post("/api/server/test", json={"host": host}).status_code == 422, host


# --- Recipes -----------------------------------------------------------------------------------

_RECIPE_KB = KnowledgeBase(
    recipes=(
        Recipe(
            "Recipe_Screw_C",
            "Screw",
            ("Build_ConstructorMk1_C",),
            (ItemAmount("Desc_IronRod_C", 10),),
            (ItemAmount("Desc_IronScrew_C", 40),),
            unlockable_by=("Schematic_1-1_C",),
        ),
        Recipe(
            "Recipe_Alternate_Screw_C",
            "Alternate: Cast Screw",
            ("Build_ConstructorMk1_C",),
            (ItemAmount("Desc_IronIngot_C", 12.5),),
            (ItemAmount("Desc_IronScrew_C", 50),),
            unlockable_by=("Schematic_Alternate_Screw_C",),
            is_alternate=True,
        ),
    ),
    buildings=(),
    technologies=(
        Technology("Schematic_1-1_C", "Base Building", 1, kind="milestone"),
        Technology("Schematic_Alternate_Screw_C", "Alternate: Cast Screw", 2, kind="alternate"),
    ),
    items=(Item("Desc_IronScrew_C", "Screw"),),
)


def _recipe_client(tmp_path: Path) -> TestClient:
    (tmp_path / "a.sav").write_bytes(b"save")
    state = replace(
        _state(tmp_path / "alfa.sav"), unlocked_technology_ids=frozenset({"Schematic_1-1_C"})
    )
    live = LiveContext(
        _RECIPE_KB,
        (),
        save_directory=str(tmp_path),
        preferences=PreferencesStore(tmp_path / "settings.json"),
        load_save=lambda path: state,
    )
    return TestClient(create_app(live=live, answer=lambda *args, **kwargs: None))


def test_the_recipes_say_what_they_make_and_whether_plans_may_use_them(tmp_path) -> None:
    body = _recipe_client(tmp_path).get("/api/recipes").json()

    assert (body["session"], body["total"], body["enabled"], body["customized"]) == (
        "alfa",
        2,
        1,
        False,
    )
    screw, cast = body["recipes"]
    assert (screw["enabled"], screw["unlocked"], screw["unlock"]) == (True, True, None)
    assert screw["products"] == [
        {"id": "Desc_IronScrew_C", "name": "Screw", "icon": None, "per_minute": 40}
    ]
    assert screw["building"]["id"] == "Build_ConstructorMk1_C"
    assert (cast["enabled"], cast["unlocked"], cast["alternate"]) == (False, False, True)
    assert cast["unlock"] == {"name": "Alternate: Cast Screw", "tier": 2, "kind": "alternate"}


def test_turning_recipes_on_and_off_and_back_to_the_default(tmp_path) -> None:
    client = _recipe_client(tmp_path)

    changed = client.put(
        "/api/recipes/enabled",
        json={"changes": {"Recipe_Screw_C": False, "Recipe_Alternate_Screw_C": True}},
    ).json()
    unknown = client.put("/api/recipes/enabled", json={"changes": {"Recipe_Nope_C": True}})
    reset = client.post("/api/recipes/reset").json()

    assert changed["sources"]["recipes"] == {
        "total": 2,
        "enabled": 1,
        "locked_enabled": 1,
        "customized": True,
    }
    assert unknown.status_code == 422
    assert reset["sources"]["recipes"]["customized"] is False
    assert client.get("/api/recipes").json()["recipes"][0]["enabled"] is True


def test_an_answer_needing_turned_off_recipes_says_which() -> None:
    artifact = ResponseArtifact(
        response_id="r1", chat="Turn on Screw.", missing_recipes=("Recipe_Screw_C",)
    )
    client = TestClient(
        create_app(context=lambda: (OrchestratorContext(), ""), answer=lambda *a, **k: artifact)
    )

    body = client.post("/api/ask", json={"question": "screws?"}).json()

    assert body["missing_recipes"] == ["Recipe_Screw_C"]


# --- Tech --------------------------------------------------------------------------------------

_TECH_KB = KnowledgeBase(
    recipes=(
        Recipe(
            "Recipe_Silica_C",
            "Silica",
            ("Build_ConstructorMk1_C",),
            (),
            (ItemAmount("Desc_Silica_C", 25),),
            unlockable_by=("Research_Quartz_1_2_C",),
        ),
    ),
    buildings=(),
    technologies=(
        Technology("Schematic_Tutorial1_C", "HUB Upgrade 1", 0, kind="tutorial"),
        Technology("Schematic_1-2_C", "Logistics", 1, kind="milestone"),
        Technology("Schematic_1-1_C", "Base Building", 1, kind="milestone"),
        Technology(
            "Research_Quartz_1_2_C",
            "Silica",
            3,
            kind="mam",
            tree="Quartz",
            cost=(ItemCount("Desc_RawQuartz_C", 20),),
        ),
        Technology("Research_Quartz_0_C", "Quartz", 3, kind="mam", tree="Quartz"),
        Technology("Schematic_Alternate_Screw_C", "Alternate: Cast Screw", 2, kind="alternate"),
    ),
    items=(Item("Desc_Silica_C", "Silica"), Item("Desc_RawQuartz_C", "Raw Quartz")),
)


def _tech_client(tmp_path: Path, unlocked: set[str] | None) -> TestClient:
    if unlocked is not None:
        (tmp_path / "a.sav").write_bytes(b"save")
    state = replace(_state(tmp_path / "a.sav"), unlocked_technology_ids=frozenset(unlocked or ()))
    live = LiveContext(_TECH_KB, (), save_directory=str(tmp_path), load_save=lambda path: state)
    return TestClient(create_app(live=live, answer=lambda *args, **kwargs: None))


def test_tech_shows_the_tiers_trees_and_hard_drives_with_what_is_unlocked(tmp_path) -> None:
    body = (
        _tech_client(tmp_path, {"Schematic_Tutorial1_C", "Schematic_1-1_C", "Research_Quartz_0_C"})
        .get("/api/tech")
        .json()
    )

    assert (body["known"], body["tier"]) == (True, 1)
    assert body["tiers"] == [
        {"tier": 0, "unlocked": 1, "total": 1},
        {"tier": 1, "unlocked": 1, "total": 2},
    ]
    sections = [(s["group"], s["title"], s["unlocked"], s["total"]) for s in body["sections"]]
    assert sections == [
        ("HUB", "Tier 0", 1, 1),
        ("HUB", "Tier 1", 1, 2),
        ("MAM", "Quartz", 1, 2),
        ("Hard drives", "Alternate recipes", 0, 1),
    ]
    tier_1 = [t["id"] for t in body["sections"][1]["technologies"]]
    assert tier_1 == ["Schematic_1-1_C", "Schematic_1-2_C"]  # in the HUB's order
    quartz, silica = body["sections"][2]["technologies"]
    assert (quartz["name"], quartz["unlocked"]) == ("Quartz", True)
    assert silica["cost"] == [
        {"id": "Desc_RawQuartz_C", "name": "Raw Quartz", "icon": None, "amount": 20}
    ]
    assert silica["unlocks"] == [{"id": "Recipe_Silica_C", "name": "Silica", "icon": None}]


def test_without_a_save_the_trees_are_shown_and_progress_is_unknown(tmp_path) -> None:
    body = _tech_client(tmp_path, None).get("/api/tech").json()

    assert (body["known"], body["tier"]) == (False, None)
    assert {s["unlocked"] for s in body["sections"]} == {None}
    assert {t["unlocked"] for s in body["sections"] for t in s["technologies"]} == {None}


# --- Map ---------------------------------------------------------------------------------------

_MAP_KB = KnowledgeBase(
    recipes=(
        Recipe(
            "Recipe_IngotIron_C",
            "Iron Ingot",
            ("Build_SmelterMk1_C",),
            (ItemAmount("Desc_OreIron_C", 30),),
            (ItemAmount("Desc_IronIngot_C", 30),),
        ),
    ),
    buildings=(),
    technologies=(),
    items=(
        Item("Desc_OreIron_C", "Iron Ore", is_raw_resource=True),
        Item("Desc_IronIngot_C", "Iron Ingot"),
    ),
)


def _map_client(tmp_path: Path, **options) -> TestClient:
    (tmp_path / "a.sav").write_bytes(b"save")
    smelter = PlacementRecord(
        building_id="Build_SmelterMk1_C",
        position=Coordinates(x=0, y=0),
        recipe_id="Recipe_IngotIron_C",
        object_id="smelter",
    )
    miner = PlacementRecord(
        building_id="Build_MinerMk1_C",
        position=Coordinates(x=500, y=0),
        resource_node_id="node_taken",
        object_id="miner",
    )
    state = SaveState(
        header=_header("alfa"),
        placements=(smelter, miner),
        graph=ProductionGraph(nodes=(), flows=()),
        links=(TransportLink(source_id="miner", target_id="smelter", carrier="belt"),),
    )
    nodes = (
        ResourceNode("node_taken", "Desc_OreIron_C", Purity.PURE, Coordinates(x=500, y=0)),
        ResourceNode("node_free", "Desc_OreIron_C", Purity.IMPURE, Coordinates(x=90_000, y=0)),
    )
    live = LiveContext(_MAP_KB, nodes, save_directory=str(tmp_path), load_save=lambda path: state)
    return TestClient(create_app(live=live, answer=lambda *args, **kwargs: None, **options))


def test_the_map_shows_each_factory_what_feeds_it_and_every_node(tmp_path) -> None:
    body = _map_client(tmp_path).get("/api/map").json()

    (site,) = body["sites"]
    assert (site["id"], site["x"], site["buildings"]) == ("site_1", 250, 2)
    assert site["main"]["id"] == "Desc_IronIngot_C"
    assert site["inputs"] == [
        {"id": "Desc_OreIron_C", "name": "Iron Ore", "icon": None, "per_minute": 30.0}
    ]
    assert [o["id"] for o in site["outputs"]] == ["Desc_IronIngot_C"]
    assert site["feeds"] == [{"node": "node_taken", "approximate": False}]
    assert [(n["id"], n["purity"], n["occupied"]) for n in body["nodes"]] == [
        ("node_taken", "pure", True),
        ("node_free", "impure", False),
    ]
    assert (body["image"], body["tiles"], body["world"]) == (None, None, None)


def test_with_a_map_picture_the_map_says_where_it_lies(tmp_path) -> None:
    picture = tmp_path / "map.jpg"
    picture.write_bytes(b"\xff\xd8")

    body = _map_client(tmp_path, map_image=picture).get("/api/map").json()

    assert body["image"] == "/map-image/map.jpg"
    assert body["tiles"] is None
    assert body["world"] == {
        "min_x": -324_698.832031,
        "min_y": -375_000.0,
        "max_x": 425_301.832031,
        "max_y": 375_000.0,
    }


def test_with_map_tiles_the_map_says_where_they_are(tmp_path) -> None:
    tiles = MapTiles(tmp_path, 512, 1, ((625, 625), (1250, 1250)))

    body = _map_client(tmp_path, map_tiles=tiles).get("/api/map").json()

    assert body["tiles"] == {
        "url": "/map-tiles",
        "size": 512,
        "overlap": 1,
        "levels": [[625, 625], [1250, 1250]],
    }
    assert body["image"] is None
    assert body["world"]["min_x"] == -324_698.832031
