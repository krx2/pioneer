# Pioneer — Implementation Plan

> Companion to [architecture.md](architecture.md). This document sequences the build so that
> **every module is developed and tested in isolation**, and the whole system is only wired
> together — modules reduced to plain function calls behind one orchestrator — in the very last
> stage. Read architecture.md first for the target shape; this document is about *how to get there
> without ever needing two unfinished modules at once*.

## Methodology: contracts first, isolated sprints, integrate last

The one thing every module needs is a **frozen data contract** — the shape of its inputs and
outputs (Stage 1 below). Once contracts exist, each module becomes a self-contained sprint:

1. **A module's tests never import another module's code.** Anything a module would normally
   receive from elsewhere (recipe data, a save file, an LLM response, another module's output) is
   supplied as a hand-written **fixture** conforming to the Stage 1 contract — a literal JSON/YAML
   file or an in-test constant. This is what makes a module "independent": you can write and fully
   test the Production Planner before the Knowledge Base has a single line of real data in it.
2. **A module's production code never imports the orchestrator, the API layer, or another
   in-progress module.** Its public surface is a small set of pure functions taking
   contract-shaped data in, returning contract-shaped data out.
3. **Order is a convenience, not a requirement.** Because every module only depends on Stage 1's
   contracts, they can technically be built in any order or in parallel. The order below is
   suggested purely because once an earlier module is *actually finished*, you can optionally point
   a later module's manual tests at the real thing instead of a fixture for a confidence boost —
   but this is never required to call the later module "done." A module is complete when it passes
   its own fixture-based tests, full stop.
4. **Nothing gets "wired live" until the final stage.** No module reaches out to the dedicated
   server, calls another module, or renders through a shared app shell before Stage 16. Every
   module ships as a standalone, independently runnable/testable unit up to that point.
5. **The LLM Orchestrator is not "a module" in the same sense — it *is* the integration.** Its
   entire job is calling the other modules' functions and routing between them, so building it for
   real is inseparable from Stage 16. You may sketch its intent-routing logic earlier against
   canned/fixture module outputs if you want, but treat that as a prototype, not the real thing.

### Practical tips for keeping modules decoupled

- Put shared contracts in their own package (e.g. `contracts/`) containing **only** data
  definitions (dataclasses / typed structures), zero logic. Every module imports from `contracts/`
  and nothing else in the codebase.
- Give each module its own directory and its own test suite: `knowledge_base/`, `verifier/`,
  `production_planner/`, etc. If a module's test file needs to reach into another module's
  directory to pass, that's a signal the contract leaked and needs tightening.
- Keep a `fixtures/` folder per module with the synthetic data it needs (a handful of recipes, a
  tiny sample `.sav`-shaped object, a handful of resource nodes). These fixtures are what make the
  module testable without the real game, the real server, or any other module.
- Don't reach for dependency injection or interfaces to fake other modules — you don't need that
  ceremony. Just don't call other modules' code from within a module's own package or tests.

---

## Stage 0 — Project scaffolding

**Goal:** a repo that runs, tests, and lints, with nothing domain-specific in it yet.

**Deliverables:**
- Language/runtime choice and package layout — one top-level package per module, plus `contracts/`.
- Test runner + linter wired up.
- A `config` mechanism for secrets (LLM API key, dedicated server address) — never hardcoded.

**Done when:** `<test command>` runs green on an empty test suite, `<lint command>` runs clean.

---

## Stage 1 — Shared contracts

**Goal:** freeze the data shapes every module will read or write, so every later stage can be
built against a fixture instead of another module's code. This is the only piece of the system
every module is allowed to depend on.

**Deliverables:** plain data definitions (no logic) for:
- `Recipe`, `Building`, `Technology` (produced/consumed by Knowledge Base, consumed by Planner, Q&A)
- `ProductionGraph` (nodes = recipes/machines, edges = material flow) — produced by Planner and
  Save Parser, consumed by Verifier, Expansion Advisor, Anomaly Detector, Graph presentation
- `ResourceNode` (type, coordinates, purity) — produced by Resource DB, consumed by Location
  Advisor, Map presentation
- `PlacementRecord` (building type + coordinates, from a save) — produced by Save Parser, consumed
  by Location Advisor, Anomaly Detector
- `GameState` (progress, phase) — produced by Server Client
- `ChangeSet` (extend/add instructions) — produced by Expansion Advisor
- `AnomalyRecord` (resource/location/severity) — produced by Anomaly Detector
- `RankedLocation` — produced by Location Advisor
- `ResponseArtifact` (the Chat/Graph/Map payload a single assistant turn produces, plus a feedback
  field) — produced by the Orchestrator (Stage 16), consumed by all three Presentation modules and
  by the Verification/Feedback module
- `Intent` (the classification the Orchestrator produces internally) — only needed at Stage 16,
  but worth sketching here while the other shapes are fresh

**Done when:** every module below can be described purely in terms of "takes a `<Contract>`,
returns a `<Contract>`" without referencing any other module by name.

**Note:** contracts are allowed to evolve — if Stage 9 discovers Stage 1's `ProductionGraph` is
missing a field, fix it in `contracts/` and patch the (few, fixture-based) tests that touched it.
That's cheap precisely because nothing else has been wired together yet.

---

The following modules (Stages 2–15) can be built **in any order**, each independently, each tested
against its own fixtures per the methodology above. A sensible solo-dev order is suggested (roughly
simplest/leaf-first), but no stage here is blocked on another finishing first.

## Stage 2 — Knowledge base (module)

**Goal:** a queryable data set of recipes, buildings, and technologies.

**Contract:** produces `Recipe` / `Building` / `Technology` (Stage 1).

**Deliverables:**
- Loader that ingests recipe/building/technology data (from game files or a hand-curated export)
  into the Stage 1 shapes.
- Pure lookup functions: recipe(s) for output X, building for a recipe, prerequisites for a
  recipe/building.

**Test fixtures:** a small hand-curated recipe set (10-20 recipes covering the iron chain examples
from the source deck is enough) — no need to ingest the full game data set to consider this module
done; that can grow later without touching any other module.

**Done when:** "what recipes produce Reinforced Iron Plate" returns correct, tested answers against
the fixture set, no other module involved.

---

## Stage 3 — Resource / map database (module)

**Goal:** a data set of resource node locations and purity.

**Contract:** produces `ResourceNode` (Stage 1).

**Deliverables:**
- Loader (hand-curated fixture to start; full map data can be filled in later).
- Lookup functions: nearest node(s) of type X to a coordinate, unclaimed nodes of type X (given a
  fixture list of "claimed" coordinates — not a real save, just test data).

**Done when:** "nearest pure iron node to (x, y)" returns correct, tested output against the
fixture set.

---

## Stage 4 — Verifier (module)

**Goal:** the shared arithmetic core: machine counts, throughput balance, power balance, distance.

**Contract:** consumes `ProductionGraph`, produces balance/power results; consumes two coordinates,
produces a distance.

**Deliverables:**
- `balance(graph) -> surplus/deficit per resource`
- `machine_count(recipe, target_rate) -> int`
- `power_balance(graph) -> surplus/deficit`
- `distance(a, b) -> float`

**Test fixtures:** hand-built small `ProductionGraph` fixtures (e.g. the iron ingot → iron rod
chain from the source deck) with known-correct expected results — don't call the Planner or Save
Parser to generate them, write them by hand.

**Done when:** every fixture graph's numbers match hand-calculated expected values.

---

## Stage 5 — Save file (`.sav`) parser (module)

**Goal:** read a Satisfactory save file into `ProductionGraph` + `PlacementRecord` shapes.

**Contract:** produces `ProductionGraph` and `PlacementRecord` (Stage 1).

**Deliverables:**
- Parser for the `.sav` format (or the relevant subset).
- Mapping from parsed data into the Stage 1 shapes. Recipe IDs are resolved against a **fixture**
  recipe list for this module's own tests — not a live call into the Stage 2 module.

**Test fixtures:** one or two real (or hand-constructed) sample `.sav` files with known contents.

**Done when:** parsing a fixture save file produces a `ProductionGraph` whose shape is correct
against the known contents — you may optionally run the real Verifier over it once Stage 4 exists,
as a bonus sanity check, but it's not required to call this module done.

---

## Stage 6 — Dedicated server API client (module)

**Goal:** pull live game state: progress, phase, session info.

**Contract:** produces `GameState` (Stage 1).

**Deliverables:**
- Client for the dedicated server's API.
- Explicit typed "unavailable" result when the server can't be reached (per the graceful-
  degradation invariant in architecture.md §7) — this must never be a raw exception.

**Test fixtures:** mocked HTTP responses (both success and unreachable-server cases).

**Done when:** the client returns normalized state against a mocked server, and a typed
"unavailable" result when the mock simulates a dead connection.

---

## Stage 7 — Production Planner (module)

**Goal:** given a target output rate for resource X, produce a `ProductionGraph` from raw
resources to X via graph search over recipe data.

**Contract:** consumes a target rate + `Recipe` list, produces `ProductionGraph`.

**Deliverables:**
- BFS/graph-search implementation over `Recipe` data.
- Alternate-recipe awareness: expose the choice when multiple recipes produce the same output
  rather than silently picking one.

**Test fixtures:** the same small recipe fixture set from Stage 2 (copy it into this module's own
`fixtures/`, don't import Stage 2's loader) plus hand-verified expected output graphs.

**Done when:** "10/min of Reinforced Iron Plate" produces a correctly-shaped graph matching the
example chains in the source deck, checked by hand or against a fixture expected-output — no live
call to the Verifier required to pass this module's own tests.

---

## Stage 8 — Expansion Advisor (module)

**Goal:** given a new target and an *existing* production state, compute the minimal delta —
which factories to extend, what new stage to add — instead of planning from scratch.

**Contract:** consumes an existing `ProductionGraph` + a target "from-scratch" `ProductionGraph`,
produces a `ChangeSet`.

**Deliverables:**
- Diff logic between the two graphs.

**Test fixtures:** hand-built "existing factories" graph + hand-built "from scratch" graph (you can
copy a Stage 7 example output as a fixture, no live dependency), with a known-correct expected
`ChangeSet`.

**Done when:** given the fixture pair, the Advisor picks extension over rebuilding whenever it's
numerically sufficient — matching the worked example in architecture.md §5.

---

## Stage 9 — Location Advisor (module)

**Goal:** recommend where to place new buildings based on unclaimed resource deposits.

**Contract:** consumes `ResourceNode` list + `PlacementRecord` list, produces `RankedLocation` list.

**Deliverables:**
- Cross-reference to find unclaimed deposits.
- Ranking by purity + distance (using the Stage 4 `distance()` signature, but callable with a
  fixture/stub distance function in this module's own tests if Stage 4 isn't finished yet).

**Test fixtures:** a small hand-built resource-node list + placement list.

**Done when:** given the fixtures, a ranked candidate list comes back excluding claimed nodes.

---

## Stage 10 — Anomaly Detector (module)

**Goal:** scan a production state for gaps, power blackouts, and congestion.

**Contract:** consumes `ProductionGraph`, produces `AnomalyRecord` list.

**Deliverables:**
- Classification of balance/power deficits into anomaly records (resource, location if derivable,
  severity).

**Test fixtures:** a deliberately-broken hand-built graph (e.g. under-provisioned smelters).

**Done when:** the fixture produces the expected anomaly list.

---

## Stage 11 — Q&A Engine / RAG (module)

**Goal:** answer free-form game-mechanics questions via retrieval-augmented generation. First LLM
touchpoint in the system, but still fully independent.

**Contract:** consumes a question + a text corpus, produces an answer + citations.

**Deliverables:**
- Indexing/embedding pipeline over a text corpus.
- Retrieval + answer-synthesis call to the LLM, scoped to rephrase retrieved content only.
- The LLM call goes through `pioneer.config.Settings` (`llm_base_url` + `llm_model`) against a
  local, OpenAI-compatible endpoint — this is the point where the specific backend (Ollama,
  llama.cpp, vLLM, ...) gets picked; nothing before this stage needs to care.

**Test fixtures:** a small hand-written corpus (a few recipes + a few wiki-style paragraphs) — not
the full Knowledge Base.

**Done when:** a handful of known questions against the fixture corpus get answers traceable to
specific retrieved passages.

---

## Stage 12 — Chat presentation (module)

**Goal:** render a text response.

**Contract:** consumes the `chat` field of a `ResponseArtifact` (Stage 1), renders/returns it via
UI or API.

**Test fixtures:** hand-written `ResponseArtifact.chat` samples — no orchestrator needed.

**Done when:** fixture chat payloads render correctly through a real interface.

---

## Stage 13 — Graph presentation (module)

**Goal:** render a `ProductionGraph` as an interactive D3.js node/flow diagram.

**Contract:** consumes `ProductionGraph` (+ a new/existing flag per node, per architecture.md §5).

**Test fixtures:** Stage 7/8 example outputs, copied in as static fixture JSON.

**Done when:** a fixture graph renders with new-vs-existing nodes visually distinguished.

---

## Stage 14 — Map presentation (module)

**Goal:** render resource nodes, existing buildings, and recommended locations on the static map.

**Contract:** consumes `ResourceNode` list, `PlacementRecord` list, `RankedLocation` list.

**Test fixtures:** Stage 3/9 example outputs, copied in as static fixture JSON.

**Done when:** a fixture recommendation renders as pins alongside fixture existing placements.

---

## Stage 15 — Verification & feedback scoring (module)

**Goal:** the scoring functions from architecture.md §6, built as pure functions over a
`ResponseArtifact`, independent of whether the Orchestrator or Presentation is live yet.

**Contract:** consumes a `ResponseArtifact`, produces a score record (qualitative 1-5, pass/fail +
error %, or mixed, per channel).

**Deliverables:**
- RAG-consistency check function (Chat channel).
- Distance-from-optimum function (Graph channel) — can call the real Stage 4 Verifier once it
  exists, since that's just calling a finished function library, not a live integration.
- Distance/purity check + LLM-as-a-judge hook (Map channel).
- Feedback-capture schema/storage (thumbs up/down, "did you apply this", "did you build here"),
  keyed to a specific `ResponseArtifact`, not a session.

**Test fixtures:** hand-built `ResponseArtifact` samples per channel, with known-correct expected
scores.

**Done when:** each fixture artifact produces the expected score via its scoring function, with no
dependency on a live orchestrator or rendered UI.

---

## Stage 16 — Integration: LLM Orchestrator + final wiring

**Goal:** the one stage where modules stop being standalone and become plain function calls inside
a single application. Everything above is finished and independently proven; this stage only
plumbs it together.

**Deliverables:**
- Intent classification (new plan / expand existing / locate factory / diagnose problem / general
  question).
- Tool-calling registration exposing every Stage 2–11 module's real function as a callable tool.
- The routing loop: classify intent → call module(s) → run Stage 4 Verifier / Stage 15 scoring on
  the result → compose a `ResponseArtifact`.
- Intent classification and tool-call routing go through the same local, OpenAI-compatible LLM
  endpoint as Stage 11 (`pioneer.config.Settings`) — reuse that connection, don't stand up a
  second LLM client.
- Replace every fixture used by Presentation (Stages 12–14) with real Orchestrator output.
- Replace the fixture recipe/corpus data used during isolated development with the real Stage 2
  Knowledge Base and Stage 6 Server Client / Stage 5 Save Parser, end to end.
- End-to-end tests (the example flow in architecture.md §5, run for real) become the top-level
  confidence check, on top of — not instead of — every module's own fixture-based test suite.

**Done when:** "I want to produce 10/min of X" goes in through Chat and comes back out as a
verified Chat + Graph + Map response, built entirely from real module calls, with no fixtures left
standing in for a finished module.

---

## Explicitly deferred (out of scope per architecture.md §2)

Do not build these unless the scope changes:

- Multiplayer / non-dedicated-server play.
- Live save-file streaming (treat `.sav` as snapshot-only).
- Vehicle and train/rail logistics planning.
