"""The chat page: one static document whose script talks to `/api/ask/stream`, the conversation
endpoints and the feedback endpoint.

Answer bubbles arrive already rendered — and escaped — by `chat_presentation`; the player's own
text, the model's text while it's still being written and the conversations' titles are only ever
inserted with `textContent`. Graph and map open in frames pointing at their own pages, so the D3
and SVG documents stay exactly what Stages 13 and 14 render.

While an answer is on its way, its place shows what the assistant is doing — each tool it ran, a
reasoning model's thoughts, the answer's text as it's written — until the finished, rendered answer
takes its place, with that thought process folded above it to be opened again.

The sidebar, under the header, lists the stored conversations; the open one's id is the page's URL
fragment, so a reload, or a link, opens it again.

An answer proposing a plan offers to accept it as the conversation's plan -- the first one, or a
revision of the one it has -- and once one is accepted, a bar over the conversation says what the
plan makes and how much of it the save shows built, stage by stage on hover. It asks again every
minute while the page is in view, so the game's autosaves move it along. Only a proposal for the
plan as it stands can be accepted: once one is, the others' buttons go.

Clicked, the bar opens the plan's panel under it: each stage with what's built of it, a box to
tick a stage built that the save doesn't show yet, a link to the plan's graph, and the revisions
the plan went through -- when, asked by which question, changing what.
"""

from __future__ import annotations

from typing import Any

from pioneer.chat_presentation.renderer import STYLE as CHAT_STYLE
from pioneer.web.panels import PANELS_SCRIPT, PANELS_STYLE, POPOVER, render_sources


def render_chat_page(*, status: str, sources: dict[str, Any] | None = None) -> str:
    """`status` is the one-line summary of what the assistant works from; `sources` says the same
    source by source (see `app.Sources`), for the header's buttons."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pioneer</title>
<style>{CHAT_STYLE}{PAGE_STYLE}{PANELS_STYLE}</style>
</head>
<body>
<header class="bar" id="bar">
  <div class="bar-inner">
    <button type="button" class="menu" id="menu" aria-label="Conversations">&#9776;</button>
    <h1 class="brand">{WORDMARK}</h1>
    <p class="tagline">{TAGLINE}</p>
    {render_sources(status, sources)}
  </div>
</header>
{POPOVER}
<aside class="sidebar" id="sidebar" aria-label="Conversations">
  <button type="button" class="new-chat" id="new-chat">+ New conversation</button>
  <h2 class="sidebar-title">History</h2>
  <nav class="conversations" id="conversations"></nav>
</aside>
<div class="scrim" id="scrim"></div>
<div class="main">
  <div class="plan-bar" id="plan-bar" hidden></div>
  <main class="chat" id="chat"></main>
  <form id="ask" class="composer">
    <div class="composer-box">
      <textarea id="question" rows="2" required
        placeholder="e.g. I want to produce 10/min of Reinforced Iron Plate"></textarea>
      <button type="submit">Ask</button>
    </div>
  </form>
</div>
<script>{PAGE_SCRIPT}{PANELS_SCRIPT}</script>
</body>
</html>
"""


# The composer lines its text field up with `.chat`'s column: 760px of content inside 20px of side
# padding, so every bubble and the field share both edges. The header spans the page instead: the
# logo in the top left corner, the data sources in the top right (see `panels`), and the sidebar
# starts under it —
# `--bar-height` is kept at the header's height by the script, since its tagline may wrap. On a
# narrow screen the sidebar slides in under the header from the menu button.
PAGE_STYLE = """
:root { --sidebar: 264px; --bar-height: 70px; }
body { padding-bottom: 120px; }
.main { margin-left: var(--sidebar); }
.sidebar {
  position: fixed; top: var(--bar-height); bottom: 0; left: 0; z-index: 3; width: var(--sidebar);
  box-sizing: border-box; padding: 14px 10px; display: flex; flex-direction: column; gap: 8px;
  background: #1b2023; border-right: 1px solid var(--line);
}
.new-chat {
  padding: 10px 12px; text-align: left; color: var(--on-ficsit); background: var(--ficsit);
  font: 700 14px var(--display); text-transform: uppercase; letter-spacing: 0.06em;
  border: 0; border-radius: 8px; cursor: pointer;
}
.new-chat:hover { background: var(--ficsit-hover); }
.sidebar-title {
  margin: 10px 10px 0; color: var(--muted);
  font: 12px var(--display); text-transform: uppercase; letter-spacing: 0.08em;
}
.conversations {
  flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 2px;
}
.sidebar.busy .conversations, .sidebar.busy .new-chat { opacity: 0.5; pointer-events: none; }
.conversation { display: flex; align-items: center; border-radius: 8px; }
.conversation:hover { background: var(--surface); }
.conversation.active { background: var(--steel); box-shadow: inset 3px 0 var(--ficsit); }
.conversation a {
  flex: 1; min-width: 0; padding: 8px 10px; color: var(--text); text-decoration: none;
  font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.conversation .delete {
  visibility: hidden; padding: 4px 10px; color: var(--muted); background: none; border: 0;
  font-size: 16px; cursor: pointer;
}
.conversation:hover .delete, .conversation.active .delete, .conversation .delete:focus {
  visibility: visible;
}
.conversation .delete:hover { color: #ff8f80; }
.sidebar-empty { margin: 0; padding: 8px 10px; color: var(--muted); font-size: 13px; }
.menu {
  display: none; padding: 4px 10px; color: var(--text); background: none;
  border: 1px solid var(--line); border-radius: 8px; font-size: 18px; cursor: pointer;
}
.scrim {
  display: none; position: fixed; top: var(--bar-height); right: 0; bottom: 0; left: 0;
  z-index: 2; background: rgba(0, 0, 0, 0.5);
}
@media (max-width: 900px) {
  .main { margin-left: 0; }
  .sidebar { transform: translateX(-100%); transition: transform 0.2s; }
  .sidebar-open .sidebar { transform: none; }
  .sidebar-open .scrim { display: block; }
  .menu { display: inline-block; }
  .composer { left: 0 !important; }
}
.welcome { margin: 10vh 0 0; text-align: center; color: var(--muted); }
.welcome h2 {
  margin: 0 0 6px; color: var(--text); font: 600 24px var(--display); letter-spacing: 0.02em;
}
.welcome p { margin: 0; }
.suggestions {
  display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-top: 18px;
}
.suggestions button {
  padding: 8px 14px; color: var(--text); background: var(--surface);
  border: 1px solid var(--line); border-radius: 999px; font: inherit; font-size: 14px;
  cursor: pointer;
}
.suggestions button:hover { border-color: var(--ficsit); }
.progress { display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 14px; }
.spinner {
  flex: none; width: 14px; height: 14px; border-radius: 50%;
  border: 2px solid var(--line); border-top-color: var(--ficsit);
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.steps {
  margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 2px;
  color: var(--muted); font-size: 13px;
}
.steps:empty { display: none; }
.steps li::before { content: "\\2713  "; color: #8fcf86; }
.process {
  padding-left: 10px; border-left: 2px solid var(--line); color: var(--muted); font-size: 13px;
}
.process summary { cursor: pointer; width: fit-content; }
.process summary:hover { color: var(--text); }
.process[open] summary { margin-bottom: 6px; }
.thinking-text {
  margin-top: 8px; padding: 8px 10px; max-height: 240px; overflow-y: auto; white-space: pre-wrap;
  background: var(--surface); border-radius: 8px; font-style: italic;
}
.draft p { white-space: pre-wrap; }
.draft p::after {
  content: "\\258D"; margin-left: 1px; color: var(--ficsit); animation: blink 1s steps(2) infinite;
}
@keyframes blink { 50% { opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
  .spinner, .draft p::after { animation: none; }
}
.bar {
  position: sticky; top: 0; z-index: 4;
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
.meta .open-recipes { color: var(--ficsit); border-color: var(--ficsit); font-weight: 600; }
.meta .accept-plan {
  color: var(--on-ficsit); background: var(--ficsit); border-color: var(--ficsit); font-weight: 600;
}
.meta .accept-plan:hover { background: var(--ficsit-hover); }
.plan-bar {
  position: sticky; top: var(--bar-height); z-index: 1; padding: 10px 20px 0;
  background: linear-gradient(var(--bg) 75%, transparent);
}
.plan-bar[hidden] { display: none; }
.plan-inner {
  box-sizing: border-box; width: 100%; max-width: 760px; margin: 0 auto; padding: 8px 12px;
  display: flex; align-items: center; gap: 10px; color: var(--text); font: inherit;
  font-size: 13px; text-align: left; cursor: pointer;
  background: var(--steel); border: 1px solid var(--line); border-radius: 10px;
}
.plan-inner:hover { border-color: var(--ficsit); }
.plan-inner[aria-expanded="true"] { border-radius: 10px 10px 0 0; }
.plan-chevron { flex: none; color: var(--muted); transition: transform 0.15s; }
.plan-inner[aria-expanded="true"] .plan-chevron { transform: rotate(180deg); }
.plan-tag {
  flex: none; padding: 1px 8px; border-radius: 6px; color: var(--on-ficsit);
  background: var(--ficsit); font: 700 12px var(--display); letter-spacing: 0.08em;
  text-transform: uppercase;
}
.plan-targets {
  flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.plan-revision { flex: none; color: var(--muted); }
.plan-progress { flex: none; display: flex; align-items: center; gap: 6px; cursor: default; }
.plan-progress.unknown { color: var(--muted); }
.plan-meter {
  width: 64px; height: 6px; overflow: hidden; border-radius: 3px; background: var(--line);
}
.plan-meter span { display: block; height: 100%; background: #8fcf86; }
.plan-panel {
  box-sizing: border-box; max-width: 760px; margin: 0 auto; padding: 2px 14px 12px;
  max-height: calc(100vh - var(--bar-height) - 190px); overflow-y: auto; font-size: 13px;
  background: var(--steel); border: 1px solid var(--line); border-top: 0;
  border-radius: 0 0 10px 10px;
}
.plan-panel-head {
  display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
}
.plan-makes { margin: 8px 0 0; }
.plan-graph { flex: none; color: var(--ficsit); }
.plan-section h3 {
  margin: 14px 0 4px; color: var(--muted); font: 12px var(--display);
  text-transform: uppercase; letter-spacing: 0.08em;
}
.plan-stages, .plan-history, .plan-changes { margin: 0; padding: 0; list-style: none; }
.plan-stage {
  display: flex; align-items: center; gap: 10px; padding: 5px 0;
  border-top: 1px solid var(--line);
}
.plan-stage:first-child, .plan-history > li:first-child { border-top: 0; }
.plan-stage label {
  flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; cursor: pointer;
}
.plan-stage input { flex: none; width: 16px; height: 16px; margin: 0; accent-color: #8fcf86; }
.plan-stage input:disabled { cursor: default; }
.plan-stage.done .plan-stage-name { color: var(--muted); }
.plan-icon { flex: none; width: 22px; height: 22px; }
.plan-stage-name { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.plan-stage-machines { margin-left: 6px; color: var(--muted); }
.plan-stage-count {
  flex: none; min-width: 52px; text-align: right; font-variant-numeric: tabular-nums;
}
.plan-note { margin: 8px 0 0; color: var(--muted); font-size: 12px; }
.plan-history > li { padding: 8px 0; border-top: 1px solid var(--line); }
.plan-revision-head { display: flex; align-items: baseline; gap: 10px; }
.plan-revision-head span { color: var(--muted); font-size: 12px; }
.plan-question { display: block; margin: 3px 0 4px; color: var(--muted); font-style: italic; }
.plan-changes li { position: relative; padding: 1px 0 1px 14px; }
.plan-changes li::before { content: "\\2022"; position: absolute; left: 2px; color: var(--muted); }
.plan-changes .change-add::before, .plan-changes .change-extend::before { color: #8fcf86; }
.plan-changes .change-remove::before { color: #ff8f80; }
.plan-changes .change-reconfigure::before { color: var(--ficsit); }
@media (max-width: 520px) { .plan-revision, .plan-meter, .plan-stage-machines { display: none; } }
.meta .again { padding: 2px 8px; font-size: 15px; line-height: 1.2; }
.meta button:disabled { opacity: 0.45; cursor: progress; }
.answer { display: flex; flex-direction: column; gap: 14px; }
.answer.stale { opacity: 0.45; pointer-events: none; }
.badge { padding: 2px 8px; border-radius: 999px; background: #22392a; color: #bfe3b8; }
.badge.bad { background: #4e2522; color: #ffb4a8; }
.badge.info { background: var(--surface); color: var(--muted); }
.error { color: #ff8f80; }
.composer {
  position: fixed; left: var(--sidebar); right: 0; bottom: 0; padding: 20px 20px 16px;
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
    .join(" \\u00B7 ");
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
    element("span", "plan-chevron", "\\u25BE"),
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
    const graph = element("a", "plan-graph", "Open graph");
    graph.href = `/responses/${encodeURIComponent(plan.response_id)}/graph`;
    graph.target = "_blank";
    graph.rel = "noopener";
    head.appendChild(graph);
  }
  panel.appendChild(head);
  if (plan.progress) panel.appendChild(planStages(plan.progress));
  if (plan.history && plan.history.length) panel.appendChild(planHistory(plan.history));
  return panel;
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
  const machines = `${formatRate(stage.to_build)}\\u00D7 ${stage.building}`;
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
      return `\\u2212${count} ${building} for ${change.recipe}`
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
  holder.title = lines.join("\\n");
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
      summary.textContent = parts.join(" \\u00B7 ");
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
    return /^\\s*(\\{|\\[|<tool_call>|```json)/.test(text);
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
    while ((end = buffer.indexOf("\\n")) >= 0) {
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

const opened = window.location.hash.slice(1);
if (opened) openConversation(opened);
else showWelcome();
refreshConversations();
"""
