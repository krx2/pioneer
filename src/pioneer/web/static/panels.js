// --- Data sources: the header's buttons and their panels -----------------------------------------

const sourcesNav = document.getElementById("sources");
const contextWarning = document.getElementById("context-warning");
const popover = document.getElementById("popover");
const popoverTitle = document.getElementById("popover-title");
const popoverBody = document.getElementById("popover-body");
let status = JSON.parse(document.getElementById("initial-status").textContent);
let openName = null;  // the panel that's open, if one is
let panelLoads = 0;  // which load of a panel is the latest: an older one arriving late is dropped
let statusTimer = null;

async function api(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
  }
  return body;
}

function sendJson(method, body) {
  return {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

// A short label for each button, how it stands, and a tooltip saying more.
const LABELS = {
  recipes(s) {
    if (!s.knowledge_base) {
      return {text: "none", state: "error", title: "No knowledge base: docs/en-US.json is missing"};
    }
    const locked = s.recipes.locked_enabled
      ? `, ${s.recipes.locked_enabled} of them not unlocked yet` : "";
    return {
      text: `${s.recipes.enabled}/${s.recipes.total}`,
      state: "ok",
      title: `${s.recipes.enabled} of ${s.recipes.total} recipes usable in plans${locked}`,
    };
  },
  tech(s) {
    if (!s.tech.known) {
      return {text: "?", state: "off", title: "No save says what's unlocked"};
    }
    return {
      text: `T${s.tech.tier ?? 0}`,
      state: "ok",
      title: `Tier ${s.tech.tier ?? 0}: ${s.tech.milestones_unlocked} of `
        + `${s.tech.milestones_total} milestones unlocked`,
    };
  },
  map(s) {
    return {
      text: plural(s.map.sites, "site"),
      state: s.map.sites || s.map.resource_nodes ? "ok" : "off",
      title: `${plural(s.map.sites, "factory site")}, `
        + `${plural(s.map.resource_nodes, "resource node")}`,
    };
  },
  save(s) {
    const save = s.save;
    const name = save.session || save.file;
    if (save.loading) {
      return {text: "loading…", state: "loading", title: `Reading ${save.loading}`};
    }
    if (save.error) {
      return {text: name || "none", state: "error", title: save.error};
    }
    if (!save.file) return {text: "none", state: "off", title: "No save loaded"};
    return {
      text: name,
      state: "ok",
      title: `${save.file}: ${save.buildings} buildings`,
    };
  },
  server(s) {
    const server = s.server;
    if (!server.configured) {
      return {text: "off", state: "off", title: "No dedicated server configured"};
    }
    const where = `${server.host}:${server.port}`;
    if (server.error) return {text: "error", state: "error", title: `${where}: ${server.error}`};
    if (server.phase === null) return {text: "…", state: "loading", title: `Asking ${where}`};
    return {
      text: "online",
      state: "ok",
      title: `${where}: ${server.phase}, tech tier ${server.tech_tier}`,
    };
  },
};

function renderSources() {
  sourcesNav.title = status.status;
  const sources = status.sources;
  for (const button of sourcesNav.querySelectorAll(".source")) {
    const label = sources
      ? LABELS[button.dataset.panel](sources)
      : {text: "?", state: "off", title: status.status};
    button.querySelector(".source-state").textContent = label.text;
    button.dataset.state = label.state;
    button.title = label.title;
  }
  const warning = sources ? sources.context_window_warning : null;
  contextWarning.hidden = !warning;
  contextWarning.title = warning || "";
  clearTimeout(statusTimer);
  const asking = sources && sources.server.configured && sources.server.phase === null
    && !sources.server.error;
  if (sources && (sources.save.loading || asking)) {
    statusTimer = setTimeout(refreshStatus, 1500);
  }
}

let wasLoading = false;

async function refreshStatus() {
  try {
    status = await api("/api/status");
  } catch (error) {
    return;  // keep showing what was last known
  }
  const loading = Boolean(status.sources && status.sources.save.loading);
  renderSources();
  // A panel drawn from the status follows it; one with data of its own is drawn again once a
  // save that was being read has arrived, since what it shows comes from the save.
  if (openName && (PANELS[openName].fromStatus || (wasLoading && !loading))) loadPanel(false);
  wasLoading = loading;
}

// --- The panel ---------------------------------------------------------------------------------

function facts(rows) {
  const list = element("dl", "facts");
  for (const [term, value] of rows) {
    if (value === null || value === undefined || value === "") continue;
    list.append(element("dt", "", term), element("dd", "", String(value)));
  }
  return list;
}

function note(text, bad) {
  return element("p", bad ? "panel-note bad" : "panel-note", text);
}

function actionButton(text, onClick, secondary) {
  const button = element("button", secondary ? "action secondary" : "action", text);
  button.type = "button";
  button.disabled = busy;
  button.addEventListener("click", onClick);
  return button;
}

function fromStatus() {
  return Promise.resolve(status.sources);
}

const PANELS = {
  recipes: {
    title: "Recipes",
    load: () => api("/api/recipes"),
    render: renderRecipesPanel,
  },
  tech: {
    title: "Tech",
    load: () => api("/api/tech"),
    render: renderTechPanel,
  },
  map: {
    title: "Map",
    load: () => api("/api/map"),
    render: renderMapPanel,
  },
  save: {
    title: "Save",
    load: () => api("/api/saves"),
    render: renderSavePanel,
  },
  server: {
    title: "Server",
    load: () => api("/api/server"),
    render: renderServerPanel,
  },
};

// --- Recipes ------------------------------------------------------------------------------------

const UNLOCK_KINDS = {
  milestone: "milestone", mam: "MAM", alternate: "hard drive", tutorial: "HUB upgrade",
  custom: "granted", harddrive: "hard drive",
};
// How the list is looked at: kept while the page is open, so reopening the panel finds it as left.
const recipeView = {group: "product", query: "", alternates: false, locked: false, off: false,
  only: null};

function icon(src, className) {
  const image = document.createElement("img");
  image.className = className;
  image.alt = "";
  image.loading = "lazy";
  if (src) image.src = src;
  else image.style.visibility = "hidden";
  return image;
}

function unlockText(unlock) {
  if (!unlock) return "not unlocked";
  const kind = UNLOCK_KINDS[unlock.kind] || unlock.kind;
  // Only the HUB's tiers mean something to the player; MAM research and hard drives have none.
  const tiered = unlock.kind === "milestone" || unlock.kind === "tutorial";
  return `${unlock.name} · ${tiered ? `T${unlock.tier} ` : ""}${kind}`;
}

function toggleBox(checked) {
  const box = document.createElement("input");
  box.type = "checkbox";
  box.className = "toggle";
  box.checked = checked;
  box.disabled = busy;
  return box;
}

function renderRecipesPanel(body, data) {
  const recipes = data.recipes;
  if (!recipes.length) {
    body.append(note("No knowledge base: docs/en-US.json is missing, so there are no recipes."));
    return;
  }
  const problem = note("", true);
  problem.hidden = true;

  async function change(changes, boxes) {
    problem.hidden = true;
    try {
      status = await api("/api/recipes/enabled", sendJson("PUT", {changes}));
    } catch (error) {
      for (const box of boxes) box.checked = !box.checked;
      problem.textContent = `Could not change it: ${error.message}`;
      problem.hidden = false;
      return;
    }
    for (const recipe of recipes) {
      if (recipe.id in changes) recipe.enabled = changes[recipe.id];
    }
    renderSources();
    counts();
    draw();
  }

  const head = element("div", "recipe-summary");
  const tally = element("p", "recipe-counts");
  function counts() {
    const on = recipes.filter((recipe) => recipe.enabled);
    const locked = on.filter((recipe) => recipe.unlocked === false).length;
    tally.replaceChildren(`${on.length} of ${recipes.length} recipes on`);
    if (locked) tally.append(element("span", "locked-count", ` · ${locked} not unlocked yet`));
    tally.title = locked ? "Plans may use these, and then say what unlocks them" : "";
  }
  const reset = actionButton("Reset to unlocked", async () => {
    problem.hidden = true;
    try {
      status = await api("/api/recipes/reset", {method: "POST"});
    } catch (error) {
      problem.textContent = `Could not reset: ${error.message}`;
      problem.hidden = false;
      return;
    }
    renderSources();
    loadPanel(false);
  }, true);
  reset.title = "Plans use every recipe your save has unlocked, and nothing else";
  head.append(tally, reset);
  counts();

  const tools = element("div", "recipe-tools");
  const search = textInput("recipe-search", recipeView.query, "search");
  search.placeholder = "Search recipes, products, buildings";
  search.addEventListener("input", () => {
    recipeView.query = search.value;
    draw();
  });
  const grouping = element("div", "segmented");
  for (const [value, label] of [["product", "By product"], ["building", "By building"]]) {
    const button = element("button", "", label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(recipeView.group === value));
    button.addEventListener("click", () => {
      recipeView.group = value;
      for (const other of grouping.children) other.setAttribute("aria-pressed", "false");
      button.setAttribute("aria-pressed", "true");
      draw();
    });
    grouping.append(button);
  }
  const filters = element("div", "recipe-filters");
  for (const [key, label] of [["alternates", "Alternates"], ["locked", "Not unlocked"],
    ["off", "Turned off"]]) {
    const chip = element("button", "chip-toggle", label);
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(recipeView[key]));
    chip.addEventListener("click", () => {
      recipeView[key] = !recipeView[key];
      chip.setAttribute("aria-pressed", String(recipeView[key]));
      draw();
    });
    filters.append(chip);
  }
  tools.append(search, grouping, filters);

  let banner = null;
  if (recipeView.only) {
    banner = element("div", "recipe-banner");
    const wanted = recipeView.only.length;
    banner.append(element("span", "",
      `The ${wanted === 1 ? "recipe" : `${wanted} recipes`} the answer's plan could use — `
      + "turn on any one for each item it named, then ask again."));
    const all = element("button", "chip-toggle", "Show all");
    all.type = "button";
    all.addEventListener("click", () => {
      recipeView.only = null;
      banner.remove();
      draw();
    });
    banner.append(all);
  }

  const list = element("div", "recipe-list");
  function shown(recipe) {
    if (recipeView.only) return recipeView.only.includes(recipe.id);
    if (recipeView.alternates && !recipe.alternate) return false;
    if (recipeView.locked && recipe.unlocked !== false) return false;
    if (recipeView.off && recipe.enabled) return false;
    const query = recipeView.query.trim().toLowerCase();
    if (!query) return true;
    const words = [recipe.name, recipe.building?.name || "",
      ...recipe.products.map((product) => product.name)].join(" ").toLowerCase();
    return query.split(/\s+/).every((word) => words.includes(word));
  }
  function groupOf(recipe) {
    const lead = recipeView.group === "building" ? recipe.building : recipe.products[0];
    return lead || {id: "", name: "(nothing)", icon: null};
  }
  function row(recipe) {
    const line = element("label", recipe.enabled ? "recipe-row" : "recipe-row off");
    const box = toggleBox(recipe.enabled);
    box.addEventListener("change", () => change({[recipe.id]: box.checked}, [box]));
    const text = element("span", "recipe-text");
    text.append(element("span", "recipe-name", recipe.name));
    const meta = element("span", "recipe-meta");
    if (recipe.alternate) meta.append(element("span", "recipe-alt", "Alternate"), " · ");
    meta.append(recipeView.group === "building"
      ? recipe.products.map((product) => product.name).join(", ")
      : recipe.building?.name || "");
    text.append(meta);
    line.append(box, icon(recipe.products[0]?.icon, "recipe-icon"), text);
    if (recipe.unlocked === false) {
      const lock = element("span", "recipe-lock", `🔒 ${unlockText(recipe.unlock)}`);
      lock.title = "Not unlocked in your game yet: plans may still use it, and say what unlocks it";
      line.append(lock);
    }
    return line;
  }
  function draw() {
    const groups = new Map();
    for (const recipe of recipes.filter(shown)) {
      const lead = groupOf(recipe);
      if (!groups.has(lead.id)) groups.set(lead.id, {lead, recipes: []});
      groups.get(lead.id).recipes.push(recipe);
    }
    const ordered = [...groups.values()].sort((a, b) => a.lead.name.localeCompare(b.lead.name));
    const scroll = popoverBody.scrollTop;
    list.replaceChildren();
    for (const group of ordered) {
      const header = element("label", "recipe-group-head");
      const on = group.recipes.filter((recipe) => recipe.enabled).length;
      const box = toggleBox(on === group.recipes.length);
      box.indeterminate = on > 0 && on < group.recipes.length;
      box.title = "All of them on or off";
      box.addEventListener("change", () => {
        const changes = Object.fromEntries(group.recipes.map((recipe) => [recipe.id, box.checked]));
        change(changes, [box]);
      });
      header.append(box, icon(group.lead.icon, "recipe-icon"),
        element("span", "recipe-group-name", group.lead.name),
        element("span", "count", `${on}/${group.recipes.length}`));
      list.append(header, ...group.recipes.map(row));
    }
    if (!ordered.length) list.append(note("No recipe matches."));
    popoverBody.scrollTop = scroll;
  }
  draw();
  body.append(head, tools);
  if (banner) body.append(banner);
  body.append(problem, list);
}

// --- Tech ---------------------------------------------------------------------------------------

const TECH_GROUPS = {HUB: "HUB milestones", MAM: "MAM research", "Hard drives": "Hard drives"};
const techOpen = new Set();  // the sections opened, kept while the page is open

function costText(cost) {
  return cost.length
    ? `Costs ${cost.map((part) => `${part.amount}× ${part.name}`).join(", ")}`
    : "Costs nothing";
}

function techRow(technology) {
  const done = technology.unlocked;
  const row = element("li", done === true ? "tech-row done" : "tech-row");
  row.append(element("span", "tech-mark", done === true ? "✓" : done === false ? "○" : ""));
  const name = element("span", "tech-name", technology.name);
  row.append(name);
  const unlocks = element("span", "tech-unlocks");
  for (const recipe of technology.unlocks.slice(0, 6)) {
    const image = icon(recipe.icon, "tech-icon");
    image.title = recipe.name;
    unlocks.append(image);
  }
  if (technology.unlocks.length > 6) {
    unlocks.append(element("span", "tech-more", `+${technology.unlocks.length - 6}`));
  }
  row.append(unlocks);
  const recipes = technology.unlocks.map((recipe) => recipe.name);
  row.title = [costText(technology.cost),
    recipes.length ? `Unlocks ${recipes.join(", ")}` : ""].filter(Boolean).join("\n");
  return row;
}

function renderTechPanel(body, data) {
  if (!data.sections.length) {
    body.append(note("No knowledge base: docs/en-US.json is missing, so there's no tech tree."));
    return;
  }
  if (!data.known) {
    body.append(note("No save says what's unlocked, so your progress is unknown — here's "
      + "what there is to unlock."));
  }
  const bar = element("div", "tier-bar");
  for (const tier of data.tiers) {
    const cell = element("div", "tier-cell");
    if (data.known && tier.unlocked === tier.total) cell.classList.add("complete");
    if (data.known && tier.tier === data.tier) cell.classList.add("current");
    const count = data.known ? `${tier.unlocked}/${tier.total}` : `${tier.total}`;
    cell.append(element("span", "tier-name", `T${tier.tier}`),
      element("span", "tier-count", count));
    const fill = element("span", "tier-fill");
    fill.style.width = data.known ? `${(100 * tier.unlocked) / tier.total}%` : "0";
    cell.append(fill);
    cell.title = data.known
      ? `Tier ${tier.tier}: ${tier.unlocked} of ${tier.total} milestones unlocked`
      : `Tier ${tier.tier}: ${tier.total} milestones`;
    bar.append(cell);
  }
  body.append(bar);
  if (data.phase) body.append(facts([["Phase", data.phase]]));

  let group = null;
  for (const section of data.sections) {
    if (section.group !== group) {
      group = section.group;
      body.append(element("h3", "tech-group", TECH_GROUPS[group] || group));
    }
    const key = `${section.group}/${section.title}`;
    const box = element("details", "tech-section");
    box.open = techOpen.has(key);
    box.addEventListener("toggle", () => {
      if (box.open) techOpen.add(key);
      else techOpen.delete(key);
    });
    const summary = element("summary");
    const complete = section.unlocked !== null && section.unlocked === section.total;
    summary.append(element("span", "tech-title", section.title),
      element("span", complete ? "tech-tally complete" : "tech-tally",
        section.unlocked === null ? `${section.total}` : `${section.unlocked}/${section.total}`));
    const list = element("ul", "tech-list");
    list.append(...section.technologies.map(techRow));
    box.append(summary, list);
    body.append(box);
  }
}

// --- Map ----------------------------------------------------------------------------------------

const SVG_NS = "http://www.w3.org/2000/svg";
const PURITY_COLORS = {impure: "#c27b35", normal: "#9fb6cc", pure: "#f2c94c"};
const PURITIES = ["impure", "normal", "pure"];
const MAP_FILTER_KEY = "pioneer.map.filter";
let mapView = null;  // the part of the world on screen: kept while the page is open
const MARKERS_AT = 400;  // screen pixels a kilometre at which markers are drawn their full size
const ICONS_FROM = 0.75;  // drawn smaller than this, a node is a dot of its purity's colour

// How big markers are drawn, against their full size, when a screen pixel is `unit` world
// centimetres: they grow as the map is zoomed in, but by the square root of it and within bounds
// -- dots that leave the map visible when it's whole, icons that don't cover it close up.
function markerScale(unit) {
  return Math.min(Math.max(Math.sqrt(100000 / unit / MARKERS_AT), 0.45), 1.3);
}

function svgElement(tag, attributes = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

// Which nodes the side panel shows: kept in this browser, a convenience only.
function loadMapFilter() {
  const fallback = {hidden: {}, occupancy: "all", sites: true};
  try {
    const saved = JSON.parse(localStorage.getItem(MAP_FILTER_KEY) || "null");
    if (!saved || typeof saved !== "object") return fallback;
    return {
      hidden: saved.hidden && typeof saved.hidden === "object" ? saved.hidden : {},
      occupancy: ["all", "free", "taken"].includes(saved.occupancy) ? saved.occupancy : "all",
      sites: saved.sites !== false,
    };
  } catch (error) {
    return fallback;
  }
}

function saveMapFilter(filter) {
  try {
    localStorage.setItem(MAP_FILTER_KEY, JSON.stringify(filter));
  } catch (error) {
    // a private window, or storage turned off: the filter just isn't remembered
  }
}

function rate(perMinute) {
  return `${Math.round(perMinute * 10) / 10}/min`;
}

function flowList(title, flows) {
  const box = element("div", "tip-flows");
  box.append(element("h4", "", title));
  if (!flows.length) {
    box.append(element("p", "tip-none", "nothing"));
    return box;
  }
  const list = element("ul");
  for (const flow of flows.slice(0, 8)) {
    const row = element("li");
    row.append(icon(flow.icon, "tip-icon"), element("span", "tip-name", flow.name),
      element("span", "tip-rate", rate(flow.per_minute)));
    list.append(row);
  }
  if (flows.length > 8) list.append(element("li", "tip-none", `+${flows.length - 8} more`));
  box.append(list);
  return box;
}

function renderMapPanel(body, data) {
  if (!data.nodes.length && !data.sites.length) {
    body.append(note("No resource node data and no save: there's nothing to put on the map."));
    return;
  }
  const filter = loadMapFilter();
  const layout = element("div", "map-layout");
  const stage = element("div", "map-stage");
  const side = element("aside", "map-side");
  layout.append(stage, side);
  body.append(layout);

  // The world the picture shows, else just what there is to show, with room around it.
  const points = [...data.nodes, ...data.sites];
  const world = data.world
    ? {x: data.world.min_x, y: data.world.min_y, w: data.world.max_x - data.world.min_x,
      h: data.world.max_y - data.world.min_y}
    : (() => {
      const xs = points.map((p) => p.x);
      const ys = points.map((p) => p.y);
      const pad = 20000;
      const x = Math.min(...xs) - pad;
      const y = Math.min(...ys) - pad;
      return {x, y, w: Math.max(...xs) + pad - x, h: Math.max(...ys) + pad - y};
    })();

  const svg = svgElement("svg", {class: "map-svg", role: "img",
    "aria-label": "Map of factories and resource nodes"});
  const tiles = data.world ? data.tiles : null;
  const tileLayer = tiles ? svgElement("g", {class: "map-tiles"}) : null;
  if (tileLayer) {
    svg.append(tileLayer);
  } else if (data.image && data.world) {
    svg.append(svgElement("image", {href: data.image, x: world.x, y: world.y, width: world.w,
      height: world.h, preserveAspectRatio: "none"}));
  }
  const linkLayer = svgElement("g", {class: "map-links"});
  const nodeLayer = svgElement("g", {class: "map-nodes"});
  const siteLayer = svgElement("g", {class: "map-sites"});
  svg.append(linkLayer, nodeLayer, siteLayer);
  const tip = element("div", "map-tip");
  tip.hidden = true;
  const hint = element("p", "map-hint", "Scroll to zoom, drag to move, double-click to see it all."
    + " Point at a factory to see what feeds it; click to keep it.");
  stage.append(svg, tip, hint);

  const scaled = [];  // [group, x, y]: markers drawn in screen pixels at a point of the world
  function placeMarker(group, x, y) {
    scaled.push([group, x, y]);
    return group;
  }

  const nodesById = new Map();
  for (const node of data.nodes) {
    const group = svgElement("g", {class: node.occupied ? "map-node taken" : "map-node",
      color: PURITY_COLORS[node.purity] || "#9fb6cc"});
    group.append(svgElement("circle", {class: "dot", r: 8}));
    if (node.item.icon) {
      group.append(svgElement("image", {href: node.item.icon, x: -6, y: -6, width: 12,
        height: 12}));
    }
    if (node.occupied) {
      group.append(svgElement("circle", {class: "taken-dot", cx: 6, cy: -6, r: 3}));
    }
    const title = svgElement("title");
    title.textContent = `${node.item.name}, ${node.purity}${node.occupied ? " — taken" : ""}`;
    group.append(title);
    nodeLayer.append(placeMarker(group, node.x, node.y));
    nodesById.set(node.id, {node, group});
  }

  const siteMarkers = [];
  for (const site of data.sites) {
    const group = svgElement("g", {class: "map-site", tabindex: "0"});
    group.append(svgElement("rect", {x: -15, y: -15, width: 30, height: 30, rx: 6}));
    if (site.main?.icon) {
      group.append(svgElement("image", {href: site.main.icon, x: -11, y: -11, width: 22,
        height: 22}));
    } else {
      const text = svgElement("text", {y: 4, "text-anchor": "middle"});
      text.textContent = site.id.replace("site_", "");
      group.append(text);
    }
    const title = svgElement("title");
    title.textContent = `${site.id}${site.main ? `: ${site.main.name}` : ""}`;
    group.append(title);
    siteLayer.append(placeMarker(group, site.x, site.y));
    siteMarkers.push({site, group});
  }

  // --- the view ---
  function fit() {
    const rect = svg.getBoundingClientRect();
    const aspect = rect.height / Math.max(rect.width, 1);
    let w = world.w;
    let h = w * aspect;
    if (h < world.h) {
      h = world.h;
      w = h / aspect;
    }
    return {x: world.x + (world.w - w) / 2, y: world.y + (world.h - h) / 2, w, h};
  }
  const drawTiles = tileLayer ? mapTileLayer(tileLayer, tiles, world) : null;  // map-view.js

  let markerSize = 1;  // see markerScale
  function applyView() {
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return;  // not laid out (yet): nothing to fit
    if (!mapView || !mapView.w) mapView = fit();
    mapView.h = mapView.w * (rect.height / rect.width);  // the stage's shape, never stretched
    svg.setAttribute("viewBox", `${mapView.x} ${mapView.y} ${mapView.w} ${mapView.h}`);
    const unit = mapView.w / rect.width;  // world centimetres a screen pixel
    markerSize = markerScale(unit);
    svg.classList.toggle("far", markerSize < ICONS_FROM);
    for (const [group, x, y] of scaled) {
      group.setAttribute("transform", `translate(${x} ${y}) scale(${unit * markerSize})`);
    }
    if (drawTiles) drawTiles(mapView, rect.width);
    if (active) drawLinks(active);
    if (active) placeTip(active);
  }
  function minWidth() {
    // As close as the map picture stays sharp: no closer than its sharpest tiles allow.
    if (!tiles) return world.w * 0.01;
    return tileMinWidth(tiles, world, svg.getBoundingClientRect().width);
  }
  function toScreen(x, y) {
    const rect = svg.getBoundingClientRect();
    return {left: ((x - mapView.x) / mapView.w) * rect.width,
      top: ((y - mapView.y) / mapView.h) * rect.height};
  }
  const controls = panAndZoom(svg, {
    view: () => mapView,
    zoomStep: 1.2,
    bound: (width) => Math.min(Math.max(width, minWidth()), world.w * 2),
    changed: applyView,
    reset: () => {
      mapView = fit();
      applyView();
    },
  });

  // --- a factory and what feeds it ---
  let active = null;  // the site shown
  let pinned = null;  // the site kept shown
  function drawLinks(site) {
    linkLayer.replaceChildren();
    if (!mapView) return;
    const unit = (mapView.w / svg.getBoundingClientRect().width) * markerSize;
    for (const feed of site.feeds) {
      const found = nodesById.get(feed.node);
      if (!found) continue;
      const {x, y} = found.node;
      const angle = Math.atan2(site.y - y, site.x - x);
      const gap = 20 * unit;  // the arrow stops at the factory's marker
      const end = {x: site.x - Math.cos(angle) * gap, y: site.y - Math.sin(angle) * gap};
      linkLayer.append(svgElement("line", {x1: x, y1: y, x2: end.x, y2: end.y,
        class: feed.approximate ? "map-link guess" : "map-link"}));
      const head = svgElement("path", {d: "M0 0 L-10 -5 L-10 5 Z", class: "map-arrow",
        transform: `translate(${end.x} ${end.y}) rotate(${(angle * 180) / Math.PI}) `
          + `scale(${unit})`});
      linkLayer.append(head);
    }
  }
  function placeTip(site) {
    if (!mapView) return;
    const point = toScreen(site.x, site.y);
    const rect = stage.getBoundingClientRect();
    const left = Math.min(Math.max(point.left + 22, 8), rect.width - tip.offsetWidth - 8);
    const top = Math.min(Math.max(point.top - 20, 8), rect.height - tip.offsetHeight - 8);
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }
  function show(site) {
    active = site;
    svg.classList.add("focus");
    const feeding = new Set(site.feeds.map((feed) => feed.node));
    for (const {node, group} of nodesById.values()) {
      group.classList.toggle("feeding", feeding.has(node.id));
    }
    for (const marker of siteMarkers) {
      marker.group.classList.toggle("active", marker.site === site);
    }
    drawLinks(site);
    const head = element("div", "tip-head");
    head.append(icon(site.main?.icon, "tip-product"));
    const names = element("div");
    names.append(element("strong", "", site.main ? site.main.name : site.id),
      element("span", "tip-sub", `${site.id} · ${site.buildings} buildings`));
    head.append(names);
    // Water extractors draw from lakes, not nodes: they feed it, but have no pin to point from.
    const onMap = site.feeds.filter((feed) => nodesById.has(feed.node));
    const guesses = onMap.filter((feed) => feed.approximate).length;
    const elsewhere = site.feeds.length - onMap.length;
    let fed = onMap.length
      ? `Fed by ${plural(onMap.length, "node")}`
        + (guesses ? ` (${guesses} by nearness — dashed)` : "")
      : "No node on the map feeds it";
    if (elsewhere) fed += `; ${plural(elsewhere, "more extractor")} on water`;
    tip.replaceChildren(head, flowList("In", site.inputs), flowList("Out", site.outputs),
      element("p", "tip-fed", fed));
    if (pinned === site) tip.append(element("p", "tip-pin", "Kept — click the map to let go"));
    tip.classList.toggle("pinned", pinned === site);
    tip.hidden = false;
    placeTip(site);
  }
  function hide() {
    if (pinned) return show(pinned);
    active = null;
    svg.classList.remove("focus");
    linkLayer.replaceChildren();
    for (const {group} of nodesById.values()) group.classList.remove("feeding");
    for (const marker of siteMarkers) marker.group.classList.remove("active");
    tip.hidden = true;
  }
  for (const marker of siteMarkers) {
    marker.group.addEventListener("pointerenter", () => show(marker.site));
    marker.group.addEventListener("pointerleave", () => hide());
    marker.group.addEventListener("focus", () => show(marker.site));
    marker.group.addEventListener("blur", () => hide());
  }
  svg.addEventListener("click", (event) => {
    if (controls.dragged()) return;  // the end of a drag, not a click
    const hit = siteMarkers.find((marker) => marker.group.contains(event.target));
    pinned = hit && pinned !== hit.site ? hit.site : null;
    if (pinned) show(pinned);
    else hide();
  });

  // --- the side panel: which nodes to show ---
  const kinds = new Map();
  for (const node of data.nodes) {
    const kind = kinds.get(node.item.id) || {item: node.item, nodes: []};
    kind.nodes.push(node);
    kinds.set(node.item.id, kind);
  }
  const ordered = [...kinds.values()].sort((a, b) => a.item.name.localeCompare(b.item.name));
  function hiddenOf(itemId) {
    return new Set(filter.hidden[itemId] || []);
  }
  function visible(node) {
    if (hiddenOf(node.item.id).has(node.purity)) return false;
    if (filter.occupancy === "free" && node.occupied) return false;
    if (filter.occupancy === "taken" && !node.occupied) return false;
    return true;
  }
  const rows = [];
  function refresh() {
    for (const {node, group} of nodesById.values()) {
      group.style.display = visible(node) || group.classList.contains("feeding") && active
        ? "" : "none";
    }
    siteLayer.style.display = filter.sites ? "" : "none";
    for (const update of rows) update();
    saveMapFilter(filter);
  }

  side.append(element("h3", "map-side-title", "Resource nodes"));
  const occupancy = element("div", "segmented");
  for (const [value, label] of [["all", "All"], ["free", "Free"], ["taken", "Taken"]]) {
    const button = element("button", "", label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(filter.occupancy === value));
    button.addEventListener("click", () => {
      filter.occupancy = value;
      for (const other of occupancy.children) other.setAttribute("aria-pressed", "false");
      button.setAttribute("aria-pressed", "true");
      refresh();
    });
    occupancy.append(button);
  }
  const bulk = element("div", "map-bulk");
  for (const [label, hidden] of [["All", false], ["None", true]]) {
    const button = element("button", "chip-toggle", label);
    button.type = "button";
    button.addEventListener("click", () => {
      filter.hidden = hidden
        ? Object.fromEntries(ordered.map((kind) => [kind.item.id, [...PURITIES]]))
        : {};
      refresh();
    });
    bulk.append(button);
  }
  side.append(occupancy, bulk);

  const list = element("div", "map-kinds");
  for (const kind of ordered) {
    const row = element("div", "map-kind");
    const label = element("label", "map-kind-head");
    const box = document.createElement("input");
    box.type = "checkbox";
    const count = element("span", "count");
    label.append(box, icon(kind.item.icon, "recipe-icon"), element("span", "map-kind-name",
      kind.item.name), count);
    box.addEventListener("change", () => {
      filter.hidden[kind.item.id] = box.checked ? [] : [...PURITIES];
      refresh();
    });
    const chips = element("div", "map-purities");
    const chipFor = {};
    for (const purity of PURITIES) {
      const total = kind.nodes.filter((node) => node.purity === purity).length;
      if (!total) continue;
      const chip = element("button", "chip-toggle purity", `${purity} ${total}`);
      chip.type = "button";
      chip.style.setProperty("--purity", PURITY_COLORS[purity]);
      chip.addEventListener("click", () => {
        const hidden = hiddenOf(kind.item.id);
        if (hidden.has(purity)) hidden.delete(purity);
        else hidden.add(purity);
        filter.hidden[kind.item.id] = [...hidden];
        refresh();
      });
      chips.append(chip);
      chipFor[purity] = chip;
    }
    rows.push(() => {
      const hidden = hiddenOf(kind.item.id);
      const present = PURITIES.filter((purity) => chipFor[purity]);
      const shown = present.filter((purity) => !hidden.has(purity));
      box.checked = shown.length === present.length;
      box.indeterminate = shown.length > 0 && shown.length < present.length;
      for (const purity of present) {
        chipFor[purity].setAttribute("aria-pressed", String(!hidden.has(purity)));
      }
      count.textContent = `${kind.nodes.filter(visible).length}/${kind.nodes.length}`;
    });
    row.append(label, chips);
    list.append(row);
  }
  side.append(list);

  const sites = element("label", "map-kind-head map-sites-toggle");
  const sitesBox = document.createElement("input");
  sitesBox.type = "checkbox";
  sitesBox.checked = filter.sites;
  sitesBox.addEventListener("change", () => {
    filter.sites = sitesBox.checked;
    refresh();
  });
  sites.append(sitesBox, element("span", "map-kind-name", "Factories"),
    element("span", "count", String(data.sites.length)));
  side.append(sites);

  refresh();
  // Drawn in the panel already, so the stage has its size: the view can be fitted to it now.
  if (!mapView) mapView = fit();
  applyView();
  new ResizeObserver(() => applyView()).observe(stage);
}

// --- Save ---------------------------------------------------------------------------------------

const SAVE_SOURCES = {steam: "Steam", epic: "Epic", server: "Server", other: "Other"};
const SAVE_MODES = [
  ["auto", "Newest in its folder", "follows every session's autosaves there"],
  ["session", "Newest of its session", "follows that session's autosaves"],
  ["file", "This file only", "stays on it, however old"],
];

function playTime(seconds) {
  const minutes = Math.floor(seconds / 60);
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m played`;
}

function when(timestamp) {
  return new Date(timestamp * 1000).toLocaleString(undefined,
    {dateStyle: "medium", timeStyle: "short"});
}

function folderName(path) {
  return path ? path.split(/[\\/]/).filter(Boolean).pop() : "";
}

function pickedText(selection) {
  if (selection.mode === "file") return "this file only";
  if (selection.mode === "session") return `newest of session "${selection.session}"`;
  if (!selection.directory) return "newest in the folder .env names";
  return `newest in ${folderName(selection.directory)}`;
}

function radio(name, value, checked) {
  const input = document.createElement("input");
  input.type = "radio";
  input.name = name;
  input.value = value;
  input.checked = checked;
  return input;
}

function saveRow(save, inUse) {
  const row = element("label", "save-row");
  const text = element("span", "save-text");
  text.append(
    element("span", "save-name", save.name),
    element("span", "save-meta",
      `${when(save.modified)} \u00B7 ${playTime(save.play_seconds)} \u00B7 `
      + `${(save.size_bytes / 1048576).toFixed(1)} MB`));
  row.append(radio("save-path", save.path, save.path === inUse), text);
  if (save.path === inUse) row.append(element("span", "save-badge", "in use"));
  row.title = save.path;
  return row;
}

function renderSavePanel(body, data) {
  const save = status.sources ? status.sources.save : {};
  const inUse = save.file || "none";
  if (data.error) body.append(note(data.error, true));
  body.append(facts([
    ["In use", data.loading ? `${inUse} (reading ${data.loading}\u2026)` : inUse],
    ["Picked", pickedText(data.selection)],
    ["Buildings", save.file ? save.buildings : null],
  ]));
  if (!data.groups.length) {
    body.append(note("No saves found in the game's SaveGames folder."));
    return;
  }

  const modes = element("fieldset", "modes");
  modes.append(element("legend", "", "Read"));
  for (const [value, label, hint] of SAVE_MODES) {
    const option = element("label");
    const text = element("span", "", label);
    text.append(element("span", "hint", ` \u2014 ${hint}`));
    option.append(radio("save-mode", value, data.selection.mode === value), text);
    modes.append(option);
  }

  const groups = element("div", "save-groups");
  data.groups.forEach((group, index) => {
    const box = element("details", "save-group");
    box.open = group.saves.some((save) => save.path === data.in_use)
      || (index === 0 && !data.in_use);
    const summary = element("summary");
    summary.append(
      element("span", "save-session", group.session || "(no session name)"),
      element("span", "save-source", SAVE_SOURCES[group.source] || group.source),
      element("span", "save-count", String(group.saves.length)));
    summary.title = group.folder;
    box.append(summary, ...group.saves.map((save) => saveRow(save, data.in_use)));
    groups.append(box);
  });

  const problem = note("", true);
  problem.hidden = true;
  async function choose(mode, path) {
    problem.hidden = true;
    try {
      status = await api("/api/saves/selection", sendJson("PUT", {mode, path}));
    } catch (error) {
      problem.textContent = `Could not pick it: ${error.message}`;
      problem.hidden = false;
      return;
    }
    wasLoading = Boolean(status.sources && status.sources.save.loading);
    renderSources();
    loadPanel(false);
  }
  const actions = element("div", "panel-actions");
  actions.append(
    actionButton("Load", () => {
      const path = body.querySelector('input[name="save-path"]:checked');
      const mode = body.querySelector('input[name="save-mode"]:checked');
      if (!path || !mode) {
        problem.textContent = "Pick a save, and how to follow it.";
        problem.hidden = false;
        return;
      }
      choose(mode.value, path.value);
    }),
    actionButton("Back to .env", () => choose("auto", null), true));
  body.append(modes, groups, problem, actions);
}

// --- Server -------------------------------------------------------------------------------------

let serverNote = null;  // what the last change had to say, shown once the panel is drawn again

function field(label, input, className) {
  const wrap = element("label", className ? `field ${className}` : "field");
  wrap.append(element("span", "", label), input);
  return wrap;
}

function textInput(name, value, type = "text") {
  const input = document.createElement("input");
  input.name = name;
  input.type = type;
  input.value = value ?? "";
  input.autocomplete = "off";
  input.spellcheck = false;
  return input;
}

// "https://10.0.0.2:7778/" typed into the host field is a host and a port.
function splitAddress(hostInput, portInput) {
  let host = hostInput.value.trim().replace(/^https?:\/\//i, "").replace(/\/.*$/, "");
  const withPort = host.match(/^([^:\[\]]+):(\d+)$/);
  if (withPort) {
    host = withPort[1];
    portInput.value = withPort[2];
  }
  hostInput.value = host;
  return {host, port: Number(portInput.value) || 7777};
}

function renderServerPanel(body, server) {
  if (serverNote) {
    body.append(note(serverNote));
    serverNote = null;
  }
  if (!server.configured) {
    body.append(note(server.source === "page"
      ? "Disconnected: the save alone says what's built."
      : "No dedicated server configured: the save alone says what's built."));
  } else {
    if (server.error) {
      const refused = /refused|401|403/.test(server.error);
      body.append(note(refused
        ? `${server.error}. Log in again with the admin password, or paste a fresh API token.`
        : server.error, true));
    }
    body.append(facts([
      ["Address", `${server.host}:${server.port}`],
      ["Session", server.session],
      ["Phase", server.phase],
      ["Tech tier", server.tech_tier],
      ["Token", server.has_token ? "saved" : "none"],
      ["Set in", server.source === "page" ? "this page" : ".env"],
    ]));
  }

  const form = element("form", "server-form");
  form.noValidate = true;
  const host = textInput("host", server.host);
  host.placeholder = "192.168.1.10";
  const port = textInput("port", server.port || 7777, "number");
  port.min = "1";
  port.max = "65535";
  const row = element("div", "field-row");
  row.append(field("Host", host, "grow"), field("Port", port, "port"));

  const modes = element("fieldset", "modes");
  modes.append(element("legend", "", "Sign in with"));
  for (const [value, label] of [["token", "API token"], ["password", "Admin password"]]) {
    const option = element("label");
    option.append(radio("server-auth", value, value === "token"), element("span", "", label));
    modes.append(option);
  }
  function usesPassword() {
    return modes.querySelector('input[name="server-auth"]:checked').value === "password";
  }
  const secret = textInput("secret", "", "password");
  const secretField = field("API token", secret);
  const hint = note("");
  function describe() {
    const byPassword = usesPassword();
    secretField.firstChild.textContent = byPassword ? "Admin password" : "API token";
    secret.placeholder = byPassword ? "" : server.has_token
      ? "Saved — leave empty to keep it" : "from server.GenerateAPIToken";
    hint.textContent = byPassword
      ? "Pioneer logs in once to have the server make a lasting API token, and keeps only that "
        + "token. The password isn't saved."
      : "Run server.GenerateAPIToken in the dedicated server's console and paste what it prints. "
        + "The token is kept in data/settings.json, never shown again.";
  }
  modes.addEventListener("change", describe);
  describe();

  const result = note("");
  result.hidden = true;
  function say(text, bad) {
    result.textContent = text;
    result.className = bad ? "panel-note bad" : "panel-note good";
    result.hidden = false;
  }
  function request() {
    const {host: name, port: number} = splitAddress(host, port);
    if (!name) {
      say("Which host is the server on?", true);
      return null;
    }
    const byPassword = usesPassword();
    const body = {host: name, port: number};
    if (secret.value) body[byPassword ? "password" : "token"] = secret.value;
    return body;
  }
  async function run(button, work) {
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "…";
    try {
      await work();
    } catch (error) {
      say(error.message, true);
    } finally {
      button.textContent = label;
      button.disabled = busy;
    }
  }

  const save = actionButton("Save", () => run(save, async () => {
    const body = request();
    if (!body) return;
    const answer = await api("/api/server", sendJson("PUT", body));
    secret.value = "";
    serverNote = answer.note;
    status = answer;
    renderSources();
    loadPanel(false);
  }));
  const test = actionButton("Test connection", () => run(test, async () => {
    const body = request();
    if (!body) return;
    delete body.password;  // a test doesn't log in: that would make the server a token
    const check = await api("/api/server/test", sendJson("POST", body));
    if (!check.reachable) return say(`Not reachable: ${check.error}`, true);
    const health = check.health === "healthy" ? "healthy" : `${check.health} (under 10 ticks/s)`;
    if (check.token_accepted === false) {
      return say(`Reachable, ${health} \u2014 but it refuses the token.`, true);
    }
    if (check.token_accepted === true) return say(`Reachable, ${health}, token accepted.`);
    say(check.error ? `Reachable, ${health}; ${check.error}` : `Reachable, ${health}.`);
  }), true);
  const actions = element("div", "panel-actions");
  actions.append(save, test);
  if (server.configured) {
    const disconnect = actionButton("Disconnect", () => run(disconnect, async () => {
      status = await api("/api/server", {method: "DELETE"});
      renderSources();
      loadPanel(false);
    }), true);
    actions.append(disconnect);
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    save.click();
  });
  form.append(row, modes, secretField, hint, result, actions);
  body.append(form);
}

async function loadPanel(fresh = true) {
  const name = openName;
  const load = ++panelLoads;
  if (fresh) {
    const waiting = element("p", "panel-loading");
    waiting.append(element("span", "spinner"), element("span", "", "Loading…"));
    popoverBody.replaceChildren(waiting);
  }
  try {
    const data = await PANELS[name].load();
    if (load !== panelLoads || name !== openName) return;
    const scroll = popoverBody.scrollTop;
    popoverBody.replaceChildren();
    PANELS[name].render(popoverBody, data);
    if (!fresh) popoverBody.scrollTop = scroll;
  } catch (error) {
    if (load !== panelLoads || name !== openName) return;
    popoverBody.replaceChildren(note(`Could not load this: ${error.message}`, true));
  }
}

function placePopover() {
  const button = sourcesNav.querySelector(`.source[data-panel="${openName}"]`);
  if (!button || openName === "map" || window.matchMedia("(max-width: 720px)").matches) {
    popover.style.left = "";
    return;
  }
  const rect = button.getBoundingClientRect();
  const width = popover.offsetWidth;
  const left = Math.min(rect.right - width, window.innerWidth - width - 16);
  popover.style.left = `${Math.max(16, left)}px`;
}

// `options.only`: open the Recipes panel on just these recipes (an answer's missing ones).
function openPanel(name, options = null) {
  if (openName === name && !options) return closePanel();
  if (name === "recipes") recipeView.only = options && options.only ? [...options.only] : null;
  openName = name;
  for (const button of sourcesNav.querySelectorAll(".source")) {
    button.setAttribute("aria-expanded", String(button.dataset.panel === name));
  }
  popover.className = `popover popover-${name}`;
  popover.hidden = false;
  popoverTitle.textContent = PANELS[name].title;
  placePopover();
  loadPanel();
}

function closePanel(refocus = false) {
  if (!openName) return;
  const button = sourcesNav.querySelector(`.source[data-panel="${openName}"]`);
  openName = null;
  panelLoads++;
  popover.hidden = true;
  for (const other of sourcesNav.querySelectorAll(".source")) {
    other.setAttribute("aria-expanded", "false");
  }
  if (refocus) button?.focus();
}

// What changes a source is off while an answer is on its way: one answer, one context.
function setPanelsBusy(on) {
  popover.classList.toggle("busy", on);
  for (const control of popover.querySelectorAll("button.action, input.toggle")) {
    control.disabled = on;
  }
}

for (const button of sourcesNav.querySelectorAll(".source")) {
  button.addEventListener("click", () => openPanel(button.dataset.panel));
}
document.getElementById("popover-close").addEventListener("click", () => closePanel(true));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && openName) closePanel(true);
});
// The path, not the target: a click that redraws the panel has taken its target out of it.
document.addEventListener("click", (event) => {
  const path = event.composedPath();
  if (!openName || path.includes(popover)) return;
  if (path.some((node) => node.classList && node.classList.contains("source"))) return;
  closePanel();
});
window.addEventListener("resize", () => {
  if (openName) placePopover();
});
// After page.js has started too: the two scripts call into each other.
document.addEventListener("DOMContentLoaded", () => {
  renderSources();
  wasLoading = Boolean(status.sources && status.sources.save.loading);
});
