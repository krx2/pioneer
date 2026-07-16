# Pioneer — Implementation Plan

> Companion to [architecture.md](architecture.md). This document sequences the build into stages,
> ordered so that each stage can be implemented and tested in isolation before the next stage
> depends on it. Read architecture.md first — this document assumes its terminology (module
> names, the Verifier, the LLM Orchestrator, etc.) without re-explaining it.

## Ordering principle

Build **bottom-up**: static data → deterministic domain logic → state ingestion → planning
modules that combine them → the LLM orchestrator that ties modules together → presentation →
verification/feedback. This order is deliberate:

- Everything through Stage 5 requires **no LLM and no running game** — it's plain code with unit
  tests, the cheapest and fastest part of the system to get right.
- The LLM Orchestrator (Stage 10) is introduced only once there are real modules worth routing to.
  Building it earlier means mocking everything it calls, which teaches you nothing about the real
  system.
- Presentation (Stages 11-13) comes after the orchestrator produces real structured output to
  render, not before.
- The feedback/verification loop (Stage 14) is last because it measures quality of something that
  needs to already exist and be usable end-to-end.

Each stage lists: goal, deliverables, depends on, and a "done when" check.

---

## Stage 0 — Project scaffolding

**Goal:** a repo that runs, tests, and lints, with nothing domain-specific in it yet.

**Deliverables:**
- Language/runtime choice and package layout (e.g. a single backend package with clear
  sub-packages for `knowledge_base/`, `modules/`, `orchestrator/`, `api/`).
- Test runner + linter wired up (CI optional at this stage, but the commands must exist locally).
- A `config` mechanism for secrets (LLM API key, dedicated server address) — never hardcoded.

**Depends on:** nothing.

**Done when:** `<test command>` runs green on an empty test suite, `<lint command>` runs clean.

---

## Stage 1 — Static game knowledge base

**Goal:** a queryable, versioned data set of recipes, buildings, and technologies — the foundation
every other module reads from.

**Deliverables:**
- Data model for: recipe (inputs, outputs, rate, building, unlock tier), building (power draw,
  slots), technology (unlock tree / prerequisites).
- Loader that ingests this from the game's data files (or a hand-curated JSON/YAML export of them
  if direct parsing is deferred) into the data model.
- Pure lookup functions: recipe(s) for output X, building for a recipe, prerequisites for a
  recipe/building.

**Depends on:** Stage 0.

**Done when:** you can look up "what recipes produce Reinforced Iron Plate" and get correct,
tested answers with no network/game/LLM calls involved.

**Why first:** every module in the system (Planner, Verifier, Expansion Advisor, Q&A) reads
recipes and technologies. Nothing else can be meaningfully tested without this existing.

---

## Stage 2 — Static resource/map database

**Goal:** a data set of resource node locations and purity, independent of any save file.

**Deliverables:**
- Data model: resource node (type, coordinates, purity: impure/normal/pure).
- Loader (hand-curated or scraped once, since the map is fixed and never changes).
- Lookup functions: nearest node(s) of type X to a coordinate, unclaimed nodes of type X.

**Depends on:** Stage 0. Independent of Stage 1 (can be built in parallel).

**Done when:** you can query "nearest pure iron node to (x, y)" with tested, correct output.

---

## Stage 3 — Verifier (deterministic calculator)

**Goal:** the shared arithmetic core: given a production graph (nodes = recipes/machines, edges =
material flow), compute machine counts, throughput balance, and power balance; given two
coordinates, compute distance.

**Deliverables:**
- Data model for a "production graph" (this becomes the contract every planning module produces
  and the Verifier consumes — get this shape right, it's load-bearing for the rest of the project).
- Functions: `balance(graph) -> surplus/deficit per resource`, `machine_count(recipe, target_rate)`,
  `power_balance(graph)`, `distance(a, b)`.
- No LLM involvement anywhere in this stage.

**Depends on:** Stage 1 (recipe data to compute against).

**Done when:** given a hand-built small production graph (e.g. the iron ingot → iron rod chain
from the source deck), the Verifier's numbers match hand-calculated expected values.

**Why before the planning modules:** Production Planner, Expansion Advisor, and Anomaly Detector
all call into this rather than duplicating math — build and trust it first.

---

## Stage 4 — Save file (`.sav`) parser

**Goal:** read a real Satisfactory save file into the same "production graph" shape used by the
Verifier, plus raw building placement data.

**Deliverables:**
- Parser for the `.sav` binary/structured format (or the relevant subset: placed buildings,
  configured recipes, positions).
- Mapping from parsed save data into: (a) the production-graph shape from Stage 3, (b) a placement
  list (building type + coordinates) for the Location Advisor.
- This module is read-only — never writes to the save file.

**Depends on:** Stage 1 (to resolve recipe IDs from the save into the knowledge base), Stage 3
(target output shape).

**Done when:** parsing a real save file produces a production graph that the Verifier can run
`balance()` on and get sane numbers.

---

## Stage 5 — Dedicated server API client

**Goal:** pull live, non-snapshot game state: overall progress, current tech phase, session info.

**Deliverables:**
- Client for the dedicated server's API (whatever auth/transport it exposes).
- Normalized "game state" object: progress/phase, distinct from the `.sav` production graph.
- Explicit handling for "server unreachable" — this must be a clean, typed failure, not an
  exception that propagates into the orchestrator (per the graceful-degradation invariant in
  architecture.md §7).

**Depends on:** Stage 0. Independent of Stages 1-4 (can be built in parallel), but needed before
any module that claims to use "current game state."

**Done when:** the client returns normalized state against a real (or locally mocked) dedicated
server, and returns a typed "unavailable" result when the server can't be reached.

---

## Stage 6 — Production Planner

**Goal:** given a target output rate for resource X, produce a production graph from raw resources
to X, via BFS/graph search over the recipe graph.

**Deliverables:**
- Graph-search implementation over Stage 1's recipe data, backward from goal to raw inputs.
- Alternate-recipe awareness: when multiple recipes produce the same output, expose the choice
  (this feeds the "advise on alternate recipes" scope item) rather than silently picking one.
- Output in the Stage 3 production-graph shape, run through the Verifier before being returned.

**Depends on:** Stage 1, Stage 3.

**Done when:** given "10/min of Reinforced Iron Plate" with no existing factories, the Planner
returns a graph the Verifier confirms is balanced, matching the example chains in the source deck.

---

## Stage 7 — Expansion Advisor

**Goal:** given a new target and the player's existing factories (from Stage 4), compute the
*minimal delta* — which existing factories to extend and what new stage(s) to add — instead of
planning from scratch.

**Deliverables:**
- Diff logic: existing production graph (Stage 4) vs. the graph the Planner would build from
  scratch (Stage 6) for the new target.
- Output: a change-set (extend factory A by N machines, add new linking stage, etc.), still
  Verifier-checked.

**Depends on:** Stage 3, Stage 4, Stage 6.

**Done when:** given a synthetic "existing factories" state and a new target, the Advisor picks
extension over from-scratch rebuilding whenever it's numerically sufficient — matching the worked
example in architecture.md §5.

---

## Stage 8 — Location Advisor

**Goal:** recommend where to place new buildings based on unclaimed resource deposits.

**Deliverables:**
- Cross-reference Stage 2 (resource DB) against Stage 4 (placement list) to find *unclaimed*
  deposits.
- Ranking by purity + distance to relevant existing infrastructure (via Stage 3's `distance()`).

**Depends on:** Stage 2, Stage 3, Stage 4.

**Done when:** given a target resource type and a save file, the Advisor returns a ranked list of
candidate deposits with purity and distance, excluding already-claimed nodes.

---

## Stage 9 — Anomaly Detector

**Goal:** scan an existing factory (from Stage 4) for problems: production gaps, power blackouts,
belt/pipe congestion.

**Deliverables:**
- Run the Verifier's `balance()` and `power_balance()` over the current save's production graph.
- Classify deficits/surpluses and power shortfalls into human-reportable anomaly records
  (resource, location if derivable, severity).

**Depends on:** Stage 3, Stage 4.

**Done when:** a deliberately-broken synthetic save (e.g. under-provisioned smelters) produces the
expected anomaly list.

---

## Stage 10 — Q&A Engine (RAG)

**Goal:** answer free-form game-mechanics questions using the knowledge base and wiki via
retrieval-augmented generation. This is the first stage that touches an LLM.

**Deliverables:**
- Indexing/embedding pipeline over Stage 1's structured knowledge base plus wiki text.
- Retrieval + answer-synthesis call to the LLM, scoped so the model only rephrases retrieved
  content rather than inventing facts.

**Depends on:** Stage 1 (and ideally Stage 3-9 exist so the Engine can be tested alongside real
modules, though it's technically independent).

**Done when:** a set of known game-mechanics questions gets answers traceable to specific
retrieved passages.

---

## Stage 11 — LLM Orchestrator

**Goal:** the intent router that ties every module above together: classify player intent, call
the right module(s) in the right order, pass results through the Verifier, and produce a response.

**Deliverables:**
- Intent classification (new plan / expand existing / locate factory / diagnose problem / general
  question).
- Tool-calling wiring exposing Stages 6-10 as callable tools.
- Response composition step that turns structured module output into the three presentation
  channels' worth of content (text for Chat, graph data for Graph, location data for Map) — the
  actual rendering happens in Stages 12-14.

**Depends on:** Stages 3, 5, 6, 7, 8, 9, 10 (needs real modules to route to — this is why it's
built late, not first).

**Done when:** the end-to-end example from architecture.md §5 ("I want to produce 10/min of X")
runs through intent routing → Expansion Advisor → Verifier → a composed response, using real
modules, not mocks.

---

## Stage 12 — Presentation: Chat

**Goal:** surface the Orchestrator's text output to the player and accept free-form input back.

**Deliverables:** minimal chat UI/API wired to the Orchestrator from Stage 11.

**Depends on:** Stage 11.

**Done when:** a full conversation turn (ask → plan → explain) works end-to-end through a real
interface, not a test harness.

---

## Stage 13 — Presentation: Production graph (D3.js)

**Goal:** render the production-graph data structure (already flowing since Stage 3) as an
interactive node/flow diagram.

**Deliverables:** D3.js graph view consuming the same production-graph shape used internally,
highlighting new vs. pre-existing nodes (per the worked example in architecture.md §5).

**Depends on:** Stage 11 (needs the orchestrator emitting graph data for real requests).

**Done when:** the Expansion Advisor's example output renders with new/existing nodes visually
distinguished.

---

## Stage 14 — Presentation: Factory map

**Goal:** render Location Advisor / Anomaly Detector output overlaid on the static map.

**Deliverables:** map view plotting resource nodes (purity color-coded), existing buildings, and
recommended new locations.

**Depends on:** Stage 8, Stage 11.

**Done when:** a Location Advisor recommendation renders as pins on the map alongside existing
factory placements from the current save.

---

## Stage 15 — Verification & feedback loop

**Goal:** wire up the three verification pipelines from architecture.md §6 and start capturing
player feedback against specific plans/graphs/locations.

**Deliverables:**
- RAG-consistency check for Chat answers (cross-check against knowledge base/wiki).
- Distance-from-optimum scoring for Graph responses (compare Planner/Advisor output against a
  known-optimal baseline where available).
- LLM-as-a-judge passes for contextual fit (Chat: fits player's stage of game; Map: terrain/
  logistics fit) — kept strictly separate from any arithmetic, per the no-arithmetic-in-the-LLM-
  path invariant.
- Feedback capture (thumbs up/down; "did you apply this plan"; "did you build here") attached to
  the specific response artifact, not the whole session.

**Depends on:** Stages 12, 13, 14 (needs real, renderable responses to attach feedback to).

**Done when:** a response of each type (Chat/Graph/Map) carries a feedback affordance, and
feedback events are persisted in a form that can be aggregated later (qualitative 1-5, pass/fail +
error %, mixed score — matching the three scoring styles in architecture.md §6).

---

## Explicitly deferred (out of scope per architecture.md §2)

Do not build these unless the scope changes:

- Multiplayer / non-dedicated-server play.
- Live save-file streaming (treat `.sav` as snapshot-only).
- Vehicle and train/rail logistics planning.
