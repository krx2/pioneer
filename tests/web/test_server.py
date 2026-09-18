"""Tests for the web app through FastAPI's TestClient and a fake `answer`: no model, no socket."""

import json

from fastapi.testclient import TestClient

from pioneer.contracts import (
    Building,
    Coordinates,
    FactorySite,
    Feedback,
    Item,
    ItemAmount,
    MaterialFlow,
    PlacementRecord,
    ProductionGraph,
    ProductionNode,
    Purity,
    RankedLocation,
    Recipe,
    ResourceNode,
    ResponseArtifact,
)
from pioneer.orchestrator import OrchestratorContext, OrchestratorUnavailable
from pioneer.verification_feedback import (
    ChatScore,
    GraphScore,
    JsonlFeedbackStore,
    MapScore,
    ResponseLog,
    ResponseScore,
)
from pioneer.web import create_app

_GRAPH = ProductionGraph(
    nodes=(
        ProductionNode(
            node_id="node_smelter",
            recipe_id="Recipe_IngotIron_C",
            building_id="Build_SmelterMk1_C",
            machine_count=2,
        ),
    ),
    flows=(
        MaterialFlow(item_id="Desc_OreIron_C", amount_per_minute=60, target_node_id="node_smelter"),
    ),
)
_IRON_NODE = ResourceNode(
    node_id="node_iron",
    item_id="Desc_OreIron_C",
    purity=Purity.PURE,
    position=Coordinates(x=1000, y=2000),
)
_COPPER_NODE = ResourceNode(
    node_id="node_copper",
    item_id="Desc_OreCopper_C",
    purity=Purity.NORMAL,
    position=Coordinates(x=-5000, y=0),
)
_SITE = RankedLocation(
    resource_node_id="node_iron",
    position=_IRON_NODE.position,
    purity=Purity.PURE,
    distance_to_reference=100.0,
    score=1.5,
)


def _artifact(**channels) -> ResponseArtifact:
    return ResponseArtifact(
        response_id="r1", chat="Build two <Smelters>.", question="what now?", **channels
    )


def _client(result, *, context=None, **options) -> tuple[TestClient, list[str]]:
    asked: list[str] = []

    def answer(question: str, current: OrchestratorContext, history):
        asked.append(question)
        return result

    loaded = (context or OrchestratorContext(), "291 recipes | no save loaded")
    app = create_app(context=lambda: loaded, answer=answer, **options)
    return TestClient(app), asked


def _ask(client: TestClient):
    return client.post("/api/ask", json={"question": "  what now?  "})


def test_the_chat_page_says_what_data_is_loaded() -> None:
    client, _ = _client(_artifact())

    page = client.get("/")

    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "291 recipes | no save loaded" in page.text
    assert "/api/ask" in page.text


def test_asking_returns_the_answer_rendered_and_escaped() -> None:
    client, asked = _client(_artifact())

    body = _ask(client).json()

    assert asked == ["what now?"]
    assert body["response_id"] == "r1"
    assert body["status"] == "291 recipes | no save loaded"
    assert "&lt;Smelters&gt;" in body["chat_html"]
    assert (body["graph_url"], body["map_url"], body["verification"]) == (None, None, None)


def test_a_blank_question_is_rejected_without_asking() -> None:
    client, asked = _client(_artifact())

    assert client.post("/api/ask", json={"question": ""}).status_code == 422
    assert client.post("/api/ask", json={"question": "   "}).status_code == 422
    assert asked == []


def test_an_unavailable_orchestrator_is_a_503_with_its_reason() -> None:
    client, _ = _client(OrchestratorUnavailable(reason="could not reach LLM endpoint"))

    response = _ask(client)

    assert response.status_code == 503
    assert response.json()["detail"] == "could not reach LLM endpoint"


def test_a_planning_answer_links_to_its_rendered_graph() -> None:
    client, _ = _client(_artifact(graph=_GRAPH))

    body = _ask(client).json()
    page = client.get(body["graph_url"])

    assert body["graph_url"] == "/responses/r1/graph"
    assert page.status_code == 200
    assert "node_smelter" in page.text


def test_channels_an_answer_does_not_have_and_unknown_answers_are_404() -> None:
    client, _ = _client(_artifact())
    _ask(client)

    assert client.get("/responses/r1/graph").status_code == 404
    assert client.get("/responses/r1/map").status_code == 404
    assert client.get("/responses/unknown/graph").status_code == 404


def test_a_map_shows_the_sites_their_resource_and_the_players_extractors() -> None:
    context = OrchestratorContext(
        resource_nodes=(_IRON_NODE, _COPPER_NODE),
        existing_placements=(
            PlacementRecord(
                building_id="Build_MinerMk1_C",
                position=Coordinates(x=0, y=0),
                resource_node_id="node_elsewhere",
            ),
            PlacementRecord(building_id="Build_ConstructorMk1_C", position=Coordinates(x=9, y=9)),
        ),
    )
    client, _ = _client(_artifact(map_locations=(_SITE,)), context=context)

    page = client.get(_ask(client).json()["map_url"]).text

    assert "#1 Ore Iron, pure, score 1.50" in page
    assert "Ore Iron (pure)" in page
    assert "Ore Copper" not in page
    assert "Miner Mk1" in page
    assert "Constructor Mk1" not in page


def test_answers_are_verified_and_logged(tmp_path) -> None:
    score = ResponseScore(chat=ChatScore(grounded_fraction=0.8, consistent=True))
    log_path = tmp_path / "responses.jsonl"
    client, _ = _client(
        _artifact(), verify=lambda artifact, context: score, response_log=ResponseLog(log_path)
    )

    body = _ask(client).json()

    assert body["verification"] == {
        "chat": {
            "grounded_fraction": 0.8,
            "consistent": True,
            "ungrounded_numbers": [],
            "judge": None,
        }
    }
    (line,) = log_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert (record["response_id"], record["question"]) == ("r1", "what now?")
    assert record["score"]["chat"]["grounded_fraction"] == 0.8


def test_the_verification_summary_says_what_failed() -> None:
    score = ResponseScore(
        chat=ChatScore(grounded_fraction=0.3, consistent=False, ungrounded_numbers=("95", "4")),
        graph=GraphScore(
            balanced=True,
            power_ok=False,
            deviation_from_optimum_pct=33.3333,
            passed=False,
            power_draw_mw=20.04,
            available_power_mw=18.0,
        ),
        map=(
            MapScore(
                resource_node_id="node_iron",
                position_ok=True,
                purity_ok=True,
                distance_ok=None,
                still_free=False,
                judge_verdict=None,
                passed=False,
            ),
        ),
    )
    client, _ = _client(
        _artifact(graph=_GRAPH, map_locations=(_SITE,)), verify=lambda artifact, context: score
    )

    checks = _ask(client).json()["verification"]

    assert checks["chat"]["ungrounded_numbers"] == ["95", "4"]
    assert checks["graph"] == {
        "passed": False,
        "balanced": True,
        "power_ok": False,
        "power_draw_mw": 20.0,
        "spare_power_mw": 18.0,
        "over_optimum_pct": 33.3,
    }
    assert checks["map"]["problems"] == ["already taken"]


def test_each_answer_is_built_verified_and_mapped_on_the_context_of_its_time() -> None:
    """A newer save arrives between two questions: the status follows it, and the first answer's
    map still shows the extractor it was answered with."""
    before = OrchestratorContext(
        resource_nodes=(_IRON_NODE,),
        existing_placements=(
            PlacementRecord(
                building_id="Build_MinerMk1_C",
                position=Coordinates(x=0, y=0),
                resource_node_id="node_old",
            ),
        ),
    )
    after = OrchestratorContext(resource_nodes=(_IRON_NODE,))
    sources = [(before, "save one"), (before, "save one"), (after, "save two")]
    seen: list[tuple[str, OrchestratorContext]] = []

    def answer(question, current, history):
        seen.append(("answer", current))
        return _artifact(map_locations=(_SITE,))

    def verify(artifact, current):
        seen.append(("verify", current))
        return ResponseScore()

    app = create_app(context=lambda: sources.pop(0), answer=answer, verify=verify)
    client = TestClient(app)  # creating the app took the first
    map_url = _ask(client).json()["map_url"]

    assert seen == [("answer", before), ("verify", before)]
    assert "save two" in client.get("/api/status").json()["status"]
    assert "Miner Mk1" in client.get(map_url).text


def test_feedback_is_merged_field_by_field_and_kept(tmp_path) -> None:
    path = tmp_path / "feedback.jsonl"
    client, _ = _client(_artifact(), feedback_store=JsonlFeedbackStore(path))
    _ask(client)

    client.post("/api/responses/r1/feedback", json={"thumbs_up": True})
    merged = client.post("/api/responses/r1/feedback", json={"qualitative_score": 4}).json()

    assert merged == {
        "thumbs_up": True,
        "applied_plan": None,
        "built_at_location": None,
        "qualitative_score": 4,
    }
    assert JsonlFeedbackStore(path).get("r1") == Feedback(thumbs_up=True, qualitative_score=4)


def test_feedback_is_validated_and_needs_a_known_answer() -> None:
    client, _ = _client(_artifact())
    _ask(client)

    out_of_range = client.post("/api/responses/r1/feedback", json={"qualitative_score": 9})
    unknown = client.post("/api/responses/unknown/feedback", json={"thumbs_up": True})

    assert out_of_range.status_code == 422
    assert unknown.status_code == 404


def test_feedback_is_taken_for_answers_from_before_a_restart(tmp_path) -> None:
    log = ResponseLog(tmp_path / "responses.jsonl")
    log.append(ResponseArtifact(response_id="earlier", chat="old answer"), None)
    client, _ = _client(_artifact(), response_log=log)

    response = client.post("/api/responses/earlier/feedback", json={"built_at_location": True})

    assert response.status_code == 200
    assert client.get("/responses/earlier/map").status_code == 404  # its pages are gone


def test_an_answer_pointing_at_factories_gets_a_map_of_them() -> None:
    site = FactorySite(
        site_id="site_3",
        position=Coordinates(x=100, y=200),
        placements=(
            PlacementRecord(
                building_id="Build_SmelterMk1_C",
                position=Coordinates(x=100, y=200),
                recipe_id="Recipe_IngotIron_C",
            ),
        ),
    )
    client, _ = _client(_artifact(factory_sites=(site,)))

    body = _ask(client).json()
    page = client.get(body["map_url"]).text

    assert body["map_url"] == "/responses/r1/map"
    assert "site_3: Ingot Iron" in page


def test_the_conversation_so_far_is_passed_on_and_bounded() -> None:
    histories = []

    def answer(question, current, history):
        histories.append(history)
        return _artifact()

    client = TestClient(create_app(context=lambda: (OrchestratorContext(), ""), answer=answer))
    turn = {"question": "how do I make plates?", "answer": "Use a Constructor."}

    body = client.post("/api/ask", json={"question": "and rods?", "history": [turn]}).json()
    too_long = client.post("/api/ask", json={"question": "q", "history": [turn] * 9})

    assert histories == [[("how do I make plates?", "Use a Constructor.")]]
    assert body["chat"] == "Build two <Smelters>."
    assert too_long.status_code == 422


def test_icons_are_served_and_put_into_the_chat_graph_and_map(tmp_path) -> None:
    for class_id in ("Desc_OreIron_C", "Desc_IronIngot_C", "Build_SmelterMk1_C"):
        (tmp_path / f"{class_id}.png").write_bytes(b"\x89PNG fake")
    context = OrchestratorContext(
        items=(
            Item(item_id="Desc_IronIngot_C", name="Iron Ingot"),
            Item(item_id="Desc_OreIron_C", name="Iron Ore", is_raw_resource=True),
        ),
        buildings=(
            Building(
                building_id="Build_SmelterMk1_C",
                name="Smelter",
                power_consumption_mw=4,
                input_slots=1,
                output_slots=1,
            ),
        ),
        recipes=(
            Recipe(
                recipe_id="Recipe_IngotIron_C",
                name="Iron Ingot",
                building_ids=("Build_SmelterMk1_C",),
                inputs=(ItemAmount("Desc_OreIron_C", 30),),
                outputs=(ItemAmount("Desc_IronIngot_C", 30),),
            ),
        ),
        resource_nodes=(_IRON_NODE,),
    )
    artifact = ResponseArtifact(
        response_id="r1",
        chat="Two Smelters make Iron Ingots. `Smelter`",
        graph=_GRAPH,
        map_locations=(_SITE,),
    )
    client, _ = _client(artifact, context=context, icon_dir=tmp_path)

    body = _ask(client).json()
    graph = client.get(body["graph_url"]).text
    map_page = client.get(body["map_url"]).text

    assert client.get("/icons/Desc_OreIron_C.png").content == b"\x89PNG fake"
    assert client.get("/icons/Desc_Unknown_C.png").status_code == 404
    assert client.get("/icons/..%2F..%2Fpyproject.png").status_code == 404
    assert body["chat_html"].count("/icons/Build_SmelterMk1_C.png") == 1  # not in the code span
    assert "/icons/Desc_IronIngot_C.png" in body["chat_html"]
    assert '"icon": "/icons/Desc_IronIngot_C.png"' in graph  # the recipe shows what it makes
    assert '<image href="/icons/Desc_OreIron_C.png"' in map_page
    assert '"raw": true' in graph  # iron ore is mined


def test_without_an_icon_folder_no_icons_are_offered() -> None:
    client, _ = _client(_artifact(graph=_GRAPH))

    body = _ask(client).json()

    assert "<img" not in body["chat_html"]
    assert "/icons/" not in client.get(body["graph_url"]).text
    assert client.get("/icons/Desc_OreIron_C.png").status_code == 404
