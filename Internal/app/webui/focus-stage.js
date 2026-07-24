(function (root) {
  "use strict";

  const contract = Object.freeze({
    version: "mumble.focus-stage.v1",
    destinations: Object.freeze([
      Object.freeze({ id: "home", label: "Home" }),
      Object.freeze({ id: "history", label: "Deck" }),
      Object.freeze({ id: "stats", label: "Stats" }),
      Object.freeze({ id: "meetings", label: "Meetings" }),
      Object.freeze({ id: "reader", label: "Reader" }),
      Object.freeze({ id: "settings", label: "Settings" }),
    ]),
    primitives: Object.freeze([
      "focus-stage", "context-ledger", "numbered-spine",
      "command-surface", "state-surface", "control-rail",
    ]),
    effects: Object.freeze(["light", "standard", "full"]),
    statusSemantics: Object.freeze({
      gold: Object.freeze(["neutral", "warning"]),
      green: Object.freeze(["positive"]),
      red: Object.freeze(["danger"]),
    }),
    maxPrimaryActions: 1,
    states: Object.freeze({
      loading: Object.freeze({ kind: "loading", tone: "neutral", title: "Loading", message: "Mumble is getting this ready. Available content stays usable.", live: "polite", busy: true }),
      empty: Object.freeze({ kind: "empty", tone: "neutral", title: "Nothing here yet", message: "New items will appear here when they are available.", live: "polite", busy: false }),
      degraded: Object.freeze({ kind: "degraded", tone: "warning", title: "Some results are unavailable", message: "Working content remains available while Mumble retries the missing part.", live: "polite", busy: false }),
      error: Object.freeze({ kind: "error", tone: "danger", title: "This could not be loaded", message: "Your existing content is unchanged. Try the action again.", live: "assertive", busy: false }),
      success: Object.freeze({ kind: "success", tone: "positive", title: "Complete", message: "Mumble finished the action successfully.", live: "polite", busy: false }),
    }),
    controlRail: Object.freeze({
      minimumTargetPx: 28,
      timeCriticalTargetPx: 36,
      listeningOrder: Object.freeze(["mode-language", "deck", "stop"]),
      processingCancelRequiresSafeContract: true,
    }),
  });

  function describeState(kind, overrides) {
    const base = contract.states[kind];
    if (!base) throw new TypeError(`Unknown Focus Stage state: ${kind}`);
    return Object.freeze({ ...base, ...(overrides || {}), kind: base.kind, tone: base.tone });
  }

  function createStateSurface(kind, overrides, documentRef) {
    const doc = documentRef || root.document;
    const state = describeState(kind, overrides);
    const section = doc.createElement("section");
    section.className = "state-surface";
    section.dataset.state = state.kind;
    section.dataset.tone = state.tone;
    section.setAttribute("role", state.live === "assertive" ? "alert" : "status");
    section.setAttribute("aria-live", state.live);
    if (state.busy) section.setAttribute("aria-busy", "true");
    const copy = doc.createElement("div");
    copy.className = "state-surface__copy";
    const title = doc.createElement("h2");
    title.textContent = state.title;
    const message = doc.createElement("p");
    message.textContent = state.message;
    copy.append(title, message);
    section.append(copy);
    if (state.content) {
      const preserved = doc.createElement("div");
      preserved.className = "state-surface__content";
      if (typeof state.content === "string") preserved.textContent = state.content;
      else preserved.append(state.content);
      section.append(preserved);
    }
    if (state.action && state.action.label) {
      const button = doc.createElement("button");
      button.type = "button";
      button.className = "btn btn-gold state-surface__action";
      button.textContent = state.action.label;
      if (typeof state.action.onActivate === "function") button.addEventListener("click", state.action.onActivate);
      section.append(button);
    }
    return section;
  }

  function describeControlRail(input) {
    const options = input || {};
    const controls = [];
    if (options.mode || options.language) {
      controls.push({ id: "mode-language", label: [options.mode, options.language].filter(Boolean).join(" · "), action: false });
    }
    if (options.deckAvailable) controls.push({ id: "deck", label: "Deck", action: true, minimumTargetPx: 28 });
    if (options.phase === "listening") controls.push({ id: "stop", label: "Stop", action: true, minimumTargetPx: 36 });
    if (options.phase === "processing" && options.cancellable === true) {
      controls.push({ id: "cancel", label: "Cancel processing", action: true, minimumTargetPx: 36 });
    }
    return Object.freeze({ phase: options.phase || "idle", controls: Object.freeze(controls.map(Object.freeze)) });
  }

  function applyEffectsTier(tier, documentRef) {
    if (!contract.effects.includes(tier)) throw new TypeError(`Unknown effects tier: ${tier}`);
    const doc = documentRef || root.document;
    doc.documentElement.dataset.effectsTier = tier;
    return tier;
  }

  function initialise(documentRef) {
    const doc = documentRef || root.document;
    const nav = doc.querySelector(".navbar .nav");
    if (nav) {
      nav.classList.add("centred-destination-grid");
      nav.setAttribute("aria-label", "Primary destinations");
    }
    contract.destinations.forEach(destination => {
      const button = doc.querySelector(`.nav-btn[data-nav="${destination.id}"]`);
      if (!button) return;
      button.type = "button";
      button.setAttribute("aria-label", destination.label);
    });
    doc.documentElement.dataset.focusStageContract = contract.version;
  }

  const api = Object.freeze({ contract, describeState, createStateSurface, describeControlRail, applyEffectsTier, initialise });
  root.MumbleUIFoundation = api;
  if (root.document) {
    if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", () => initialise(root.document), { once: true });
    else initialise(root.document);
  }
})(typeof window !== "undefined" ? window : globalThis);
