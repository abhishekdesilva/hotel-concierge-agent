// The Grand Meridian — concierge chat widget.
//
// Vanilla JS, no build step. Two CDN deps loaded by index.html before this file:
// `marked` (markdown parser) and `DOMPurify` (sanitizer) — both required for
// rendering bot replies as markdown. User messages stay escaped (no markdown).
// Stateless server: this widget keeps the full message thread in memory and sends
// it on every POST. Five UI states: empty (greeting + chips), loading, success,
// error, tool-running. Anchored bottom-right at 32px margin.
(() => {
  "use strict";

  // A single backend: the orchestrator agent. It decides internally which
  // of the two scope-gated concierge agents (reader-only vs full-access)
  // should handle each message, delegates to it, and relays the reply back
  // — the widget just talks to one endpoint. Override via
  // window.GRAND_MERIDIAN_ORCHESTRATOR_URL (set in index.html).
  const ORCHESTRATOR_URL =
    window.GRAND_MERIDIAN_ORCHESTRATOR_URL ||
    "http://default-default.am-gateway.localhost:19080/concierge-orchestrator/chat";

  // Purely cosmetic: the orchestrator's response optionally names which
  // delegate tool it routed to (routed_to), so the reply can still be
  // tagged in the UI even though the widget itself makes no routing
  // decision anymore.
  const AGENT_LABEL = {
    ask_booking_agent: "Booking Agent",
    ask_availability_agent: "Availability Agent",
  };

  const PANEL_W = 380;
  const PANEL_H = 560;

  const GREETING =
    "Welcome to The Grand Meridian. How can I help you today? I can check availability, make a booking, share our menus, or recommend something nearby.";
  const CHIPS = ["Check availability", "Book a room", "Room service", "Things to do nearby"];

  // One session per page load. Server tracks conversation state keyed by this id.
  const SESSION_ID =
    (typeof crypto !== "undefined" && crypto.randomUUID)
      ? crypto.randomUUID()
      : `gm-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;

  // ---- DOM construction ----
  const root = document.getElementById("concierge-widget");
  if (!root) {
    console.warn("concierge-widget mount point not found");
    return;
  }

  const wrapper = document.createElement("div");
  wrapper.style.cssText = `position:fixed;right:32px;bottom:32px;z-index:50;font-family:Inter,sans-serif;`;
  root.appendChild(wrapper);

  // Launcher button
  const launcher = document.createElement("button");
  launcher.setAttribute("aria-label", "Open concierge chat");
  launcher.style.cssText = `
    width:64px;height:64px;border-radius:50%;background:#1B2B4B;color:#C9A84C;
    border:none;cursor:pointer;font-size:24px;line-height:1;
    box-shadow:0 12px 30px rgba(27,43,75,0.35);display:flex;align-items:center;justify-content:center;
    transition:transform 0.18s ease;
  `;
  launcher.innerHTML = `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#C9A84C" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>`;
  launcher.addEventListener("mouseenter", () => (launcher.style.transform = "scale(1.05)"));
  launcher.addEventListener("mouseleave", () => (launcher.style.transform = "scale(1)"));
  wrapper.appendChild(launcher);

  // Panel (hidden by default)
  const panel = document.createElement("aside");
  panel.setAttribute("aria-label", "Concierge chat");
  panel.style.cssText = `
    display:none;flex-direction:column;width:${PANEL_W}px;height:${PANEL_H}px;
    background:#fff;border:1px solid #E6E2D6;
    box-shadow:0 18px 50px rgba(27,43,75,0.18);overflow:hidden;
  `;
  wrapper.appendChild(panel);

  // Header
  const head = document.createElement("div");
  head.style.cssText = `
    padding:18px 20px;background:#1B2B4B;color:#fff;
    display:flex;align-items:center;gap:12px;flex-shrink:0;
  `;
  head.innerHTML = `
    <div style="width:28px;height:28px;border-radius:50%;background:#C9A84C;
                display:flex;align-items:center;justify-content:center;">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#1B2B4B" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>
    </div>
    <div style="display:flex;flex-direction:column;line-height:1.2;">
      <div style="font-family:'Playfair Display',serif;font-size:16px;font-weight:600;letter-spacing:0.02em;">
        Ask our concierge
      </div>
      <div style="font-size:11px;color:#cfd6e3;letter-spacing:0.06em;">● Available now</div>
    </div>
    <button id="cm-close" aria-label="Close chat"
      style="margin-left:auto;background:none;border:none;color:#cfd6e3;font-size:22px;line-height:1;cursor:pointer;">×</button>
  `;
  panel.appendChild(head);

  // Thread (scrollable)
  const thread = document.createElement("div");
  thread.style.cssText = `
    flex:1;overflow:auto;padding:20px;background:#FBFAF6;
    display:flex;flex-direction:column;gap:12px;
  `;
  panel.appendChild(thread);

  // Error toast (above input, hidden by default)
  const toast = document.createElement("div");
  toast.style.cssText = `
    display:none;background:#FEF3F2;color:#B42318;padding:8px 16px;
    font-size:12px;border-top:1px solid #F4C7C0;
  `;
  panel.appendChild(toast);

  // Input row
  const inputRow = document.createElement("form");
  inputRow.style.cssText = `
    display:flex;align-items:center;gap:10px;padding:14px 16px;
    border-top:1px solid #E6E2D6;background:#fff;flex-shrink:0;
  `;
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "Type a question…";
  input.setAttribute("aria-label", "Type a message");
  input.style.cssText = `
    flex:1;border:none;outline:none;font-family:inherit;font-size:14px;padding:8px 0;color:#1a1a1a;
  `;
  const sendBtn = document.createElement("button");
  sendBtn.type = "submit";
  sendBtn.setAttribute("aria-label", "Send");
  sendBtn.style.cssText = `
    background:#1B2B4B;color:#C9A84C;border:none;width:36px;height:36px;cursor:pointer;font-size:18px;
  `;
  sendBtn.innerHTML = "→";
  inputRow.appendChild(input);
  inputRow.appendChild(sendBtn);
  panel.appendChild(inputRow);

  // ---- State helpers ----
  function el(tag, style, html) {
    const e = document.createElement(tag);
    if (style) e.style.cssText = style;
    if (html) e.innerHTML = html;
    return e;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // Configure marked once. `breaks: true` — single newlines render as <br>,
  // matching how the LLM tends to format chat replies. `gfm: true` for
  // task lists, autolinks, fenced code.
  if (typeof marked !== "undefined" && typeof marked.setOptions === "function") {
    marked.setOptions({ breaks: true, gfm: true });
  }

  // Render LLM markdown safely. If either CDN dep failed to load, fall back
  // to escaped plaintext rather than risking unsanitized innerHTML.
  function renderMarkdown(text) {
    if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
      return escapeHtml(text).replace(/\n/g, "<br>");
    }
    return DOMPurify.sanitize(marked.parse(String(text)), {
      ADD_ATTR: ["target", "rel"],
    });
  }

  function renderBot(text, opts = {}) {
    const isGreeting = !!opts.greeting;
    const label = opts.agentLabel
      ? `<div style="font-size:10px;letter-spacing:0.08em;text-transform:uppercase;color:#9a9a9a;margin-bottom:5px;">${escapeHtml(opts.agentLabel)}</div>`
      : "";
    const bubble = el(
      "div",
      `max-width:85%;padding:12px 14px;font-size:14px;line-height:1.5;
       background:#fff;color:#1a1a1a;border:1px solid #E6E2D6;align-self:flex-start;
       border-radius:2px 14px 14px 14px;`,
      isGreeting
        ? `<div style="font-family:'Playfair Display',serif;font-size:15px;font-weight:600;color:#1B2B4B;margin-bottom:4px;">Welcome to The Grand Meridian.</div>${escapeHtml(text.replace("Welcome to The Grand Meridian. ", ""))}`
        : `${label}<div class="cm-md">${renderMarkdown(text)}</div>`
    );
    // Make rendered links open in a new tab without leaking the opener.
    bubble.querySelectorAll("a[href]").forEach((a) => {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
    });
    thread.appendChild(bubble);
    scrollToBottom();
    return bubble;
  }

  function renderUser(text) {
    const bubble = el(
      "div",
      `max-width:85%;padding:12px 14px;font-size:14px;line-height:1.5;
       background:#1B2B4B;color:#fff;align-self:flex-end;
       border-radius:14px 2px 14px 14px;`,
      escapeHtml(text)
    );
    thread.appendChild(bubble);
    scrollToBottom();
  }

  function renderChips() {
    const wrap = el(
      "div",
      `display:flex;flex-wrap:wrap;gap:8px;align-self:flex-start;margin-top:-4px;`
    );
    CHIPS.forEach((label) => {
      const chip = el(
        "button",
        `background:#fff;border:1px solid #E6E2D6;color:#1B2B4B;
         font-family:inherit;font-size:13px;padding:6px 12px;border-radius:999px;cursor:pointer;
         transition:background 0.15s ease;`,
        escapeHtml(label)
      );
      chip.addEventListener("mouseenter", () => (chip.style.background = "#F5F2E8"));
      chip.addEventListener("mouseleave", () => (chip.style.background = "#fff"));
      chip.addEventListener("click", () => {
        wrap.remove();
        send(label);
      });
      wrap.appendChild(chip);
    });
    thread.appendChild(wrap);
    scrollToBottom();
  }

  function renderTyping() {
    const dots = el(
      "div",
      `align-self:flex-start;display:flex;gap:4px;padding:14px 16px;
       background:#fff;border:1px solid #E6E2D6;border-radius:2px 14px 14px 14px;`,
      `<span class="cm-dot"></span><span class="cm-dot"></span><span class="cm-dot"></span>`
    );
    thread.appendChild(dots);
    scrollToBottom();
    return dots;
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      thread.scrollTop = thread.scrollHeight;
    });
  }

  function showToast(msg) {
    toast.textContent = msg;
    toast.style.display = "block";
    setTimeout(() => {
      toast.style.display = "none";
    }, 4000);
  }

  function setSendingState(on) {
    sendBtn.disabled = on;
    sendBtn.style.opacity = on ? "0.5" : "1";
    sendBtn.style.cursor = on ? "not-allowed" : "pointer";
    input.disabled = on;
  }

  // ---- Conversation loop ----
  let firstOpen = true;
  function openPanel() {
    panel.style.display = "flex";
    launcher.style.display = "none";
    if (firstOpen) {
      firstOpen = false;
      renderBot(GREETING, { greeting: true });
      renderChips();
      input.focus();
    } else {
      input.focus();
    }
  }

  function closePanel() {
    panel.style.display = "none";
    launcher.style.display = "flex";
  }

  launcher.addEventListener("click", openPanel);
  document.getElementById("cm-close").addEventListener("click", closePanel);

  inputRow.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    send(text);
  });

  async function send(text) {
    renderUser(text);
    setSendingState(true);
    const typing = renderTyping();

    try {
      const res = await fetch(ORCHESTRATOR_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          session_id: SESSION_ID,
          context: {},
        }),
      });
      typing.remove();
      if (!res.ok) {
        showToast(`Concierge offline (${res.status}). Retry?`);
        setSendingState(false);
        return;
      }
      const data = await res.json();
      const reply = (data && data.response) || "I'm having trouble — could you try again?";
      const agentLabel = data && AGENT_LABEL[data.routed_to];
      renderBot(reply, { agentLabel });
    } catch (err) {
      typing.remove();
      console.warn("widget send failed", err);
      showToast("Concierge offline. Retry?");
    } finally {
      setSendingState(false);
      input.focus();
    }
  }

  // Typing-dot animation + markdown styles, scoped so they only apply inside the bubble.
  const styleTag = document.createElement("style");
  styleTag.textContent = `
    .cm-dot {
      width:6px;height:6px;border-radius:50%;background:#1B2B4B;
      animation: cm-dot 1.4s infinite ease-in-out;
    }
    .cm-dot:nth-child(2) { animation-delay: 0.2s; }
    .cm-dot:nth-child(3) { animation-delay: 0.4s; }
    @keyframes cm-dot {
      0%, 80%, 100% { opacity: 0.25; transform: translateY(0); }
      40% { opacity: 1; transform: translateY(-2px); }
    }

    /* Markdown rendering inside bot bubbles */
    .cm-md > *:first-child { margin-top: 0; }
    .cm-md > *:last-child  { margin-bottom: 0; }
    .cm-md p { margin: 0 0 8px; }
    .cm-md ul, .cm-md ol { margin: 4px 0 8px; padding-left: 20px; }
    .cm-md li { margin: 2px 0; }
    .cm-md li > p { margin: 0; }
    .cm-md h1, .cm-md h2, .cm-md h3, .cm-md h4, .cm-md h5, .cm-md h6 {
      font-family: 'Playfair Display', serif;
      color: #1B2B4B;
      margin: 8px 0 4px;
      line-height: 1.3;
    }
    .cm-md h1 { font-size: 17px; }
    .cm-md h2 { font-size: 16px; }
    .cm-md h3, .cm-md h4, .cm-md h5, .cm-md h6 { font-size: 15px; }
    .cm-md a { color: #1B2B4B; text-decoration: underline; text-decoration-color: #C9A84C; }
    .cm-md a:hover { text-decoration-color: #1B2B4B; }
    .cm-md strong { font-weight: 600; color: #1B2B4B; }
    .cm-md em { font-style: italic; }
    .cm-md code {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12.5px;
      background: #F5F2E8;
      padding: 1px 5px;
      border-radius: 3px;
    }
    .cm-md pre {
      background: #FBFAF6;
      border: 1px solid #E6E2D6;
      padding: 8px 10px;
      margin: 6px 0;
      border-radius: 4px;
      overflow-x: auto;
      font-size: 12.5px;
      line-height: 1.45;
    }
    .cm-md pre code { background: transparent; padding: 0; }
    .cm-md blockquote {
      margin: 6px 0;
      padding: 2px 0 2px 10px;
      border-left: 2px solid #C9A84C;
      color: #4a4a4a;
    }
    .cm-md hr {
      border: none;
      border-top: 1px solid #E6E2D6;
      margin: 10px 0;
    }
    .cm-md table {
      border-collapse: collapse;
      margin: 6px 0;
      font-size: 13px;
    }
    .cm-md th, .cm-md td {
      border: 1px solid #E6E2D6;
      padding: 4px 8px;
      text-align: left;
    }
    .cm-md th { background: #FBFAF6; font-weight: 600; color: #1B2B4B; }
  `;
  document.head.appendChild(styleTag);
})();
