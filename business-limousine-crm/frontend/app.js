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
  recentNotes: [],
  notesFilter: "unread",
  notifOpen: false,
  composerAttachments: [],
  expandedMessages: {},
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

function safeDate(iso) {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  } catch (e) {
    return null;
  }
}

function safeUtcString(iso) {
  const d = safeDate(iso);
  return d ? d.toUTCString() : (iso || "");
}

function formatRelative(iso) {
  const d = safeDate(iso);
  if (!d) return "";
  try {
    const diffMs = Date.now() - d.getTime();
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.round(hours / 24);
    if (days < 7) return `${days}d ago`;
    return d.toLocaleDateString();
  } catch (e) {
    return "";
  }
}

function formatTimestamp(iso) {
  const d = safeDate(iso);
  if (!d) return iso || "";
  try {
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    });
  } catch (e) {
    return iso || "";
  }
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
    if (el("user-profile-badge")) el("user-profile-badge").style.display = "none";
    if (el("nav-users-settings")) el("nav-users-settings").style.display = "none";
    return;
  }
  if (el("user-profile-badge")) el("user-profile-badge").style.display = "flex";
  if (el("user-name")) el("user-name").textContent = state.user.full_name || state.user.email;
  if (el("user-role-pill")) el("user-role-pill").textContent = state.user.role || "STAFF";
  const avatarEl = el("user-avatar");
  if (avatarEl) {
    avatarEl.textContent = (state.user.full_name || state.user.email || "U")[0].toUpperCase();
    if (state.user.avatar_color) {
      avatarEl.style.background = state.user.avatar_color;
    }
  }

  const userNav = el("nav-users-settings");
  if (userNav) {
    userNav.style.display = state.user.role === "ADMIN" ? "flex" : "none";
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
  const emailView = el("email-settings-view");
  const usersView = el("users-settings-view");

  const waNavBtn = el("nav-whatsapp-btn");
  const emailNavBtn = el("nav-email-settings-btn");
  const usersNavBtn = el("nav-users-settings");

  const searchWrap = el("topbar-search-wrap");
  const topbarActions = el("topbar-actions");
  const eyebrowEl = el("topbar-eyebrow");
  const titleEl = el("panel-title");

  // Deactivate all sidebar items first
  document.querySelectorAll("#status-nav .status-nav-item, .system-nav .status-nav-item").forEach((b) => {
    b.classList.remove("active");
  });

  if (view === "whatsapp-settings") {
    if (convContent) convContent.hidden = true;
    if (emailView) emailView.hidden = true;
    if (usersView) usersView.hidden = true;
    if (waView) waView.hidden = false;
    if (waNavBtn) waNavBtn.classList.add("active");
    
    if (searchWrap) searchWrap.hidden = true;
    if (topbarActions) topbarActions.hidden = false;
    if (eyebrowEl) eyebrowEl.textContent = "AUTOMATION & ALERTS";
    if (titleEl) titleEl.textContent = "Telegram & Dispatch Alerts";

    loadWhatsAppSettings();
  } else if (view === "email-settings") {
    if (convContent) convContent.hidden = true;
    if (waView) waView.hidden = true;
    if (usersView) usersView.hidden = true;
    if (emailView) emailView.hidden = false;
    if (emailNavBtn) emailNavBtn.classList.add("active");

    if (searchWrap) searchWrap.hidden = true;
    if (topbarActions) topbarActions.hidden = false;
    if (eyebrowEl) eyebrowEl.textContent = "SETTINGS & IDENTITY";
    if (titleEl) titleEl.textContent = "Email & Signature Settings";

    loadEmailSettings();
  } else if (view === "users-settings") {
    if (convContent) convContent.hidden = true;
    if (waView) waView.hidden = true;
    if (emailView) emailView.hidden = true;
    if (usersView) usersView.hidden = false;
    if (usersNavBtn) usersNavBtn.classList.add("active");

    if (searchWrap) searchWrap.hidden = true;
    if (topbarActions) topbarActions.hidden = false;
    if (eyebrowEl) eyebrowEl.textContent = "TEAM & ACCESS";
    if (titleEl) titleEl.textContent = "Team & User Management";

    loadUsers();
  } else {
    if (convContent) convContent.hidden = false;
    if (waView) waView.hidden = true;
    if (emailView) emailView.hidden = true;
    if (usersView) usersView.hidden = true;
    
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
// Email & Signature Settings Operations
// -------------------------------------------------------------------------

const DEFAULT_OFFICIAL_HTML_SIG = `<div style="font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px; color: #333333; line-height: 1.5; margin-top: 20px;">
  <div style="font-weight: bold; font-size: 15px; color: #1E293B;">Lasaad</div>
  <div style="color: #2563EB; font-weight: 600; margin: 2px 0;">Phone: <a href="tel:+32487446773" style="color: #2563EB; text-decoration: none;">+32 487 44 67 73</a></div>
  <div style="color: #B45309; font-size: 11.5px; font-weight: 500; margin: 4px 0 10px;">Kind regards | Met vriendelijke groet | Kind regards | مع أطيب التحيات | С уважением</div>
  <div style="border-top: 1px solid #E2E8F0; padding-top: 8px;">
    <div style="font-weight: 700; color: #0F172A; text-decoration: underline; font-size: 12.5px;">Business Limousine Services - Worldwide Travel Services</div>
    <div style="color: #64748B; font-size: 11.5px; margin-top: 2px;">Groundtransportation | Private Aviation | Concierge | Bodyguard</div>
  </div>
</div>`;

async function loadEmailSettings() {
  try {
    const data = await api("/api/settings/email");
    state.emailSettings = data;

    const nameInput = el("setting-dispatcher-name");
    const phoneInput = el("setting-dispatcher-phone");
    const ccInput = el("setting-default-cc");
    const textInput = el("setting-signature-text");
    const htmlInput = el("setting-signature-html");

    if (nameInput) nameInput.value = data.dispatcher_name || "Lasaad";
    if (phoneInput) phoneInput.value = data.dispatcher_phone || "+32 487 44 67 73";
    if (ccInput) ccInput.value = data.default_cc_email || "info@business-limousine.be";
    if (textInput) textInput.value = data.signature_text || OFFICIAL_SIGNATURE;
    if (htmlInput) htmlInput.value = data.signature_html || DEFAULT_OFFICIAL_HTML_SIG;

    updateSigLivePreview();
    updateComposerSignatureRender();
  } catch (err) {
    console.error("Failed to load email settings:", err);
  }
}

function updateComposerSignatureRender() {
  const sigContainer = el("composer-signature-html-render");
  if (!sigContainer) return;
  const htmlSig = state.emailSettings?.signature_html || DEFAULT_OFFICIAL_HTML_SIG;
  sigContainer.innerHTML = htmlSig;
}

function updateSigLivePreview() {
  const previewArea = el("sig-preview-render-area");
  if (!previewArea) return;

  const isHtmlTab = el("sigtab-btn-html")?.classList.contains("active");
  const htmlCode = el("setting-signature-html")?.value || "";
  const textCode = el("setting-signature-text")?.value || "";

  if (isHtmlTab && htmlCode.trim()) {
    previewArea.innerHTML = htmlCode;
  } else {
    const cleanText = textCode.trim() || OFFICIAL_SIGNATURE;
    previewArea.innerHTML = `<div class="sig-preview-text-rendered">${escapeHtml(cleanText)}</div>`;
  }
}

async function saveEmailSettings() {
  const saveBtn = el("btn-save-email-settings");
  const feedbackEl = el("email-settings-feedback");
  const btnText = el("save-email-settings-btn-text");

  const name = el("setting-dispatcher-name")?.value.trim() || "Lasaad";
  const phone = el("setting-dispatcher-phone")?.value.trim() || "+32 487 44 67 73";
  const defaultCc = el("setting-default-cc")?.value.trim() || "info@business-limousine.be";
  const sigText = el("setting-signature-text")?.value || "";
  const sigHtml = el("setting-signature-html")?.value || "";

  if (saveBtn) saveBtn.disabled = true;
  if (btnText) btnText.textContent = "Saving…";

  try {
    const result = await api("/api/settings/email", {
      method: "POST",
      body: JSON.stringify({
        dispatcher_name: name,
        dispatcher_phone: phone,
        default_cc_email: defaultCc,
        signature_text: sigText,
        signature_html: sigHtml,
      }),
    });

    state.emailSettings = {
      dispatcher_name: name,
      dispatcher_phone: phone,
      default_cc_email: defaultCc,
      signature_text: sigText,
      signature_html: sigHtml,
    };

    updateComposerSignatureRender();

    if (feedbackEl) {
      feedbackEl.textContent = "✓ Email & signature settings saved successfully!";
      feedbackEl.className = "test-feedback ok";
      feedbackEl.hidden = false;
      setTimeout(() => { feedbackEl.hidden = true; }, 3500);
    }
  } catch (err) {
    if (feedbackEl) {
      feedbackEl.textContent = `Error: ${err.message}`;
      feedbackEl.className = "test-feedback error";
      feedbackEl.hidden = false;
    }
  } finally {
    if (saveBtn) saveBtn.disabled = false;
    if (btnText) btnText.textContent = "💾 Save Email Settings";
  }
}

function resetDefaultSignature() {
  const textInput = el("setting-signature-text");
  const htmlInput = el("setting-signature-html");
  const nameInput = el("setting-dispatcher-name");
  const phoneInput = el("setting-dispatcher-phone");
  const ccInput = el("setting-default-cc");

  if (nameInput) nameInput.value = "Lasaad";
  if (phoneInput) phoneInput.value = "+32 487 44 67 73";
  if (ccInput) ccInput.value = "info@business-limousine.be";
  if (textInput) textInput.value = OFFICIAL_SIGNATURE;
  if (htmlInput) htmlInput.value = DEFAULT_OFFICIAL_HTML_SIG;

  updateSigLivePreview();

  const feedbackEl = el("email-settings-feedback");
  if (feedbackEl) {
    feedbackEl.textContent = "↺ Reset to official Business Limousine template. Remember to click Save.";
    feedbackEl.className = "test-feedback ok";
    feedbackEl.hidden = false;
    setTimeout(() => { feedbackEl.hidden = true; }, 3000);
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
// Team & User Management (Admin Only)
// -------------------------------------------------------------------------

let usersDataCache = [];

async function loadUsers() {
  if (!state.user || state.user.role !== "ADMIN") return;
  try {
    const data = await api("/api/users");
    usersDataCache = data.users || [];
    renderUsersTable();
  } catch (err) {
    console.error("Failed to load users:", err);
  }
}

function renderUsersTable(filter = "") {
  const tbody = el("users-table-body");
  const countEl = el("users-total-count");
  if (!tbody) return;

  const query = (filter || "").toLowerCase().trim();
  const filtered = usersDataCache.filter((u) => {
    if (!query) return true;
    return (
      (u.full_name || "").toLowerCase().includes(query) ||
      (u.email || "").toLowerCase().includes(query) ||
      (u.role || "").toLowerCase().includes(query)
    );
  });

  if (countEl) countEl.textContent = filtered.length;

  if (filtered.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 32px 16px;">
          No team members found matching your search.
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = filtered.map((u) => {
    const isYou = state.user && state.user.id === u.id;
    const roleLower = (u.role || "dispatcher").toLowerCase();
    const active = u.is_active !== undefined ? Boolean(u.is_active) : true;
    const initials = (u.full_name || u.email || "U")
      .split(" ")
      .map((w) => w[0])
      .slice(0, 2)
      .join("")
      .toUpperCase();

    return `
      <tr data-user-id="${u.id}">
        <td>
          <div class="user-cell-flex">
            <div class="user-table-avatar" style="background: ${u.avatar_color || "#C5A059"};">
              ${escapeHtml(initials)}
            </div>
            <div>
              <span class="user-table-name">${escapeHtml(u.full_name)}</span>
              ${isYou ? `<span class="user-table-you-pill">YOU</span>` : ""}
            </div>
          </div>
        </td>
        <td class="mono" style="color: var(--text-muted); font-size: 12px;">${escapeHtml(u.email)}</td>
        <td>
          <span class="user-role-badge ${roleLower}">${escapeHtml(u.role)}</span>
        </td>
        <td>
          <span class="user-status-badge ${active ? "active" : "disabled"}">
            ${active ? "Active" : "Disabled"}
          </span>
        </td>
        <td style="color: var(--text-muted); font-size: 11.5px;">${formatRelative(u.created_at)}</td>
        <td>
          <div class="user-actions-cell">
            <button type="button" class="btn-user-action btn-edit-user" data-user-id="${u.id}" title="Edit User">
              ✏️ Edit
            </button>
            ${
              !isYou
                ? `
              <button type="button" class="btn-user-action btn-toggle-user" data-user-id="${u.id}" data-active="${active}" title="${active ? "Disable Account" : "Enable Account"}">
                ${active ? "⏸ Disable" : "▶ Enable"}
              </button>
              <button type="button" class="btn-user-action danger btn-delete-user" data-user-id="${u.id}" data-email="${escapeHtml(u.email)}" title="Delete User">
                🗑
              </button>
            `
                : ""
            }
          </div>
        </td>
      </tr>
    `;
  }).join("");

  // Bind actions
  tbody.querySelectorAll(".btn-edit-user").forEach((btn) => {
    btn.addEventListener("click", () => {
      const uid = Number(btn.dataset.userId);
      const targetUser = usersDataCache.find((u) => u.id === uid);
      if (targetUser) openEditUserModal(targetUser);
    });
  });

  tbody.querySelectorAll(".btn-toggle-user").forEach((btn) => {
    btn.addEventListener("click", () => {
      const uid = Number(btn.dataset.userId);
      const curActive = btn.dataset.active === "true";
      handleToggleUserStatus(uid, curActive);
    });
  });

  tbody.querySelectorAll(".btn-delete-user").forEach((btn) => {
    btn.addEventListener("click", () => {
      const uid = Number(btn.dataset.userId);
      const email = btn.dataset.email;
      handleDeleteUser(uid, email);
    });
  });
}

function openAddUserModal() {
  const modal = el("user-modal");
  const form = el("user-edit-form");
  const title = el("user-modal-title");
  const idInput = el("user-form-id");
  const nameInput = el("user-form-fullname");
  const emailInput = el("user-form-email");
  const roleSelect = el("user-form-role");
  const colorSelect = el("user-form-avatar-color");
  const pwInput = el("user-form-password");
  const pwLabel = el("user-form-password-label");
  const pwHint = el("user-form-password-hint");
  const feedback = el("user-modal-feedback");

  if (!modal || !form) return;

  if (form) form.reset();
  if (idInput) idInput.value = "";
  if (title) title.textContent = "Add New Team Member";
  if (emailInput) emailInput.disabled = false;
  if (pwInput) {
    pwInput.required = true;
    pwInput.placeholder = "Minimum 6 characters";
  }
  if (pwLabel) pwLabel.textContent = "Password *";
  if (pwHint) pwHint.style.display = "none";
  if (feedback) feedback.hidden = true;

  modal.hidden = false;
  if (nameInput) nameInput.focus();
}

function openEditUserModal(user) {
  const modal = el("user-modal");
  const title = el("user-modal-title");
  const idInput = el("user-form-id");
  const nameInput = el("user-form-fullname");
  const emailInput = el("user-form-email");
  const roleSelect = el("user-form-role");
  const colorSelect = el("user-form-avatar-color");
  const pwInput = el("user-form-password");
  const pwLabel = el("user-form-password-label");
  const pwHint = el("user-form-password-hint");
  const feedback = el("user-modal-feedback");

  if (!modal) return;

  if (idInput) idInput.value = user.id;
  if (title) title.textContent = `Edit User — ${user.full_name || user.email}`;
  if (nameInput) nameInput.value = user.full_name || "";
  if (emailInput) {
    emailInput.value = user.email || "";
    emailInput.disabled = true;
  }
  if (roleSelect) roleSelect.value = user.role || "DISPATCHER";
  if (colorSelect) colorSelect.value = user.avatar_color || "#C5A059";
  if (pwInput) {
    pwInput.value = "";
    pwInput.required = false;
    pwInput.placeholder = "Leave blank to keep existing password";
  }
  if (pwLabel) pwLabel.textContent = "Reset Password (Optional)";
  if (pwHint) pwHint.style.display = "block";
  if (feedback) feedback.hidden = true;

  modal.hidden = false;
}

function closeUserModal() {
  const modal = el("user-modal");
  if (modal) modal.hidden = true;
}

async function handleSaveUser(e) {
  e.preventDefault();
  const idInput = el("user-form-id");
  const nameInput = el("user-form-fullname");
  const emailInput = el("user-form-email");
  const roleSelect = el("user-form-role");
  const colorSelect = el("user-form-avatar-color");
  const pwInput = el("user-form-password");
  const saveBtn = el("user-modal-save-btn");
  const feedback = el("user-modal-feedback");

  const userId = idInput ? idInput.value : "";
  const fullName = nameInput ? nameInput.value.trim() : "";
  const email = emailInput ? emailInput.value.trim().toLowerCase() : "";
  const role = roleSelect ? roleSelect.value : "DISPATCHER";
  const avatarColor = colorSelect ? colorSelect.value : "#C5A059";
  const password = pwInput ? pwInput.value : "";

  if (!fullName || (!userId && !email)) {
    if (feedback) {
      feedback.textContent = "Please fill in all required fields.";
      feedback.className = "test-feedback error";
      feedback.hidden = false;
    }
    return;
  }

  if (saveBtn) saveBtn.disabled = true;

  try {
    if (userId) {
      const payload = {
        full_name: fullName,
        role: role,
        avatar_color: avatarColor,
      };
      if (password) payload.password = password;

      await api(`/api/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
    } else {
      if (!password || password.length < 4) {
        throw new Error("Password must be at least 4 characters.");
      }
      await api("/api/users", {
        method: "POST",
        body: JSON.stringify({
          full_name: fullName,
          email: email,
          password: password,
          role: role,
          avatar_color: avatarColor,
        }),
      });
    }

    closeUserModal();
    await loadUsers();

    if (state.user && state.user.id === Number(userId)) {
      state.user.full_name = fullName;
      state.user.avatar_color = avatarColor;
      state.user.role = role;
      renderUserProfile();
    }
  } catch (err) {
    if (feedback) {
      feedback.textContent = `Error: ${err.message}`;
      feedback.className = "test-feedback error";
      feedback.hidden = false;
    }
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function handleToggleUserStatus(userId, currentActive) {
  try {
    await api(`/api/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: !currentActive }),
    });
    await loadUsers();
  } catch (err) {
    alert(`Could not update user status: ${err.message}`);
  }
}

async function handleDeleteUser(userId, userEmail) {
  if (!confirm(`Are you sure you want to permanently delete user "${userEmail}"?`)) {
    return;
  }
  try {
    await api(`/api/users/${userId}`, { method: "DELETE" });
    await loadUsers();
  } catch (err) {
    alert(`Could not delete user: ${err.message}`);
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
    await fetchRecentNotes();

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
}

// -------------------------------------------------------------------------
// Unread Notes & Notifications Tracking
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

async function fetchRecentNotes() {
  if (!state.user) return;
  try {
    const data = await api("/api/notes/recent?limit=50");
    const notes = data.notes || [];
    const readMap = getNotesReadMap();

    notes.forEach((n) => {
      const lastReadStr = readMap[n.conversation_id];
      const lastReadTime = lastReadStr ? new Date(lastReadStr).getTime() : 0;
      n.isUnread = !lastReadStr || new Date(n.created_at).getTime() > lastReadTime;
    });

    state.recentNotes = notes;
    updateNotificationUI();
  } catch (err) {
    console.error("Failed to fetch recent notes:", err);
  }
}

function updateNotificationUI() {
  const notes = state.recentNotes || [];
  const unreadCount = notes.filter((n) => n.isUnread).length;

  const badgeEl = el("notif-badge");
  if (badgeEl) {
    if (unreadCount > 0) {
      badgeEl.textContent = unreadCount > 99 ? "99+" : unreadCount;
      badgeEl.hidden = false;
    } else {
      badgeEl.hidden = true;
    }
  }

  const pillEl = el("notif-unread-pill");
  if (pillEl) {
    if (unreadCount > 0) {
      pillEl.textContent = `${unreadCount} new`;
      pillEl.hidden = false;
    } else {
      pillEl.hidden = true;
    }
  }

  const tabUnreadCountEl = el("notif-unread-tab-count");
  if (tabUnreadCountEl) {
    tabUnreadCountEl.textContent = unreadCount;
  }

  renderNotificationItems();
}

function renderNotificationItems() {
  const container = el("notif-list-container");
  if (!container) return;

  const filter = state.notesFilter || "unread";
  const allNotes = state.recentNotes || [];
  const filtered = filter === "unread" ? allNotes.filter((n) => n.isUnread) : allNotes;

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="notif-empty-state">
        <svg class="notif-empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path>
          <path d="M13.73 21a2 2 0 0 1-3.46 0"></path>
        </svg>
        <div class="notif-empty-title">${filter === "unread" ? "No unread notes" : "No notes yet"}</div>
        <div class="notif-empty-desc">${filter === "unread" ? "You're all caught up! No unread team notes." : "Internal notes created on reservations will appear here."}</div>
      </div>
    `;
    return;
  }

  container.innerHTML = filtered.map((n) => {
    const authorName = escapeHtml(n.author || "Staff");
    const role = (n.author_role || "STAFF").toUpperCase();
    const avatarColor = n.author_avatar || "#B87A21";
    const initials = (n.author || "ST")
      .split(" ")
      .map((w) => w[0])
      .slice(0, 2)
      .join("")
      .toUpperCase();
    
    const clientName = escapeHtml(n.client_name || n.client_email?.split("@")[0] || `Reservation #${n.conversation_id}`);
    const route = (n.origin && n.destination) 
      ? `${escapeHtml(n.origin)} ➔ ${escapeHtml(n.destination)}`
      : (n.origin ? escapeHtml(n.origin) : (n.destination ? escapeHtml(n.destination) : ""));

    return `
      <div class="notif-item ${n.isUnread ? "unread" : ""}" data-convo-id="${n.conversation_id}" data-note-id="${n.id}">
        <div class="notif-avatar" style="background-color: ${avatarColor}">${initials}</div>
        <div class="notif-item-content">
          <div class="notif-item-top">
            <div class="notif-item-author-group">
              <span class="notif-item-author">${authorName}</span>
              <span class="notif-item-role">${escapeHtml(role)}</span>
            </div>
            <span class="notif-item-time">${formatRelative(n.created_at)}</span>
          </div>
          <div class="notif-item-client-row">
            <span class="notif-item-client-name">${clientName}</span>
            ${route ? `<span>•</span><span class="notif-item-route">${route}</span>` : ""}
          </div>
          <div class="notif-item-text">${escapeHtml(n.note_text)}</div>
        </div>
        ${n.isUnread ? `<span class="notif-unread-dot" title="Unread"></span>` : ""}
      </div>
    `;
  }).join("");

  container.querySelectorAll(".notif-item").forEach((item) => {
    item.addEventListener("click", async () => {
      const convoId = Number(item.dataset.convoId);
      const noteId = Number(item.dataset.noteId);
      await openConversationFromNotification(convoId, noteId);
    });
  });
}

async function openConversationFromNotification(convoId, noteId) {
  toggleNotifications(false);

  if (state.view !== "conversations") {
    switchView("conversations");
  }

  setConversationNotesRead(convoId);
  await selectConversation(convoId, true, false);
  setTab("notes");
  await fetchRecentNotes();
}

function markAllNotesRead() {
  const notes = state.recentNotes || [];
  if (notes.length === 0) return;

  notes.forEach((n) => {
    setConversationNotesRead(n.conversation_id);
    n.isUnread = false;
  });

  updateNotificationUI();
}

function toggleNotifications(forceState) {
  const popover = el("notif-dropdown");
  const btn = el("notif-btn");
  if (!popover || !btn) return;

  const shouldOpen = typeof forceState === "boolean" ? forceState : popover.hidden;
  popover.hidden = !shouldOpen;
  btn.classList.toggle("active", shouldOpen);
  state.notifOpen = shouldOpen;

  if (shouldOpen) {
    fetchRecentNotes();
  }
}


// -------------------------------------------------------------------------
// Official Business Limousine Signature & Constants
// -------------------------------------------------------------------------

const OFFICIAL_SIGNATURE = `Lasaad
Phone: +32 487 44 67 73
Kind regards | Met vriendelijke groet | Kind regards | مع أطيب التحيات | С уважением

Business Limousine Services - Worldwide Travel Services
Groundtransportation | Private Aviation | Concierge | Bodyguard`;

const DEFAULT_CC_EMAIL = "info@business-limousine.be";

function getCombinedCc(existingCc) {
  const defaultCc = state.emailSettings?.default_cc_email || DEFAULT_CC_EMAIL;
  const addresses = new Set();
  if (defaultCc) {
    defaultCc.split(",").map((s) => s.trim()).filter(Boolean).forEach((addr) => addresses.add(addr));
  }
  if (existingCc) {
    existingCc.split(",").map((s) => s.trim()).filter(Boolean).forEach((addr) => {
      if (!addr.includes("noreply")) {
        addresses.add(addr);
      }
    });
  }
  return Array.from(addresses).join(", ");
}

function getActiveSignatureText() {
  return state.emailSettings?.signature_text || OFFICIAL_SIGNATURE;
}

function ensureDefaultSignature(text) {
  const activeSig = getActiveSignatureText();
  if (!text || text.trim() === "") {
    return `\n\n${activeSig}`;
  }
  if (text.includes("Business Limousine Services") || text.includes("+32 487 44 67 73")) {
    return text;
  }
  return `${text.trim()}\n\n${activeSig}`;
}

// -------------------------------------------------------------------------
// Conversation detail
// -------------------------------------------------------------------------

async function selectConversation(id, reloadDetail = true, switchTab = false) {
  if (!id) return;
  state.selectedId = id;
  if (switchTab) {
    setTab("thread");
  }
  document.querySelectorAll(".conversation-row").forEach((row) => {
    row.classList.toggle("selected", Number(row.dataset.id) === id);
  });

  try {
    if (reloadDetail || !state.selectedDetail || state.selectedDetail.conversation?.id !== id) {
      state.selectedDetail = await api(`/api/conversations/${id}`);
    }

    if (!state.selectedDetail || !state.selectedDetail.conversation) {
      return;
    }

    const { conversation, messages = [], notes = [] } = state.selectedDetail;

    if (el("detail-empty")) el("detail-empty").hidden = true;
    if (el("detail-content")) el("detail-content").hidden = false;
    document.body.classList.add("mobile-detail-open");

    if (el("client-name")) el("client-name").textContent = conversation.client_name || conversation.client_email?.split("@")[0] || "Valued Client";
    if (el("client-email")) el("client-email").textContent = conversation.client_email || "";
    if (el("client-phone")) el("client-phone").textContent = conversation.client_phone || "";

    renderStatusSelect(conversation.status);

    if (el("trip-origin")) el("trip-origin").textContent = conversation.origin || "Pickup not set";
    if (el("trip-destination")) el("trip-destination").textContent = conversation.destination || "Drop-off not set";
    if (el("trip-date")) el("trip-date").textContent = conversation.trip_date || "Date not set";
    if (el("trip-edit")) el("trip-edit").hidden = true;

    if (el("edit-client-name")) el("edit-client-name").value = conversation.client_name || "";
    if (el("edit-client-phone")) el("edit-client-phone").value = conversation.client_phone || "";
    if (el("edit-trip-date")) el("edit-trip-date").value = conversation.trip_date || "";
    if (el("edit-origin")) el("edit-origin").value = conversation.origin || "";
    if (el("edit-destination")) el("edit-destination").value = conversation.destination || "";

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

    // Pre-populate email composer with default CC and dynamic signature
    const toInput = el("reply-to");
    if (toInput) toInput.value = conversation.client_email || "";

    const ccInput = el("reply-cc");
    const lastInboundMsg = [...messages].reverse().find((m) => m.direction === "inbound");
    if (ccInput) ccInput.value = getCombinedCc(lastInboundMsg?.cc_addr);

    const ccRow = el("cc-field-row");
    if (ccRow) ccRow.hidden = false;

    state.composerAttachments = [];
    renderComposerAttachments();

    const toolbarHeading = el("thread-subject-heading");
    const countPill = el("thread-msg-count-pill");
    const subj = lastInboundMsg?.subject || messages[0]?.subject || "Your inquiry";
    if (toolbarHeading) toolbarHeading.textContent = subj;
    if (countPill) countPill.textContent = `${messages.length} email${messages.length === 1 ? "" : "s"}`;

    const subjInput = el("reply-subject");
    if (subjInput) subjInput.value = subj.startsWith("Re:") ? subj : `Re: ${subj}`;

    const replyBody = el("reply-body");
    if (replyBody) {
      replyBody.value = "";
    }
    updateComposerSignatureRender();
    if (el("reply-status")) el("reply-status").textContent = "";

    state.aiDrafts = null;
    const aiDrawer = el("ai-draft-drawer");
    if (aiDrawer) aiDrawer.hidden = true;
    const aiBtn = el("btn-toggle-ai-draft");
    if (aiBtn) aiBtn.classList.remove("active");

    renderThread(messages, conversation);
    renderNotes(notes);
  } catch (err) {
    console.error("Error displaying conversation:", err);
  }
}

// -------------------------------------------------------------------------
// AI Smart Draft Assistant (Gemini)
// -------------------------------------------------------------------------

let currentAiTone = "quote";

async function fetchAiSmartDraft(tone = "quote", customPrompt = null) {
  const id = state.selectedId;
  if (!id) return;

  const subjectEl = el("ai-draft-subject-line");
  const bodyEl = el("ai-draft-body-preview");
  const langPill = el("ai-lang-pill");
  const genBtnText = el("ai-generate-btn-text");

  currentAiTone = tone;
  document.querySelectorAll(".ai-tone-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tone === tone);
  });

  if (bodyEl) {
    bodyEl.innerHTML = `<span style="color:var(--brass); font-style:italic;">✨ Gemini AI is analyzing the trip and composing luxury response drafts…</span>`;
  }
  if (genBtnText) genBtnText.textContent = "Thinking…";

  try {
    const payload = { tone, instructions: customPrompt };
    const res = await api(`/api/conversations/${id}/ai-draft`, {
      method: "POST",
      body: JSON.stringify(payload),
    });

    state.aiDrafts = res;
    if (langPill && res.language) {
      langPill.textContent = `${res.language} detected`;
    }

    displaySelectedAiDraft(tone);
  } catch (err) {
    if (bodyEl) {
      bodyEl.innerHTML = `<span style="color:#EF4444;">Failed to generate AI draft: ${escapeHtml(err.message)}</span>`;
    }
  } finally {
    if (genBtnText) genBtnText.textContent = "Generate";
  }
}

function displaySelectedAiDraft(tone = "quote") {
  currentAiTone = tone;
  document.querySelectorAll(".ai-tone-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tone === tone);
  });

  const subjectEl = el("ai-draft-subject-line");
  const bodyEl = el("ai-draft-body-preview");
  if (!state.aiDrafts || !state.aiDrafts.drafts) return;

  const draft = state.aiDrafts.drafts.find((d) => d.id === tone) || state.aiDrafts.drafts[0];
  if (draft) {
    if (subjectEl) subjectEl.textContent = `Subject: ${draft.subject || "Re: Executive Chauffeur Service"}`;
    if (bodyEl) bodyEl.textContent = draft.body || "";
  }
}

function applyCurrentAiDraftToComposer() {
  if (!state.aiDrafts || !state.aiDrafts.drafts) return;
  const draft = state.aiDrafts.drafts.find((d) => d.id === currentAiTone) || state.aiDrafts.drafts[0];
  if (!draft) return;

  const replyBody = el("reply-body");
  const replySubj = el("reply-subject");

  if (replyBody) {
    replyBody.value = draft.body;
    replyBody.focus();
  }
  if (replySubj && draft.subject) {
    replySubj.value = draft.subject;
  }

  // Close AI drawer
  const drawer = el("ai-draft-drawer");
  const btnToggle = el("btn-toggle-ai-draft");
  if (drawer) drawer.hidden = true;
  if (btnToggle) btnToggle.classList.remove("active");

  const statusEl = el("reply-status");
  if (statusEl) {
    statusEl.textContent = "✓ AI draft applied to message.";
    statusEl.className = "reply-status ok";
    setTimeout(() => { if (statusEl.textContent.includes("AI draft")) statusEl.textContent = ""; }, 3500);
  }
}

function formatFileSize(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

function openImageLightbox(imageUrl, title) {
  const modal = el("lightbox-modal");
  const img = el("lightbox-img");
  const cap = el("lightbox-caption");
  const dl = el("lightbox-download-link");
  if (!modal || !img) return;

  img.src = imageUrl;
  if (cap) cap.textContent = title || "Attachment Image";
  if (dl) {
    dl.href = imageUrl;
    dl.setAttribute("download", title || "image");
  }
  modal.hidden = false;
}

function closeImageLightbox() {
  const modal = el("lightbox-modal");
  if (modal) modal.hidden = true;
}

async function uploadAttachmentFile(file) {
  if (!file) return null;
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch("/api/upload", {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Upload failed (${res.status})`);
  }
  return await res.json();
}

function renderComposerAttachments() {
  const container = el("composer-attachments");
  const chipsEl = el("composer-attachment-chips");
  const countEl = el("attachments-count");
  if (!container || !chipsEl) return;

  const atts = state.composerAttachments || [];
  if (atts.length === 0) {
    container.hidden = true;
    chipsEl.innerHTML = "";
    return;
  }

  container.hidden = false;
  if (countEl) countEl.textContent = atts.length;

  chipsEl.innerHTML = atts.map((att, idx) => {
    const isImg = att.content_type && att.content_type.startsWith("image/");
    const thumbHtml = isImg
      ? `<img src="${att.url}" class="att-thumb-preview" alt="Preview" />`
      : `<svg class="att-file-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>`;

    return `
      <div class="composer-att-chip" data-idx="${idx}">
        ${thumbHtml}
        <span class="att-name" title="${escapeHtml(att.filename)}">${escapeHtml(att.filename)}</span>
        <span class="att-size">(${formatFileSize(att.file_size)})</span>
        <button type="button" class="att-remove-btn" data-remove-idx="${idx}" title="Remove attachment">✕</button>
      </div>
    `;
  }).join("");

  chipsEl.querySelectorAll(".att-remove-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = Number(btn.dataset.removeIdx);
      state.composerAttachments.splice(idx, 1);
      renderComposerAttachments();
    });
  });
}

function applyQuickTemplate(templateKey) {
  if (!templateKey) return;
  const convo = state.selectedDetail?.conversation || {};
  const clientName = convo.client_name || convo.client_email?.split("@")[0] || "Valued Client";
  const origin = convo.origin || "Bruxelles (Aéroport / Ville)";
  const dest = convo.destination || "Destination";
  const date = convo.trip_date || "Date à convenir";

  const templates = {
    quote_confirmation: `Bonjour ${clientName},

Nous avons le plaisir de vous proposer un véhicule VIP Mercedes pour votre trajet :

• Date : ${date}
• Départ : ${origin}
• Destination : ${dest}
• Nombre de passagers : A préciser
• Véhicule : Mercedes-Benz Sprinter / Classe S VIP / Classe V
• Tarif : [Tarif à compléter] € HTVA 6%

Ce tarif est proposé pour le transport selon les informations communiquées.

Nous restons à votre entière disposition pour convenir ensemble des éventuels détails complémentaires.`,

    chauffeur_assigned: `Bonjour ${clientName},

Votre chauffeur VIP privé a été officiellement assigné pour votre trajet du ${date} :

• Chauffeur : Chauffeur VIP Business Limousine
• Véhicule : Mercedes-Benz VIP
• Lieu de prise en charge : ${origin} (Accueil personnalisé avec pancarte à votre nom)
• Destination : ${dest}

Notre service dispatching suit votre trajet et l'état de votre vol en temps réel.`,

    flight_delay: `Bonjour ${clientName},

Nous suivons activement l'évolution de votre vol en temps réel.
Soyez assuré(e) que votre chauffeur adapte son heure d'arrivée à ${origin} en fonction de l'horaire réel d'atterrissage.

Aucun supplément d'attente ne sera facturé pour les retards de vol.`,

    booking_confirmed: `Bonjour ${clientName},

Nous vous confirmons avec plaisir que votre réservation auprès de Business Limousine est validée :

• Client : ${clientName}
• Prise en charge : ${origin}
• Destination : ${dest}
• Date & Heure : ${date}

Nous vous remercions de votre confiance et restons à votre entière disposition 24/7.`,
  };

  const bodyEl = el("reply-body");
  if (bodyEl && templates[templateKey]) {
    bodyEl.value = templates[templateKey];
    bodyEl.focus();
  }
}

function applyFormatting(formatType) {
  const textarea = el("reply-body");
  if (!textarea) return;

  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selectedText = textarea.value.substring(start, end);

  let replacement = "";
  if (formatType === "bold") {
    replacement = `**${selectedText || "bold text"}**`;
  } else if (formatType === "italic") {
    replacement = `*${selectedText || "italic text"}*`;
  } else if (formatType === "bullet") {
    replacement = selectedText
      ? selectedText.split("\n").map((line) => `• ${line}`).join("\n")
      : "\n• Option 1\n• Option 2\n";
  } else if (formatType === "quote") {
    replacement = selectedText
      ? selectedText.split("\n").map((line) => `> ${line}`).join("\n")
      : "\n> Quoted text\n";
  }

  textarea.setRangeText(replacement, start, end, "end");
  textarea.focus();
}

function renderStatusSelect(currentStatus) {
  const sel = el("status-select");
  sel.innerHTML = STATUSES.filter((s) => s.key !== "ALL").map((s) => `
    <option value="${s.key}" ${s.key === currentStatus ? "selected" : ""}>
      ${s.label}
    </option>
  `).join("");
}

function sanitizeEmailHtml(html) {
  if (!html) return "";
  return html
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    .replace(/<meta\s+http-equiv=["']?refresh["']?[^>]*>/gi, "")
    .replace(/<base[^>]*>/gi, "");
}

function buildSafeEmailIframeHtml(bodyHtml, msgId) {
  const safeContent = sanitizeEmailHtml(bodyHtml);
  const srcDoc = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <base target="_blank">
  <style>
    html, body {
      margin: 0;
      padding: 2px 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      font-size: 13.5px;
      line-height: 1.55;
      color: #1B2027;
      background: transparent;
      word-break: break-word;
    }
    img { max-width: 100% !important; height: auto !important; }
    table { max-width: 100% !important; }
    a { color: #2563EB; }
  </style>
</head>
<body>${safeContent}</body>
</html>`;

  return `
    <div class="email-body-html-frame-wrap">
      <iframe class="email-body-iframe" data-msg-id="${msgId}" srcdoc="${escapeHtml(srcDoc)}" sandbox="allow-same-origin allow-popups" frameborder="0" scrolling="no"></iframe>
    </div>
  `;
}

function renderThread(messages, convo) {
  const threadEl = el("thread");
  const toolbarHeading = el("thread-subject-heading");
  const countPill = el("thread-msg-count-pill");

  if (!messages || messages.length === 0) {
    threadEl.innerHTML = `<div style="color:var(--text-muted); font-size:13px; padding: 20px 0;">No messages in this thread yet.</div>`;
    if (countPill) countPill.textContent = "0 emails";
    return;
  }

  const sortedMessages = [...messages].sort((a, b) => {
    const timeA = safeDate(a.received_at || a.created_at)?.getTime() || 0;
    const timeB = safeDate(b.received_at || b.created_at)?.getTime() || 0;
    return timeA - timeB;
  });

  const lastInbound = [...sortedMessages].reverse().find((m) => m.direction === "inbound");
  const mainSubject = lastInbound?.subject || sortedMessages[0]?.subject || "VIP Reservation Inquiry";
  if (toolbarHeading) toolbarHeading.textContent = mainSubject;
  if (countPill) countPill.textContent = `${sortedMessages.length} email${sortedMessages.length === 1 ? "" : "s"}`;

  const lastMsgId = sortedMessages[sortedMessages.length - 1]?.id;

  threadEl.innerHTML = sortedMessages.map((m) => {
    const isInbound = m.direction === "inbound";
    const senderName = isInbound
      ? (convo?.client_name || m.from_addr || "Client")
      : "Business Limousine Dispatch";
    const avatarInitial = (senderName.trim().charAt(0) || (isInbound ? "C" : "B")).toUpperCase();
    const roleTag = isInbound ? "CLIENT" : "DISPATCH";

    const isExpanded = state.expandedMessages[m.id] !== undefined
      ? state.expandedMessages[m.id]
      : (m.id === lastMsgId || sortedMessages.length === 1);

    // Clean quoted email text
    let bodyText = (m.body_text || "").trim();
    let quotedText = "";
    const quotePattern = /(?:^|\n)(?:>|On\s+.+wrote:|Le\s+.+a\s+écrit\s*:|El\s+.+escribió:)/i;
    const match = bodyText.search(quotePattern);
    if (match !== -1 && match > 0) {
      quotedText = bodyText.slice(match).trim();
      bodyText = bodyText.slice(0, match).trim();
    }
    if (!bodyText && !m.body_html && !quotedText) {
      bodyText = "(empty message)";
    }

    // Attachments
    const attachments = m.attachments || [];
    let attachmentsHtml = "";
    if (attachments && attachments.length > 0) {
      const imgCards = [];
      const docCards = [];

      attachments.forEach((att) => {
        const isImg = att.content_type && att.content_type.startsWith("image/");
        if (isImg) {
          imgCards.push(`
            <div class="att-img-card" data-img-url="${att.url}" data-img-title="${escapeHtml(att.filename)}">
              <img src="${att.url}" class="att-img-thumb" alt="${escapeHtml(att.filename)}" loading="lazy" />
              <div class="att-img-overlay">
                <span class="att-img-title">${escapeHtml(att.filename)}</span>
              </div>
            </div>
          `);
        } else {
          docCards.push(`
            <a href="${att.url}" download="${escapeHtml(att.filename)}" target="_blank" class="attachment-chip" title="Download ${escapeHtml(att.filename)}">
              <svg class="att-file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>
              <span class="att-name">${escapeHtml(att.filename)}</span>
              <span class="att-size">(${formatFileSize(att.file_size)})</span>
            </a>
          `);
        }
      });

      attachmentsHtml = `
        <div class="email-card-attachments">
          <div class="attachments-section-title">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path></svg>
            <span>Attachments (${attachments.length})</span>
          </div>
          <div class="email-attachment-grid">
            ${imgCards.join("")}
            ${docCards.join("")}
          </div>
        </div>
      `;
    }

    const ccPill = m.cc_addr 
      ? `<span class="cc-badge-chip">CC: ${escapeHtml(m.cc_addr)}</span>` 
      : "";

    const renderedBodyHtml = m.body_html
      ? buildSafeEmailIframeHtml(m.body_html, m.id)
      : `<div class="email-body-text">${escapeHtml(bodyText)}</div>`;

    return `
      <div class="email-card ${m.direction} ${isExpanded ? "" : "collapsed"}" data-msg-id="${m.id}">
        <div class="email-card-head">
          <div class="email-card-head-left">
            <div class="email-sender-avatar ${isInbound ? "inbound-avatar" : "outbound-avatar"}">
              ${escapeHtml(avatarInitial)}
            </div>
            <div class="email-header-info">
              <div class="email-header-top-row">
                <span class="email-sender-name">${escapeHtml(senderName)}</span>
                <span class="email-sender-role-tag ${isInbound ? "" : "outbound-tag"}">${roleTag}</span>
                ${m.ai_category ? `<span class="status-pill status-${m.ai_category}" style="font-size: 8.5px; padding: 1px 5px;">${m.ai_category}</span>` : ""}
              </div>
              <div class="email-header-subrow">
                <span class="email-recipients-summary">
                  to ${escapeHtml(m.to_addr || convo?.client_email || "recipient")}
                  ${ccPill}
                </span>
              </div>
            </div>
          </div>
          <div class="email-card-head-right">
            <span class="email-time">${formatTimestamp(m.received_at || m.created_at)}</span>
            <span class="email-collapse-toggle">▼</span>
          </div>
        </div>

        <div class="email-meta-details">
          <div class="meta-detail-row">
            <span class="meta-detail-label">From:</span>
            <span class="meta-detail-value">${escapeHtml(m.from_addr || senderName)}</span>
          </div>
          <div class="meta-detail-row">
            <span class="meta-detail-label">To:</span>
            <span class="meta-detail-value">${escapeHtml(m.to_addr || convo?.client_email || "")}</span>
          </div>
          ${m.cc_addr ? `
            <div class="meta-detail-row">
              <span class="meta-detail-label">Cc:</span>
              <span class="meta-detail-value">${escapeHtml(m.cc_addr)}</span>
            </div>
          ` : ""}
          <div class="meta-detail-row">
            <span class="meta-detail-label">Date:</span>
            <span class="meta-detail-value">${safeUtcString(m.received_at || m.created_at)}</span>
          </div>
          ${m.subject ? `
            <div class="meta-detail-row">
              <span class="meta-detail-label">Subject:</span>
              <span class="meta-detail-value">${escapeHtml(m.subject)}</span>
            </div>
          ` : ""}
        </div>

        <div class="email-card-body">
          ${m.subject && isExpanded ? `<div class="email-subject-line">${escapeHtml(m.subject)}</div>` : ""}
          ${renderedBodyHtml}
          ${quotedText ? `
            <details class="message-quote">
              <summary>Quoted email history</summary>
              <div class="message-quote-body">${escapeHtml(quotedText)}</div>
            </details>
          ` : ""}
          ${attachmentsHtml}
        </div>

        <div class="email-card-footer">
          <button type="button" class="card-action-btn card-reply-all-btn" data-from="${escapeHtml(m.from_addr || convo?.client_email || '')}" data-cc="${escapeHtml(m.cc_addr || '')}" data-subj="${escapeHtml(m.subject || '')}" title="Reply All (Outlook-style)">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polyline points="7 17 2 12 7 7"></polyline><polyline points="12 17 7 12 12 7"></polyline><path d="M22 18v-2a4 4 0 0 0-4-4H7"></path></svg>
            <span>Reply All</span>
          </button>
          <button type="button" class="card-action-btn card-reply-btn" data-from="${escapeHtml(m.from_addr || convo?.client_email || '')}" data-subj="${escapeHtml(m.subject || '')}" title="Direct Reply">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 17 4 12 9 7"></polyline><path d="M20 18v-2a4 4 0 0 0-4-4H4"></path></svg>
            <span>Reply</span>
          </button>
        </div>
      </div>
    `;
  }).join("");

  // Auto-resize iframes to fit email height perfectly
  threadEl.querySelectorAll(".email-body-iframe").forEach((iframe) => {
    function adjustIframeHeight() {
      try {
        const doc = iframe.contentDocument || iframe.contentWindow?.document;
        if (doc && doc.body) {
          const scrollH = Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight, 30);
          iframe.style.height = `${scrollH + 20}px`;
        }
      } catch (e) {}
    }
    iframe.addEventListener("load", () => {
      adjustIframeHeight();
      setTimeout(adjustIframeHeight, 150);
      setTimeout(adjustIframeHeight, 500);
    });
    setTimeout(adjustIframeHeight, 100);
  });

  // Attach card collapse/expand toggles
  threadEl.querySelectorAll(".email-card-head").forEach((head) => {
    head.addEventListener("click", (e) => {
      const card = head.closest(".email-card");
      if (!card) return;
      const msgId = Number(card.dataset.msgId);
      const isNowCollapsed = !card.classList.contains("collapsed");
      card.classList.toggle("collapsed", isNowCollapsed);
      state.expandedMessages[msgId] = !isNowCollapsed;
    });
  });

  // Attach Lightbox click handlers
  threadEl.querySelectorAll(".att-img-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      e.stopPropagation();
      openImageLightbox(card.dataset.imgUrl, card.dataset.imgTitle);
    });
  });

  // Attach Reply / Reply All buttons inside cards
  threadEl.querySelectorAll(".card-reply-all-btn, .card-reply-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const fromAddr = btn.dataset.from;
      const ccAddr = btn.dataset.cc;
      const subj = btn.dataset.subj;
      if (fromAddr) el("reply-to").value = fromAddr;
      el("reply-cc").value = getCombinedCc(ccAddr);
      el("cc-field-row").hidden = false;
      if (subj) el("reply-subject").value = subj.startsWith("Re:") ? subj : `Re: ${subj}`;

      const replyBody = el("reply-body");
      if (replyBody) {
        if (!replyBody.value.trim() || replyBody.value.trim() === OFFICIAL_SIGNATURE) {
          replyBody.value = `\n\n${OFFICIAL_SIGNATURE}`;
        }
        replyBody.focus();
        replyBody.setSelectionRange(0, 0);
      }
      el("email-composer").scrollIntoView({ behavior: "smooth" });
    });
  });

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
    fetchRecentNotes();
  }
}



// -------------------------------------------------------------------------
// Event wiring
// -------------------------------------------------------------------------

function wireEvents() {
  // Topbar Notification Bell and Dropdown Box
  const notifBtn = el("notif-btn");
  if (notifBtn) {
    notifBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleNotifications();
    });
  }

  document.addEventListener("click", (e) => {
    const notifWrapper = el("notif-wrapper");
    if (notifWrapper && !notifWrapper.contains(e.target)) {
      toggleNotifications(false);
    }
  });

  const markAllNotesBtn = el("notif-mark-all-btn");
  if (markAllNotesBtn) {
    markAllNotesBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      markAllNotesRead();
    });
  }

  const tabUnread = el("notif-tab-unread");
  const tabAll = el("notif-tab-all");
  if (tabUnread && tabAll) {
    tabUnread.addEventListener("click", (e) => {
      e.stopPropagation();
      state.notesFilter = "unread";
      tabUnread.classList.add("active");
      tabAll.classList.remove("active");
      renderNotificationItems();
    });
    tabAll.addEventListener("click", (e) => {
      e.stopPropagation();
      state.notesFilter = "all";
      tabAll.classList.add("active");
      tabUnread.classList.remove("active");
      renderNotificationItems();
    });
  }

  // Login form
  el("login-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = el("login-email").value.trim();
    const password = el("login-password").value;
    await handleLogin(email, password);
  });

  // Password visibility toggles
  const loginPwToggle = el("login-pw-toggle");
  const loginPwInput = el("login-password");
  if (loginPwToggle && loginPwInput) {
    loginPwToggle.addEventListener("click", () => {
      const isPw = loginPwInput.type === "password";
      loginPwInput.type = isPw ? "text" : "password";
      loginPwToggle.textContent = isPw ? "🔒" : "👁";
    });
  }

  const userPwToggle = el("user-form-pw-toggle");
  const userPwInput = el("user-form-password");
  if (userPwToggle && userPwInput) {
    userPwToggle.addEventListener("click", () => {
      const isPw = userPwInput.type === "password";
      userPwInput.type = isPw ? "text" : "password";
      userPwToggle.textContent = isPw ? "🔒" : "👁";
    });
  }

  // Logout button
  el("logout-btn")?.addEventListener("click", handleLogout);

  let searchTimer = null;
  el("search-input")?.addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.search = e.target.value.trim();
      loadConversations();
    }, 250);
  });

  // Delegated click handler on conversation manifest
  el("conversation-list")?.addEventListener("click", (e) => {
    const row = e.target.closest(".conversation-row");
    if (!row) return;
    const id = Number(row.dataset.id);
    if (id) {
      selectConversation(id, true, true);
    }
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

  async function handleSendEmailReply() {
    const id = state.selectedId;
    if (!id) return;

    const toAddr = el("reply-to")?.value.trim() || "";
    const ccAddr = el("reply-cc")?.value.trim() || "";
    const subject = el("reply-subject")?.value.trim() || "";
    const userMessage = el("reply-body")?.value.trim() || "";
    const attachments = state.composerAttachments || [];
    const statusEl = el("reply-status");
    const sendBtn = el("reply-send-btn");

    if (!userMessage && attachments.length === 0) {
      if (statusEl) {
        statusEl.textContent = "Write a message or attach a file before sending.";
        statusEl.className = "reply-status error";
      }
      return;
    }

    if (sendBtn) sendBtn.disabled = true;
    if (statusEl) {
      statusEl.textContent = "Sending email…";
      statusEl.className = "reply-status";
    }

    try {
      await api(`/api/conversations/${id}/reply`, {
        method: "POST",
        body: JSON.stringify({
          to_addr: toAddr,
          cc_addr: ccAddr,
          subject: subject,
          body_text: userMessage,
          attachments: attachments,
        }),
      });

      if (statusEl) {
        statusEl.textContent = "✓ Sent successfully with rich HTML signature.";
        statusEl.className = "reply-status ok";
      }

      el("reply-body").value = "";
      state.composerAttachments = [];
      renderComposerAttachments();

      await selectConversation(id, true, false);
      await loadConversations();
    } catch (err) {
      if (statusEl) {
        statusEl.textContent = `Error: ${err.message}`;
        statusEl.className = "reply-status error";
      }
    } finally {
      if (sendBtn) sendBtn.disabled = false;
    }
  }

  // Edit Signature shortcut button inside composer
  const editSigBtn = el("composer-edit-signature-btn");
  if (editSigBtn) {
    editSigBtn.addEventListener("click", (e) => {
      e.preventDefault();
      switchView("email-settings");
    });
  }

  // CC Toggle Button
  const btnToggleCc = el("btn-toggle-cc");
  if (btnToggleCc) {
    btnToggleCc.addEventListener("click", () => {
      const ccRow = el("cc-field-row");
      if (ccRow) {
        ccRow.hidden = !ccRow.hidden;
        if (!ccRow.hidden) {
          el("reply-cc")?.focus();
        }
      }
    });
  }

  // Jump to Reply Button
  const jumpReplyBtn = el("thread-jump-reply-btn");
  if (jumpReplyBtn) {
    jumpReplyBtn.addEventListener("click", () => {
      el("email-composer")?.scrollIntoView({ behavior: "smooth" });
      el("reply-body")?.focus();
    });
  }

  // File & Image Attach Buttons
  const btnAttachFile = el("btn-attach-file");
  const fileInput = el("reply-file-input");
  if (btnAttachFile && fileInput) {
    btnAttachFile.addEventListener("click", () => fileInput.click());
  }

  const btnAttachImage = el("btn-attach-image");
  const imgInput = el("reply-image-input");
  if (btnAttachImage && imgInput) {
    btnAttachImage.addEventListener("click", () => imgInput.click());
  }

  async function handleFilesSelected(files) {
    if (!files || files.length === 0) return;
    const statusEl = el("reply-status");
    if (statusEl) {
      statusEl.textContent = "Uploading attachments…";
      statusEl.className = "reply-status";
    }

    for (const file of files) {
      try {
        const uploaded = await uploadAttachmentFile(file);
        if (uploaded) {
          if (!state.composerAttachments) state.composerAttachments = [];
          state.composerAttachments.push(uploaded);
          renderComposerAttachments();
        }
      } catch (err) {
        if (statusEl) {
          statusEl.textContent = `Upload failed: ${err.message}`;
          statusEl.className = "reply-status error";
        }
      }
    }

    if (statusEl) {
      statusEl.textContent = "";
    }
  }

  if (fileInput) {
    fileInput.addEventListener("change", (e) => {
      handleFilesSelected(e.target.files);
      fileInput.value = "";
    });
  }

  if (imgInput) {
    imgInput.addEventListener("change", (e) => {
      handleFilesSelected(e.target.files);
      imgInput.value = "";
    });
  }

  // Drag & Drop onto Composer
  const dropzone = el("composer-dropzone");
  const dropOverlay = el("composer-drop-overlay");
  if (dropzone && dropOverlay) {
    ["dragenter", "dragover"].forEach((eventName) => {
      dropzone.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropOverlay.hidden = false;
      });
    });

    ["dragleave", "dragend"].forEach((eventName) => {
      dropzone.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropOverlay.hidden = true;
      });
    });

    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropOverlay.hidden = true;
      if (e.dataTransfer && e.dataTransfer.files) {
        handleFilesSelected(e.dataTransfer.files);
      }
    });
  }

  // Quick Response Templates
  const templateSelect = el("composer-template-select");
  if (templateSelect) {
    templateSelect.addEventListener("change", (e) => {
      applyQuickTemplate(e.target.value);
      templateSelect.value = "";
    });
  }

  // Formatting Toolbar Buttons
  document.querySelectorAll(".format-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyFormatting(btn.dataset.format);
    });
  });

  // Ctrl + Enter shortcut
  const replyBody = el("reply-body");
  if (replyBody) {
    replyBody.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        handleSendEmailReply();
      }
    });
  }

  // Reply Send Button
  const replySendBtn = el("reply-send-btn");
  if (replySendBtn) {
    replySendBtn.addEventListener("click", handleSendEmailReply);
  }

  // AI Smart Draft Assistant
  const btnToggleAi = el("btn-toggle-ai-draft");
  const aiDrawer = el("ai-draft-drawer");
  const aiCloseBtn = el("ai-drawer-close-btn");

  if (btnToggleAi && aiDrawer) {
    btnToggleAi.addEventListener("click", () => {
      aiDrawer.hidden = !aiDrawer.hidden;
      btnToggleAi.classList.toggle("active", !aiDrawer.hidden);
      if (!aiDrawer.hidden && !state.aiDrafts) {
        fetchAiSmartDraft(currentAiTone);
      }
    });
  }

  if (aiCloseBtn && aiDrawer && btnToggleAi) {
    aiCloseBtn.addEventListener("click", () => {
      aiDrawer.hidden = true;
      btnToggleAi.classList.remove("active");
    });
  }

  document.querySelectorAll(".ai-tone-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tone = btn.dataset.tone;
      if (state.aiDrafts && state.aiDrafts.drafts) {
        displaySelectedAiDraft(tone);
      } else {
        fetchAiSmartDraft(tone);
      }
    });
  });

  const btnAiGenerate = el("btn-ai-generate-action");
  const aiCustomInput = el("ai-custom-instructions");

  function triggerCustomAiDraft() {
    const promptVal = aiCustomInput?.value.trim() || null;
    fetchAiSmartDraft(currentAiTone, promptVal);
  }

  if (btnAiGenerate) {
    btnAiGenerate.addEventListener("click", triggerCustomAiDraft);
  }

  if (aiCustomInput) {
    aiCustomInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        triggerCustomAiDraft();
      }
    });
  }

  const btnAiApply = el("btn-ai-apply");
  if (btnAiApply) {
    btnAiApply.addEventListener("click", applyCurrentAiDraftToComposer);
  }

  // Lightbox Modal Controls
  const lightboxCloseBtn = el("lightbox-close-btn");
  const lightboxBackdrop = el("lightbox-backdrop");
  if (lightboxCloseBtn) lightboxCloseBtn.addEventListener("click", closeImageLightbox);
  if (lightboxBackdrop) lightboxBackdrop.addEventListener("click", closeImageLightbox);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeImageLightbox();
      toggleNotifications(false);
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
      fetchRecentNotes();
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

  // Email & Signature Settings Navigation
  const navEmailBtn = el("nav-email-settings-btn");
  if (navEmailBtn) {
    navEmailBtn.addEventListener("click", () => {
      switchView("email-settings");
    });
  }

  // Team & User Management Navigation
  const navUsersBtn = el("nav-users-settings");
  if (navUsersBtn) {
    navUsersBtn.addEventListener("click", () => {
      switchView("users-settings");
    });
  }

  // User Management Toolbar & Modal controls
  const btnOpenAddUser = el("btn-open-add-user-modal");
  if (btnOpenAddUser) {
    btnOpenAddUser.addEventListener("click", openAddUserModal);
  }

  const userModalCloseBtn = el("user-modal-close-btn");
  if (userModalCloseBtn) {
    userModalCloseBtn.addEventListener("click", closeUserModal);
  }

  const userModalCancelBtn = el("user-modal-cancel-btn");
  if (userModalCancelBtn) {
    userModalCancelBtn.addEventListener("click", closeUserModal);
  }

  const userModalBackdrop = el("user-modal-backdrop");
  if (userModalBackdrop) {
    userModalBackdrop.addEventListener("click", closeUserModal);
  }

  const userEditForm = el("user-edit-form");
  if (userEditForm) {
    userEditForm.addEventListener("submit", handleSaveUser);
  }

  const userSearchInput = el("users-search-input");
  if (userSearchInput) {
    userSearchInput.addEventListener("input", (e) => {
      renderUsersTable(e.target.value);
    });
  }

  // Signature Tabs (Plain Text vs HTML)
  const sigTabBtnText = el("sigtab-btn-text");
  const sigTabBtnHtml = el("sigtab-btn-html");
  const sigPanelText = el("sigtab-panel-text");
  const sigPanelHtml = el("sigtab-panel-html");

  if (sigTabBtnText && sigTabBtnHtml) {
    sigTabBtnText.addEventListener("click", () => {
      sigTabBtnText.classList.add("active");
      sigTabBtnHtml.classList.remove("active");
      if (sigPanelText) sigPanelText.hidden = false;
      if (sigPanelHtml) sigPanelHtml.hidden = true;
      updateSigLivePreview();
    });

    sigTabBtnHtml.addEventListener("click", () => {
      sigTabBtnHtml.classList.add("active");
      sigTabBtnText.classList.remove("active");
      if (sigPanelHtml) sigPanelHtml.hidden = false;
      if (sigPanelText) sigPanelText.hidden = true;
      updateSigLivePreview();
    });
  }

  // Realtime Signature preview update on typing
  const textInput = el("setting-signature-text");
  const htmlInput = el("setting-signature-html");
  if (textInput) textInput.addEventListener("input", updateSigLivePreview);
  if (htmlInput) htmlInput.addEventListener("input", updateSigLivePreview);

  // Save and Reset buttons
  const saveEmailBtn = el("btn-save-email-settings");
  if (saveEmailBtn) saveEmailBtn.addEventListener("click", saveEmailSettings);

  const resetSigBtn = el("btn-reset-signature");
  if (resetSigBtn) resetSigBtn.addEventListener("click", resetDefaultSignature);

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
      const count = result.count ?? result.processed ?? 0;
      el("sync-meta").textContent = `Last sync: ${count} new message(s) · ${new Date().toLocaleTimeString()}`;
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
    await loadEmailSettings();
  }
})();
