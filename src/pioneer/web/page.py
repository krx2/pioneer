"""The chat page: one static document whose script talks to `/api/ask` and the feedback endpoint.

Answer bubbles arrive already rendered — and escaped — by `chat_presentation`; the player's own
text is only ever inserted with `textContent`. Graph and map open in frames pointing at their own
pages, so the D3 and SVG documents stay exactly what Stages 13 and 14 render.
"""

from __future__ import annotations

import html

from pioneer.chat_presentation.renderer import STYLE as CHAT_STYLE


def render_chat_page(*, status: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pioneer</title>
<style>{CHAT_STYLE}{PAGE_STYLE}</style>
</head>
<body>
<header class="bar">
  <div class="bar-inner">
    <h1 class="brand">{WORDMARK}</h1>
    <p class="tagline">{TAGLINE}</p>
    <span class="status">{html.escape(status)}</span>
  </div>
</header>
<main class="chat" id="chat"></main>
<form id="ask" class="composer">
  <div class="composer-box">
    <textarea id="question" rows="2" required
      placeholder="e.g. I want to produce 10/min of Reinforced Iron Plate"></textarea>
    <button type="submit">Ask</button>
  </div>
</form>
<script>{PAGE_SCRIPT}</script>
</body>
</html>
"""


# The composer lines its text field up with `.chat`'s column: 760px of content inside 20px of side
# padding, so every bubble and the field share both edges. The header spans the page instead: the
# logo in the top left corner, the status in the top right.
PAGE_STYLE = """
body { padding-bottom: 120px; }
.bar {
  position: sticky; top: 0; z-index: 1;
  background: linear-gradient(#232a2e, #1b2023);
}
.bar::after {
  content: ""; display: block; height: 4px;
  background: repeating-linear-gradient(-45deg, var(--ficsit) 0 8px, #1b2023 8px 16px);
}
.bar-inner {
  padding: 10px 20px;
  display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center;
}
.brand { margin: 0; line-height: 0; }
.wordmark { display: block; width: 150px; height: 46px; }
.tagline {
  margin: 0; max-width: 430px; padding-left: 16px; border-left: 2px solid var(--line);
  color: var(--muted); font: 13px/1.35 var(--display); letter-spacing: 0.02em;
}
.tagline b { color: var(--ficsit); }
@media (max-width: 720px) { .tagline { display: none; } }
.status {
  margin-left: auto; text-align: right; color: var(--muted);
  font: 13px/1.3 var(--display); text-transform: uppercase; letter-spacing: 0.06em;
}
.panel {
  width: 100%; height: 460px; align-self: stretch; box-sizing: border-box;
  border: 1px solid var(--line); border-radius: 12px; background: var(--surface);
}
.meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 12px; }
.meta button, .meta select {
  background: var(--surface); color: var(--text); border: 1px solid var(--line);
  border-radius: 8px; padding: 4px 8px; font: inherit; cursor: pointer;
}
.meta button:hover, .meta select:hover { border-color: var(--ficsit); }
.meta .chosen { background: var(--ficsit); color: var(--on-ficsit); border-color: var(--ficsit); }
.meta .again { padding: 2px 8px; font-size: 15px; line-height: 1.2; }
.meta button:disabled { opacity: 0.45; cursor: progress; }
.answer { display: flex; flex-direction: column; gap: 14px; }
.answer.stale { opacity: 0.45; pointer-events: none; }
.badge { padding: 2px 8px; border-radius: 999px; background: #22392a; color: #bfe3b8; }
.badge.bad { background: #4e2522; color: #ffb4a8; }
.badge.info { background: var(--surface); color: var(--muted); }
.error { color: #ff8f80; }
.composer {
  position: fixed; left: 0; right: 0; bottom: 0; padding: 20px 20px 16px;
  background: linear-gradient(transparent, var(--bg) 20px);
}
.composer-box {
  box-sizing: border-box; max-width: 760px; margin: 0 auto;
  display: flex; gap: 8px; align-items: flex-end; padding: 8px 8px 8px 14px;
  background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
}
.composer-box:focus-within {
  border-color: var(--ficsit); box-shadow: 0 0 0 3px rgba(242, 163, 68, 0.2);
}
.composer textarea {
  flex: 1; min-width: 0; min-height: 44px; max-height: 40vh; resize: vertical;
  padding: 6px 0; font: inherit; color: var(--text);
  background: transparent; border: 0; outline: none;
}
.composer textarea::placeholder { color: #7e8a91; }
.composer button {
  height: 38px; padding: 0 20px; color: var(--on-ficsit); background: var(--ficsit);
  font: 700 15px var(--display); text-transform: uppercase; letter-spacing: 0.08em;
  border: 0; border-radius: 8px; cursor: pointer;
}
.composer button:hover { background: var(--ficsit-hover); }
.composer button:disabled { opacity: 0.5; cursor: progress; }
"""

# What the name stands for, its letters picked out.
TAGLINE = (
    "<b>P</b>roduction <b>I</b>ntelligence &amp; <b>O</b>ptimization for <b>N</b>etworked "
    "<b>E</b>ngineering, <b>E</b>xpansion and <b>R</b>esource-planning for Satisfactory"
)

# "PIONEER" lettered after Satisfactory's logo: heavy slab letters with narrow slot counters, a
# white-to-ice-blue face over a navy extrusion, on a riveted steel-over-rust plate. The orange
# badge that ends the logo's Y arm ends the R's leg here, and the plate widens along that leg the
# way the logo's widens along the Y. Drawn as paths rather than set in a font: no download.
WORDMARK = """<svg class="wordmark" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 832 256"
  role="img" aria-labelledby="pw-title">
<title id="pw-title">Pioneer</title>
<defs>
  <linearGradient id="pw-face" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#ffffff"/>
    <stop offset=".45" stop-color="#f2f6f9"/>
    <stop offset=".9" stop-color="#bcd3e5"/>
    <stop offset="1" stop-color="#a9c5db"/>
  </linearGradient>
  <linearGradient id="pw-steel" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#4b5e67"/>
    <stop offset="1" stop-color="#3c4b52"/>
  </linearGradient>
  <linearGradient id="pw-rust" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#4c4944"/>
    <stop offset="1" stop-color="#45342b"/>
  </linearGradient>
  <radialGradient id="pw-rivet" cx=".38" cy=".34" r=".72">
    <stop offset="0" stop-color="#c3ccd0"/>
    <stop offset=".55" stop-color="#6c797f"/>
    <stop offset="1" stop-color="#262d31"/>
  </radialGradient>
  <clipPath id="pw-plate"><path d="M30 10H792V136L822 240H6V34A24 24 0 0 1 30 10Z"/></clipPath>
  <clipPath id="pw-badge"><path d="M64 129.5H104.4L131.2 196.5H90.8Z"/></clipPath>
  <filter id="pw-grunge" x="0" y="0" width="1" height="1">
    <feTurbulence type="fractalNoise" baseFrequency=".06" numOctaves="4" seed="3"/>
    <feColorMatrix values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  4 0 0 0 -2.1"/>
  </filter>
  <g id="pw-letters" fill-rule="evenodd">
    <path transform="translate(24 26)"
      d="M0 0H84A32 32 0 0 1 116 32V96A30 30 0 0 1 86 126H44V200H0ZM44 44V82H72V44Z"/>
    <path transform="translate(147 26)" d="M0 0H44V200H0Z"/>
    <path transform="translate(198 26)"
      d="M30 0H84A30 30 0 0 1 114 30V170A30 30 0 0 1 84 200
         H30A30 30 0 0 1 0 170V30A30 30 0 0 1 30 0ZM44 44V156H70V44Z"/>
    <path transform="translate(319 26)" d="M0 0H52L80 80V0H124V200H72L44 120V200H0Z"/>
    <path id="pw-e" transform="translate(450 26)"
      d="M30 0H100V44H44V78H92L84 122H44V156H100V200H30A30 30 0 0 1 0 170V30A30 30 0 0 1 30 0Z"/>
    <use href="#pw-e" x="107"/>
    <path transform="translate(664 26)"
      d="M0 0H84A32 32 0 0 1 116 32V98L102 114L136.4 200H88.4L58.8 126H44V200H0Z
         M44 44V82H72V44Z"/>
  </g>
</defs>
<path d="M6 238H822L825 249H6Z" fill="#2b1d17"/>
<path d="M6 238H822L823 241H6Z" fill="#5e4b41"/>
<g clip-path="url(#pw-plate)">
  <rect width="832" height="136" fill="url(#pw-steel)"/>
  <rect y="136" width="832" height="120" fill="url(#pw-rust)"/>
  <rect width="832" height="256" filter="url(#pw-grunge)" opacity=".12"/>
  <path d="M0 11H832" stroke="#7f939b" stroke-width="2"/>
  <path d="M0 135H832" stroke="#1a1d1e" stroke-width="3"/>
  <path d="M0 138H832" stroke="#65625c" stroke-width="1"/>
</g>
<g fill="url(#pw-rivet)" stroke="#1f2629">
  <circle cx="169" cy="18" r="4.5"/>
  <circle cx="503" cy="18" r="4.5"/>
  <circle cx="776" cy="18" r="4.5"/>
  <circle cx="373" cy="214" r="4.5"/>
  <circle cx="728" cy="216" r="4.5"/>
</g>
<use href="#pw-letters" transform="translate(5 8)" fill="#150f0d" opacity=".55"/>
<use href="#pw-letters" transform="translate(3 5)" fill="#4f5874"/>
<use href="#pw-letters" fill="url(#pw-face)"/>
<g transform="translate(664 26)">
  <path d="M58.8 126H106.8L136.4 200H88.4Z" fill="#f2a344" stroke="#3b4460" stroke-width="2"/>
  <g clip-path="url(#pw-badge)">
    <rect x="50" y="120" width="100" height="90" fill="#4c5a74"/>
    <path d="M50 120H77.9L69.8 160H50Z" fill="#f2a344"/>
    <path fill="#f2a344" d="M113.3 120L95.9 206L81.9 206L80.9 196L86.5 188L84.4 181L89.9 174
      L87 166L91.6 158L91 151L95.7 143L93.1 136L97.7 128L99.3 120Z"/>
  </g>
</g>
</svg>"""

PAGE_SCRIPT = """
const chat = document.getElementById("chat");
const form = document.getElementById("ask");
const input = document.getElementById("question");
const submit = form.querySelector("button");
const history = [];  // this tab's conversation, one {question, answer} per turn
const KEPT_TURNS = 8;
const MAX_ANSWER_CHARS = 8000;  // what the server takes of an earlier answer
let busy = false;  // one question at a time: each answer is asked with the turns before it

function append(node) {
  chat.appendChild(node);
  window.scrollTo(0, document.body.scrollHeight);
  return node;
}

function setBusy(on) {
  busy = on;
  submit.disabled = on;
  for (const button of document.querySelectorAll(".again")) button.disabled = on;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
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

// An answer's bubble, panels and feedback row share one box, so answering again replaces them all.
function fillAnswer(box, answer, turn) {
  const holder = document.createElement("div");
  holder.innerHTML = answer.chat_html;  // rendered and escaped server-side by chat_presentation
  box.replaceChildren(holder.firstElementChild);
  if (answer.graph_url) box.appendChild(panel(answer.graph_url, "Production graph"));
  if (answer.map_url) box.appendChild(panel(answer.map_url, "Factory map"));
  box.appendChild(meta(answer, box, turn));
}

async function ask(question, earlier) {
  const response = await fetch("/api/ask", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({question, history: earlier.slice(-KEPT_TURNS)}),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
  }
  if (body.status) document.querySelector(".status").textContent = body.status;
  return body;
}

function remember(turn, question, answer) {
  history[turn] = {question, answer: (answer.chat || "").slice(0, MAX_ANSWER_CHARS)};
}

// Asks a turn's question again with the conversation as it stood then, and puts the new answer
// in the old one's place -- on the page and in what later questions are sent.
async function answerAgain(box, turn) {
  if (busy) return;
  setBusy(true);
  box.classList.add("stale");
  box.querySelector(":scope > .error")?.remove();
  try {
    const {question} = history[turn];
    const answer = await ask(question, history.slice(0, turn));
    remember(turn, question, answer);
    fillAnswer(box, answer, turn);
  } catch (error) {
    box.appendChild(element("p", "error", `Pioneer could not answer again: ${error.message}`));
  } finally {
    box.classList.remove("stale");
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
  addChecks(meta, answer.verification || {});

  const id = answer.response_id;
  const thumbs = [];
  thumbs.push(feedbackButton("\\u{1F44D}", id, {thumbs_up: true}, thumbs));
  thumbs.push(feedbackButton("\\u{1F44E}", id, {thumbs_up: false}, thumbs));
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

  const again = element("button", "again", "\\u21BB");
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
  try {
    const turn = history.length;
    const answer = await ask(question, history);
    remember(turn, question, answer);
    const box = element("div", "answer");
    fillAnswer(box, answer, turn);
    append(box);
  } catch (error) {
    append(element("p", "error", `Pioneer could not answer: ${error.message}`));
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
"""
