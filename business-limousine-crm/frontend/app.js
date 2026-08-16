/* Business Limousine — Dispatch Console frontend.
   Vanilla JS, no build step. Talks to the Flask API on the same origin. */

const STATUSES = [
  { key: "ALL", label: "All conversations" },
  { key: "NEW_REQUEST", label: "New requests" },
  { key: "DISCUSSION", label: "In discussion" },
  { key: "CONFIRMED", label: "Confirmed" },
  { key: "CLOSED", label: "Closed" },
  { key: "OTHER", label: "Other" },
];

const state = {
  status: "ALL",
  search: "",
  conversations: [],
  counts: {},
  selectedId: null,
  selectedDetail: null,
  tab: "thread",
};

const el = (id) => document.getElementById(id);

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatRelative(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffMs = Date.now() - then;
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function formatTimestamp(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

// -------------------------------------------------------------------------
// Sidebar / status nav
// -------------------------------------------------------------------------

function renderStatusNav() {
  const nav = el("status-nav");
  nav.innerHTML = STATUSES.map((s) => {
    const count = state.counts[s.key] ?? 0;
    const activeClass = s.key === state.status ? "active" : "";
    const dotClass = s.key === "ALL" ? "" : `status-${s.key}`;
    return `
      <button class="status-nav-item ${activeClass}" data-status="${s.key}">
        <span class="label">
          ${s.key !== "ALL" ? `<span class="dot ${dotClass}"></span>` : ""}
          ${s.label}
        </span>
        <span class="count">${count}</span>
      </button>
    `;
  }).join("");

  nav.querySelectorAll(".status-nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.status = btn.dataset.status;
      el("panel-title").textContent = STATUSES.find((s) => s.key === state.status).label;
      loadConversations();
    });
  });
}

// -------------------------------------------------------------------------
// Conversation list
// -------------------------------------------------------------------------

function routeLabel(convo) {
  if (convo.origin || convo.destination) {
    return `${convo.origin || "?"} → ${convo.destination || "?"}`;
  }
  return "";
}

function renderConversationList() {
  const listEl = el("conversation-list");
  const emptyEl = el("list-empty");

  if (state.conversations.length === 0) {
    listEl.innerHTML = "";
    emptyEl.hidden = false;
    return;
  }
  emptyEl.hidden = true;

  listEl.innerHTML = state.conversations.map((c) => {
    const selected = c.id === state.selectedId ? "selected" : "";
    const route = routeLabel(c);
    return `
      <button class="conversation-row ${selected}" data-id="${c.id}">
        <span class="row-tag status-${c.status}"></span>
        <span class="row-body">
          <span class="row-top">
            <span class="row-name">${escapeHtml(c.client_name || c.client_email)}</span>
            <span class="row-time mono">${formatRelative(c.last_message_at)}</span>
          </span>
          <span class="row-email">${escapeHtml(c.client_email)}</span>
          <span class="row-bottom">
            <span class="row-route ${route ? "" : "empty"}">${route ? escapeHtml(route) : "Route not set"}</span>
            <span class="status-pill status-${c.status}">${c.status.replace("_", " ")}</span>
          </span>
        </span>
      </button>
    `;
  }).join("");

  listEl.querySelectorAll(".conversation-row").forEach((row) => {
    row.addEventListener("click", () => selectConversation(Number(row.dataset.id)));
  });
}

async function loadConversations() {
  renderStatusNav();
  const params = new URLSearchParams();
  if (state.status !== "ALL") params.set("status", state.status);
  if (state.search) params.set("search", state.search);
  const data = await api(`/api/conversations?${params.toString()}`);
  state.conversations = data.conversations;
  state.counts = data.counts;
  renderStatusNav();
  renderConversationList();
}

// -------------------------------------------------------------------------
// Detail panel
// -------------------------------------------------------------------------

async function selectConversation(id) {
  state.selectedId = id;
  document.body.classList.add("mobile-detail-open");
  renderConversationList();
  const data = await api(`/api/conversations/${id}`);
  state.selectedDetail = data;
  renderDetail();
}

function renderStatusOptions(currentStatus) {
  return STATUSES.filter((s) => s.key !== "ALL").map((s) =>
    `<option value="${s.key}" ${s.key === currentStatus ? "selected" : ""}>${s.label}</option>`
  ).join("");
}

function renderDetail() {
  const { conversation, messages, notes } = state.selectedDetail;

  el("detail-empty").hidden = true;
  el("detail-content").hidden = false;

  el("client-name").textContent = conversation.client_name || conversation.client_email;
  el("client-email").textContent = conversation.client_email || "";
  el("client-phone").textContent = conversation.client_phone || "";

  const statusSelect = el("status-select");
  statusSelect.innerHTML = renderStatusOptions(conversation.status);
  statusSelect.className = `status-select status-${conversation.status}`;

  el("trip-origin").textContent = conversation.origin || "Origin —";
  el("trip-destination").textContent = conversation.destination || "Destination —";
  el("trip-date").textContent = conversation.trip_date || "Date not set";

  // pre-fill edit form
  el("edit-client-name").value = conversation.client_name || "";
  el("edit-client-phone").value = conversation.client_phone || "";
  el("edit-trip-date").value = conversation.trip_date || "";
  el("edit-origin").value = conversation.origin || "";
  el("edit-destination").value = conversation.destination || "";

  el("trip-edit").hidden = true;
  el("trip-card").hidden = false;

  renderThread(messages);
  renderNotes(notes);

  const lastInbound = [...messages].reverse().find((m) => m.direction === "inbound");
  el("reply-subject").value = lastInbound ? `Re: ${lastInbound.subject || ""}` : "";
  el("reply-body").value = "";
  el("reply-status").textContent = "";
  el("reply-status").className = "reply-status";

  setTab(state.tab);
}

function renderThread(messages) {
  const threadEl = el("thread");
  if (!messages || messages.length === 0) {
    threadEl.innerHTML = `<p style="color:var(--text-muted); font-size:13px; padding: 20px 0;">No messages yet.</p>`;
    return;
  }

  // Strict chronological sorting: oldest message at top, newest at bottom
  const sorted = [...messages].sort((a, b) => {
    const timeA = new Date(a.received_at || a.created_at || 0).getTime();
    const timeB = new Date(b.received_at || b.created_at || 0).getTime();
    if (timeA !== timeB) return timeA - timeB;
    return (a.id || 0) - (b.id || 0);
  });

  threadEl.innerHTML = sorted.map((m) => {
    const isInbound = m.direction === "inbound";
    const senderName = isInbound
      ? (state.selectedDetail?.conversation?.client_name || m.from_addr || "Client")
      : "Business Limousine";
    const avatarInitial = (senderName.trim().charAt(0) || (isInbound ? "C" : "B")).toUpperCase();

    // Clean up quoted email chains from main body
    let bodyText = (m.body_text || "").trim();
    let quotedText = "";
    const quotePattern = /(?:^|\n)(?:>|On\s+.+wrote:|Le\s+.+a\s+écrit\s*:|El\s+.+escribió:)/i;
    const match = bodyText.search(quotePattern);
    if (match !== -1 && match > 0) {
      quotedText = bodyText.slice(match).trim();
      bodyText = bodyText.slice(0, match).trim();
    }
    if (!bodyText && !quotedText) {
      bodyText = "(empty message)";
    }

    return `
      <div class="message ${m.direction}">
        <div class="message-header">
          <div class="message-avatar">${escapeHtml(avatarInitial)}</div>
          <div class="message-meta-info">
            <span class="message-author">${escapeHtml(senderName)}</span>
            <span class="message-time">${formatTimestamp(m.received_at || m.created_at)}</span>
          </div>
        </div>
        <div class="message-bubble">
          ${m.subject ? `<div class="message-subject">${escapeHtml(m.subject)}</div>` : ""}
          ${bodyText ? `<div class="message-body">${escapeHtml(bodyText)}</div>` : ""}
          ${quotedText ? `
            <details class="message-quote">
              <summary>Quoted email history</summary>
              <div class="message-quote-body">${escapeHtml(quotedText)}</div>
            </details>
          ` : ""}
        </div>
      </div>
    `;
  }).join("");

  setTimeout(() => {
    threadEl.scrollTop = threadEl.scrollHeight;
  }, 20);
}

function renderNotes(notes) {
  const notesEl = el("notes-list");
  if (notes.length === 0) {
    notesEl.innerHTML = `<div class="notes-empty">No internal notes yet.</div>`;
    return;
  }
  notesEl.innerHTML = notes.map((n) => `
    <div class="note-item">
      <div>${escapeHtml(n.note_text)}</div>
      <div class="note-time">${formatTimestamp(n.created_at)}${n.author ? " · " + escapeHtml(n.author) : ""}</div>
    </div>
  `).join("");
}

function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  el("tab-thread").hidden = tab !== "thread";
  el("tab-notes").hidden = tab !== "notes";
}

// -------------------------------------------------------------------------
// Event wiring
// -------------------------------------------------------------------------

function wireEvents() {
  let searchTimer = null;
  el("search-input").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.search = e.target.value.trim();
      loadConversations();
    }, 250);
  });

  el("back-btn").addEventListener("click", () => {
    document.body.classList.remove("mobile-detail-open");
  });

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => setTab(btn.dataset.tab));
  });

  el("status-select").addEventListener("change", async (e) => {
    const id = state.selectedId;
    const newStatus = e.target.value;
    await api(`/api/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: newStatus }),
    });
    await selectConversation(id);
    await loadConversations();
  });

  el("edit-trip-btn").addEventListener("click", () => {
    el("trip-edit").hidden = false;
  });
  el("cancel-trip-btn").addEventListener("click", () => {
    el("trip-edit").hidden = true;
  });
  el("save-trip-btn").addEventListener("click", async () => {
    const id = state.selectedId;
    await api(`/api/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({
        client_name: el("edit-client-name").value.trim(),
        client_phone: el("edit-client-phone").value.trim(),
        trip_date: el("edit-trip-date").value.trim(),
        origin: el("edit-origin").value.trim(),
        destination: el("edit-destination").value.trim(),
      }),
    });
    await selectConversation(id);
    await loadConversations();
  });

  el("reply-send-btn").addEventListener("click", async () => {
    const id = state.selectedId;
    const subject = el("reply-subject").value.trim();
    const body = el("reply-body").value.trim();
    const statusEl = el("reply-status");
    if (!body) {
      statusEl.textContent = "Write a message before sending.";
      statusEl.className = "reply-status error";
      return;
    }
    el("reply-send-btn").disabled = true;
    statusEl.textContent = "Sending…";
    statusEl.className = "reply-status";
    try {
      await api(`/api/conversations/${id}/reply`, {
        method: "POST",
        body: JSON.stringify({ subject, body_text: body }),
      });
      statusEl.textContent = "Sent.";
      statusEl.className = "reply-status ok";
      el("reply-body").value = "";
      await selectConversation(id);
    } catch (err) {
      statusEl.textContent = err.message;
      statusEl.className = "reply-status error";
    } finally {
      el("reply-send-btn").disabled = false;
    }
  });

  el("note-add-btn").addEventListener("click", async () => {
    const id = state.selectedId;
    const text = el("note-input").value.trim();
    if (!text) return;
    await api(`/api/conversations/${id}/notes`, {
      method: "POST",
      body: JSON.stringify({ note_text: text }),
    });
    el("note-input").value = "";
    await selectConversation(id);
  });

  el("sync-btn").addEventListener("click", async () => {
    const btn = el("sync-btn");
    const label = el("sync-label");
    btn.disabled = true;
    btn.classList.add("syncing");
    label.textContent = "Syncing…";
    try {
      const result = await api("/api/sync", { method: "POST" });
      el("sync-meta").textContent = `Last sync: ${result.processed} new message(s) · ${new Date().toLocaleTimeString()}`;
      await loadConversations();
      if (state.selectedId) await selectConversation(state.selectedId);
    } catch (err) {
      el("sync-meta").textContent = `Sync failed: ${err.message}`;
    } finally {
      btn.disabled = false;
      btn.classList.remove("syncing");
      label.textContent = "Sync inbox";
    }
  });
}

// -------------------------------------------------------------------------
// Init
// -------------------------------------------------------------------------

(async function init() {
  wireEvents();
  await loadConversations();
})();
