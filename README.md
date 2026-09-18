# Pioneer

An AI planning assistant for [Satisfactory](https://www.satisfactorygame.com/). It reads the
player's own game state — the latest save file and, when one is configured, the dedicated server —
and answers questions about it: plan a production chain for a target rate, extend what's already
built, find where to build next, diagnose what's short, or explain a game mechanic.

The reasoning is a local LLM; the arithmetic never is. Machine counts, throughput, power, distances
and unlock orders come from plain, tested code that the model calls as tools, so every number in an
answer traces back to a module call. See [docs/architecture.md](docs/architecture.md) for the
design and [docs/implementation.md](docs/implementation.md) for how it was built, module by module.

## What you need

- Python 3.12+.
- A local OpenAI-compatible model that supports tool calling — [Ollama](https://ollama.com) with
  e.g. `qwen2.5:14b` is what this was developed against.
- Optional: a Satisfactory dedicated server, for live game state.
- Optional: save files. Without them the assistant still plans and answers questions, and says
  that it doesn't know what you've built.

## Getting started

```
pip install -e .[dev]
copy .env.example .env      # cp on Linux/macOS
ollama pull qwen2.5:14b
```

Then fill in `.env`: at minimum `PIONEER_LLM_BASE_URL=http://localhost:11434/v1` and
`PIONEER_LLM_MODEL=qwen2.5:14b`. Every setting is documented in `.env.example`; the save folder
defaults to the game's own dedicated-server save location.

**Give the model a 16k context window.** Ollama loads models with 4096 tokens by default, and the
system prompt and tool schemas alone take about 2000. When a conversation outgrows the window,
Ollama silently drops the *front* of the prompt — the rules and the tools — and the model answers
from the conversation alone: no tool calls, no graph or map, made-up recipes. Set the context
length to 16384 in the Ollama app's settings, or set `OLLAMA_CONTEXT_LENGTH=16384` for Ollama
and restart it (`startup.py` does that for an Ollama it starts itself). `qwen2.5:14b` then needs
about 12 GB of VRAM. The page's status line says when the loaded model's window is too small.

## Running it

```
python startup.py
```

starts the model server and the dedicated server if they aren't running already, serves the web UI
on http://127.0.0.1:8000, and stops whatever it started when you quit. `--no-model`,
`--no-game-server`, `--host` and `--port` narrow that down.

The pieces also run on their own:

```
python -m pioneer.web                                    # just the web UI
python -m pioneer.app "I want to produce 10/min of Iron Plate"   # one question, in the terminal
```

The web UI shows each answer's chat, its production graph and its map, the verification badges for
that answer (are its numbers from the tools, does the plan balance, does it fit the spare power)
and the feedback buttons. Feedback and a log of every answer go to `data/`, which is gitignored.

## Item icons

The map, the graph and the chat show the game's own icons, committed in `docs/icons/`. They come
from the game's files, which are UE5 IoStore archives since Update 8: umodel can't read those, but
[FModel](https://github.com/4sval/FModel) can. After a game update adds items, export them again:

1. In FModel, add the game's `FactoryGame/Content/Paks` folder as an undetected game (UE version as
   the [modding docs](https://docs.ficsit.app/satisfactory-modding/latest/Development/ExtractGameFiles.html)
   say — `GAME_UE5_6` at the time of writing).
2. Settings → General: turn on "Local Mapping File" and pick `CommunityResources/FactoryGame.usmap`
   from the game folder; paste `CommunityResources/CustomVersions.json` into "Custom Versions".
   Settings → Models: Texture Format PNG.
3. Right-click `FactoryGame/Resource`, `FactoryGame/Buildable` and `FactoryGame/Equipment` →
   "Save Folder's Packages Textures".
4. `python -m pioneer.icons <FModel>/Output/Exports` picks out the icon of every item, building and
   belt/pipe tier `docs/en-US.json` names, scales it to 96 px and writes `docs/icons/<class id>.png`.
   It lists the folders of any icon it didn't find, to export those too.

## What it can answer

- "I want to produce 10 Reinforced Iron Plates a minute" — a plan from raw resources up, with each
  stage's building, machines, power and belt tier, plus a free deposit for every raw input.
- "Add 400 screws a minute to my factory" — the minimal change to what's already built, using its
  spare output first, and which of your factories to extend.
- "Which alternate recipe for Iron Ingot is best?" — every recipe planned side by side.
- "What do I need to unlock for Computers?" — the technologies, their prerequisites and costs.
- "How do I get 500 MW?" — generators, fuel, water and the extractors for it.
- "Where should I build a copper mine?" — free deposits ranked by purity and distance.
- "What's wrong with my factory?" — shortfalls, waste and blackouts in the current save.
- "How fast is a Mk.3 belt?" — retrieval over the game's own descriptions.

## Tests and lint

```
pytest
ruff check . && ruff format --check .
```

Every module has its own fixture-based suite; the bonus suites run the real game data and the
committed save files through the same code. `tests/end_to_end/test_live_model.py` asks a real
model and only runs with `PIONEER_LIVE_LLM_TESTS=1`.

## Layout

```
src/pioneer/
  contracts/            data shapes every module shares, and nothing else
  knowledge_base/       recipes, buildings, technologies, belts (docs/en-US.json)
  resource_db/          resource nodes and purity (docs/resource_nodes.json)
  save_parser/          .sav -> placed buildings + production graph
  server_client/        the dedicated server's HTTPS API
  verifier/             the arithmetic: machines, balance, power, distance, transport
  production_planner/   target rate -> production graph
  expansion_advisor/    plan + existing factory -> what to build
  location_advisor/     free deposits, and where your factories are
  anomaly_detector/     shortfalls, waste, blackouts, congestion
  qa_engine/            retrieval + answer synthesis
  *_presentation/       chat, D3 graph, SVG map
  verification_feedback/  scoring an answer, and player feedback
  orchestrator/         the tools the model calls, and the routing loop
  llm_client/           the HTTP transport to the model
  icons.py              imports item and building icons (docs/icons/)
  web/, app.py, startup.py
tests/                  mirrors the layout above
```
