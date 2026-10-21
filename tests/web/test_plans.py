"""Tests for the player's plan in the web app: kept on disk and read back, proposed with an answer,
accepted through the API, and handed to every later question of its conversation."""

import json

import pytest
from fastapi.testclient import TestClient

from pioneer.contracts import (
    ChangeAction,
    ChangeItem,
    Coordinates,
    Item,
    MaterialFlow,
    PlacementRecord,
    Plan,
    PlanTarget,
    ProductionGraph,
    ProductionNode,
    ResponseArtifact,
)
from pioneer.orchestrator import OrchestratorContext
from pioneer.web import create_app
from pioneer.web.conversations import ConversationStore
from pioneer.web.plans import history_summary, marks_from_json, plan_from_json, plan_to_json

_R1 = "11111111-1111-4111-8111-111111111111"
_R2 = "22222222-2222-4222-8222-222222222222"
_R3 = "33333333-3333-4333-8333-333333333333"

_GRAPH = ProductionGraph(
    nodes=(
        ProductionNode(
            node_id="node_Desc_IronPlate_C",
            recipe_id="Recipe_IronPlate_C",
            building_id="Build_ConstructorMk1_C",
            machine_count=3,
            is_existing=True,
            existing_machine_count=1,
            production_boost=1.5,
        ),
    ),
    flows=(
        MaterialFlow(
            item_id="Desc_IronIngot_C", amount_per_minute=90, target_node_id="node_Desc_IronPlate_C"
        ),
        MaterialFlow(
            item_id="Desc_IronPlate_C", amount_per_minute=60, source_node_id="node_Desc_IronPlate_C"
        ),
    ),
)
_PLAN = Plan(
    targets=(PlanTarget("Desc_IronPlate_C", 40),),
    graph=_GRAPH,
    recipe_choices=(("Desc_IronPlate_C", "Recipe_IronPlate_C"),),
    extends_factory=True,
    available_supply=(("Desc_IronIngot_C", 30.0),),
    baseline_machines=(("Recipe_IronPlate_C", 1.0),),
    baseline_buildings=(("Recipe_IronPlate_C", 1),),
)
_REVISED = Plan(
    targets=(PlanTarget("Desc_IronPlate_C", 80),),
    graph=_GRAPH,
    revision=2,
    changes=(
        ChangeItem(
            ChangeAction.RECONFIGURE,
            "Recipe_Alternate_IronPlate_C",
            2,
            "node_Desc_IronPlate_C",
            from_recipe_id="Recipe_IronPlate_C",
            already_built=1,
            building_id="Build_ConstructorMk1_C",
        ),
    ),
)


def test_a_plan_is_kept_as_json_and_read_back_as_it_was() -> None:
    for plan in (_PLAN, _REVISED):
        assert plan_from_json(plan_to_json(plan)) == plan


def test_a_plan_kept_before_progress_was_tracked_reads_back_without_it() -> None:
    stored = plan_to_json(_REVISED)
    del stored["baseline_buildings"]
    del stored["changes"][0]["already_built"]

    plan = plan_from_json(stored)

    assert plan.baseline_buildings == ()
    assert plan.changes[0].already_built is None


@pytest.mark.parametrize(
    "stored",
    [
        None,
        {},
        {**plan_to_json(_PLAN), "targets": 5},
        {**plan_to_json(_PLAN), "recipe_choices": [["Desc_IronPlate_C", "Recipe_IronPlate_C"]]},
        {
            **plan_to_json(_REVISED),
            "changes": [{**plan_to_json(_REVISED)["changes"][0], "action": "x"}],
        },
    ],
)
def test_anything_but_a_stored_plan_is_refused(stored) -> None:
    with pytest.raises(ValueError):
        plan_from_json(stored)


_ITEMS = (Item(item_id="Desc_IronPlate_C", name="Iron Plate"),)


def _client(
    tmp_path, results, seen: list, context: OrchestratorContext | None = None
) -> TestClient:
    """A client answering with `results` in turn, noting the plan each question was asked with."""
    answers = iter(results)

    def answer(question, current, history, on_event=None):
        seen.append(current.plan)
        return next(answers)

    context = context or OrchestratorContext(items=_ITEMS)
    app = create_app(
        context=lambda: (context, "status"),
        answer=answer,
        conversations=ConversationStore(tmp_path),
    )
    return TestClient(app)


def _proposing(response_id: str, plan: Plan | None) -> ResponseArtifact:
    return ResponseArtifact(response_id=response_id, chat="Here.", graph=_GRAPH, plan_proposal=plan)


def test_a_proposed_plan_is_accepted_and_every_later_question_asked_with_it(tmp_path) -> None:
    seen: list = []
    client = _client(tmp_path, [_proposing(_R1, _PLAN), _proposing(_R2, _REVISED)], seen)

    first = client.post("/api/ask", json={"question": "40 plates"}).json()
    conversation_id = first["conversation_id"]
    accepted = client.post(f"/api/conversations/{conversation_id}/plan", json={"response_id": _R1})
    client.post("/api/ask", json={"question": "make it 80", "conversation_id": conversation_id})

    assert first["plan_proposal"] == {
        "revision": 1,
        "targets": [{"item_id": "Desc_IronPlate_C", "name": "Iron Plate", "rate_per_minute": 40}],
    }
    assert accepted.status_code == 200
    shown = accepted.json()["plan"]
    assert {key: shown[key] for key in ("revision", "targets")} == first["plan_proposal"]
    assert shown["response_id"] == _R1
    assert seen == [None, _PLAN]


def test_the_plan_says_how_far_the_save_has_built_it(tmp_path) -> None:
    """The plan's one stage adds 2 constructors to the 1 standing; 2 stand now, so 1 is built."""
    saved = OrchestratorContext(
        items=_ITEMS,
        existing_graph=ProductionGraph(nodes=(), flows=()),
        existing_placements=tuple(
            PlacementRecord(
                building_id="Build_ConstructorMk1_C",
                position=Coordinates(x=0, y=0),
                recipe_id="Recipe_IronPlate_C",
            )
            for _ in range(2)
        ),
    )
    with_save = _client(tmp_path, [_proposing(_R1, _PLAN)], [], saved)
    conversation_id = with_save.post("/api/ask", json={"question": "q"}).json()["conversation_id"]
    with_save.post(f"/api/conversations/{conversation_id}/plan", json={"response_id": _R1})
    without_save = _client(tmp_path, [], [])

    progress = with_save.get(f"/api/conversations/{conversation_id}/plan").json()["plan"][
        "progress"
    ]
    unknown = without_save.get(f"/api/conversations/{conversation_id}").json()["plan"]["progress"]

    assert progress == {
        "built": 1,
        "to_build": 2,
        "known": "all",
        "as_of": None,  # no `LiveContext` to say when the save was written
        "stages": [
            {
                "recipe_id": "Recipe_IronPlate_C",
                "recipe": "Recipe_IronPlate_C",
                "building": "Build_ConstructorMk1_C",
                "icon": None,
                "to_build": 2,
                "built": 1,
                "in_save": 1,
                "marked": 0,
            }
        ],
    }
    assert (unknown["known"], unknown["built"], unknown["stages"][0]["built"]) == ("none", 0, None)
    assert with_save.get(f"/api/conversations/{_R3}/plan").status_code == 404


def test_only_a_proposal_for_the_plan_as_it_stands_can_be_accepted(tmp_path) -> None:
    client = _client(
        tmp_path,
        [_proposing(_R1, _PLAN), _proposing(_R2, _REVISED), _proposing(_R3, _PLAN)],
        [],
    )
    conversation_id = client.post("/api/ask", json={"question": "q"}).json()["conversation_id"]
    for turn in (1, 2):
        client.post(
            "/api/ask", json={"question": "q", "conversation_id": conversation_id, "turn": turn}
        )

    def accept(response_id: str) -> int:
        return client.post(
            f"/api/conversations/{conversation_id}/plan", json={"response_id": response_id}
        ).status_code

    assert accept(_R2) == 409  # a revision of a plan the conversation doesn't have yet
    assert accept(_R1) == 200
    assert accept(_R1) == 200  # accepted already
    assert accept(_R3) == 409  # another first plan
    assert accept(_R2) == 200
    assert accept(_R1) == 409  # what it revised

    stored = client.get(f"/api/conversations/{conversation_id}").json()
    assert stored["plan"]["revision"] == 2
    assert stored["plan"]["response_id"] == _R2
    assert all("plan_proposal" not in turn for turn in stored["turns"])
    assert [turn["answer"]["plan_proposal"]["revision"] for turn in stored["turns"]] == [1, 2, 1]


def test_an_answer_without_a_proposal_or_conversation_has_no_plan_to_accept(tmp_path) -> None:
    client = _client(tmp_path, [_proposing(_R1, None)], [])
    conversation_id = client.post("/api/ask", json={"question": "q"}).json()["conversation_id"]

    def accept(conversation: str, response_id: str) -> int:
        return client.post(
            f"/api/conversations/{conversation}/plan", json={"response_id": response_id}
        ).status_code

    assert accept(conversation_id, _R1) == 404
    assert accept(conversation_id, _R2) == 404
    assert accept(_R3, _R1) == 404
    assert client.get(f"/api/conversations/{conversation_id}").json()["plan"] is None


def test_the_plan_outlives_a_restart(tmp_path) -> None:
    first = _client(tmp_path, [_proposing(_R1, _PLAN)], [])
    conversation_id = first.post("/api/ask", json={"question": "q"}).json()["conversation_id"]
    first.post(f"/api/conversations/{conversation_id}/plan", json={"response_id": _R1})

    seen: list = []
    again = _client(tmp_path, [_proposing(_R2, None)], seen)
    again.post("/api/ask", json={"question": "and now?", "conversation_id": conversation_id})

    assert seen == [_PLAN]


def test_a_torn_plan_is_answered_without(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    client = _client(tmp_path, [_proposing(_R1, _PLAN), _proposing(_R2, None)], seen := [])
    conversation_id = client.post("/api/ask", json={"question": "q"}).json()["conversation_id"]
    client.post(f"/api/conversations/{conversation_id}/plan", json={"response_id": _R1})
    path = tmp_path / f"{conversation_id}.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace('"extends_factory"', '"torn"'), encoding="utf-8"
    )

    client.post("/api/ask", json={"question": "q", "conversation_id": conversation_id})

    assert store.plan(conversation_id) is not None  # still there, just unreadable as a plan
    assert seen == [None, None]
    assert client.get(f"/api/conversations/{conversation_id}").json()["plan"] is None


def test_without_a_conversation_store_no_plan_is_offered() -> None:
    context = OrchestratorContext()
    app = create_app(
        context=lambda: (context, "status"), answer=lambda *args, **kwargs: _proposing(_R1, _PLAN)
    )

    body = TestClient(app).post("/api/ask", json={"question": "q"}).json()

    assert body["plan_proposal"] is None


def _accepted(tmp_path, *plans: Plan, seen: list | None = None) -> tuple[TestClient, str]:
    """A conversation that asked once for each of `plans` -- "first", "second", ... -- and accepted
    each, and the client to ask it more: answering with no plan from then on."""
    ids = (_R1, _R2, _R3)[: len(plans)]
    results = [_proposing(i, plan) for i, plan in zip(ids, plans, strict=True)]
    client = _client(
        tmp_path,
        [*results, *(_proposing(_R3, None) for _ in range(3))],
        seen if seen is not None else [],
    )
    conversation_id = None
    for turn, (response_id, question) in enumerate(
        zip(ids, ("first", "second", "third")[: len(ids)], strict=True)
    ):
        asked = {"question": question, "conversation_id": conversation_id, "turn": turn}
        conversation_id = client.post("/api/ask", json=asked).json()["conversation_id"]
        client.post(f"/api/conversations/{conversation_id}/plan", json={"response_id": response_id})
    return client, conversation_id


def test_a_stage_the_player_marks_built_counts_as_built_also_for_the_model(tmp_path) -> None:
    seen: list = []
    client, conversation_id = _accepted(tmp_path, _PLAN, seen=seen)
    marks = f"/api/conversations/{conversation_id}/plan/marks"

    marked = client.post(marks, json={"recipe_id": "Recipe_IronPlate_C", "built": 2}).json()
    client.post("/api/ask", json={"question": "how far?", "conversation_id": conversation_id})
    cleared = client.post(marks, json={"recipe_id": "Recipe_IronPlate_C", "built": 0}).json()

    (stage,) = marked["plan"]["progress"]["stages"]
    assert (stage["built"], stage["in_save"], stage["marked"]) == (2, None, 2)
    assert marked["plan"]["progress"]["known"] == "all"  # no save, but the mark tells
    assert seen[-1].marked_built == (("Recipe_IronPlate_C", 2),)
    assert cleared["plan"]["progress"]["stages"][0]["built"] is None


def test_only_a_stage_of_the_plan_can_be_marked(tmp_path) -> None:
    client, conversation_id = _accepted(tmp_path, _PLAN)
    nothing, _ = _accepted(tmp_path / "other")

    other_recipe = client.post(
        f"/api/conversations/{conversation_id}/plan/marks",
        json={"recipe_id": "Recipe_Rotor_C", "built": 1},
    )
    negative = client.post(
        f"/api/conversations/{conversation_id}/plan/marks",
        json={"recipe_id": "Recipe_IronPlate_C", "built": -1},
    )
    no_plan = nothing.post(
        f"/api/conversations/{_R3}/plan/marks", json={"recipe_id": "Recipe_IronPlate_C", "built": 1}
    )

    assert (other_recipe.status_code, negative.status_code, no_plan.status_code) == (422, 422, 404)


def test_marks_stay_with_the_plan_through_its_revisions(tmp_path) -> None:
    client = _client(tmp_path, [_proposing(_R1, _PLAN), _proposing(_R2, _REVISED)], [])
    conversation_id = client.post("/api/ask", json={"question": "q"}).json()["conversation_id"]
    client.post(f"/api/conversations/{conversation_id}/plan", json={"response_id": _R1})
    client.post(
        f"/api/conversations/{conversation_id}/plan/marks",
        json={"recipe_id": "Recipe_IronPlate_C", "built": 1},
    )
    client.post("/api/ask", json={"question": "q", "conversation_id": conversation_id, "turn": 1})
    client.post(f"/api/conversations/{conversation_id}/plan", json={"response_id": _R2})

    plan = client.get(f"/api/conversations/{conversation_id}/plan").json()["plan"]

    assert plan["revision"] == 2
    assert plan["progress"]["stages"][0]["marked"] == 1


def test_the_revisions_are_kept_newest_first_with_what_asked_for_them(tmp_path) -> None:
    client, conversation_id = _accepted(tmp_path, _PLAN, _REVISED)

    history = client.get(f"/api/conversations/{conversation_id}/plan").json()["plan"]["history"]

    assert [(entry["revision"], entry["question"]) for entry in history] == [
        (2, "second"),
        (1, "first"),
    ]
    assert history[0]["response_id"] == _R2
    assert history[0]["changes"] == [
        {
            "action": "reconfigure",
            "recipe": "Recipe_Alternate_IronPlate_C",
            "from_recipe": "Recipe_IronPlate_C",
            "building": "Build_ConstructorMk1_C",
            "machines": 2,
            "already_built": 1,
        }
    ]
    assert history[1]["changes"] == []


def test_a_plan_kept_before_revisions_were_noted_is_its_own_first_revision(tmp_path) -> None:
    client = _client(tmp_path, [_proposing(_R1, _PLAN), _proposing(_R2, _REVISED)], [])
    conversation_id = client.post("/api/ask", json={"question": "first"}).json()["conversation_id"]
    client.post(f"/api/conversations/{conversation_id}/plan", json={"response_id": _R1})
    path = tmp_path / f"{conversation_id}.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    del stored["plan"]["history"]
    path.write_text(json.dumps(stored), encoding="utf-8")

    def revisions() -> list:
        plan = client.get(f"/api/conversations/{conversation_id}/plan").json()["plan"]
        return [(entry["revision"], entry["question"]) for entry in plan["history"]]

    assert revisions() == [(1, "first")]
    client.post(
        "/api/ask", json={"question": "second", "conversation_id": conversation_id, "turn": 1}
    )
    client.post(f"/api/conversations/{conversation_id}/plan", json={"response_id": _R2})
    assert revisions() == [(2, "second"), (1, "first")]


def test_unreadable_history_entries_and_marks_are_left_out() -> None:
    entries = [{"revision": "x"}, {"changes": []}, {"revision": 1, "changes": [{"action": "?"}]}]

    assert history_summary([*entries, {"revision": 3}], {}) == [
        {"revision": 3, "accepted_at": None, "question": None, "response_id": None, "changes": []}
    ]
    assert history_summary("nope", {}) == []
    assert marks_from_json({"a": 2, "b": "3", "c": True, "d": 0, "e": -1}) == (("a", 2.0),)
    assert marks_from_json(None) == ()
