const chat = document.getElementById("chat");
const form = document.getElementById("ask");
const input = document.getElementById("question");
const submit = form.querySelector("button");
const sidebar = document.getElementById("sidebar");
const list = document.getElementById("conversations");
const KEPT_TURNS = 8;
const MAX_ANSWER_CHARS = 8000;  // what the server takes of an earlier answer
const SUGGESTIONS = [
  "I want to produce 10/min of Reinforced Iron Plate",
  "Where should I build my next iron factory?",
  "What's wrong with my factory?",
];
let turns = [];  // the open conversation, one {question, answer} per turn
let conversationId = null;  // its id on the server; null until its first answer
let plan = null;  // its plan, as the server keeps it; null until one is accepted
const planBar = document.getElementById("plan-bar");
const planControls = [];  // each proposing answer's accept control, redrawn as the plan changes
const PLAN_REFRESH_MS = 60000;  // how often the plan's progress is asked again, the page in view
let planTimer = null;
let planOpen = false;  // the plan's panel, open under its bar
let busy = false;  // one question at a time: each answer is asked with the turns before it

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function nearBottom() {
  return window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
}

function scrollToEnd() {
  window.scrollTo(0, document.body.scrollHeight);
}

function append(node) {
  chat.querySelector(".welcome")?.remove();
  chat.appendChild(node);
  scrollToEnd();
  return node;
}

function setBusy(on) {
  busy = on;
  submit.disabled = on;
  sidebar.classList.toggle("busy", on);
  for (const button of document.querySelectorAll(".again")) button.disabled = on;
  setPanelsBusy(on);
}

function addQuestion(text) {
  const bubble = element("div", "message message-user");
  bubble.appendChild(element("p", "", text));
  append(bubble);
}

function panel(url, title) {
  const frame = element("iframe", "panel");
  frame.src = url;
  frame.title = title;
  return frame;
}

function showWelcome() {
  const welcome = element("div", "welcome");
  welcome.appendChild(element("h2", "", "What are we building?"));
  welcome.appendChild(element("p", "", "Plan a production line, extend your factory, find where "
    + "to build next, or ask how the game works."));
  const suggestions = element("div", "suggestions");
  for (const text of SUGGESTIONS) {
    const button = element("button", "", text);
    button.type = "button";
    button.addEventListener("click", () => {
      input.value = text;
      form.requestSubmit();
    });
    suggestions.appendChild(button);
  }
  welcome.appendChild(suggestions);
  chat.replaceChildren(welcome);
}

// --- Conversations -------------------------------------------------------------------------

function setLocation(id) {
  window.history.replaceState(null, "", id ? `#${id}` : window.location.pathname);
}

function closeSidebar() {
  document.body.classList.remove("sidebar-open");
}

function startConversation() {
  if (busy) return;
  conversationId = null;
  turns = [];
  planControls.length = 0;
  setPlan(null);
  showWelcome();
  setLocation(null);
  markActive();
  closeSidebar();
  input.focus();
}

async function openConversation(id) {
  if (busy) return;
  const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`);
  if (!response.ok) {
    startConversation();
    refreshConversations();
    return;
  }
  const conversation = await response.json();
  conversationId = conversation.id;
  turns = [];
  plan = conversation.plan || null;
  planControls.length = 0;
  showPlan();
  clearTimeout(planTimer);
  if (plan) planTimer = setTimeout(refreshPlan, PLAN_REFRESH_MS);
  chat.replaceChildren();
  conversation.turns.forEach((stored, turn) => {
    addQuestion(stored.question);
    const box = element("div", "answer");
    fillAnswer(box, stored.answer, turn);
    chat.appendChild(box);
    remember(turn, stored.question, stored.answer);
  });
  if (!conversation.turns.length) showWelcome();
  scrollToEnd();
  setLocation(conversationId);
  markActive();
  closeSidebar();
}

function markActive() {
  for (const row of list.querySelectorAll(".conversation")) {
    row.classList.toggle("active", row.dataset.id === conversationId);
  }
}

async function deleteConversation(id, title) {
  if (busy || !window.confirm(`Delete "${title}"?`)) return;
  const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`, {method: "DELETE"});
  if (response.ok && id === conversationId) startConversation();
  refreshConversations();
}

function conversationRow(conversation) {
  const row = element("div", "conversation");
  row.dataset.id = conversation.id;
  const link = element("a", "", conversation.title);
  link.href = `#${conversation.id}`;
  link.title = conversation.title;
  link.addEventListener("click", (event) => {
    event.preventDefault();
    if (conversation.id !== conversationId) openConversation(conversation.id);
    else closeSidebar();
  });
  const remove = element("button", "delete", "×");
  remove.type = "button";
  remove.title = "Delete conversation";
  remove.setAttribute("aria-label", `Delete conversation: ${conversation.title}`);
  remove.addEventListener("click", () => deleteConversation(conversation.id, conversation.title));
  row.append(link, remove);
  return row;
}

async function refreshConversations() {
  let conversations = [];
  try {
    const response = await fetch("/api/conversations");
    if (response.ok) conversations = await response.json();
  } catch (error) {
    return;  // keep the list as it was
  }
  list.replaceChildren(...conversations.map(conversationRow));
  if (!conversations.length) {
    list.appendChild(element("p", "sidebar-empty", "Your conversations will show up here."));
  }
  markActive();
}

// --- The plan ------------------------------------------------------------------------------

function formatRate(rate) {
  return String(Math.round(rate * 100) / 100);
}

function describeTargets(targets) {
  return targets.map((target) => `${formatRate(target.rate_per_minute)}/min ${target.name}`)
    .join(" \u00B7 ");
}

function showPlan() {
  planBar.hidden = !plan;
  if (!plan) {
    planOpen = false;
    planBar.replaceChildren();
    return;
  }
  const inner = element("button", "plan-inner");
  inner.type = "button";
  inner.setAttribute("aria-expanded", String(planOpen));
  inner.setAttribute("aria-controls", "plan-panel");
  const targets = element("span", "plan-targets", describeTargets(plan.targets));
  targets.title = targets.textContent;
  inner.append(element("span", "plan-tag", "Plan"), targets);
  if (plan.progress) inner.appendChild(planProgress(plan.progress));
  inner.append(
    element("span", "plan-revision", `revision ${plan.revision}`),
    element("span", "plan-chevron", "\u25BE"),
  );
  inner.addEventListener("click", () => {
    planOpen = !planOpen;
    showPlan();
  });
  planBar.replaceChildren(inner);
  if (planOpen) planBar.appendChild(planPanel());
}

function meter(share) {
  const bar = element("span", "plan-meter");
  const fill = document.createElement("span");
  fill.style.width = `${Math.round(Math.max(0, Math.min(share, 1)) * 100)}%`;
  bar.appendChild(fill);
  return bar;
}

// The plan opened under its bar: every stage with how much of it is built, a box to tick what
// the save doesn't show yet, and the revisions the plan went through.
function planPanel() {
  const panel = element("div", "plan-panel");
  panel.id = "plan-panel";
  const head = element("div", "plan-panel-head");
  head.appendChild(element("p", "plan-makes", `Makes ${describeTargets(plan.targets)}`));
  if (plan.response_id) {
    const links = element("span", "plan-links");
    for (const [kind, label] of [["graph", "Open graph"], ["map", "Open map"]]) {
      if (kind === "map" && !plan.where) continue;
      const link = element("a", "plan-graph", label);
      link.href = `/responses/${encodeURIComponent(plan.response_id)}/${kind}`;
      link.target = "_blank";
      link.rel = "noopener";
      links.appendChild(link);
    }
    head.appendChild(links);
  }
  panel.appendChild(head);
  if (plan.where) panel.appendChild(planWhere(plan.where));
  if (plan.progress) panel.appendChild(planStages(plan.progress));
  if (plan.history && plan.history.length) panel.appendChild(planHistory(plan.history));
  return panel;
}

// Where the plan is built: each site with its deposits -- how many have an extractor standing on
// them yet -- and what it builds, and what the plan takes of the factories' spare output.
function planWhere(where) {
  const section = element("section", "plan-section");
  section.appendChild(element("h3", "", "Where"));
  const list = element("ul", "plan-sites");
  for (const site of where.sites) {
    const item = element("li");
    const head = element("div", "plan-site-head");
    const deposits = site.with_extractor === null
      ? `${site.deposits} deposit${site.deposits === 1 ? "" : "s"}`
      : `${site.with_extractor}/${site.deposits} deposits with an extractor`;
    head.append(element("b", "", site.site_id),
      element("span", "", `${deposits} · ${site.mines.join(", ")}`));
    item.appendChild(head);
    if (site.builds.length) {
      item.appendChild(element("span", "plan-site-builds", `Builds ${site.builds.join(", ")}`));
    }
    list.appendChild(item);
  }
  for (const taken of where.reserved) {
    list.appendChild(element("li", "plan-site-builds",
      `Takes ${formatRate(taken.rate_per_minute)}/min ${taken.item} of ${taken.site_id}'s spare`));
  }
  section.appendChild(list);
  return section;
}

function planStages(progress) {
  const section = element("section", "plan-section");
  section.appendChild(element("h3", "", "Stages"));
  const list = element("ul", "plan-stages");
  for (const stage of progress.stages) list.appendChild(stageRow(stage, progress.stages));
  section.appendChild(list);
  const notes = [];
  if (progress.known === "none") {
    notes.push("The save can't tell how much of this plan you've built: tick what you have.");
  } else if (progress.as_of) {
    notes.push(`Built by the save written ${new Date(progress.as_of * 1000).toLocaleString()}.`);
  }
  notes.push("A stage you tick counts as built, whatever the save says.");
  section.appendChild(element("p", "plan-note", notes.join(" ")));
  return section;
}

function stageRow(stage, stages) {
  const done = stage.built !== null && stage.built >= stage.to_build;
  const bySave = stage.in_save !== null && stage.in_save >= stage.to_build;
  const row = element("li", done ? "plan-stage done" : "plan-stage");
  const label = element("label");
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = done;
  box.disabled = bySave;
  box.title = bySave ? "The save shows all of them built"
    : done ? "Untick to count only what the save shows" : "Tick once you've built them all";
  box.addEventListener("change", () => markStage(stage, stages, box));
  label.appendChild(box);
  if (stage.icon) {
    const icon = document.createElement("img");
    icon.className = "plan-icon";
    icon.src = stage.icon;
    icon.alt = "";
    label.appendChild(icon);
  }
  const name = element("span", "plan-stage-name", stage.recipe);
  const machines = `${formatRate(stage.to_build)}\u00D7 ${stage.building}`;
  name.appendChild(element("span", "plan-stage-machines", machines));
  name.title = `${stage.building} running ${stage.recipe}`;
  label.appendChild(name);
  const built = stage.built === null ? "?" : formatRate(stage.built);
  const count = element("span", "plan-stage-count", `${built}/${formatRate(stage.to_build)}`);
  const sources = [stage.in_save === null ? "the save can't tell"
    : `${formatRate(stage.in_save)} in the save`];
  if (stage.marked) sources.push(`${formatRate(stage.marked)} ticked by you`);
  count.title = sources.join(", ");
  row.append(label, meter(stage.built === null ? 0 : stage.built / stage.to_build), count);
  return row;
}

// Ticking marks every machine of the stage built; unticking takes its mark back. A recipe's
// mark is for all its stages together, so the others' part of it stays.
async function markStage(stage, stages, box) {
  const others = stages
    .filter((other) => other !== stage && other.recipe_id === stage.recipe_id)
    .reduce((sum, other) => sum + other.marked, 0);
  const built = box.checked ? others + stage.to_build : others;
  box.disabled = true;
  try {
    const response = await fetch(
      `/api/conversations/${encodeURIComponent(conversationId)}/plan/marks`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({recipe_id: stage.recipe_id, built}),
      });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
    }
    setPlan(body.plan);
  } catch (error) {
    box.checked = !box.checked;
    box.disabled = false;
    box.title = `Could not mark it: ${error.message}`;
  }
}

function planHistory(history) {
  const section = element("section", "plan-section");
  section.appendChild(element("h3", "", "Revisions"));
  const list = element("ol", "plan-history");
  for (const entry of history) {
    const item = element("li");
    const head = element("div", "plan-revision-head");
    head.appendChild(element("b", "", `Revision ${entry.revision}`));
    if (entry.accepted_at) {
      head.appendChild(element("span", "", new Date(entry.accepted_at).toLocaleString()));
    }
    item.appendChild(head);
    if (entry.question) item.appendChild(element("q", "plan-question", entry.question));
    const changes = element("ul", "plan-changes");
    for (const change of entry.changes) {
      changes.appendChild(element("li", `change-${change.action}`, describeChange(change)));
    }
    if (!entry.changes.length) {
      changes.appendChild(element("li", "",
        entry.revision === 1 ? "The plan as first accepted." : "No machines changed."));
    }
    item.appendChild(changes);
    list.appendChild(item);
  }
  section.appendChild(list);
  return section;
}

function describeChange(change) {
  const count = formatRate(Math.abs(change.machines));
  const building = change.building || "machines";
  const standing = change.already_built ? formatRate(change.already_built) : null;
  switch (change.action) {
    case "add":
      return `+${count} ${building} for ${change.recipe} (a new stage)`;
    case "extend":
      return `+${count} ${building} for ${change.recipe}`;
    case "remove":
      return `\u2212${count} ${building} for ${change.recipe}`
        + (standing ? ` (${standing} built already: take them down)` : "");
    case "reconfigure":
      return `Switch ${count} ${building} from ${change.from_recipe} to ${change.recipe}`
        + (standing ? ` (${standing} built already)` : "");
    default:
      return `${change.action} ${count} ${building} for ${change.recipe}`;
  }
}

// How much of the plan the save shows built: in all, and stage by stage on hover.
function planProgress(progress) {
  const holder = element("span", "plan-progress");
  const lines = progress.stages.map((stage) => {
    const built = stage.built === null ? "?" : formatRate(stage.built);
    return `${stage.building} running ${stage.recipe}: ${built} of ${formatRate(stage.to_build)}`;
  });
  if (progress.as_of) {
    const when = new Date(progress.as_of * 1000);
    lines.push(`By the save written ${when.toLocaleString()}`);
  }
  if (progress.known === "none") {
    holder.classList.add("unknown");
    holder.textContent = "progress unknown";
    lines.push("No save to count your buildings from -- now, or from when the plan was made.");
  } else {
    const some = progress.known === "some" ? "*" : "";
    holder.append(meter(progress.to_build ? progress.built / progress.to_build : 1),
      element("span", "",
      `${formatRate(progress.built)}/${formatRate(progress.to_build)} built${some}`));
    if (some) lines.push("* Only the stages whose progress can be told.");
  }
  holder.title = lines.join("\n");
  return holder;
}

// Asks for the plan again -- its progress follows the game's saves -- and keeps doing so.
async function refreshPlan() {
  clearTimeout(planTimer);
  if (!conversationId || !plan) return;
  const id = conversationId;
  if (!document.hidden) {
    try {
      const response = await fetch(`/api/conversations/${encodeURIComponent(id)}/plan`);
      const next = response.ok ? (await response.json()).plan : plan;
      // Unchanged, the open panel is left as it is -- scrolled, say.
      if (id === conversationId && JSON.stringify(next) !== JSON.stringify(plan)) setPlan(next);
    } catch (error) {
      // keep showing what was last known
    }
  }
  if (plan && id === conversationId) planTimer = setTimeout(refreshPlan, PLAN_REFRESH_MS);
}

function setPlan(next) {
  plan = next || null;
  showPlan();
  for (let i = planControls.length - 1; i >= 0; i--) {
    if (!planControls[i].holder.isConnected) planControls.splice(i, 1);  // answered again
  }
  for (const control of planControls) control.draw();
}

async function acceptPlan(responseId, holder) {
  if (!conversationId) return;
  const response = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}/plan`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({response_id: responseId}),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const reason = typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`;
    holder.replaceChildren(badge(`could not accept the plan: ${reason}`, false));
    return;
  }
  setPlan(body.plan);
  refreshPlan();
}

// Accepting the answer's plan: offered while it's a proposal for the plan as it stands.
function planControl(answer) {
  const holder = element("span", "plan-control");
  const proposal = answer.plan_proposal;
  function draw() {
    const revision = plan ? plan.revision : 0;
    if (plan && plan.response_id === answer.response_id) {
      holder.replaceChildren(badge(revision > 1 ? "plan changes accepted" : "your plan", true));
    } else if (proposal.revision === revision + 1) {
      const label = proposal.revision > 1 ? "Accept plan changes" : "Accept as plan";
      const button = element("button", "accept-plan", label);
      button.type = "button";
      button.title = describeTargets(proposal.targets);
      button.addEventListener("click", () => acceptPlan(answer.response_id, holder));
      holder.replaceChildren(button);
    } else {
      holder.replaceChildren();
    }
  }
  draw();
  planControls.push({holder, draw});
  return holder;
}

// --- Answers -------------------------------------------------------------------------------

// An answer's bubble, panels and feedback row share one box, so answering again replaces them all.
function fillAnswer(box, answer, turn) {
  const holder = document.createElement("div");
  holder.innerHTML = answer.chat_html;  // rendered and escaped server-side by chat_presentation
  box.replaceChildren(holder.firstElementChild);
  if (answer.process) {
    const process = processBlock();
    for (const step of answer.process.steps || []) process.addStep(step);
    process.addThought(answer.process.thinking || "");
    process.settle();
    box.prepend(process.root);
  }
  if (answer.graph_url) box.appendChild(panel(answer.graph_url, "Production graph"));
  if (answer.map_url) box.appendChild(panel(answer.map_url, "Factory map"));
  box.appendChild(meta(answer, box, turn));
}

// How an answer was worked out: each tool it ran, ticked, and the model's reasoning, if it shows
// any. Open while the answer is on its way; folded above it once it's there.
function processBlock() {
  const root = element("details", "process");
  root.open = true;
  root.hidden = true;
  const summary = element("summary", "", "Thought process");
  const steps = element("ul", "steps");
  const thoughts = element("div", "thinking-text");
  thoughts.hidden = true;
  root.append(summary, steps, thoughts);
  return {
    root,
    addStep(text) {
      steps.appendChild(element("li", "", text));
      root.hidden = false;
    },
    addThought(text) {
      if (!text) return;
      thoughts.textContent += text;
      thoughts.hidden = false;
      root.hidden = false;
      thoughts.scrollTop = thoughts.scrollHeight;
    },
    settle() {
      const count = steps.children.length;
      const parts = ["Thought process"];
      if (count) parts.push(`${count} step${count === 1 ? "" : "s"}`);
      if (!thoughts.hidden) parts.push("reasoning");
      summary.textContent = parts.join(" \u00B7 ");
      root.open = false;
    },
  };
}

// What goes in an answer's place while it's on its way: the thought process so far, the answer's
// text as it's written, and what it's doing right now.
function liveView(box) {
  const process = processBlock();
  const draft = element("div", "message message-assistant draft");
  const draftText = element("p");
  draft.appendChild(draftText);
  draft.hidden = true;
  const progress = element("div", "progress");
  progress.setAttribute("role", "status");
  const label = element("span", "", "Sending the question…");
  progress.append(element("span", "spinner"), label);
  box.replaceChildren(process.root, draft, progress);

  let text = "";
  let running = null;  // the tool being run, until the next step starts

  function setLabel(line) {
    label.textContent = `${line}…`;
  }
  function stepDone() {
    if (running) process.addStep(running);
    running = null;
  }
  // A tool call the model wrote out as text is no answer: don't show it as one.
  function looksLikeToolCall() {
    return /^\s*(\{|\[|<tool_call>|```json)/.test(text);
  }

  return {
    handle(event) {
      const follow = nearBottom();
      switch (event.type) {
        case "status":
          stepDone();
          setLabel(event.text);
          break;
        case "tool":
          stepDone();
          running = event.text;
          setLabel(event.text);
          break;
        case "thinking":
          process.addThought(event.text);
          break;
        case "text":
          text += event.text;
          draft.hidden = looksLikeToolCall();
          if (!draft.hidden) {
            draftText.textContent = text;
            setLabel("Writing the answer");
          }
          break;
        case "discard":
          text = "";
          draft.hidden = true;
          draftText.textContent = "";
          break;
      }
      if (follow) scrollToEnd();
    },
  };
}

// Asks over the streaming endpoint: one JSON event per line, the answer itself last.
async function ask(question, earlier, turn, live) {
  const response = await fetch("/api/ask/stream", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      question,
      history: earlier.slice(-KEPT_TURNS),
      conversation_id: conversationId,
      turn,
    }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const {value, done} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    let end;
    while ((end = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, end).trim();
      buffer = buffer.slice(end + 1);
      if (!line) continue;
      const event = JSON.parse(line);
      if (event.type === "error") throw new Error(event.detail);
      if (event.type === "answer") return answered(event);
      live.handle(event);
    }
  }
  throw new Error("the connection closed before the answer came");
}

function answered(answer) {
  refreshStatus();
  if (answer.conversation_id && answer.conversation_id !== conversationId) {
    conversationId = answer.conversation_id;
    setLocation(conversationId);
  }
  refreshPlan();  // the save may have moved on while the question was answered
  refreshConversations();  // a new conversation, or one that's now the most recent
  return answer;
}

function remember(turn, question, answer) {
  turns[turn] = {question, answer: (answer.chat || "").slice(0, MAX_ANSWER_CHARS)};
}

// Asks a turn's question again with the conversation as it stood then, and puts the new answer
// in the old one's place -- on the page, in what later questions are sent, and on the server.
async function answerAgain(box, turn) {
  if (busy) return;
  setBusy(true);
  const before = [...box.childNodes].filter((node) => !node.classList.contains("error"));
  try {
    const {question} = turns[turn];
    const answer = await ask(question, turns.slice(0, turn), turn, liveView(box));
    remember(turn, question, answer);
    fillAnswer(box, answer, turn);
  } catch (error) {
    box.replaceChildren(...before);
    box.appendChild(element("p", "error", `Pioneer could not answer again: ${error.message}`));
  } finally {
    setBusy(false);
  }
}

async function sendFeedback(responseId, payload, control, alternatives = []) {
  const response = await fetch(`/api/responses/${responseId}/feedback`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  if (!response.ok) return;
  for (const other of alternatives) other.classList.remove("chosen");
  control.classList.add("chosen");
}

function feedbackButton(label, responseId, payload, alternatives = []) {
  const button = element("button", "", label);
  button.type = "button";
  button.addEventListener("click", () => {
    sendFeedback(responseId, payload, button, alternatives.filter((other) => other !== button));
  });
  return button;
}

function badge(text, ok) {
  // ok: true -> passed, false -> failed, null -> just information
  const className = ok === false ? "badge bad" : ok === true ? "badge" : "badge info";
  return element("span", className, text);
}

function addChecks(meta, checks) {
  if (checks.chat) {
    const numbers = checks.chat.ungrounded_numbers;
    const chat = checks.chat.consistent
      ? badge("numbers verified", true)
      : badge(`unverified numbers: ${numbers.join(", ")}`, false);
    if (checks.chat.judge) chat.title = checks.chat.judge.rationale;
    meta.appendChild(chat);
    const percent = Math.round(checks.chat.grounded_fraction * 100);
    meta.appendChild(badge(`words grounded ${percent}%`, null));
  }
  if (checks.graph) {
    const graph = checks.graph;
    meta.appendChild(graph.balanced
      ? badge("plan balances", true)
      : badge("plan does not balance", false));
    const spare = graph.spare_power_mw;
    if (spare === null) {
      meta.appendChild(badge(`needs ${graph.power_draw_mw} MW`, null));
    } else if (spare < 0) {
      meta.appendChild(badge(
        `needs ${graph.power_draw_mw} MW; the grid is already ${-spare} MW short`, false));
    } else {
      meta.appendChild(badge(
        `needs ${graph.power_draw_mw} MW of ${spare} MW spare`, graph.power_ok));
    }
    if (graph.over_optimum_pct) {
      meta.appendChild(badge(
        `${graph.over_optimum_pct}% more machines than the exact need (underclock to match)`,
        null));
    }
  }
  if (checks.map) {
    meta.appendChild(checks.map.passed
      ? badge("sites verified", true)
      : badge(`site check failed: ${checks.map.problems.join(", ")}`, false));
  }
}

function meta(answer, box, turn) {
  const meta = element("div", "meta");
  if (answer.missing_recipes && answer.missing_recipes.length) {
    const recipes = element("button", "open-recipes", "Open recipes");
    recipes.type = "button";
    recipes.title = "The recipes this plan needs turned on";
    recipes.addEventListener("click", (event) => {
      event.stopPropagation();  // a click outside the panel would close it again
      openPanel("recipes", {only: answer.missing_recipes});
    });
    meta.append(recipes);
  }
  if (answer.plan_proposal) meta.appendChild(planControl(answer));
  addChecks(meta, answer.verification || {});

  const id = answer.response_id;
  const thumbs = [];
  thumbs.push(feedbackButton("\u{1F44D}", id, {thumbs_up: true}, thumbs));
  thumbs.push(feedbackButton("\u{1F44E}", id, {thumbs_up: false}, thumbs));
  for (const button of thumbs) meta.appendChild(button);
  if (answer.graph_url) {
    meta.appendChild(feedbackButton("I applied this plan", id, {applied_plan: true}));
  }
  if (answer.map_url) {
    meta.appendChild(feedbackButton("I built here", id, {built_at_location: true}));
  }

  const rating = element("select");
  rating.appendChild(element("option", "", "rate 1-5"));
  for (const value of [1, 2, 3, 4, 5]) rating.appendChild(element("option", "", String(value)));
  rating.addEventListener("change", () => {
    const value = Number(rating.value);
    if (value) sendFeedback(id, {qualitative_score: value}, rating);
  });
  meta.appendChild(rating);

  const again = element("button", "again", "\u21BB");
  again.type = "button";
  again.title = "Answer again";
  again.setAttribute("aria-label", "Answer again");
  again.disabled = busy;
  again.addEventListener("click", () => answerAgain(box, turn));
  meta.appendChild(again);
  return meta;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = input.value.trim();
  if (!question || busy) return;
  addQuestion(question);
  input.value = "";
  setBusy(true);
  const box = append(element("div", "answer"));
  try {
    const turn = turns.length;
    const answer = await ask(question, turns, turn, liveView(box));
    remember(turn, question, answer);
    fillAnswer(box, answer, turn);
  } catch (error) {
    box.replaceChildren(element("p", "error", `Pioneer could not answer: ${error.message}`));
  } finally {
    setBusy(false);
    input.focus();
  }
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && planOpen) {
    planOpen = false;
    showPlan();
  }
});

document.getElementById("new-chat").addEventListener("click", startConversation);
document.getElementById("menu").addEventListener("click", () => {
  document.body.classList.toggle("sidebar-open");
});
document.getElementById("scrim").addEventListener("click", closeSidebar);
// The sidebar starts where the header ends, however tall its wrapped tagline makes it.
const bar = document.getElementById("bar");
new ResizeObserver(() => {
  document.documentElement.style.setProperty("--bar-height", `${bar.offsetHeight}px`);
}).observe(bar);
window.addEventListener("hashchange", () => {
  const id = window.location.hash.slice(1);
  if (id && id !== conversationId) openConversation(id);
});

// Once panels.js has loaded too: the two scripts call into each other.
document.addEventListener("DOMContentLoaded", () => {
  const opened = window.location.hash.slice(1);
  if (opened) openConversation(opened);
  else showWelcome();
  refreshConversations();
});
