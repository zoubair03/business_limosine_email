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
  user: null,
  view: "conversations",
  waSettings: {
    enabled: true,
    threshold_minutes: 10,
    dispatcher_numbers: [],
  },
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
  if (res.status === 401) {
    state.user = null;
    showAuthModal();
    throw new Error(data.error || "Authentication required");
  }
  if (!res.ok) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

// -------------------------------------------------------------------------
// Authentication & User Profile
// -------------------------------------------------------------------------

function showAuthModal(errorMsg = null) {
  const modal = el("auth-modal");
  const errorEl = el("auth-error");
  modal.hidden = false;
  if (errorMsg) {
    errorEl.textContent = errorMsg;
    errorEl.hidden = false;
  } else {
    errorEl.hidden = true;
  }
}

function hideAuthModal() {
  el("auth-modal").hidden = true;
  el("auth-error").hidden = true;
}

function renderUserProfile() {
  if (!state.user) {
    el("user-profile-badge").style.display = "none";
    return;
  }
  el("user-profile-badge").style.display = "flex";
  el("user-name").textContent = state.user.full_name || state.user.email;
  el("user-role-pill").textContent = state.user.role || "STAFF";
  const avatarEl = el("user-avatar");
  avatarEl.textContent = (state.user.full_name || state.user.email || "U")[0].toUpperCase();
  if (state.user.avatar_color) {
    avatarEl.style.background = state.user.avatar_color;
  }
}

async function checkAuth() {
  try {
    const data = await api("/api/auth/me");
    if (data.authenticated && data.user) {
      state.user = data.user;
      renderUserProfile();
      hideAuthModal();
      return true;
    }
  } catch (err) {
    // 401 or network error
  }
  state.user = null;
  renderUserProfile();
  showAuthModal();
  return false;
}

async function handleLogin(email, password) {
  const submitBtn = el("login-submit-btn");
  submitBtn.disabled = true;
  submitBtn.textContent = "Signing In…";
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || "Invalid credentials");
    }
    state.user = data.user;
    state.status = "ALL"; // Always land on All Conversations
    state.selectedId = null; // Fresh dashboard on every login
    state.selectedDetail = null;
    setTab("thread"); // Always start on Thread tab
    if (el("panel-title")) el("panel-title").textContent = "All conversations";
    if (el("detail-empty")) el("detail-empty").hidden = false;
    if (el("detail-content")) el("detail-content").hidden = true;
    renderUserProfile();
    hideAuthModal();
    await loadConversations();
  } catch (err) {
    showAuthModal(err.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Sign In to Console";
  }
}

async function handleLogout() {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch (err) {}
  state.user = null;
  state.status = "ALL"; // Reset view filter to ALL
  state.selectedId = null;
  state.selectedDetail = null;
  setTab("thread");
  if (el("detail-empty")) el("detail-empty").hidden = false;
  if (el("detail-content")) el("detail-content").hidden = true;
  renderUserProfile();
  showAuthModal("You have been signed out.");
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
      switchView("conversations");
      loadConversations();
    });
  });
}

// -------------------------------------------------------------------------
// View switching (conversations manifest vs. settings screens)
// -------------------------------------------------------------------------

function switchView(view) {
  state.view = view;

  const convContent = el("conversations-content");
  const waView = el("whatsapp-settings-view");
  const waNavBtn = el("nav-whatsapp-btn");
  const searchWrap = el("topbar-search-wrap");
  const topbarActions = el("topbar-actions");
  const eyebrowEl = el("topbar-eyebrow");
  const titleEl = el("panel-title");

  if (view === "whatsapp-settings") {
    if (convContent) convContent.hidden = true;
    if (waView) waView.hidden = false;
    if (waNavBtn) waNavBtn.classList.add("active");
    document.querySelectorAll("#status-nav .status-nav-item").forEach((b) => b.classList.remove("active"));
    
    if (searchWrap) searchWrap.hidden = true;
    if (topbarActions) topbarActions.hidden = false;
    if (eyebrowEl) eyebrowEl.textContent = "AUTOMATION & ALERTS";
    if (titleEl) titleEl.textContent = "WhatsApp Dispatch Alerts";

    loadWhatsAppSettings();
  } else {
    if (convContent) convContent.hidden = false;
    if (waView) waView.hidden = true;
    if (waNavBtn) waNavBtn.classList.remove("active");
    
    const activeStatusBtn = document.querySelector(`#status-nav .status-nav-item[data-status="${state.status}"]`);
    if (activeStatusBtn) activeStatusBtn.classList.add("active");

    if (searchWrap) searchWrap.hidden = false;
    if (topbarActions) topbarActions.hidden = true;
    if (eyebrowEl) eyebrowEl.textContent = "Manifest";
    
    const currStatus = STATUSES.find((s) => s.key === state.status);
    if (titleEl) titleEl.textContent = currStatus ? currStatus.label : "All conversations";
  }
}

// -------------------------------------------------------------------------
// WhatsApp Alerts Operations & Settings
// -------------------------------------------------------------------------

function getCountryBadge(numberStr) {
  const s = String(numberStr || "").trim();
  if (s.startsWith("+32") || s.startsWith("0032")) return "🇧🇪 BE";
  if (s.startsWith("+33") || s.startsWith("0033")) return "🇫🇷 FR";
  if (s.startsWith("+41") || s.startsWith("0041")) return "🇨🇭 CH";
  if (s.startsWith("+1") || s.startsWith("001")) return "🇺🇸 US";
  if (s.startsWith("+44") || s.startsWith("0044")) return "🇬🇧 UK";
  if (s.startsWith("+49") || s.startsWith("0049")) return "🇩🇪 DE";
  if (s.startsWith("+39") || s.startsWith("0039")) return "🇮🇹 IT";
  if (s.startsWith("+34") || s.startsWith("0034")) return "🇪🇸 ES";
  if (s.startsWith("+216") || s.startsWith("00216")) return "🇹🇳 TN";
  return "🌐 INT";
}

function parseDispatcher(item) {
  if (typeof item === "object" && item !== null) {
    if (item.type === "telegram" || item.chat_id) {
      return { type: "telegram", id: String(item.chat_id || item.id || "").trim(), label: item.label || "Telegram Dispatcher" };
    }
    const phone = String(item.phone || "").trim();
    const apikey = String(item.apikey || "").trim();
    // Auto-promote: if stored as { phone: "7982805639", apikey: "" } it's actually a Telegram ID
    if (/^\d{6,}$/.test(phone) && !phone.startsWith("+") && !apikey) {
      return { type: "telegram", id: phone, label: "Telegram Dispatcher" };
    }
    return { type: "whatsapp", phone, apikey };
  }
  const s = String(item || "").trim();
  if (/^\d{6,}$/.test(s) && !s.startsWith("+")) {
    return { type: "telegram", id: s, label: "Telegram Dispatcher" };
  }
  if (s.includes(":")) {
    const parts = s.split(":");
    return { type: "whatsapp", phone: parts[0].trim(), apikey: parts[1].trim() };
  }
  return { type: "whatsapp", phone: s, apikey: "" };
}

async function loadWhatsAppSettings() {
  try {
    const data = await api("/api/settings/whatsapp");
    state.waSettings = {
      enabled: data.enabled !== false,
      threshold_minutes: data.threshold_minutes || 10,
      dispatcher_numbers: data.dispatcher_numbers && data.dispatcher_numbers.length > 0 ? data.dispatcher_numbers : ["8000019066"],
    };

    const toggle = el("wa-toggle-enabled");
    if (toggle) toggle.checked = state.waSettings.enabled;

    const pill = el("wa-active-pill");
    if (pill) {
      pill.textContent = state.waSettings.enabled ? "ACTIVE" : "PAUSED";
      pill.style.color = state.waSettings.enabled ? "#25D366" : "#EAB308";
    }

    const threshInput = el("wa-threshold-input");
    const threshSlider = el("wa-threshold-slider");
    const previewMins = el("wa-preview-minutes");
    if (threshInput) threshInput.value = state.waSettings.threshold_minutes;
    if (threshSlider) threshSlider.value = state.waSettings.threshold_minutes;
    if (previewMins) previewMins.textContent = state.waSettings.threshold_minutes;

    const gateway = data.gateway || {};
    const provider = data.provider || gateway.provider || "telegram";
    const tgInfo = data.telegram || gateway.telegram || {};

    const cardTitle = el("gateway-card-title");
    const sidEl = el("twilio-sid-display");
    const senderEl = el("twilio-sender-display");
    const badgeText = el("twilio-badge-text");
    const badgeDot = document.querySelector("#twilio-connection-badge .status-indicator-dot");

    if (provider === "telegram" || tgInfo.configured) {
      if (cardTitle) cardTitle.textContent = "Telegram Official Bot Gateway";
      if (sidEl) {
        sidEl.textContent = `@${tgInfo.bot_username || "BL_Dispatch_Bot"}`;
        sidEl.style.color = "#0088cc";
      }
      if (senderEl) {
        senderEl.textContent = "🟢 Connected (< 0.2s Instant Push)";
        senderEl.style.color = "#22C55E";
      }

      if (badgeText) badgeText.textContent = "Telegram Bot Active";
      if (badgeDot) badgeDot.className = "status-indicator-dot online";
    } else {
      if (cardTitle) cardTitle.textContent = "CallMeBot Gateway";
      if (sidEl) sidEl.textContent = "CallMeBot Active";
      if (senderEl) senderEl.textContent = "Ready";
      if (badgeText) badgeText.textContent = "Gateway Ready";
      if (badgeDot) badgeDot.className = "status-indicator-dot online";
    }

    renderWaNumbers();
  } catch (err) {
    console.error("Failed to load WhatsApp settings:", err);
  }
}

function renderWaNumbers() {
  const container = el("wa-number-list");
  if (!container) return;

  const rawList = state.waSettings.dispatcher_numbers && state.waSettings.dispatcher_numbers.length > 0 
    ? state.waSettings.dispatcher_numbers 
    : ["8000019066"];

  container.innerHTML = rawList.map((item, idx) => {
    const d = parseDispatcher(item);
    if (d.type === "telegram") {
      return `
        <div class="number-item" style="border-left: 3px solid #0088cc;">
          <div class="number-item-left">
            <span class="number-badge-country" style="background:rgba(0,136,204,0.15); color:#0088cc; border-color:rgba(0,136,204,0.3);">✈️ TG</span>
            <span class="number-text">ID: ${escapeHtml(d.id)}</span>
            <span class="number-key-badge" style="background:#F0F9FF; border-color:#BAE6FD; color:#0369A1;">👤 ${escapeHtml(d.label)}</span>
          </div>
          <button class="number-remove-btn" type="button" data-index="${idx}">Remove</button>
        </div>
      `;
    }

    const maskedKey = d.apikey ? (d.apikey.length > 3 ? "••••" + d.apikey.slice(-3) : d.apikey) : "Key Not Set";
    const keyClass = d.apikey ? "number-key-badge" : "number-key-badge error";

    return `
      <div class="number-item">
        <div class="number-item-left">
          <span class="number-badge-country">${getCountryBadge(d.phone)}</span>
          <span class="number-text">${escapeHtml(d.phone)}</span>
          <span class="${keyClass}">🔑 ${escapeHtml(maskedKey)}</span>
        </div>
        <button class="number-remove-btn" type="button" data-index="${idx}">Remove</button>
      </div>
    `;
  }).join("");

  container.querySelectorAll(".number-remove-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const index = Number(btn.dataset.index);
      state.waSettings.dispatcher_numbers.splice(index, 1);
      renderWaNumbers();
      await silentSaveDispatchers();
      // Show brief confirmation
      const feedback = el("wa-test-feedback");
      if (feedback) {
        feedback.textContent = "✓ Dispatcher removed and saved.";
        feedback.className = "test-feedback ok";
        feedback.hidden = false;
        setTimeout(() => { feedback.hidden = true; }, 2500);
      }
    });
  });
}

// Auto-persist the current dispatcher list to the backend silently
async function silentSaveDispatchers() {
  try {
    const toggle = el("wa-toggle-enabled");
    const threshInput = el("wa-threshold-input");
    await api("/api/settings/whatsapp", {
      method: "POST",
      body: JSON.stringify({
        enabled: toggle ? toggle.checked : true,
        threshold_minutes: threshInput ? parseInt(threshInput.value, 10) || 10 : 10,
        dispatcher_numbers: state.waSettings.dispatcher_numbers || [],
      }),
    });
  } catch (err) {
    console.warn("Auto-save dispatchers failed:", err.message);
  }
}

async function saveWhatsAppSettings() {
  const saveBtn = el("wa-save-btn");
  const statusMsg = el("wa-status-msg");
  const statusText = el("wa-status-text");
  
  if (saveBtn) saveBtn.disabled = true;

  const toggle = el("wa-toggle-enabled");
  const threshInput = el("wa-threshold-input");

  const payload = {
    enabled: toggle ? toggle.checked : true,
    threshold_minutes: threshInput ? parseInt(threshInput.value, 10) || 10 : 10,
    dispatcher_numbers: state.waSettings.dispatcher_numbers || [],
  };

  try {
    const res = await api("/api/settings/whatsapp", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    state.waSettings.enabled = payload.enabled;
    state.waSettings.threshold_minutes = payload.threshold_minutes;

    const pill = el("wa-active-pill");
    if (pill) {
      pill.textContent = payload.enabled ? "ACTIVE" : "PAUSED";
      pill.style.color = payload.enabled ? "#25D366" : "#EAB308";
    }

    if (statusMsg) {
      if (statusText) statusText.textContent = res.message || "Paramètres enregistrés.";
      statusMsg.classList.add("show");
      setTimeout(() => statusMsg.classList.remove("show"), 3500);
    }
  } catch (err) {
    if (statusMsg) {
      if (statusText) statusText.textContent = `Erreur : ${err.message}`;
      statusMsg.classList.add("show");
    }
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function sendTestWhatsAppAlert() {
  const testInput = el("wa-test-number-input");
  const testBtn = el("wa-send-test-btn");
  const testBtnText = el("wa-test-btn-text");
  const feedbackEl = el("wa-test-feedback");

  let rawTarget = testInput ? testInput.value.trim() : "";
  let phone = "";
  let apikey = "";

  if (rawTarget) {
    const parsed = parseDispatcher(rawTarget);
    if (parsed.type === "telegram") {
      // Telegram Chat ID — use id directly as the phone_number field
      phone = parsed.id;
    } else {
      phone = parsed.phone;
      apikey = parsed.apikey;
    }
  } else if (state.waSettings.dispatcher_numbers && state.waSettings.dispatcher_numbers.length > 0) {
    const parsed = parseDispatcher(state.waSettings.dispatcher_numbers[0]);
    if (parsed.type === "telegram") {
      phone = parsed.id;
      if (testInput) testInput.value = phone;
    } else {
      phone = parsed.phone;
      apikey = parsed.apikey;
      if (testInput) testInput.value = phone;
    }
  }

  if (!phone) {
    if (feedbackEl) {
      feedbackEl.textContent = "Please enter a Telegram Chat ID or phone number.";
      feedbackEl.className = "test-feedback error";
      feedbackEl.hidden = false;
    }
    return;
  }

  if (testBtn) testBtn.disabled = true;
  if (testBtnText) testBtnText.textContent = "Sending...";
  if (feedbackEl) feedbackEl.hidden = true;

  try {
    const result = await api("/api/settings/whatsapp/test", {
      method: "POST",
      body: JSON.stringify({ phone_number: phone, apikey }),
    });

    if (feedbackEl) {
      feedbackEl.textContent = result.message || `Test message sent successfully to ${phone}!`;
      feedbackEl.className = "test-feedback ok";
      feedbackEl.hidden = false;
    }
  } catch (err) {
    if (feedbackEl) {
      feedbackEl.textContent = `Error: ${err.message}`;
      feedbackEl.className = "test-feedback error";
      feedbackEl.hidden = false;
    }
  } finally {
    if (testBtn) testBtn.disabled = false;
    if (testBtnText) testBtnText.textContent = "⚡ Send Test Alert";
  }
}

// -------------------------------------------------------------------------
// Conversation list
// -------------------------------------------------------------------------

async function loadConversations() {
  if (!state.user) return;
  const params = new URLSearchParams();
  if (state.status !== "ALL") params.set("status", state.status);
  if (state.search) params.set("search", state.search);

  try {
    const data = await api(`/api/conversations?${params.toString()}`);
    state.conversations = data.conversations;
    state.counts = data.counts;
    renderStatusNav();
    renderConversations();

    if (state.selectedId) {
      const stillExists = state.conversations.some((c) => c.id === state.selectedId);
      if (stillExists) {
        selectConversation(state.selectedId, false);
      } else {
        state.selectedId = null;
        if (el("detail-empty")) el("detail-empty").hidden = false;
        if (el("detail-content")) el("detail-content").hidden = true;
      }
    }
  } catch (err) {
    console.error("Failed to load conversations:", err);
  }
}

function renderConversations() {
  const list = el("conversation-list");
  const empty = el("list-empty");
  if (state.conversations.length === 0) {
    list.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  list.innerHTML = state.conversations.map((c) => {
    const selected = c.id === state.selectedId ? "selected" : "";
    const name = c.client_name || c.client_email.split("@")[0];
    const route = (c.origin && c.destination)
      ? `${c.origin} → ${c.destination}`
      : (c.origin || c.destination || "");
    return `
      <button class="conversation-row ${selected}" data-id="${c.id}">
        <span class="row-tag status-${c.status}"></span>
        <span class="row-body">
          <span class="row-top">
            <span class="row-name">${escapeHtml(name)}</span>
            <span class="row-time mono">${formatRelative(c.last_message_at || c.created_at)}</span>
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

  list.querySelectorAll(".conversation-row").forEach((row) => {
    row.addEventListener("click", () => {
      selectConversation(Number(row.dataset.id), true, true);
    });
  });
}

// -------------------------------------------------------------------------
// Unread Notes Tracking
// -------------------------------------------------------------------------

function getNotesReadMap() {
  try {
    const key = `bl_notes_read_${state.user?.id || "anon"}`;
    return JSON.parse(localStorage.getItem(key) || "{}");
  } catch (e) {
    return {};
  }
}

function setConversationNotesRead(convoId) {
  if (!convoId) return;
  try {
    const key = `bl_notes_read_${state.user?.id || "anon"}`;
    const map = getNotesReadMap();
    map[convoId] = new Date().toISOString();
    localStorage.setItem(key, JSON.stringify(map));
  } catch (e) {}
}

function hasUnreadNotes(convoId, notes) {
  if (!notes || notes.length === 0) return false;
  const readMap = getNotesReadMap();
  const lastRead = readMap[convoId];
  if (!lastRead) return true;
  const lastReadTime = new Date(lastRead).getTime();
  return notes.some((n) => new Date(n.created_at).getTime() > lastReadTime);
}

// -------------------------------------------------------------------------
// Conversation detail
// -------------------------------------------------------------------------

async function selectConversation(id, reloadDetail = true, switchTab = false) {
  state.selectedId = id;
  if (switchTab) {
    setTab("thread"); // Only switch to Thread tab when selecting a conversation from the manifest
  }
  document.querySelectorAll(".conversation-row").forEach((row) => {
    row.classList.toggle("selected", Number(row.dataset.id) === id);
  });

  if (reloadDetail) {
    try {
      state.selectedDetail = await api(`/api/conversations/${id}`);
    } catch (err) {
      console.error(err);
      return;
    }
  }

  const { conversation, messages, notes } = state.selectedDetail;

  el("detail-empty").hidden = true;
  el("detail-content").hidden = false;
  document.body.classList.add("mobile-detail-open");

  el("client-name").textContent = conversation.client_name || conversation.client_email.split("@")[0];
  el("client-email").textContent = conversation.client_email || "";
  el("client-phone").textContent = conversation.client_phone || "";

  renderStatusSelect(conversation.status);

  el("trip-origin").textContent = conversation.origin || "Pickup not set";
  el("trip-destination").textContent = conversation.destination || "Drop-off not set";
  el("trip-date").textContent = conversation.trip_date || "Date not set";
  el("trip-edit").hidden = true;

  el("edit-client-name").value = conversation.client_name || "";
  el("edit-client-phone").value = conversation.client_phone || "";
  el("edit-trip-date").value = conversation.trip_date || "";
  el("edit-origin").value = conversation.origin || "";
  el("edit-destination").value = conversation.destination || "";

  // Check for unread notes to show glowing indicator circle
  const unreadNotesExist = hasUnreadNotes(id, notes);
  const dotEl = el("notes-unread-dot");
  if (dotEl) {
    if (unreadNotesExist && state.tab !== "notes") {
      dotEl.hidden = false;
    } else {
      dotEl.hidden = true;
    }
  }

  if (state.tab === "notes") {
    setConversationNotesRead(id);
    if (dotEl) dotEl.hidden = true;
  }

  renderThread(messages, conversation);
  renderNotes(notes);

  const lastInbound = [...messages].reverse().find((m) => m.direction === "inbound");
  const subj = lastInbound?.subject || "Your inquiry";
  el("reply-subject").value = subj.startsWith("Re:") ? subj : `Re: ${subj}`;
  el("reply-status").textContent = "";
}

function renderStatusSelect(currentStatus) {
  const sel = el("status-select");
  sel.innerHTML = STATUSES.filter((s) => s.key !== "ALL").map((s) => `
    <option value="${s.key}" ${s.key === currentStatus ? "selected" : ""}>
      ${s.label}
    </option>
  `).join("");
}

function renderThread(messages, convo) {
  const threadEl = el("thread");
  if (!messages || messages.length === 0) {
    threadEl.innerHTML = `<div style="color:var(--text-muted); font-size:13px; padding: 20px 0;">No messages in this thread yet.</div>`;
    return;
  }

  const sortedMessages = [...messages].sort((a, b) => {
    const timeA = new Date(a.received_at || a.created_at || 0).getTime();
    const timeB = new Date(b.received_at || b.created_at || 0).getTime();
    return timeA - timeB;
  });

  threadEl.innerHTML = sortedMessages.map((m) => {
    const isInbound = m.direction === "inbound";
    const senderName = isInbound
      ? (convo?.client_name || m.from_addr || "Client")
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
            ${m.ai_category ? `<span class="status-pill status-${m.ai_category}" style="font-size: 8.5px; padding: 1px 5px;">${m.ai_category}</span>` : ""}
          </div>
        </div>
        <div class="message-bubble">
          ${m.subject ? `<div class="message-subject">${escapeHtml(m.subject)}</div>` : ""}
          <div class="message-body">${escapeHtml(bodyText)}</div>
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
  if (!notes || notes.length === 0) {
    notesEl.innerHTML = `<div class="notes-empty">No internal notes yet. Notes are saved to your staff account.</div>`;
    return;
  }

  const readMap = getNotesReadMap();
  const lastReadStr = readMap[state.selectedId];
  const lastReadTime = lastReadStr ? new Date(lastReadStr).getTime() : 0;

  notesEl.innerHTML = notes.map((n) => {
    const authorName = escapeHtml(n.author || "Staff");
    const roleClass = (n.author_role || "dispatcher").toLowerCase();
    const roleLabel = escapeHtml(n.author_role || "STAFF");
    const isUnread = state.tab !== "notes" && lastReadTime > 0 && new Date(n.created_at).getTime() > lastReadTime;

    return `
      <div class="note-item ${isUnread ? "unread" : ""}">
        <div class="note-header">
          <span class="note-author-badge">${authorName}</span>
          <span class="note-role-tag ${roleClass}">${roleLabel}</span>
          ${isUnread ? `<span class="note-unread-tag">New</span>` : ""}
          <span class="note-time">${formatTimestamp(n.created_at)}</span>
        </div>
        <div>${escapeHtml(n.note_text)}</div>
      </div>
    `;
  }).join("");
}

function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  el("tab-thread").hidden = tab !== "thread";
  el("tab-notes").hidden = tab !== "notes";

  if (tab === "notes" && state.selectedId) {
    setConversationNotesRead(state.selectedId);
    const dotEl = el("notes-unread-dot");
    if (dotEl) dotEl.hidden = true;
    if (state.selectedDetail?.notes) {
      renderNotes(state.selectedDetail.notes);
    }
  }
}



// -------------------------------------------------------------------------
// Event wiring
// -------------------------------------------------------------------------

function wireEvents() {
  // Login form
  el("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = el("login-email").value.trim();
    const password = el("login-password").value;
    await handleLogin(email, password);
  });

  // Staff credential autofill shortcuts
  el("demo-admin-btn")?.addEventListener("click", () => {
    el("login-email").value = "admin@businesslimousine.com";
    el("login-password").value = "admin123";
    handleLogin("admin@businesslimousine.com", "admin123");
  });

  el("demo-iheb-btn")?.addEventListener("click", () => {
    el("login-email").value = "iheb@businesslimousine.com";
    el("login-password").value = "dispatch123";
    handleLogin("iheb@businesslimousine.com", "dispatch123");
  });

  el("demo-zoubair-btn")?.addEventListener("click", () => {
    el("login-email").value = "zoubair@businesslimousine.com";
    el("login-password").value = "dispatch123";
    handleLogin("zoubair@businesslimousine.com", "dispatch123");
  });

  // Logout button
  el("logout-btn")?.addEventListener("click", handleLogout);

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

  el("note-add-btn").addEventListener("click", async (e) => {
    e?.preventDefault?.();
    const id = state.selectedId;
    const input = el("note-input");
    const text = input.value.trim();
    if (!text) return;
    const addBtn = el("note-add-btn");
    addBtn.disabled = true;
    try {
      await api(`/api/conversations/${id}/notes`, {
        method: "POST",
        body: JSON.stringify({ note_text: text }),
      });
      input.value = "";
      const detail = await api(`/api/conversations/${id}`);
      state.selectedDetail = detail;
      setConversationNotesRead(id);
      renderNotes(detail.notes);
      const dotEl = el("notes-unread-dot");
      if (dotEl) dotEl.hidden = true;
    } catch (err) {
      console.error("Failed to add note:", err);
    } finally {
      addBtn.disabled = false;
    }
  });

  const navWaBtn = el("nav-whatsapp-btn") || el("whatsapp-settings-nav-btn");
  if (navWaBtn) {
    navWaBtn.addEventListener("click", () => {
      switchView("whatsapp-settings");
    });
  }

  const backManifestBtn = el("btn-back-to-manifest");
  if (backManifestBtn) {
    backManifestBtn.addEventListener("click", () => {
      switchView("conversations");
    });
  }

  const toggleWa = el("wa-toggle-enabled");
  if (toggleWa) {
    toggleWa.addEventListener("change", (e) => {
      const pill = el("wa-active-pill");
      if (pill) {
        pill.textContent = e.target.checked ? "ACTIVE" : "PAUSED";
        pill.style.color = e.target.checked ? "#25D366" : "#EAB308";
      }
    });
  }

  const threshSlider = el("wa-threshold-slider");
  const threshInput = el("wa-threshold-input");
  const threshPreview = el("wa-preview-minutes");

  if (threshSlider) {
    threshSlider.addEventListener("input", (e) => {
      if (threshInput) threshInput.value = e.target.value;
      if (threshPreview) threshPreview.textContent = e.target.value;
    });
  }

  if (threshInput) {
    threshInput.addEventListener("input", (e) => {
      const v = e.target.value || "10";
      if (threshSlider) threshSlider.value = v;
      if (threshPreview) threshPreview.textContent = v;
    });
  }

  const addNumBtn = el("wa-add-number-btn");
  const newNumInput = el("wa-new-number-input");
  const newKeyInput = el("wa-new-key-input");

  async function doAddDispatcher() {
    const rawInput = newNumInput ? newNumInput.value.trim() : "";
    const apikey  = newKeyInput  ? newKeyInput.value.trim()  : "";
    if (!rawInput) return;
    if (!state.waSettings.dispatcher_numbers) state.waSettings.dispatcher_numbers = [];

    // Detect Telegram Chat ID: pure digits, 6+ chars, no + prefix
    const isTelegramId = /^\d{6,}$/.test(rawInput) && !rawInput.startsWith("+");

    let newEntry;
    if (isTelegramId && !apikey) {
      // Store as Telegram recipient
      newEntry = { type: "telegram", chat_id: rawInput, label: "Telegram Dispatcher" };
    } else {
      // Store as WhatsApp (CallMeBot) recipient
      newEntry = { phone: rawInput, apikey: apikey || "" };
    }

    state.waSettings.dispatcher_numbers.push(newEntry);
    if (newNumInput) newNumInput.value = "";
    if (newKeyInput) newKeyInput.value = "";
    renderWaNumbers();

    // Auto-save immediately so the entry persists after page reload
    await silentSaveDispatchers();

    // Brief success toast
    const feedback = el("wa-test-feedback");
    if (feedback) {
      const label = isTelegramId && !apikey
        ? `✓ Telegram ID ${rawInput} added and saved!`
        : `✓ ${rawInput} added and saved!`;
      feedback.textContent = label;
      feedback.className = "test-feedback ok";
      feedback.hidden = false;
      setTimeout(() => { feedback.hidden = true; }, 3000);
    }
  }

  if (addNumBtn) {
    addNumBtn.addEventListener("click", doAddDispatcher);
  }

  if (newNumInput) {
    newNumInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        // If there's a key input and it's empty, move focus there (WhatsApp flow)
        // But for Telegram IDs (pure digits), just add immediately
        const rawVal = newNumInput.value.trim();
        const looksLikeTelegram = /^\d{6,}$/.test(rawVal) && !rawVal.startsWith("+");
        if (newKeyInput && !newKeyInput.value && !looksLikeTelegram) {
          newKeyInput.focus();
        } else {
          doAddDispatcher();
        }
      }
    });
  }

  if (newKeyInput) {
    newKeyInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (addNumBtn) addNumBtn.click();
      }
    });
  }

  const saveWaBtn = el("wa-save-btn");
  if (saveWaBtn) {
    saveWaBtn.addEventListener("click", saveWhatsAppSettings);
  }

  const testWaBtn = el("wa-send-test-btn");
  if (testWaBtn) {
    testWaBtn.addEventListener("click", sendTestWhatsAppAlert);
  }

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
  const authenticated = await checkAuth();
  if (authenticated) {
    await loadConversations();
  }
})();
