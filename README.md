# Pioneer

An AI planning assistant for [Satisfactory](https://www.satisfactorygame.com/). It reads the
player's own game state — the latest save file and, when one is configured, the dedicated server —
and answers questions about it: plan a production chain for a target rate, extend what's already
built, find where to build next, diagnose what's short, or explain a game mechanic.

The reasoning is a local LLM; the arithmetic never is. Machine counts, throughput, power, distances
and unlock orders come from plain, tested code that the model calls as tools, so every number in an
answer traces back to a module call. See [docs/architecture.md](docs/architecture.md) for the
design.

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
about 12 GB of VRAM. A red chip in the page's header says when the loaded model's window is too
small.

## Running it

```
pioneer                     # or: python startup.py
```

starts the model server and the dedicated server if they aren't running already, serves the web UI
on http://127.0.0.1:8000, and stops whatever it started when you quit. `--no-model`,
`--no-game-server`, `--host` and `--port` narrow that down.

The pieces also run on their own:

```
pioneer-web                                             # just the web UI (python -m pioneer.web)
pioneer-ask "I want to produce 10/min of Iron Plate"    # one question, in the terminal
```

`pip install -e .` puts these commands in the virtual environment; `python -m pioneer.web` and
`python -m pioneer.app` work without it.

The web UI shows each answer's chat, its production graph and its map, the verification badges for
that answer (are its numbers from the tools, does the plan balance, does it fit the spare power)
and the feedback buttons. While an answer is on its way, the page shows what the assistant is doing
(which tool it runs on what) and the answer's text as the model writes it. The sidebar lists past
conversations, graphs and maps included; "New conversation" starts from a clean context.
Feedback, a log of every answer and the conversations — their plans included — go to `data/`,
which is gitignored.

## One conversation, one plan

An answer that plans something — a new production chain, or an extension of your factory — offers
**Accept as plan**. Accepted, it becomes the conversation's plan, and what follows works on it:

- **Changing it.** Ask for a change — "make it 20 a minute", "add 5 Rotors a minute", "use Cast
  Screw for the screws", "drop the rotors" — and the plan is revised, not planned again from
  scratch. It's planned anew from what it was made of, every stage keeping its recipe unless you
  asked for another, so whatever you didn't ask to change comes out exactly as it was. The answer
  says what changes: machines to add, to take down, or to switch over to another recipe in the
  same building — and, for the last two, how many of them you've built already. A revision is a
  proposal until **Accept plan changes** makes it the plan. Only a proposal for the plan as it
  stands can be accepted: once one is, the others' buttons go.
- **How far you are.** The bar over the conversation says what the plan makes and how much of it is
  built. That's read from the save: the buildings of each stage's recipe beyond what stood when the
  recipe came into the plan, whatever their clock — an underclocked machine is still built. It
  counts every such building, also one put up for something else: the save doesn't say what a
  building is for. The bar asks again every minute, so the game's autosaves move it along.
- **Where it's built stays.** The plan keeps its deposits and sites as accepted. A revision keeps
  them too: for more, the deposits it has are pushed further and new ones added near them; for
  less, it drops the deposits it no longer needs — the ones you've already put an extractor on are
  kept first. A stage stays at the site it was at. Your miners on the plan's deposits don't make
  them "taken" for the plan itself.
- **The panel.** Click the bar for where the plan is built — each site, how many of its deposits
  have an extractor yet, what it builds, and what the plan takes of your factories' spare output —
  every stage with how much of it stands, a box to tick a stage built before the save shows it —
  ticked stages count as built for the assistant too — links to the plan's graph and map, and its
  revisions: when each was accepted, the question that asked for it, and what it changed.

A plan made with no save loaded has nothing to count from: its progress shows as unknown until you
tick stages. The plan, its revisions and your ticks are kept with the conversation.

## Where a plan is built

A plan says where to build it (`location_advisor.plan_sites`), from the free resource nodes:

- **Worked as players do.** Each node gets the best extractor you've unlocked — Miner Mk.3 over
  Mk.2 over Mk.1, the Oil Extractor for oil, the Resource Well Extractor on a well — overclocked
  with power shards up to 250%, but no further than your fastest belt or pipe carries. The answer
  says each node's clock speed and shards, and how many shards and how much power it all takes. A
  well's satellites run at the pressurizer's clock, not overclocked here.
- **No better node than needed.** Of the nodes that cover a resource, the ones sparing the least
  capacity: 400 iron ore a minute is one normal node at 167%, not a pure one underclocked — the
  pure one stays free for when it's needed — and one normal node rather than two impure ones.
- **One site if it fits.** When every deposit the plan needs lies within 400 m of one spot, it's
  all one site.
- **Else several, each making its parts.** What can't be had close together is split: each
  resource is mined where it can be, the deposits close to each other make a site, and each site
  builds what its own deposits are for — the steel where the iron and coal are, the concrete at the
  limestone, a far mine just smelting its ore — and sends it on. The answer says what each site
  builds and what goes where, on how many belts of which tier; the map shows the sites, a line from
  every deposit to its site, and what each site sends another.
- **Just what it needs of a megafactory.** A plan extending your factory takes what your factories
  have to spare before building anything — and reserves of it, factory by factory, only what it
  uses: 30 ingots a minute of a megafactory's 600 spare, the rest left spare.
- **Reserved.** The deposits an accepted plan works, and what it reserves of your factories' spare
  output, aren't offered to the plans of your other conversations. Deleting a conversation frees
  them.

## What it works from: the header

The header's five buttons say what the assistant knows right now, each opening a panel under it:

- **Recipes** (`147/291`): which recipes plans may use. By default every recipe the save has
  unlocked; tick or untick any of them — also ones not unlocked yet, which plans then use and say
  what unlocks them. An unticked recipe is never planned with: when a plan can't be made without
  one, the answer says which recipes it would take (any one per item) and offers to open the list
  on exactly those. The pick is kept per session, as the difference from the default, so a recipe
  unlocked later in the game comes in ticked.
- **Tech** (`T8`): the HUB's tiers 0–9 with how many milestones of each are unlocked, then the
  milestones, every MAM tree and the hard drives' alternates, each ticked when unlocked — hover
  one for its cost and what it unlocks. Read only: things are unlocked in the game.
- **Map** (`17 sites`): the game's map with the save's factories, each marked with what it makes.
  Point at one for what it takes in and puts out a minute, and arrows from the resource nodes
  that feed it — along its belts and pipes, or, dashed, the nearest when there are none; click to
  keep it, click the map to let go. The side panel picks which nodes to show — by resource,
  purity, free or taken — and remembers it in the browser. Scroll to zoom, drag to move,
  double-click to see it all. Seen whole, nodes are dots in their purity's colour; zoomed in,
  they show their resource's icon.
- **Save**: every save under the game's `SaveGames` folder — Steam, Epic and dedicated-server ones
  — by session, and which one is read: the newest in a folder (following the autosaves), the
  newest of one session, or one file, pinned. A big save is read in the background; the last one
  stays in use until it's in.
- **Server**: the dedicated server's address and how it last answered. Paste an API token
  (`server.GenerateAPIToken` in the server console), or give the admin password once: Pioneer
  logs in to have the server make a lasting token, keeps that and forgets the password. The token
  is never sent back to the page.

A red dot on a button means that source failed — hover it for why. What's picked in the panels is
kept in `data/settings.json` and wins over `.env`, which is never written; the CLI uses it too.

## The map

The maps — the Map panel and an answer's map alike — lie on the game's own map:
`assets/map/map.jpg`, the official map from the game's wiki (5000 px square), like the icons the
developers' own artwork. `pioneer.map_image` says where in the world it lies: x from -324 698.83
to 425 301.83 and y from -375 000 to 375 000 cm, east and south — `tests/test_map_image.py` checks
that the shipped deposits land on its land.

The pages draw it from tiles, committed in `assets/map/tiles/`: four levels from 625 to 5000 px,
512 px tiles, each page loading only those on screen at the level sharp at its zoom. A new picture
is cut into them with

```
python -m pioneer.map_image [picture]      # default: assets/map/map.jpg; needs Pillow (dev extra)
```

Without the tiles the maps draw the picture whole, and without either a plain grid. Markers grow
as the map is zoomed in, but by the square root of it, within bounds: zoomed in, they don't
cover the map, and zoomed out they don't bury it.

## Item icons

The map, the graph and the chat show the game's own icons, committed in `assets/icons/`. They come
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
   belt/pipe tier `assets/en-US.json` names, scales it to 96 px and writes it to
   `assets/icons/<class id>.png`. It lists the folders of any icon it didn't find, to export those
   too.

## What it can answer

- "I want to produce 10 Reinforced Iron Plates a minute" — a plan from raw resources up, with each
  stage's building, machines, power and belt tier, and where to build it (see "Where a plan is
  built" below).
- "Add 400 screws a minute to my factory" — the minimal change to what's already built, using its
  spare output first, and which of your factories to extend.
- "Make it 20 a minute and add 5 Rotors" — once a conversation has a plan, the plan revised: what to
  build, take down or switch over, and how much of that you've built already.
- "How far am I with the plan?" — each stage, and what of it the save shows built.
- "Which alternate recipe for Iron Ingot is best?" — every recipe planned side by side.
- "What do I need to unlock for Computers?" — the technologies, their prerequisites and costs.
- "How do I get 500 MW?" — generators, fuel, water and the extractors for it.
- "Where should I build a copper mine?" — free deposits ranked by purity and distance; "...for 600
  copper ore a minute?" — the deposits close together that give that much, and how to work them.
- "What's wrong with my factory?" — shortfalls, waste and blackouts in the current save, and its
  wiring: machines no belt or pipe feeds, products with nowhere to go, belts over their tier.
- "Show me how my factory makes Computers" / "draw site_2" — a graph of what's already built, from
  the save: the stages behind one item across every factory, or one factory and where it stands.
  The arrows follow the save's belts and pipes; the rates on them come from the recipes.
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
  common/               small helpers any module may use: names, recipe counting, atomic writes
  knowledge_base/       recipes, buildings, technologies, belts (assets/en-US.json)
  resource_db/          resource nodes and purity (assets/resource_nodes.json)
  save_parser/          .sav -> placed buildings + production graph
  server_client/        the dedicated server's HTTPS API
  verifier/             the arithmetic: machines, balance, power, distance, transport, plan progress
  production_planner/   target rates -> production graph
  expansion_advisor/    plan + existing factory -> what to build; a plan's revision -> what changes
  location_advisor/     free deposits, where your factories are, and where to build a plan
  anomaly_detector/     shortfalls, waste, blackouts, congestion
  qa_engine/            retrieval + answer synthesis
  *_presentation/       chat, D3 graph, SVG map
  verification_feedback/  scoring an answer, and player feedback
  orchestrator/         the tools the model calls, and the routing loop
  llm_client/           the HTTP transport to the model
  icons.py              imports item and building icons (assets/icons/)
  map_image.py          the game's map picture (assets/map/), its tiles, and where in the world it lies
  app/                  the composition root: data loading, the live context the header steers
  web/, startup.py
tests/                  mirrors the layout above; save files to test the parser on in save_parser/
assets/                 game data the assistant ships with: the game's export, resource nodes,
                        icons, the map picture and its tiles
docs/                   architecture.md
```
