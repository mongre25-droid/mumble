"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"></use></svg>`;

const els = {
  voiceZone: $("#voice-zone"),
  voiceOrb: $("#voice-orb"),
  voiceTitle: $("#voice-state-title"),
  voiceSub: $("#voice-state-sub"),
  form: $("#command-form"),
  input: $("#command-input"),
  planPanel: $("#plan-panel"),
  planSummary: $("#plan-summary"),
  planSteps: $("#plan-steps"),
  planNotice: $("#plan-notice"),
  planStatus: $("#plan-status"),
  runPlan: $("#run-plan"),
  hostState: $("#host-state"),
  hostCapability: $("#host-capability"),
  contextDetail: $("#context-detail"),
  activityList: $("#activity-list"),
  activityCount: $("#activity-count"),
  hours: $("#hours-saved"),
  modalLayer: $("#milestone-modal"),
  modal: $(".modal", $("#milestone-modal")),
  toastRegion: $("#toast-region"),
};

const app = {
  connected: false,
  listening: false,
  currentPlan: null,
  activities: 1,
  lastHotkeyGeneration: 0,
  pollingTimer: null,
  previousFocus: null,
  modalKeyHandler: null,
};

function safeStorageGet(key, fallback = null) {
  try { return localStorage.getItem(key) ?? fallback; } catch (_) { return fallback; }
}
function safeStorageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (_) {}
}

function setVoiceState(mode, title, subtitle) {
  els.voiceZone.dataset.mode = mode;
  els.voiceTitle.textContent = title;
  els.voiceSub.textContent = subtitle;
  els.voiceOrb.setAttribute("aria-label", mode === "listening" ? "Stop listening" : "Start listening");
}

function toggleListening(force) {
  app.listening = typeof force === "boolean" ? force : !app.listening;
  if (app.listening) {
    setVoiceState("listening", "Listening…", "Voice transcription is not connected — type the command below");
    els.input.focus();
    toast("Listening state active. This prototype does not capture audio.");
  } else {
    setVoiceState("idle", "Ready when you are", "Tap the orb or press Right Shift");
  }
}

function toast(message) {
  const item = document.createElement("div");
  item.className = "toast";
  item.textContent = message;
  els.toastRegion.append(item);
  window.setTimeout(() => item.remove(), 3600);
}

function addActivity(title, detail, glyph = "check") {
  app.activities += 1;
  const li = document.createElement("li");
  li.innerHTML = `<span class="activity-icon">${icon(glyph)}</span><div><strong></strong><small></small></div>`;
  $("strong", li).textContent = title;
  $("small", li).textContent = detail;
  els.activityList.prepend(li);
  while (els.activityList.children.length > 4) els.activityList.lastElementChild.remove();
  els.activityCount.textContent = `${app.activities} events`;
}

function titleCase(value) {
  return value.replace(/\b\w/g, (char) => char.toUpperCase());
}

function fallbackAction(step) {
  const clean = step.trim().replace(/[.?!]+$/, "");
  const lower = clean.toLowerCase();
  let match;
  if ((match = clean.match(/^(?:open|launch|start)\s+(calculator|calc|notepad|paint|settings|file explorer|explorer)$/i))) {
    const names = { calc: "calculator", explorer: "file-explorer", "file explorer": "file-explorer" };
    const target = names[match[1].toLowerCase()] || match[1].toLowerCase();
    return { kind: "open_app", label: `Open ${titleCase(match[1])}`, target, value: "", risk: "low", executable: false, note: "Connect the optional local host to run this action." };
  }
  if ((match = clean.match(/^(?:open|show)\s+(?:my\s+)?(desktop|documents|downloads|home|pictures)(?:\s+folder)?$/i))) {
    return { kind: "open_path", label: `Open ${titleCase(match[1])}`, target: `~/${titleCase(match[1])}`, value: "", risk: "low", executable: false, note: "Connect the optional local host to run this action." };
  }
  if ((match = clean.match(/^(?:switch to|focus|bring (?:up|forward))\s+(.+)$/i))) {
    return { kind: "focus_window", label: `Focus the existing ${match[1]} window`, target: match[1], value: "", risk: "confirm", executable: false, note: "Window focus needs the optional local host." };
  }
  if ((match = clean.match(/^(?:search(?:\s+for)?|find|look up)\s+(.+?)(?:\s+(?:on|in)\s+(brave|google|github|youtube|reddit|linkedin|x|twitter))?$/i))) {
    const site = (match[2] || "brave").toLowerCase().replace("twitter", "x");
    return { kind: "open_url", label: `Search ${titleCase(site)} for “${match[1]}”`, target: site, value: match[1], risk: "low", executable: false, note: "Plan preview only until the local host is connected." };
  }
  if (/^(?:edit|change|replace|append to)\s+/i.test(clean)) {
    return { kind: "edit_file", label: `Prepare ${clean}`, target: clean, value: "", risk: "blocked", executable: false, note: "File mutation requires a diff preview and production approval adapter." };
  }
  if (lower.startsWith("paste ")) {
    return { kind: "paste_text", label: "Prepare clipboard-safe paste", target: clean, value: "", risk: "blocked", executable: false, note: "Paste injection is intentionally not connected in this sandbox." };
  }
  return { kind: "unsupported", label: `Clarify “${clean}”`, target: "", value: clean, risk: "blocked", executable: false, note: "The prototype could not map this safely to a reviewed action." };
}

function fallbackPlan(command) {
  const steps = command.split(/\s*(?:,?\s+and\s+then\s+|,?\s+then\s+)\s*/i).filter(Boolean);
  const actions = steps.map(fallbackAction);
  return {
    command,
    summary: actions.length === 1 ? actions[0].label : `${actions.length}-step workflow · ${actions.map((a) => a.label).join(" → ")}`,
    actions,
    confirmation_required: actions.length > 1 || actions.some((a) => a.risk !== "low"),
    executable: false,
  };
}

async function api(path, options = {}, timeout = 1800) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      signal: controller.signal,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
    return payload;
  } finally {
    window.clearTimeout(timer);
  }
}

async function buildPlan(command) {
  setVoiceState("planning", "Building the safest route…", "Interpreting intent and checking each action");
  els.planPanel.hidden = true;
  let plan;
  if (app.connected) {
    const result = await api("/api/plan", { method: "POST", body: JSON.stringify({ command }) });
    plan = result.plan;
  } else {
    await new Promise((resolve) => window.setTimeout(resolve, 420));
    plan = fallbackPlan(command);
  }
  app.currentPlan = plan;
  renderPlan(plan);
  setVoiceState("idle", "Plan ready", "Review every step before anything runs");
  addActivity("Plan prepared", plan.summary, "spark");
}

function renderPlan(plan) {
  els.planSummary.textContent = plan.summary;
  els.planSteps.replaceChildren();
  plan.actions.forEach((action, index) => {
    const li = document.createElement("li");
    li.className = "plan-step";
    const riskLabel = action.risk === "low" ? "Reviewed" : action.risk === "confirm" ? "Confirm" : "Blocked";
    li.innerHTML = `<span class="step-number">${String(index + 1).padStart(2, "0")}</span><span class="step-copy"><strong></strong><small></small></span><span class="risk-chip ${action.risk}">${riskLabel}</span>`;
    $("strong", li).textContent = action.label;
    $("small", li).textContent = action.note || "Local, reviewed action.";
    els.planSteps.append(li);
  });
  const canRun = app.connected && plan.executable;
  els.runPlan.disabled = !canRun;
  els.planStatus.textContent = !app.connected ? "Preview only" : plan.executable ? (plan.confirmation_required ? "Confirmation needed" : "Ready to run") : "Action gated";
  if (!canRun) {
    els.planNotice.hidden = false;
    els.planNotice.textContent = !app.connected
      ? "This is an interactive plan preview. Run desktop_host.py to connect the narrow Windows executor."
      : "At least one step is intentionally unavailable. Operator will not partially run an ambiguous or blocked workflow.";
  } else {
    els.planNotice.hidden = true;
  }
  const firstTarget = plan.actions.find((action) => action.target)?.target;
  els.contextDetail.textContent = firstTarget ? `Target · ${firstTarget}` : "No desktop target selected";
  els.planPanel.hidden = false;
  els.planPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function runCurrentPlan() {
  if (!app.currentPlan || !app.connected || !app.currentPlan.executable) return;
  els.runPlan.disabled = true;
  els.planStatus.textContent = "Running";
  setVoiceState("planning", "Working through the plan…", "Stopping immediately if a step cannot complete safely");
  try {
    const result = await api("/api/execute", {
      method: "POST",
      body: JSON.stringify({ plan: app.currentPlan, confirmed: true }),
    }, 7000);
    if (!result.ok) throw new Error("The host stopped the workflow safely.");
    els.planStatus.textContent = "Complete";
    setVoiceState("idle", "Done", "The reviewed actions completed");
    addActivity("Workflow complete", app.currentPlan.summary, "check");
    toast("Workflow complete.");
  } catch (error) {
    els.planStatus.textContent = "Stopped safely";
    setVoiceState("idle", "Stopped safely", error.message);
    addActivity("Workflow stopped", error.message, "shield");
    toast(error.message);
  } finally {
    els.runPlan.disabled = !(app.currentPlan?.executable && app.connected);
  }
}

async function checkHost() {
  try {
    const state = await api("/api/state", {}, 700);
    const becameConnected = !app.connected;
    app.connected = Boolean(state.connected);
    els.hostState.classList.add("connected");
    $("span:last-child", els.hostState).textContent = "Local host connected";
    els.hostCapability.textContent = "Connected";
    els.hostCapability.className = "on";
    if (becameConnected) {
      addActivity("Local host connected", "Reviewed Windows actions are available", "shield");
      if (app.currentPlan) renderPlan(app.currentPlan);
    }
    if (state.hotkey_generation > app.lastHotkeyGeneration) {
      if (app.lastHotkeyGeneration !== 0) toggleListening();
      app.lastHotkeyGeneration = state.hotkey_generation;
    }
  } catch (_) {
    app.connected = false;
    els.hostState.classList.remove("connected");
    $("span:last-child", els.hostState).textContent = "Preview mode";
    els.hostCapability.textContent = "Preview only";
    els.hostCapability.className = "off";
  }
  window.clearTimeout(app.pollingTimer);
  if (!document.hidden && document.hasFocus()) app.pollingTimer = window.setTimeout(checkHost, app.connected ? 900 : 2600);
}

function showModalView(name) {
  $$("[data-modal-view]", els.modal).forEach((view) => view.classList.toggle("is-active", view.dataset.modalView === name));
  const activeView = $(`[data-modal-view="${name}"]`, els.modal);
  const heading = $("h2", activeView);
  if (heading) {
    heading.id = heading.id || `operator-modal-${name}-title`;
    els.modal.setAttribute("aria-labelledby", heading.id);
  }
  els.modal.scrollTop = 0;
  const focusTarget = $("button, input", activeView);
  window.setTimeout(() => focusTarget?.focus(), 20);
}

function openModal(view = "thanks", milestoneTriggered = false) {
  app.previousFocus = document.activeElement;
  els.modalLayer.hidden = false;
  document.body.style.overflow = "hidden";
  showModalView(view);
  if (milestoneTriggered) safeStorageSet("mumble-exp-56-milestone-seen", "1");
  const focusable = () => $$("button:not([disabled]), a[href], input:not([disabled])", $("[data-modal-view].is-active", els.modal)).filter((el) => el.offsetParent !== null);
  app.modalKeyHandler = (event) => {
    if (event.key === "Escape") return closeModal();
    if (event.key !== "Tab") return;
    const items = focusable();
    if (!items.length) { event.preventDefault(); els.modal.focus(); return; }
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  els.modalLayer.addEventListener("keydown", app.modalKeyHandler);
  window.setTimeout(() => els.modal.focus(), 10);
}

function closeModal() {
  if (els.modalLayer.hidden) return;
  els.modalLayer.hidden = true;
  document.body.style.overflow = "";
  if (app.modalKeyHandler) els.modalLayer.removeEventListener("keydown", app.modalKeyHandler);
  app.previousFocus?.focus?.();
}

function simulateMilestone() {
  safeStorageSet("mumble-exp-56-saved-hours", "20.4");
  safeStorageSet("mumble-exp-56-milestone-seen", "0");
  els.hours.textContent = "20.4";
  addActivity("20-hour threshold reached", "One-time appreciation flow opened", "clock");
  openModal("thanks", true);
}

function checkMilestone() {
  const hours = Number(safeStorageGet("mumble-exp-56-saved-hours", "0")) || 0;
  els.hours.textContent = hours.toFixed(1);
  if (hours >= 20 && safeStorageGet("mumble-exp-56-milestone-seen", "0") !== "1") openModal("thanks", true);
}

function wireEvents() {
  els.voiceOrb.addEventListener("click", () => toggleListening());
  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const command = els.input.value.trim();
    if (!command) { toast("Type a command to build a plan."); els.input.focus(); return; }
    toggleListening(false);
    try { await buildPlan(command); } catch (error) { setVoiceState("idle", "Couldn’t build that plan", error.message); toast(error.message); }
  });
  els.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); els.form.requestSubmit(); }
  });
  $$("[data-command]").forEach((button) => button.addEventListener("click", () => {
    els.input.value = button.dataset.command;
    els.form.requestSubmit();
  }));
  $("#clear-btn").addEventListener("click", () => {
    els.input.value = "";
    app.currentPlan = null;
    els.planPanel.hidden = true;
    els.contextDetail.textContent = "No desktop target selected";
    toggleListening(false);
    els.input.focus();
  });
  $("#cancel-plan").addEventListener("click", () => { app.currentPlan = null; els.planPanel.hidden = true; setVoiceState("idle", "Ready when you are", "Tap the orb or press Right Shift"); });
  els.runPlan.addEventListener("click", runCurrentPlan);
  $("#milestone-preview").addEventListener("click", simulateMilestone);
  $("#report-preview").addEventListener("click", () => openModal("report-intro"));
  $$('[data-close-modal]').forEach((button) => button.addEventListener("click", closeModal));
  $$('[data-go-view]').forEach((button) => button.addEventListener("click", () => showModalView(button.dataset.goView)));
  $$('[data-placeholder-link]').forEach((link) => link.addEventListener("click", (event) => {
    event.preventDefault();
    toast(`${link.dataset.placeholderLink} link is intentionally a placeholder.`);
  }));
  $("#newsletter-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const email = $("#newsletter-email");
    const consent = $("#newsletter-consent");
    const error = $("#newsletter-error");
    error.textContent = "";
    if (!email.validity.valid) { error.textContent = "Enter a valid email address."; email.focus(); return; }
    if (!consent.checked) { error.textContent = "Please confirm that you’d like to join the newsletter."; consent.focus(); return; }
    safeStorageSet("mumble-exp-56-newsletter-preview", "unlocked-locally");
    addActivity("Sample report unlocked", "No email was submitted by this prototype", "spark");
    showModalView("sample-report");
    toast("Prototype unlock complete — no email was sent.");
  });
  document.addEventListener("keydown", (event) => {
    if (event.code === "ShiftRight" && !event.repeat && els.modalLayer.hidden) {
      event.preventDefault();
      toggleListening();
    }
  });
  const setAmbient = () => {
    const paused = document.hidden || !document.hasFocus();
    document.body.classList.toggle("paused", paused);
    window.clearTimeout(app.pollingTimer);
    if (!paused) checkHost();
  };
  document.addEventListener("visibilitychange", setAmbient);
  window.addEventListener("blur", setAmbient);
  window.addEventListener("focus", setAmbient);
}

wireEvents();
checkMilestone();
checkHost();
