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
  <strong>Pioneer</strong><span class="status">{html.escape(status)}</span>
</header>
<main class="chat" id="chat"></main>
<form id="ask" class="composer">
  <textarea id="question" rows="2" required
    placeholder="e.g. I want to produce 10/min of Reinforced Iron Plate"></textarea>
  <button type="submit">Ask</button>
</form>
<script>{PAGE_SCRIPT}</script>
</body>
</html>
"""


PAGE_STYLE = """
body { padding-bottom: 110px; }
.bar {
  position: sticky; top: 0; z-index: 1; display: flex; gap: 12px; align-items: baseline;
  padding: 12px 20px; background: #0b0f14ee; border-bottom: 1px solid #1c2733;
}
.status { color: #9aa4af; font-size: 12px; }
.panel {
  width: 100%; height: 460px; align-self: stretch;
  border: 1px solid #2a3542; border-radius: 12px; background: #11161c;
}
.meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 12px; }
.meta button, .meta select {
  background: #1c2733; color: #e8eaed; border: 1px solid #2a3542; border-radius: 8px;
  padding: 4px 8px; font: inherit; cursor: pointer;
}
.meta .chosen { background: #2f6fed; }
.badge { padding: 2px 8px; border-radius: 999px; background: #1f3a2b; color: #c7ced6; }
.badge.bad { background: #5a2626; }
.error { color: #f28b82; }
.composer {
  position: fixed; left: 0; right: 0; bottom: 0; display: flex; gap: 8px;
  padding: 12px 20px; background: #0b0f14; border-top: 1px solid #1c2733;
}
.composer textarea {
  flex: 1; resize: vertical; padding: 8px; font: inherit; color: #e8eaed;
  background: #131a22; border: 1px solid #2a3542; border-radius: 10px;
}
.composer button {
  padding: 0 18px; font: inherit; color: white; background: #2f6fed;
  border: 0; border-radius: 10px; cursor: pointer;
}
.composer button:disabled { opacity: 0.5; cursor: progress; }
"""

PAGE_SCRIPT = """
const chat = document.getElementById("chat");
const form = document.getElementById("ask");
const input = document.getElementById("question");
const submit = form.querySelector("button");

function append(node) {
  chat.appendChild(node);
  window.scrollTo(0, document.body.scrollHeight);
  return node;
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

function addAnswerHtml(markup) {
  const holder = document.createElement("div");
  holder.innerHTML = markup;  // rendered and escaped server-side by chat_presentation
  append(holder.firstElementChild);
}

function addPanel(url, title) {
  const frame = element("iframe", "panel");
  frame.src = url;
  frame.title = title;
  append(frame);
}

async function sendFeedback(responseId, payload, control) {
  const response = await fetch(`/api/responses/${responseId}/feedback`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  if (response.ok) control.classList.add("chosen");
}

function feedbackButton(label, responseId, payload) {
  const button = element("button", "", label);
  button.type = "button";
  button.addEventListener("click", () => sendFeedback(responseId, payload, button));
  return button;
}

function addMeta(answer) {
  const meta = element("div", "meta");
  const checks = answer.verification || {};
  if (checks.chat) {
    const percent = Math.round(checks.chat.grounded_fraction * 100);
    const badge = element("span", checks.chat.consistent ? "badge" : "badge bad");
    badge.textContent = `grounded ${percent}%`;
    if (checks.chat.judge) badge.title = checks.chat.judge.rationale;
    meta.appendChild(badge);
  }
  if (checks.graph) {
    const text = checks.graph.passed ? "plan verified" : "plan does not balance";
    meta.appendChild(element("span", checks.graph.passed ? "badge" : "badge bad", text));
  }
  if (checks.map) {
    const text = checks.map.passed ? "sites verified" : "site check failed";
    meta.appendChild(element("span", checks.map.passed ? "badge" : "badge bad", text));
  }

  const id = answer.response_id;
  meta.appendChild(feedbackButton("\\u{1F44D}", id, {thumbs_up: true}));
  meta.appendChild(feedbackButton("\\u{1F44E}", id, {thumbs_up: false}));
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
  append(meta);
}

function addError(message) {
  append(element("p", "error", message));
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = input.value.trim();
  if (!question) return;
  addQuestion(question);
  input.value = "";
  submit.disabled = true;
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question}),
    });
    const body = await response.json();
    if (!response.ok) {
      const detail = typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`;
      addError(`Pioneer could not answer: ${detail}`);
      return;
    }
    addAnswerHtml(body.chat_html);
    if (body.graph_url) addPanel(body.graph_url, "Production graph");
    if (body.map_url) addPanel(body.map_url, "Factory map");
    addMeta(body);
  } catch (error) {
    addError(`Pioneer could not answer: ${error}`);
  } finally {
    submit.disabled = false;
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
