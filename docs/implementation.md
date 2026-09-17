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
- Give each module its own directory under `src/pioneer/`: `knowledge_base/`, `verifier/`,
  `production_planner/`, etc. If a module's test file needs to reach into another module's
  directory to pass, that's a signal the contract leaked and needs tightening.
- Tests live under the top-level `tests/` tree, mirroring the module layout one-to-one:
  `tests/knowledge_base/`, `tests/verifier/`, etc. — not colocated inside `src/pioneer/<module>/`.
- Keep a `fixtures/` folder per module **inside its `tests/<module>/` directory**, not inside
  `src/pioneer/<module>/` — fixtures are test data, not something the module needs at runtime, so
  they have no business being bundled into the installable package. Resolve a fixture path
  relative to the test file itself (e.g. `Path(__file__).parent / "fixtures" / "..."`); since
  fixtures now live next to the tests that use them, there's no need to go through the package's
  `__file__` as a stable anchor. These fixtures are what make a module testable without the real
  game, the real server, or any other module. Where a module has a real committed data source
  worth checking against too (Knowledge Base's `docs/en-US.json`), add that as a *second*, bonus
  test file — the fixture-based suite stays the one that decides whether the module is "done."
- Don't reach for dependency injection or interfaces to fake other modules — you don't need that
  ceremony. Just don't call other modules' code from within a module's own package or tests.

---

## Stage 0 — Project scaffolding

**Goal:** a repo that runs, tests, and lints, with nothing domain-specific in it yet.

**Deliverables:**
- Language/runtime choice and package layout — one top-level package per module, plus `contracts/`.
- Test runner + linter wired up.
- A `config` mechanism for secrets (LLM API key, dedicated server address) — never hardcoded.

**Done when:** `pytest` runs green on an empty test suite, and `ruff check .` and
`ruff format --check .` run clean. Python 3.12, with `pytest` and `ruff` as the dev extras of
`pyproject.toml`. `.github/workflows/ci.yml` runs all three on every push, and
`tests/test_architecture.py` checks the dependency rule below rather than trusting it.

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

`contracts/` also holds the one thing shared by every module that talks to the outside world and
isn't data: `TransportError`, what an injected transport raises when a request couldn't complete at
all. Each such module used to declare its own.

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
- Loader that ingests recipe/building/technology data from the game's own `Docs.json`-style
  export (committed at `docs/en-US.json`) into the Stage 1 shapes.
- Pure lookup functions: recipe(s) for output X, building for a recipe, prerequisites for a
  recipe/building.
- Normalization of the export's own quirks into the units the rest of the project expects. The
  significant one: liquid and gas amounts are stored in litres (×1000 vs the m³ the game's UI
  shows), distinguishable only via the item descriptors' `mForm` field, so the loader indexes
  those and scales fluids back down. See loader.py's module docstring for the full list.
- Only *factory* recipes are kept — 291 of the export's 872; the rest are build-gun, Equipment
  Workshop and Craft Bench recipes no module can plan, place or count. Each `Item` records whether
  it's a raw resource (the export's `FGResourceDescriptor`s) and each `Recipe` whether the game
  presents it as an alternate ("Alternate: ..."). Recipes alone can't answer either question on
  real data: 1.0's Converter has recipes *producing* ores, so a planner that treats "has a
  recipe" as "crafted" walks ore -> ore -> ore in a circle.
- `find_items` resolves the in-game names a player (or the LLM) uses into item ids, and
  `resolve_item` picks the one item a name unambiguously means: an exact name, the same name with
  plurals ignored ("Screw" is Screws), or the only best match ("reinforced plates"). "Iron" names
  several items and resolves to none.
- Generators carry what they consume besides fuel and what they leave behind (`Building.fuels`,
  `Building.supplemental_per_minute_per_mw`): coal and nuclear plants need water — 45 and 240
  m³/min — and fuel rods leave waste. Counting only the fuel made a real save's 36 coal generators
  look like 1620 m³/min of spare water.
- Technologies carry how they're unlocked (`kind`: milestone, MAM, alternate, tutorial, custom)
  and their cost, and each recipe lists every technology that unlocks it (`unlockable_by`) — 19
  recipes have more than one way in. `unlock_order` puts the technologies a goal needs, with their
  prerequisites, in the order to get them.
- Belt and pipeline tiers (`transport_tiers`) come with their capacity per minute.

**Test fixtures:** a small hand-curated `Docs.json`-shaped export (`fixtures/mini_docs.json`)
covering the iron chain from the source deck, plus one alternate recipe and one schematic that
should be filtered out — no need to ingest the full game data set to consider this module done;
that can grow later without touching any other module. A second test file
(`tests/knowledge_base/test_real_docs.py`) runs the same kind of checks against the real,
committed `docs/en-US.json` as a bonus confidence check — proof the parsing logic holds up
against the actual, messy source data, not just the small stand-in — but it is not the bar this
module has to clear to be "done."

**Done when:** "what recipes produce Reinforced Iron Plate" returns correct, tested answers
against the fixture set, no other module involved.

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

**Real data:** saves don't store what a resource node yields or how pure it is, so the node data
ships with the project at `docs/resource_nodes.json`, next to the Knowledge Base export. It was
converted once from a community table — `sav_data/resourcePurity.py` in GreyHak/sat_sav_parse
(game version 1.2.0.0; GPL-3.0, itself extracted from SCIM), recorded in the file's own header —
since the default world's nodes never change. It is keyed by the same actor path names saves use:
both fixture saves match all 607 of its nodes at the same positions, and every extractor's
`mExtractableResource` names one of them. That reference is what the Location Advisor uses to tell
occupied deposits from free ones, and what extraction rates are computed from. Worlds generated
with 1.2's randomized node mode won't match it.

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
- Per placed building: its recipe (`mCurrentRecipe`), clock speed (`mCurrentPotential` — absent
  means 100%, since the game doesn't save defaults) and, for generators, fuel
  (`mCurrentFuelClass`). The existing-factory graph's `machine_count` is clock-scaled: effective
  machines at 100%, so it can be fractional.
- Standby (`mIsProductionPaused`, a `BoolProperty` in the newer tag format — see properties.py):
  a paused building makes, burns and draws nothing, so it's left out of the graph and of every
  placement-based sum. Somersloop boost (`mCurrentProductionBoost`) multiplies a building's output
  but not its input, and its power by the boost squared; the graph carries it as the node's
  clock-weighted `production_boost`. No fixture save has a boosted machine, so that property's
  name is inferred from its siblings rather than seen.
- What the player has unlocked: the schematic manager's `mPurchasedSchematics`, an
  `ArrayProperty` of class references (`SaveState.unlocked_technology_ids`).
- A save holds an object table per level; the persistent level's — the one with the buildings —
  is recognized by what follows it rather than by its size (see object_table.py): size thresholds
  alone missed a small early-game save.
- Saves from before 1.0 (header version 6, save version 22-25, e.g. Update 5) use an older chunk
  and body layout the parser doesn't read; loading one fails at decompression. Re-saving them in
  the current game fixes that.
- Not recoverable from a save, by design or by format: belt routing (the graph's `flows` stay
  empty — see production_graph.py), and resource node purity/type (resource node actors carry only
  `mResourcesLeft`), which has to come from Stage 3's static data instead.

**Test fixtures:** real sample `.sav` files with known contents — two developed factories
(`stal_mielec`, `wielka_polska_niesmiertelna`) and two small early-game ones (`alfa`, `tak`).

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

`server_client.transport.post_json` is the real HTTPS transport (standard library; it accepts the
server's self-signed certificate, as the game's own API docs require). The app queries the server
when `PIONEER_SERVER_HOST` and `PIONEER_SERVER_API_TOKEN` are set, and the model is told the result
either way.

---

## Stage 7 — Production Planner (module)

**Goal:** given a target output rate for resource X, produce a `ProductionGraph` from raw
resources to X via graph search over recipe data.

**Contract:** consumes a target rate + `Recipe` list, produces `ProductionGraph`.

**Deliverables:**
- BFS/graph-search implementation over `Recipe` data.
- Alternate-recipe awareness: expose the choice when multiple recipes produce the same output
  rather than silently picking one.
- Found on real data, not in the fixtures: expansion stops at raw resources (passed in by the
  caller — the game has recipes *producing* ores), the default recipe choice prefers standard
  recipes, primary outputs and shallow chains, and a candidate that loops back on the item being
  planned is skipped (Fuel <- unpackaging <- Packaged Fuel <- Fuel). `tests/end_to_end/` plans
  every craftable item in the real Knowledge Base to keep it that way.
- `available_supply`: demand already covered from outside the plan — an existing factory's spare
  output — gets no new machines, and neither does anything upstream of it. This is what the
  Expansion Advisor (Stage 8) is fed.

**Test fixtures:** a small hand-written `Recipe` set covering the iron chain (own `fixtures/`,
don't import Stage 2's loader or read `docs/en-US.json` from here) plus hand-verified expected
output graphs.

**Done when:** "10/min of Reinforced Iron Plate" produces a correctly-shaped graph matching the
example chains in the source deck, checked by hand or against a fixture expected-output — no live
call to the Verifier required to pass this module's own tests.

---

## Stage 8 — Expansion Advisor (module)

**Goal:** given a new target and an *existing* production state, compute the minimal delta —
which factories to extend, what new stage to add — instead of planning from scratch.

**Contract:** consumes an existing `ProductionGraph` + the Stage 7 plan of *additions* (planned
with the existing factory's surplus as `available_supply`), produces a `ChangeSet`.

**Deliverables:**
- Every node records how many of its machines already stand (`existing_machine_count`), so an
  extended node shows what's new and `verifier.added_machines` can score only the additions.
- Mapping each addition onto the existing factory: `EXTEND` a node already running its recipe,
  `ADD` otherwise, with the plan's flows rewired onto the resulting node ids.

**Revised after real-data testing:** the original design diffed a from-scratch plan against the
existing factory's *gross* machine counts. On a real save that answered "5/min more Reinforced
Iron Plate" with "nothing to build" for a factory already 30 Iron Ingot/min short — machines busy
feeding the existing factory were counted as free. Spare capacity is now the Verifier's positive
balance, consumed by the planner before any machine is planned.

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

`find_factory_sites` also groups the player's running production buildings into factories —
machines within 50 m of each other, through any chain of them — so an expansion can say where to
build: real saves come out as 4 to 22 sites.

---

## Stage 10 — Anomaly Detector (module)

**Goal:** scan a production state for gaps, power blackouts, and congestion.

**Contract:** consumes `ProductionGraph`, produces `AnomalyRecord` list.

**Deliverables:**
- Classification of balance/power deficits into anomaly records (resource, location if derivable,
  severity).

**Test fixtures:** a deliberately-broken hand-built graph (e.g. under-provisioned smelters).

**Done when:** the fixture produces the expected anomaly list.

**Found on real data:** a save's graph has no flows, so a deficit's severity and the node it's
pinned on can't come from them — every one came out MEDIUM, pinned on nothing. The caller now
passes `item_demand` (gross consumption, `verifier.consumption` plus generator fuel and water) and
`item_consumers`, and those stand in for what the flows would say.

---

## Stage 11 — Q&A Engine / RAG (module)

**Goal:** answer free-form game-mechanics questions via retrieval-augmented generation. First LLM
touchpoint in the system, but still fully independent.

**Contract:** consumes a question + a text corpus, produces an answer + citations.

**Deliverables:**
- An index over a text corpus. TF-IDF in the end, not embeddings: the corpus is a thousand short
  passages, and a dependency-free ranking that's a page of code beats a model download.
- Retrieval + answer-synthesis call to the LLM, scoped to rephrase retrieved content only.
- The LLM call goes through `pioneer.config.Settings` (`llm_base_url` + `llm_model`) against a
  local, OpenAI-compatible endpoint — this is the point where the specific backend (Ollama,
  llama.cpp, vLLM, ...) gets picked; nothing before this stage needs to care.

**Test fixtures:** a small hand-written corpus (a few recipes + a few wiki-style paragraphs) — not
the full Knowledge Base.

**Done when:** a handful of known questions against the fixture corpus get answers traceable to
specific retrieved passages.

**Real corpus:** `build_corpus` turns the Knowledge Base into passages — the game's own description
of every class it describes (items, buildings, belts, equipment, schematics) plus one generated
passage per factory recipe with its building, rates and unlock. Retrieval stays TF-IDF, with a
bonus for passages whose title the question names; there is no wiki text yet.

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

**Test fixtures:** Stage 7/8 example outputs, copied in as constants in the test file.

**Done when:** a fixture graph renders with new-vs-existing nodes visually distinguished — and,
since expansions extend nodes, existing nodes with new machines as a third kind, labelled with how
many are new.

---

## Stage 14 — Map presentation (module)

**Goal:** render resource nodes, existing buildings, and recommended locations on the static map.

**Contract:** consumes `ResourceNode` list, `PlacementRecord` list, `RankedLocation` list.

**Test fixtures:** Stage 3/9 example outputs, copied in as constants in the test file.

**Done when:** a fixture recommendation renders as pins alongside fixture existing placements.

Factory sites an answer points at render as pins too, labelled with what they mostly make. The
background is still a plain grid: a real map image needs an asset whose source and licence are
decided first.

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

`score_response` is the one place that decides which channels an answer gets scored on and how
(see its docstring); the Orchestrator's `verify_response` only says what data to score against.

The LLM-as-a-judge hooks have real implementations in `llm_client.judges` (used only when
`PIONEER_LLM_JUDGE` is set — they cost a model call each), and `JsonlFeedbackStore` / `ResponseLog`
keep player feedback and a record of every answer under `data/`.

**What the checks settled on, after running them on real answers:**
- Chat: consistency is decided by the numbers — every number the answer states must be in its
  grounding, allowing for the rounding it was written with (or 1%). Word overlap is still
  reported, but a correct answer in plain sentences scored under 50% against its JSON tool result,
  while one with made-up numbers scored barely lower: it can't judge anything on its own.
- Graph: an expansion is scored on the machines it adds (`verifier.added_machines`), its draw is
  held against the power the save's grid has to spare, and its distance from optimum is measured
  against the same plan in fractional, underclocked machines (`verifier.minimal_machine_graph`).
- Map: a site is checked for its position and purity against the node data, its distance against
  the reference point the answer used (`ResponseArtifact.map_reference`), and against the save
  already extracting from that node.

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

**Status / decisions so far:**
- Tool calling *is* the intent classification: which tool the model reaches for is the intent, so
  there's no separate classification call and the `Intent` contract is currently unused.
- Tools take in-game item names as well as ids (`find_item`, `list_recipes_for_item`, and
  `knowledge_base.resolve_item` inside every other tool, with closest-match suggestions when a
  name fits several items or none).
- Planning and expansion results name each stage's building and its power, the power an expansion
  adds, and what the save's grid has to spare — so the model never has to guess a building or add
  up watts itself. They also name the belt or pipe tier each flow needs, the best free deposit for
  each raw input (published as the answer's map, so "I want to produce N/min of X" comes back as
  Chat + Graph + Map), and, for an expansion, the factory site each extended recipe is built at.
  Distances are in metres.
- Planning prefers what the player has unlocked (from the save) and names what a stage still
  needs; `plan_unlocks` gives the unlock order with costs, `compare_recipes` plans every recipe
  for an item side by side, and `plan_power` lists the generators, fuel, water and extractors for a
  power target, with the chain that makes a named fuel.
- `handle_query` takes the conversation so far (`history`); the web page sends its last eight
  turns with each question.
- **Found running against a real model** (qwen2.5:14b through Ollama, which both live smoke tests
  now pass): it answered a game-mechanics question from its own memory — wrongly, and the chat
  check caught the number — so the system prompt now tells it that what it remembers about the
  game is out of date and to ask the Q&A tool; and it sometimes writes a tool call into its answer
  instead of using the API's `tool_calls` field, which `handle_query` now executes anyway.
- No tool failure escapes `handle_query`: expected and unexpected errors alike come back to the
  model as an error result it can explain.
- Diagnosis judges the existing factory against every *placed* building — extractors, pumps and
  generators, which its recipe graph never contains — counts generator fuel burn and the water
  coal and nuclear plants need as consumption, and their waste as output, rates each shortfall
  against the item's real demand, and treats raw resources and hand-gathered items (no factory
  recipe) as inputs, not shortfalls.
- `tests/end_to_end/test_real_data.py` runs the real Knowledge Base and the fixture saves through
  `app.build_context` with a scripted model standing in for the LLM;
  `tests/end_to_end/test_live_model.py` asks a real one, when `PIONEER_LIVE_LLM_TESTS=1`.
- Every answer carries its `question` and its `grounding` — each tool result (with a `names` map
  for the ids in it) and each retrieved passage — and `orchestrator.verify_response` scores it with
  the Stage 15 functions.
- The web UI (`python -m pioneer.web`, FastAPI) serves the chat, each answer's graph and map, the
  verification badges and the feedback buttons; `python -m pioneer.app "question"` is the CLI.
- `app.LiveContext` keeps the context current while the web UI runs: a newer save (or a new write
  of the newest) is read on the next question, the server is asked again once its answer is a
  minute old, and a save that fails to parse leaves the last good one in use. Each answer is
  verified and mapped against the context it was built from.
- Still open: belt routing from saves (so a surplus can be told apart from items fed to storage or
  the sink — until then diagnosis flags every end product as overproduced, and congestion in a
  real factory can't be seen), fetching the save from a dedicated server on another machine
  (`EnumerateSessions` + `DownloadSaveGame` exist, but both need an admin token), pre-1.0 saves, a
  real map image behind the Map channel, which buildings (belts, generators) are unlocked — only
  recipes are tracked — and a check of an answer's factory sites alongside its deposits.

**Starting everything:** `pioneer.startup` brings up the model server (Ollama) and the dedicated
server if nothing is listening on their ports yet, serves the web UI, and stops whatever it
started. `PIONEER_SERVER_EXE` says where `FactoryServer.exe` lives.

**Running it:**

```
pip install -e .[dev]
python startup.py       # the model, the dedicated server and the web UI, whichever isn't up
python -m pioneer.web   # just the web UI; needs PIONEER_LLM_* in .env
```

`pioneer.startup` starts only what isn't already listening on its port and stops what it started;
see README.md for the rest of the setup.

**Done when:** "I want to produce 10/min of X" goes in through Chat and comes back out as a
verified Chat + Graph + Map response, built entirely from real module calls, with no fixtures left
standing in for a finished module.

---

## Explicitly deferred (out of scope per architecture.md §2)

Do not build these unless the scope changes:

- Multiplayer / non-dedicated-server play.
- Live save-file streaming (treat `.sav` as snapshot-only).
- Vehicle and train/rail logistics planning.
