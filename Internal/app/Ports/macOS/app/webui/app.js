/* ============================================================================
   Mumble — main-window SPA logic + Python bridge
   ----------------------------------------------------------------------------
   • Talks to the Python backend through window.pywebview.api.* (snake_case).
   • Degrades gracefully to realistic MOCK data when opened as a plain file in a
     browser (no pywebview) — so every screen, flow and state is verifiable.
   • Hand-rolled inline-SVG icons (no FontAwesome/CDN) → 100% offline.
   ========================================================================== */
"use strict";

/* ---- Inline SVG icon set (feather-style, currentColor) ------------------ */
const ICONS = {
  mic: '<path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="8" y1="22" x2="16" y2="22"/>',
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>',
  history:
    '<path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7v5l4 2"/>',
  chart:
    '<line x1="6" y1="20" x2="6" y2="12"/><line x1="12" y1="20" x2="12" y2="6"/><line x1="18" y1="20" x2="18" y2="14"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-2.82 1.17V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 7.6 19.4l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 5 13.6H4.91a2 2 0 0 1 0-4H5a1.65 1.65 0 0 0 1.51-1.18l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 11 4.6V4.51a2 2 0 0 1 4 0V4.6a1.65 1.65 0 0 0 2.82-1.18l-.06.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 11h.09a2 2 0 0 1 0 4z"/>',
  keyboard:
    '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 13h.01M18 13h.01M8 17h8"/>',
  wand: '<path d="m3 21 12-12"/><path d="M15 5l4 4"/><path d="M18 2l1 2 2 1-2 1-1 2-1-2-2-1 2-1z"/><path d="M5 13l.7 1.4 1.4.7-1.4.7L5 17l-.7-1.4L2.9 15l1.4-.7z"/>',
  robot:
    '<rect x="4" y="8" width="16" height="11" rx="2.5"/><path d="M12 8V4"/><circle cx="12" cy="3" r="1.4"/><circle cx="9" cy="13" r="1.2"/><circle cx="15" cy="13" r="1.2"/><path d="M9.5 16.5h5"/>',
  envelope:
    '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  list: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="3.5" cy="6" r="1.2"/><circle cx="3.5" cy="12" r="1.2"/><circle cx="3.5" cy="18" r="1.2"/>',
  reply:
    '<polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>',
  globe:
    '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18z"/>',
  star: '<polygon points="12 2.5 15 9 22 9.7 16.8 14.2 18.5 21 12 17.3 5.5 21 7.2 14.2 2 9.7 9 9"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  trash:
    '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.5 12.5 9-9"/><path d="m17 5 2 2"/><path d="m15 7 2 2"/>',
  info: '<circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16"/><circle cx="12" cy="8" r="0.6" fill="currentColor"/>',
  bolt: '<polygon points="13 2 4 14 11 14 10 22 20 9 13 9"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  plug: '<path d="M12 22v-5"/><path d="M9 8V2M15 8V2"/><path d="M7 8h10v3a5 5 0 0 1-10 0z"/>',
  bulb: '<path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V17h6v-.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z"/>',
  layers:
    '<polygon points="12 2 22 8.5 12 15 2 8.5"/><polyline points="2 15.5 12 22 22 15.5"/>',
  search:
    '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/>',
  circle: '<circle cx="12" cy="12" r="7" fill="currentColor" stroke="none"/>',
  rotate:
    '<polyline points="23 4 23 10 17 10"/><path d="M20.5 15a9 9 0 1 1-2.1-9.4L23 10"/>',
  chevronRight: '<polyline points="9 6 15 12 9 18"/>',
  chevronLeft: '<polyline points="15 6 9 12 15 18"/>',
  chevronDown: '<polyline points="6 9 12 15 18 9"/>',
  download:
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  folder:
    '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  external:
    '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
  sliders:
    '<line x1="4" y1="8" x2="20" y2="8"/><line x1="4" y1="16" x2="20" y2="16"/><circle cx="9" cy="8" r="2.2" fill="var(--surface)"/><circle cx="15" cy="16" r="2.2" fill="var(--surface)"/>',
  user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
  book: '<path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v18H6.5A2.5 2.5 0 0 0 4 22.5z"/><path d="M4 4.5v15"/>',
  volume:
    '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
  monitor:
    '<rect x="2" y="3.5" width="20" height="13" rx="2"/><line x1="8" y1="20.5" x2="16" y2="20.5"/><line x1="12" y1="16.5" x2="12" y2="20.5"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  edit: '<path d="M11 4H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-6"/><path d="M18.5 2.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4z"/>',
  arrowRight:
    '<line x1="4" y1="12" x2="20" y2="12"/><polyline points="14 6 20 12 14 18"/>',
  comment:
    '<path d="M21 11.5a8.5 8.5 0 0 1-12.3 7.6L3 21l1.9-5.7A8.5 8.5 0 1 1 21 11.5z"/><circle cx="8.5" cy="12" r="0.7" fill="currentColor"/><circle cx="12" cy="12" r="0.7" fill="currentColor"/><circle cx="15.5" cy="12" r="0.7" fill="currentColor"/>',
  sparkle:
    '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
  type: '<polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="1.5"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/>',
  share:
    '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><line x1="8.2" y1="10.8" x2="15.8" y2="7.2"/><line x1="8.2" y1="13.2" x2="15.8" y2="16.8"/>',
  power: '<path d="M12 4v8"/><path d="M7.5 7a7 7 0 1 0 9 0"/>',
  pin: '<path d="M9 3.5h6l-1 5.5 2.5 2.5v1.5h-4.2L12 21l-1.3-6.5H6.5V13L9 10.5 9 3.5z"/>',
  expand: '<polyline points="7 10 12 15 17 10"/>',
  refresh:
    '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.5 9a9 9 0 0 1 15-3.4L23 10M1 14l4.5 4.4A9 9 0 0 0 20.5 15"/>',
  pen: '<path d="M12 19l7.5-7.5a2.1 2.1 0 0 0-3-3L9 16l-1.2 4.2z"/><path d="M15 8l3 3"/><path d="M4 21h4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  gauge:
    '<path d="M5 19a9 9 0 1 1 14 0"/><path d="M12 14l3.5-4.5"/><circle cx="12" cy="15" r="1.3" fill="currentColor"/>',
  fire: '<path d="M12 22a6 6 0 0 0 6-6c0-3.4-2.4-5.4-3.7-7.8C13.2 6.2 13 4 12 2c-.6 2.2-1.7 3.4-3.1 5C7.4 8.8 6 10.8 6 13a6 6 0 0 0 6 9z"/><path d="M12 22a3 3 0 0 0 3-3c0-1.7-1.4-2.6-3-4.8-1.6 2.2-3 3.1-3 4.8a3 3 0 0 0 3 3z"/>',
  cloud: '<path d="M7 18a4 4 0 0 1 0-8 5.5 5.5 0 0 1 10.6-1.4A3.7 3.7 0 0 1 18 18z"/>',
  play: '<polygon points="6 4 20 12 6 20 6 4" fill="currentColor" stroke="none"/>',
  pause:
    '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" stroke="none"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
  'eye-off': '<path d="M9.9 5.2A9.9 9.9 0 0 1 12 5c6.5 0 10 7 10 7a18 18 0 0 1-3 3.7M6.1 6.1A18 18 0 0 0 2 12s3.5 7 10 7a9.6 9.6 0 0 0 4.1-.9"/><path d="M3 3l18 18"/>',
};

function svg(name, cls) {
  const p = ICONS[name] || "";
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"${cls ? ` class="${cls}"` : ""}>${p}</svg>`;
}
function paintIcons(root) {
  (root || document).querySelectorAll("[data-icon]").forEach((el) => {
    if (el.dataset.painted) return;
    el.innerHTML = svg(el.dataset.icon, el.dataset.iconClass);
    el.dataset.painted = "1";
  });
}

/* ---- Small DOM helpers --------------------------------------------------- */
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
// HTML-escape for BOTH text and attribute contexts. The textContent round-trip
// escapes &<> but NOT quotes, so interpolating clipboard/transcript text into a
// double-quoted attribute (data-copy, data-fav, data-selkey…) let a `"` break out
// and inject a live event handler into the privileged webview. Escape the quotes
// too. (data-* round-trips fine — the browser decodes entities on dataset read.)
const esc = (t) =>
  (t == null ? "" : String(t))
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
const fmtNum = (n) => (Number(n) || 0).toLocaleString();

/* ============================================================================
   BRIDGE — Python Api with mock fallback
   ========================================================================== */
const HAS_PY = () => !!(window.pywebview && window.pywebview.api);

const MODE_COLORS = {
  text: "var(--mode-text)",
  prompt: "var(--mode-prompt)",
  email: "var(--mode-email)",
  reply: "var(--mode-reply)",
  foreign: "var(--mode-foreign)",
  convert: "var(--mode-convert)",
  context: "var(--mode-context)",
  clip: "var(--mode-context)",
  prompt_src: "var(--mode-prompt)",
};
// Readable mode names — so a favourite keeps its origin identity (Email reads
// "Email", not a generic "transcript").
const MODE_LABELS = {
  text: "Text",
  prompt: "Prompt",
  email: "Email",
  foreign: "Foreign",
  convert: "Convert",
  context: "Context",
};
// One-line descriptions (refinement pass §13) — surfaced as tooltips on mode chips
// so each mode's purpose is discoverable everywhere it appears.
const MODE_DESCRIPTIONS = {
  text: "Clean, punctuated plain text — the default, no AI needed",
  prompt: "Turns a rough ask into a polished, structured AI prompt",
  email: "Drafts a tidy email — greeting, body and sign-off",
  foreign: "Keeps non-English terms accurate instead of anglicising them",
  convert: "Reshape one item into another mode (a per-item, one-off transform)",
  context: "A captured AI conversation used as reply/prompt context",
};

/* realistic mock data for browser preview */
const MOCK = {
  overview: {
    version: "0.95",
    tagline: "Speak. It types.",
    total_words: 48213,
    total_transcripts: 1024,
    current_streak: 6,
    best_streak: 19,
    today_words: 1840,
    time_saved: "17.8 h",
    provider: "cerebras",
    model: "small.en",
    pro_mode: true,
    key_configured: false,
    first_run: false,
    lite_available: true,
  },
  hotkeys: {
    hotkey: "Ctrl + Option + D",
    quick_paste_hotkey: "Ctrl + Option + V",
    history_hotkey: "Ctrl + Option + H",
    search_hotkey: "Ctrl + Option + S",
  },
  transcripts: [
    {
      time: "14:02",
      stamp: "2026-06-10 14:02",
      mode: "email",
      words: 64,
      duration: 33,
      quality: "good",
      text: "Hi Sarah, thanks for the quick turnaround on the deck. I have reviewed the latest revision and it looks great — let us ship it on Friday as planned. Best, Alex",
      raw: "um email to sarah thanks for the quick turnaround on the deck uh i reviewed the latest revision looks great lets ship friday as planned",
      fav: false,
    },
    {
      time: "13:48",
      stamp: "2026-06-10 13:48",
      mode: "prompt",
      words: 120,
      duration: 58,
      quality: "fair",
      text: "You are an expert technical writer. Produce clear, concise API documentation for the following endpoint, including parameters, return shape and one example…",
      fav: true,
    },
    {
      time: "13:31",
      stamp: "2026-06-10 13:31",
      mode: "text",
      words: 28,
      duration: 13,
      quality: "good",
      text: "Let us move the standup to ten thirty tomorrow so the design review has room to breathe.",
      fav: false,
    },
    {
      time: "12:55",
      stamp: "2026-06-10 12:55",
      mode: "text",
      words: 22,
      duration: 16,
      quality: "bad",
      text: "• Renew the domain\n• Email the accountant\n• Push the v2.9 release notes\n• Book the venue",
      fav: false,
    },
    {
      time: "11:20",
      stamp: "2026-06-10 11:20",
      mode: "prompt",
      words: 41,
      duration: 21,
      quality: "good",
      text: "Appreciate you flagging this — I agree the latency spike is worth a closer look. Let us pair on it after lunch and trace the slow path together.",
      fav: false,
    },
  ],
  clipboard: [
    {
      type: "text",
      time: "14:05",
      stamp: "2026-06-10 14:05",
      text: "https://cloud.cerebras.ai/ — free API key, ~1800 tokens/s",
      fav: false,
    },
    {
      type: "text",
      time: "13:50",
      stamp: "2026-06-10 13:50",
      text: "The quarterly figures came in 12% above forecast, driven mostly by the EMEA region.",
      fav: true,
    },
    {
      type: "image",
      time: "13:10",
      stamp: "2026-06-10 13:10",
      size: "512x288",
      fav: false,
    },
    {
      type: "text",
      time: "12:30",
      stamp: "2026-06-10 12:30",
      text: "def transcribe(path): return model.transcribe(path, beam_size=5)",
      fav: false,
    },
  ],
  prompts: [
    {
      time: "2026-06-10 13:48",
      request: "write api docs for the transcribe endpoint",
      prompt:
        "You are an expert technical writer. Produce clear, concise API documentation for the POST /transcribe endpoint. Cover: each parameter and its type, the JSON return shape, error codes, and a single end-to-end example. Use British English and keep prose tight.",
      fav: true,
    },
    {
      time: "2026-06-10 10:12",
      request: "prompt to brainstorm names for a voice app",
      prompt:
        "You are a world-class brand strategist. Brainstorm 20 distinctive product names for a private, on-device voice-to-text desktop app. For each, give the name, a one-line rationale, and a tone tag (playful / premium / technical).",
      fav: false,
    },
  ],
  daily: (() => {
    // 91 days ending 2026-06-10 with a believable weekly rhythm (heatmap demo)
    const out = [],
      end = new Date("2026-06-10T00:00:00");
    for (let i = 90; i >= 0; i--) {
      const d = new Date(end);
      d.setDate(end.getDate() - i);
      const wd = (d.getDay() + 6) % 7; // 0=Mon
      const base = [1400, 2100, 1700, 2400, 1900, 350, 150][wd];
      const w = Math.max(0, Math.round(base * (0.4 + ((i * 37) % 100) / 100)));
      out.push({
        day: d.toISOString().slice(0, 10),
        words: i === 5 ? 0 : w,
        transcripts: Math.round(w / 45),
      });
    }
    out[out.length - 1].words = 1840;
    return out;
  })(),
  modes: [
    { mode: "text", count: 612, words: 24800 },
    { mode: "prompt", count: 188, words: 13200 },
    { mode: "email", count: 121, words: 6400 },
    { mode: "foreign", count: 8, words: 413 },
    { mode: "convert", count: 14, words: 980 },
  ],
  presets: [
    [1, "Chat context", "Rebuilds copied turns into a chat", 1],
    [2, "Focus", "Cuts to what actually matters", 1],
    [3, "Deep Thinking", "Reasons through it thoroughly", 1],
    [4, "Summarise", "Condenses it to the key points", 1],
    [5, "Answer it", "Answers the question or task", 1],
    [6, "Continue", "Carries on where it left off", 1],
    [8, "Improve", "Clearer & tighter, same meaning", 1],
    [9, "Fix errors", "Spelling & grammar only", 1],
    [10, "Make shorter", "Cuts it to half or less", 1],
    [11, "Expand", "Adds depth & detail", 1],
    [12, "Explain simply", "Plain-language explainer", 1],
    [13, "Key points", "Bulleted main points", 1],
    [14, "Action items", "Tasks, owners & deadlines", 1],
    [15, "Humanise", "Makes AI text read naturally", 1],
    [16, "Merge", "Blends all items into one", 1],
    [17, "Extract facts", "Pulls out names, dates, numbers", 1],
    [18, "Make formal", "Professional, polished tone", 1],
    [19, "Critique", "Honest feedback + fixes", 1],
    [20, "Outline", "Structures it into an outline", 1],
  ],
  readerLib: [
    {
      id: "doc1",
      title: "The Fall of the House of Usher",
      text: "During the whole of a dull, dark, and soundless day in the autumn of the year, when the clouds hung oppressively low in the heavens, I had been passing alone, on horseback, through a singularly dreary tract of country; and at length found myself, as the shades of the evening drew on, within view of the melancholy House of Usher. I know not how it was — but, with the first glimpse of the building, a sense of insufferable gloom pervaded my spirit.",
      position: 18,
      opened: Date.now() / 1000 - 3600,
      starred: true,
      collections: ["Fiction", "Classics"],
      format: "txt",
      reading_sessions: [{ start: Date.now() / 1000 - 3600, end: Date.now() / 1000 - 1800, start_pos: 0, end_pos: 18, duration_sec: 180 }],
    },
    {
      id: "doc2",
      title: "Quarterly product notes",
      text: "Shipping the new Reader this week. It reads any document aloud in a natural AI voice and remembers where you left off. Next up: PDF import and a mobile layout. We should measure how many people finish a document versus abandon it midway.",
      position: 12,
      opened: Date.now() / 1000 - 7200,
      starred: false,
      collections: ["Work"],
      format: "docx",
      reading_sessions: [{ start: Date.now() / 1000 - 7200, end: Date.now() / 1000 - 7000, start_pos: 0, end_pos: 12, duration_sec: 200 }],
    },
    {
      id: "doc3",
      title: "Meeting notes — design review",
      text: "Reviewed the new island bar design. Agreed on gold bloom intensity and the breathing rim animation. Action items: (1) Adjust the 9px gap to zero, (2) Test on 125% DPI, (3) Port to Mac overlay. Next review Friday 3pm.",
      position: 0,
      opened: Date.now() / 1000 - 86400,
      starred: true,
      collections: ["Work"],
      format: "txt",
      reading_sessions: [],
    },
    {
      id: "doc4",
      title: "Q3 Sales Data",
      text: "",
      position: 0,
      opened: Date.now() / 1000 - 36000,
      starred: false,
      collections: ["Work", "Data"],
      format: "csv",
      blocks: [
        { type: "heading", text: "Q3 Regional Sales", level: 1, meta: null },
        { type: "paragraph", text: "Sales figures for Q3 2026 across all regions. Data sourced from the CRM pipeline.", level: 0, meta: null },
        { type: "table", text: "", level: 0, meta: {
          headers: ["Region", "Revenue", "Growth", "Deals Closed"],
          rows: [
            ["North America", "$2.4M", "+12%", "48"],
            ["Europe", "$1.8M", "+8%", "32"],
            ["Asia Pacific", "$1.2M", "+18%", "24"],
            ["Latin America", "$0.6M", "+22%", "14"],
            ["Middle East", "$0.4M", "+6%", "8"]
          ]
        }},
        { type: "paragraph", text: "North America continues to lead with $2.4M in revenue. Asia Pacific shows the strongest growth momentum at +18%. The total Q3 pipeline across all regions reached $6.4M.", level: 0, meta: null },
        { type: "table", text: "", level: 0, meta: {
          headers: ["Month", "Pipeline", "Won", "Lost"],
          rows: [
            ["July", "$1.8M", "$1.2M", "$0.3M"],
            ["August", "$2.1M", "$1.5M", "$0.4M"],
            ["September", "$2.5M", "$1.7M", "$0.2M"]
          ],
          truncated: true
        }},
      ],
      reading_sessions: [],
    },
    {
      id: "doc5",
      title: "Sprint Retrospective Summary",
      text: "The team completed 12 of 14 sprint items this cycle. Velocity increased by 8% compared to the previous sprint. The QA automation pipeline caught 3 regressions before deployment, saving approximately 4 hours of manual testing. Key takeaway: invest more time in code review to reduce the bug bounce rate.",
      position: 5,
      opened: Date.now() / 1000 - 108000,
      starred: false,
      collections: ["Work"],
      format: "xlsx",
      reading_sessions: [{ start: Date.now() / 1000 - 108000, end: Date.now() / 1000 - 107700, start_pos: 0, end_pos: 5, duration_sec: 300 }],
    },
  ],
  settings: {
    user_name: "Alex",
    hotkey: "ctrl+option+d",
    quick_paste_hotkey: "ctrl+option+v",
    history_hotkey: "ctrl+option+h",
    search_hotkey: "ctrl+option+s",
    search_engine: "perplexity",
    prompt_mode_enabled: false,
    auto_format: true,
    english_only: true,
    compute_type: "int8",
    device: "auto",
    cpu_threads: 0,
    deck_pinned: true,
    resource_saver: false,
    meeting_processing_mode: "lightweight",
    sync_settings: true,
    sync_stats: true,
    sync_history: true,
    sync_reader: false,
    sync_favorites: true,
    sync_presets: true,
    modes: {
      prompt: true,
      email: true,
      foreign: true,
      convert: true,
    },
    prompt_prefs: {
      tone: "Neutral",
      detail: "Balanced",
      structure: "Bullets & headings",
      audience: "General",
      reasoning: "Just the answer",
    },
    polish_aggressiveness: "Light",
    instant_text: true,
    pro_mode: true,
    llm_provider: "cerebras",
    cerebras_api_key: "",
    cerebras_model: "gpt-oss-120b",
    openai_api_key: "",
    groq_api_key: "",
    openrouter_api_key: "",
    openrouter_model: "openai/gpt-5.4-mini",
    transcription_mode: "local",
    cloud_transcription_provider: "groq",
    groq_transcription_model: "whisper-large-v3-turbo",
    openai_transcription_model: "gpt-4o-mini-transcribe",
    openrouter_transcription_model: "openai/gpt-4o-mini-transcribe",
    local_url: "http://localhost:11434",
    local_model: "llama3",
    browser: "default",
    model: "small.en",
    language: "en",
    mic_device: null,
    foreign_languages: ["arabic"],
    history_max: 5000,
    clipboard_enabled: true,
    clipboard_max: 5000,
    autostart: true,
    vocabulary_terms: ["Mumble", "Cerebras", "faster-whisper"],
    vocabulary: { mambo: "Mumble" },
    ui_effects: "enhanced",
  },
  mics: [
    { index: -1, name: "System default" },
    { index: 0, name: "Microphone (Realtek Audio)" },
    { index: 1, name: "Headset (Bluetooth)" },
  ],
  meetings: [
    {
      id: "a1b2c3d4e5f6",
      title: "Sprint Planning — Jun 28, 2026 · 52 min",
      created: Date.now() / 1000 - 86400,
      duration_sec: 3120.5,
      duration_display: "52:00",
      segment_count: 34,
      speaker_count: 3,
      speakers: [
        { label: "Speaker 1", name: "Alice", color: "#D4AF37" },
        { label: "Speaker 2", name: "Bob", color: "#5AA9E6" },
        { label: "Speaker 3", name: "Charlie", color: "#46C9A8" },
      ],
      has_summary: true,
      action_item_count: 3,
      processing_mode: "deep",
      key_decision_count: 2,
      open_question_count: 2,
      starred: true,
      tags: [],
      preview: "Alright, let's go through the sprint backlog. We have 12 items and 80 points to allocate across the team. Alice, you're on the payment integration — where are we with the Stripe webhook handling? The QA environment needs that endpoint live by Wednesday for the demo.",
      summary: "The sprint planning meeting covered the payment integration status (Alice), the search performance improvements (Bob), and the onboarding flow redesign (Charlie). The team agreed to allocate 80 points across 12 items with specific assignments. Key decisions included moving the demo to Thursday and adopting Vitest for testing. Open questions remain around the mobile layout timeline and offline mode approach.",
      action_items: ["Alice: Finalise Stripe webhook handling by Wednesday", "Bob: Profile the search query and report back by Tuesday", "Charlie: Draft the new onboarding screens and share in Figma by Friday"],
      key_decisions: ["Sprint demo moved to Thursday 3pm (from Wednesday)", "Adopt Vitest for front-end testing starting this sprint"],
      open_questions: ["Do we need a mobile-responsive layout for the dashboard, or can it wait until next sprint?", "Should the offline mode use IndexedDB or localStorage for meeting transcripts?"],
      segments: [
        { speaker: "Speaker 1", start_sec: 0.0, end_sec: 8.5, text: "Alright, let's go through the sprint backlog. We have 12 items and 80 points to allocate across the team.", confidence: 0.95 },
        { speaker: "Speaker 1", start_sec: 8.5, end_sec: 15.2, text: "Alice, you're on the payment integration — where are we with the Stripe webhook handling?", confidence: 0.92 },
        { speaker: "Speaker 2", start_sec: 15.2, end_sec: 24.8, text: "The webhook endpoint is 80% done. I need to handle the idempotency keys properly — Stripe can send duplicate events and we don't want to double-charge anyone.", confidence: 0.88 },
        { speaker: "Speaker 2", start_sec: 24.8, end_sec: 30.1, text: "The QA environment needs that endpoint live by Wednesday for the demo, so I'll wrap it up by Tuesday evening.", confidence: 0.91 },
        { speaker: "Speaker 1", start_sec: 30.1, end_sec: 35.0, text: "Great, thanks Alice. Bob, how's the search performance work coming along?", confidence: 0.94 },
        { speaker: "Speaker 3", start_sec: 35.0, end_sec: 46.2, text: "I profiled the search query yesterday — the issue is the full-text index on the notes field. It's doing a sequential scan for any query longer than three words. I think we can add a GIN index and it'll drop from 2.4 seconds to maybe 80 milliseconds.", confidence: 0.87 },
        { speaker: "Speaker 3", start_sec: 46.2, end_sec: 52.0, text: "I'll run the migration on staging first and report back by Tuesday. If it's good, we ship it Wednesday.", confidence: 0.89 },
        { speaker: "Speaker 1", start_sec: 52.0, end_sec: 58.5, text: "Perfect. Charlie, the onboarding flow — where are we on the redesign?", confidence: 0.93 },
        { speaker: "Speaker 2", start_sec: 58.5, end_sec: 67.3, text: "I've got the wireframes ready in Figma — it's a three-step wizard now instead of the single page. Step one is account details, step two is preferences, step three is the guided tour.", confidence: 0.90 },
        { speaker: "Speaker 2", start_sec: 67.3, end_sec: 74.0, text: "I'll share the Figma link in Slack today and we can review async. Implementation target is Friday.", confidence: 0.91 },
        { speaker: "Speaker 1", start_sec: 74.0, end_sec: 82.1, text: "Sounds good. One more thing — should we move the demo from Wednesday to Thursday? The Stripe endpoint won't be ready until Tuesday night and we want it polished for the demo.", confidence: 0.92 },
        { speaker: "Speaker 3", start_sec: 82.1, end_sec: 88.0, text: "Thursday 3pm works for me. Gives us an extra day and everyone's free at that slot.", confidence: 0.90 },
        { speaker: "Speaker 1", start_sec: 88.0, end_sec: 94.5, text: "Done — Thursday 3pm. I'll update the calendar invite. Also, I've been looking at Vitest for our front-end tests — it's faster than Jest and has native ESM support.", confidence: 0.88 },
        { speaker: "Speaker 2", start_sec: 94.5, end_sec: 102.0, text: "I've used Vitest on my last project. The migration from Jest is straightforward and the watch mode is instant. I'm in favour.", confidence: 0.93 },
        { speaker: "Speaker 1", start_sec: 102.0, end_sec: 110.5, text: "Alright, let's adopt Vitest starting this sprint. I'll set up the config and we can migrate the existing tests incrementally. Anything else before we wrap up?", confidence: 0.91 },
        { speaker: "Speaker 3", start_sec: 110.5, end_sec: 117.8, text: "One question — do we need a mobile-responsive layout for the dashboard? The product team mentioned it but it wasn't in the original scope.", confidence: 0.85 },
        { speaker: "Speaker 1", start_sec: 117.8, end_sec: 125.0, text: "Let's table that for next sprint. If product pushes for it, we can slot it in when we have more data on mobile usage. Also — should the offline mode use IndexedDB or localStorage for meeting transcripts?", confidence: 0.86 },
        { speaker: "Speaker 2", start_sec: 125.0, end_sec: 131.2, text: "IndexedDB — localStorage has a 5MB cap and meeting transcripts can be large. IndexedDB is async too, which fits our architecture better.", confidence: 0.88 },
        { speaker: "Speaker 1", start_sec: 131.2, end_sec: 137.0, text: "Good point. We'll decide that offline-mode choice next sprint alongside the mobile layout. Alright everyone, great meeting — let's ship it!", confidence: 0.92 },
      ],
    },
    {
      id: "f6e5d4c3b2a1",
      title: "Design Review — Jun 27, 2026 · 28 min",
      created: Date.now() / 1000 - 172800,
      duration_sec: 1680.0,
      duration_display: "28:00",
      segment_count: 18,
      speaker_count: 2,
      speakers: [
        { label: "Speaker 1", name: "Diana", color: "#D4AF37" },
        { label: "Speaker 2", name: null, color: "#5AA9E6" },
      ],
      has_summary: true,
      action_item_count: 2,
      processing_mode: "lightweight",
      key_decision_count: 0,
      open_question_count: 0,
      starred: false,
      tags: [],
      preview: "Let's go through the new island bar design. I adjusted the gold bloom intensity and added the breathing rim animation you suggested. The 9px gap between the bars is now zero — they sit flush and the waveform looks continuous.",
      summary: "The design review focused on the island bar visual polish. Diana presented the updated gold bloom intensity and breathing rim animation. The team agreed to test on 125% DPI and to port the design to the Mac overlay. Two action items were captured.",
      action_items: ["Diana: Test the island bar on 125% DPI and fix any scaling artifacts", "Diana: Port the island bar design to the Mac overlay component"],
      key_decisions: [],
      open_questions: [],
      segments: [
        { speaker: "Speaker 1", start_sec: 0.0, end_sec: 9.2, text: "Let's go through the new island bar design. I adjusted the gold bloom intensity and added the breathing rim animation you suggested.", confidence: 0.94 },
        { speaker: "Speaker 1", start_sec: 9.2, end_sec: 16.5, text: "The 9px gap between the bars is now zero — they sit flush and the waveform looks continuous. Here, let me share my screen.", confidence: 0.91 },
        { speaker: "Speaker 2", start_sec: 16.5, end_sec: 23.0, text: "That looks much better. The breathing rim is subtle — you barely notice it unless you're looking for it, which is exactly what we want.", confidence: 0.88 },
        { speaker: "Speaker 2", start_sec: 23.0, end_sec: 29.8, text: "One thing though — have you tested this on 125% DPI? The rim animation might look different at higher scaling.", confidence: 0.87 },
        { speaker: "Speaker 1", start_sec: 29.8, end_sec: 37.2, text: "Good catch. I've only tested on 100% and 150%. I'll run it on 125% this afternoon and fix any scaling artifacts.", confidence: 0.90 },
        { speaker: "Speaker 2", start_sec: 37.2, end_sec: 42.0, text: "Also, when we port this to the Mac overlay, the transparency model is different — we use vibrancy there instead of the solid glass gradient.", confidence: 0.86 },
        { speaker: "Speaker 1", start_sec: 42.0, end_sec: 49.5, text: "Right, the Mac port. I'll tackle that after the DPI fix. Should I use the same gold bloom intensity or dial it back for the light-mode vibrancy background?", confidence: 0.89 },
        { speaker: "Speaker 2", start_sec: 49.5, end_sec: 56.0, text: "Dial it back about 30% — on a light background the bloom can look muddy instead of luminous. Keep the breathing rim the same though.", confidence: 0.91 },
        { speaker: "Speaker 1", start_sec: 56.0, end_sec: 62.8, text: "Got it. I'll have both fixes ready by Friday's review. Anything else on the island?", confidence: 0.92 },
        { speaker: "Speaker 2", start_sec: 62.8, end_sec: 68.0, text: "Nope, that's it. Nice work on the animation — it feels alive without being distracting.", confidence: 0.93 },
      ],
    },
    {
      id: "c3d4e5f6a1b2",
      title: "Quick Sync — Jun 26, 2026 · 15 min",
      created: Date.now() / 1000 - 259200,
      duration_sec: 900.0,
      duration_display: "15:00",
      segment_count: 10,
      speaker_count: 2,
      speakers: [
        { label: "Speaker 1", name: null, color: "#D4AF37" },
        { label: "Speaker 2", name: null, color: "#5AA9E6" },
      ],
      has_summary: false,
      action_item_count: 0,
      processing_mode: "lightweight",
      key_decision_count: 0,
      open_question_count: 0,
      starred: false,
      tags: [],
      preview: "Quick check-in on the release timeline. We're on track for the Friday deploy — all tests are green and the staging environment looks stable. The only blocker was the database migration, but that's resolved now.",
      summary: null,
      action_items: [],
      key_decisions: [],
      open_questions: [],
      segments: [
        { speaker: "Speaker 1", start_sec: 0.0, end_sec: 6.5, text: "Quick check-in on the release timeline. We're on track for the Friday deploy.", confidence: 0.93 },
        { speaker: "Speaker 1", start_sec: 6.5, end_sec: 13.2, text: "All tests are green and the staging environment looks stable. The only blocker was the database migration, but that's resolved now.", confidence: 0.91 },
        { speaker: "Speaker 2", start_sec: 13.2, end_sec: 19.8, text: "Great news. What about the load test results? Last I checked they were still running.", confidence: 0.89 },
        { speaker: "Speaker 1", start_sec: 19.8, end_sec: 26.0, text: "They finished this morning — 500 concurrent users, p99 latency under 200ms. We're well within the SLO.", confidence: 0.92 },
        { speaker: "Speaker 2", start_sec: 26.0, end_sec: 31.5, text: "Perfect. Let's do the deploy Friday morning then, around 9am before the US team comes online.", confidence: 0.90 },
        { speaker: "Speaker 1", start_sec: 31.5, end_sec: 37.0, text: "Works for me. I'll send the release notes to the team by Thursday evening.", confidence: 0.94 },
        { speaker: "Speaker 2", start_sec: 37.0, end_sec: 42.0, text: "Sounds good. Anything else? No? Alright, short and sweet — thanks!", confidence: 0.91 },
      ],
    },
  ],
  meetingStats: {
    meeting_count: 3,
    total_meeting_minutes: 95,
    total_meeting_segments: 62,
  },
};

async function call(name, ...args) {
  if (HAS_PY() && typeof window.pywebview.api[name] === "function") {
    try {
      return await window.pywebview.api[name](...args);
    } catch (e) {
      console.error("api." + name + " failed", e);
      throw e;
    }
  }
  // ---- mock fallback ----
  await new Promise((r) => {
    setTimeout(r, 60);
  });
  switch (name) {
    case "get_overview": {
      var ov = { ...MOCK.overview };
      var ms = MOCK.meetingStats || {};
      ov.meeting_count = (MOCK.meetings || []).length;
      ov.total_meeting_minutes = ms.total_meeting_minutes || 0;
      return ov;
    }
    case "get_hotkeys":
      return { ...MOCK.hotkeys };
    case "get_transcripts":
      return MOCK.transcripts.slice(0, args[0] || 50);
    case "get_clipboard":
      return MOCK.clipboard.slice(0, args[0] || 50);
    case "get_prompts":
      return MOCK.prompts.slice();
    case "get_daily_stats":
      return MOCK.daily.slice(-(args[0] || 7));
    case "get_mode_stats":
      return MOCK.modes.slice();
    case "get_presets":
      return MOCK.presets.slice();
    case "get_openrouter_credits":
      return { ok: true, total: 10, used: 3.42, remaining: 6.58, message: "$6.58 of $10.00 left" };
    case "reader_supported_formats":
      return [".csv", ".odt", ".pptx", ".xlsx", ".pdf", ".docx", ".html", ".htm", ".epub", ".rtf", ".md", ".markdown", ".txt"];
    case "reader_tts_models":
      // Mirror the REAL catalogue: multi-provider TTS with voice tags.
      return {
        providers: [
          { id: "openrouter", label: "OpenRouter", auth_setting: "openrouter_api_key", has_key: true, default_model: "google/gemini-3.1-flash-tts-preview" },
          { id: "openai", label: "OpenAI", auth_setting: "openai_api_key", has_key: true, default_model: "gpt-4o-mini-tts" },
        ],
        provider: "openrouter",
        models: [
          ["google/gemini-3.1-flash-tts-preview", "Google Gemini Flash TTS — expressive (recommended)"],
          ["mistralai/voxtral-mini-tts-2603", "Mistral Voxtral mini TTS"],
          ["microsoft/mai-voice-2", "Microsoft MAI Voice 2"],
          ["gpt-4o-mini-tts", "GPT-4o mini TTS"],
        ],
        voices: [
          { id: "Fenrir", name: "Fenrir", gender: "male", quality: "high", persona: "deep", provider: "openrouter", provider_label: "OpenRouter", model: "google/gemini-3.1-flash-tts-preview", model_label: "Google Gemini Flash TTS" },
          { id: "Puck", name: "Puck", gender: "male", quality: "high", persona: "warm", provider: "openrouter", provider_label: "OpenRouter", model: "google/gemini-3.1-flash-tts-preview", model_label: "Google Gemini Flash TTS" },
          { id: "Charon", name: "Charon", gender: "male", quality: "high", persona: "deep", provider: "openrouter", provider_label: "OpenRouter", model: "google/gemini-3.1-flash-tts-preview", model_label: "Google Gemini Flash TTS" },
          { id: "onyx", name: "Onyx", gender: "male", quality: "high", persona: "deep", provider: "openai", provider_label: "OpenAI", model: "gpt-4o-mini-tts", model_label: "GPT-4o mini TTS" },
        ],
        default_model: "google/gemini-3.1-flash-tts-preview",
        default_voice: "Fenrir",
        has_key: true,
      };
    case "reader_tts_providers":
      return [
        { id: "openrouter", label: "OpenRouter", auth_setting: "openrouter_api_key", has_key: true, default_model: "google/gemini-3.1-flash-tts-preview" },
        { id: "openai", label: "OpenAI", auth_setting: "openai_api_key", has_key: true, default_model: "gpt-4o-mini-tts" },
      ];
    case "reader_list":
      return (MOCK.readerLib || []).map((d) => ({
        id: d.id, title: d.title, preview: (d.text || "").slice(0, 160),
        length: (d.text || "").split(/\s+/).length, position: d.position || 0,
        percent: Math.round((100 * (d.position || 0)) / Math.max(1, (d.text || "").split(/\s+/).length)),
        opened: d.opened || 0, added: d.added || d.opened || 0,
        starred: !!d.starred, bookmark_count: (d.bookmarks || []).length,
        collections: d.collections || [],
        format: d.format || "txt",
        session_count: (d.reading_sessions || []).length,
        total_minutes: Math.round(((d.reading_sessions || []).reduce((a, s) => a + (s.duration_sec || 0), 0) / 60) * 10) / 10,
        last_read: (d.reading_sessions || [])[0]?.start || 0,
      }));
    case "reader_open": {
      const d = (MOCK.readerLib || []).find((x) => x.id === args[0]);
      return d ? { id: d.id, title: d.title, text: d.text, blocks: d.blocks || null, format: d.format || "txt", position: d.position || 0, starred: !!d.starred, bookmarks: d.bookmarks || [] } : null;
    }
    case "reader_save": {
      MOCK.readerLib = MOCK.readerLib || [];
      const id = "doc" + (MOCK.readerLib.length + 1);
      MOCK.readerLib.unshift({ id, title: (args[1] || "").split("\n")[0].slice(0, 48) || "Untitled", text: args[1] || "", position: 0, opened: Date.now(), format: "txt", collections: [], reading_sessions: [] });
      return { ok: true, id };
    }
    case "reader_position": {
      const d = (MOCK.readerLib || []).find((x) => x.id === args[0]);
      if (d) d.position = args[1];
      return { ok: true };
    }
    case "reader_delete":
      MOCK.readerLib = (MOCK.readerLib || []).filter((x) => x.id !== args[0]);
      return { ok: true };
    case "reader_import_file": {
      // Mock: parse filename for format and add a dummy entry
      var path = args[0] || "";
      var name = path.split(/[/\\]/).pop() || "Imported document";
      var ext = (name.split(".").pop() || "").toLowerCase();
      var formatMap = { csv: "csv", odt: "odt", pptx: "pptx", xlsx: "xlsx", pdf: "pdf", docx: "docx", html: "html", htm: "html", epub: "epub", rtf: "rtf", md: "markdown", markdown: "markdown", txt: "txt" };
      var fmt = formatMap[ext] || "txt";
      var idVal = "doc" + (MOCK.readerLib.length + 1);
      MOCK.readerLib.unshift({ id: idVal, title: name, text: "Imported " + fmt.toUpperCase() + " document: " + name + "\n\nThis is a mock preview for file:// testing. In the live app, the full document content would be parsed and displayed here.", position: 0, opened: Date.now() / 1000, format: fmt, collections: [], reading_sessions: [] });
      return { ok: true, id: idVal, title: name, format: fmt, block_count: 3 };
    }
    case "reader_import_bytes": {
      var filename = args[1] || "imported.docx";
      var ext = (filename.split(".").pop() || "").toLowerCase();
      var formatMap = { csv: "csv", odt: "odt", pptx: "pptx", xlsx: "xlsx", pdf: "pdf", docx: "docx", html: "html", htm: "html", epub: "epub", rtf: "rtf", md: "markdown", markdown: "markdown", txt: "txt" };
      var fmt = formatMap[ext] || "txt";
      var idVal = "doc" + (MOCK.readerLib.length + 1);
      MOCK.readerLib.unshift({ id: idVal, title: filename, text: "Imported " + fmt.toUpperCase() + " document from bytes: " + filename + "\n\nThis is a mock preview for file:// testing.", position: 0, opened: Date.now() / 1000, format: fmt, collections: [], reading_sessions: [] });
      return { ok: true, id: idVal, title: filename, format: fmt, block_count: 3 };
    }
    case "reader_add_to_collection": {
      var d = (MOCK.readerLib || []).find((x) => x.id === args[0]);
      if (d) {
        d.collections = d.collections || [];
        if (d.collections.indexOf(args[1]) < 0) d.collections.push(args[1]);
      }
      MOCK.readerCollections = MOCK.readerCollections || [];
      if (args[1] && MOCK.readerCollections.indexOf(args[1]) < 0)
        MOCK.readerCollections.push(args[1]);
      return { ok: true };
    }
    case "reader_create_collection": {
      var collectionName = String(args[0] || "").trim();
      if (!collectionName) return { ok: false, message: "Enter a collection name." };
      MOCK.readerCollections = MOCK.readerCollections || [];
      var created = MOCK.readerCollections.indexOf(collectionName) < 0;
      if (created) MOCK.readerCollections.push(collectionName);
      return { ok: true, created: created };
    }
    case "reader_remove_from_collection": {
      var d = (MOCK.readerLib || []).find((x) => x.id === args[0]);
      if (d) {
        d.collections = (d.collections || []).filter((c) => c !== args[1]);
      }
      return { ok: true };
    }
    case "reader_list_collections": {
      var counts = {};
      (MOCK.readerCollections || []).forEach(function (name) { counts[name] = 0; });
      (MOCK.readerLib || []).forEach(function (d) {
        (d.collections || []).forEach(function (c) {
          counts[c] = (counts[c] || 0) + 1;
        });
      });
      return Object.keys(counts).sort().map(function (k) { return { name: k, count: counts[k] }; });
    }
    case "reader_list_collection_docs":
      return (MOCK.readerLib || [])
        .filter((d) => (d.collections || []).includes(args[0]))
        .map((d) => ({
          id: d.id, title: d.title, preview: (d.text || "").slice(0, 160),
          length: (d.text || "").split(/\s+/).length, position: d.position || 0,
          percent: Math.round((100 * (d.position || 0)) / Math.max(1, (d.text || "").split(/\s+/).length)),
          opened: d.opened || 0, starred: !!d.starred,
        }));
    case "reader_delete_collection": {
      var n = 0;
      MOCK.readerCollections = (MOCK.readerCollections || []).filter((c) => c !== args[0]);
      (MOCK.readerLib || []).forEach(function (d) {
        var before = (d.collections || []).length;
        d.collections = (d.collections || []).filter((c) => c !== args[0]);
        if (d.collections.length < before) n++;
      });
      return { ok: true, affected: n };
    }
    case "reader_log_session": {
      var d = (MOCK.readerLib || []).find((x) => x.id === args[0]);
      if (d) {
        d.reading_sessions = d.reading_sessions || [];
        d.reading_sessions.unshift({
          start: Date.now() / 1000 - (args[3] || 0),
          end: Date.now() / 1000,
          start_pos: args[1] || 0,
          end_pos: args[2] || 0,
          duration_sec: args[3] || 0,
        });
        d.reading_sessions = d.reading_sessions.slice(0, 25);
      }
      return { ok: true };
    }
    case "reader_record_stats": {
      // Accumulate into mock reader stats (for file:// preview)
      MOCK.readerStats = MOCK.readerStats || {
        reader_total_seconds: 0, reader_pages_read: 0, reader_docs_completed: 0,
        reader_words_read: 0, reader_sessions: 0, reader_days: {},
      };
      var words = args[0] || 0;
      var dur = args[1] || 0;
      var completed = args[2] || false;
      MOCK.readerStats.reader_total_seconds += dur;
      MOCK.readerStats.reader_pages_read += words / 250;
      MOCK.readerStats.reader_words_read += words;
      MOCK.readerStats.reader_sessions += 1;
      if (completed) MOCK.readerStats.reader_docs_completed += 1;
      var today = new Date().toISOString().slice(0, 10);
      MOCK.readerStats.reader_days[today] = (MOCK.readerStats.reader_days[today] || 0) + dur;
      return { ok: true };
    }
    case "get_reader_stats": {
      var s = MOCK.readerStats || {};
      var totalSec = s.reader_total_seconds || 0;
      var sessions = s.reader_sessions || 0;
      var avgSec = sessions > 0 ? totalSec / sessions : 0;
      return {
        total_reading_seconds: Math.round(totalSec * 10) / 10,
        total_reading_display: totalSec < 60 ? Math.round(totalSec) + "s"
          : totalSec < 3600 ? Math.round(totalSec / 60) + "m"
          : (totalSec / 3600).toFixed(1) + "h",
        pages_read: Math.round((s.reader_pages_read || 0) * 10) / 10,
        docs_completed: s.reader_docs_completed || 0,
        words_read: s.reader_words_read || 0,
        total_sessions: sessions,
        avg_session_sec: Math.round(avgSec * 10) / 10,
        avg_session_display: avgSec < 60 ? Math.round(avgSec) + "s"
          : avgSec < 3600 ? Math.round(avgSec / 60) + "m"
          : (avgSec / 3600).toFixed(1) + "h",
        current_streak: 0,
        best_streak: 0,
      };
    }
    case "reader_reading_history": {
      var all = [];
      (MOCK.readerLib || []).forEach(function (d) {
        (d.reading_sessions || []).forEach(function (s) {
          all.push({
            doc_id: d.id, doc_title: d.title,
            start: s.start, end: s.end,
            start_pos: s.start_pos, end_pos: s.end_pos,
            duration_sec: s.duration_sec,
          });
        });
      });
      all.sort(function (a, b) { return (b.start || 0) - (a.start || 0); });
      return all.slice(0, args[0] || 100);
    }
    case "reader_continue_reading": {
      var best = null;
      (MOCK.readerLib || []).forEach(function (d) {
        var len = (d.text || "").split(/\s+/).length;
        var pos = d.position || 0;
        if (pos > 0 && len > 0 && pos < len) {
          if (!best || (d.opened || 0) > (best.opened || 0)) best = d;
        }
      });
      if (!best) return null;
      var len = (best.text || "").split(/\s+/).length;
      return {
        id: best.id, title: best.title,
        length: len, position: best.position || 0,
        percent: Math.round((100 * (best.position || 0)) / Math.max(1, len)),
        opened: best.opened || 0, starred: !!best.starred,
        bookmarks: best.bookmarks || [],
      };
    }
    case "reader_tts": {
      // Preview: return a ~0.15s silent WAV so the player advances + the word
      // highlight moves (proves the pipeline without a live TTS key).
      const sr = 8000, n = Math.floor(sr * 0.15), bytes = 44 + n * 2;
      const buf = new ArrayBuffer(bytes), dv = new DataView(buf);
      const ws = (o, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); };
      ws(0, "RIFF"); dv.setUint32(4, bytes - 8, true); ws(8, "WAVE"); ws(12, "fmt ");
      dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
      dv.setUint32(24, sr, true); dv.setUint32(28, sr * 2, true); dv.setUint16(32, 2, true);
      dv.setUint16(34, 16, true); ws(36, "data"); dv.setUint32(40, n * 2, true);
      let bin = ""; const u8 = new Uint8Array(buf);
      for (let i = 0; i < u8.length; i++) bin += String.fromCharCode(u8[i]);
      return { ok: true, mime: "audio/wav", audio: btoa(bin) };
    }
    case "get_settings":
      return JSON.parse(JSON.stringify(MOCK.settings));
    case "list_microphones":
      return MOCK.mics.slice();
    case "get_favorites":
      return [
        {
          text: MOCK.transcripts[1].text,
          source: "transcript",
          time: "13:48",
          added: "2026-06-10 13:50",
          fav: true,
        },
        {
          text: MOCK.clipboard[1].text,
          source: "clipboard",
          time: "13:50",
          added: "2026-06-10 13:55",
          fav: true,
        },
        {
          text: MOCK.prompts[0].prompt,
          source: "prompt",
          time: "13:48",
          added: "2026-06-10 14:00",
          fav: true,
        },
      ];
    case "get_insights":
      return {
        active_days: 38,
        avg_per_active_day: 1269,
        busiest_day: "2026-06-07",
        busiest_words: 3100,
        weekday_words: [5200, 7400, 6100, 8000, 6900, 1200, 900],
        weekday_names: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        peak_weekday: "Thu",
        this_week: 10790,
        prev_week: 8200,
        trend_pct: 32,
        yesterday_words: 1430,
        today_delta_pct: 29,
        month_this: 41200,
        month_prev: 35600,
        month_trend_pct: 16,
        avg_wpm: 118,
        best_wpm: 156,
        spoken_minutes: 412.5,
        today_words: 1840,
        tod_hours: [0,0,0,0,0,120,380,620,1100,2400,3200,2800,1800,2400,3200,2800,1800,1500,900,600,300,120,50,0],
        tod_blocks: [9200, 14800, 11200, 2600],
        tod_peak_hour: 14,
        tod_peak_label: "2pm–3pm",
        tod_peak_share: 18,
      };
    case "is_popup":
      return false;
    case "close_history_popup":
      return { ok: true };
    case "get_clip_thumb":
      return "";
    case "check_updates":
      return {
        status: "uptodate",
        message: "You're on the latest version (v0.95). (Preview)",
      };
    case "reset_window_size":
      return true;
    case "set_pinned":
      return true;
    case "set_deck_palette":
      return true;
    case "minimize_window":
      return true;
    case "set_setting": {
      dbgSet(args[0], args[1]);
      return { ok: true, value: args[1], applied: false };
    }
    case "toggle_favorite":
      return { fav: true };
    case "clear_transcripts":
    case "clear_clipboard":
    case "clear_prompts":
      return { ok: true };
    case "delete_transcript":
    case "delete_clip":
      return { ok: true };
    case "save_presets":
      return { ok: true };
    case "test_key":
      return /.{8,}/.test(args[1] || "")
        ? { ok: true, message: "Connected — key is valid" }
        : { ok: false, message: "Enter a key to test" };
    case "test_mic":
      return { ok: true, message: "Microphone OK — level detected" };
    case "capture_binding":
      return { spec: "mouse:x2", pretty: "Mouse ▸ Forward" };
    case "validate_binding":
      return { ok: true, message: "" };
    case "pretty_binding":
      return { pretty: args[0] };
    case "run_deck_job":
      return {
        ok: false,
        message: "Deck actions run in the live app. This is a preview.",
      };
    case "capture_selection":
      // Preview: simulate grabbing a highlighted selection so the Deck flow is
      // demonstrable without the controller (the live app reads your real one).
      return {
        ok: true,
        selection:
          "Highlighted text from another app (preview) — in the live app this is whatever you have selected.",
      };
    case "toggle_fullscreen":
      return true;
    case "apply_shortcuts":
      return { ok: true, done: ["desktop", "start_menu"], failed: [] };
    case "get_app_status":
      return { live: false, state: "idle", text: "Ready", recording: false };
    case "start_recording":
      return {
        ok: false,
        live: false,
        message: "Preview — recording runs in the full app",
      };
    case "deck_paste":
    case "deck_paste_image":
      return { ok: false, live: false };
    case "controller_alive":
      return false;
    case "open_url":
      window.open(args[0], "_blank");
      return true;
    case "open_data_folder":
      return true;
    case "switch_to_lite":
      return true;
    case "finish_onboarding":
      return { ok: true };
    case "start_recording_hint":
      return true;
    // ---- Meeting mock fallback ----
    case "meeting_list": {
      var ml = (MOCK.meetings || []).map(function (m) {
        return {
          id: m.id, title: m.title, created: m.created,
          duration_sec: m.duration_sec, duration_display: m.duration_display,
          segment_count: m.segment_count, speaker_count: m.speaker_count,
          speakers: m.speakers, has_summary: !!m.summary,
          action_item_count: m.action_item_count || 0,
          key_decision_count: m.key_decision_count || 0,
          open_question_count: m.open_question_count || 0,
          processing_mode: m.processing_mode || "lightweight",
          starred: !!m.starred, preview: m.preview || "",
          tags: m.tags || [],
        };
      });
      return ml;
    }
    case "meeting_open": {
      var mo = (MOCK.meetings || []).find(function (x) { return x.id === args[0]; });
      return mo ? JSON.parse(JSON.stringify(mo)) : null;
    }
    case "meeting_star": {
      var ms = (MOCK.meetings || []).find(function (x) { return x.id === args[0]; });
      if (ms) ms.starred = args[1] !== false;
      return { ok: true };
    }
    case "meeting_delete": {
      MOCK.meetings = (MOCK.meetings || []).filter(function (x) { return x.id !== args[0]; });
      return { ok: true };
    }
    case "meeting_rename_speaker": {
      var mr = (MOCK.meetings || []).find(function (x) { return x.id === args[0]; });
      if (mr) {
        var sp = (mr.speakers || []).find(function (s) { return s.label === args[1]; });
        if (sp) { sp.name = (args[2] || "").trim() || null; return { ok: true }; }
      }
      return { ok: false, message: "Speaker not found" };
    }
    case "meeting_update_title": {
      var mu = (MOCK.meetings || []).find(function (x) { return x.id === args[0]; });
      if (mu) { mu.title = (args[1] || "").trim() || mu.title; return { ok: true }; }
      return { ok: false };
    }
    case "meeting_summarize": {
      var ms2 = (MOCK.meetings || []).find(function (x) { return x.id === args[0]; });
      if (!ms2) return { ok: false, message: "Meeting not found" };
      if (!ms2.segments || !ms2.segments.length) return { ok: false, message: "No transcript to summarise" };
      // Simulate a summarised result (the real app uses the LLM)
      ms2.summary = "This is a mock summary generated for preview. In the live app, your configured AI provider analyses the transcript and produces a concise summary of the meeting discussion, covering the main topics and conclusions.";
      ms2.has_summary = true;
      return { ok: true, summary: ms2.summary };
    }
    case "meeting_extract_actions": {
      var ma = (MOCK.meetings || []).find(function (x) { return x.id === args[0]; });
      if (!ma) return { ok: false, message: "Meeting not found" };
      if (!ma.segments || !ma.segments.length) return { ok: false, items: [] };
      ma.action_items = ma.action_items && ma.action_items.length ? ma.action_items : ["Sample action item: Follow up on the key discussion points (mock preview)", "Sample action item: Review the decisions before the next meeting (mock preview)"];
      ma.action_item_count = ma.action_items.length;
      return { ok: true, items: ma.action_items };
    }
    case "meeting_extract_decisions": {
      var mkd = (MOCK.meetings || []).find(function (x) { return x.id === args[0]; });
      if (!mkd) return { ok: false, message: "Meeting not found" };
      if (!mkd.segments || !mkd.segments.length) return { ok: false, items: [] };
      mkd.key_decisions = mkd.key_decisions && mkd.key_decisions.length ? mkd.key_decisions : ["Sample key decision: Proceed with the recommended approach (mock preview)"];
      mkd.key_decision_count = mkd.key_decisions.length;
      return { ok: true, items: mkd.key_decisions };
    }
    case "meeting_extract_questions": {
      var mq = (MOCK.meetings || []).find(function (x) { return x.id === args[0]; });
      if (!mq) return { ok: false, message: "Meeting not found" };
      if (!mq.segments || !mq.segments.length) return { ok: false, items: [] };
      mq.open_questions = mq.open_questions && mq.open_questions.length ? mq.open_questions : ["Sample open question: What is the timeline for the proposed changes? (mock preview)"];
      mq.open_question_count = mq.open_questions.length;
      return { ok: true, items: mq.open_questions };
    }
    case "meeting_extract_deep": {
      var mdp = (MOCK.meetings || []).find(function (x) { return x.id === args[0]; });
      if (!mdp) return { ok: false, message: "Meeting not found" };
      if (!mdp.segments || !mdp.segments.length) return { ok: false, message: "No transcript to process" };
      mdp.summary = mdp.summary || "This is a deep-process mock summary. In the live app, a single LLM call produces the summary, action items, key decisions, and open questions all at once.";
      mdp.action_items = mdp.action_items && mdp.action_items.length ? mdp.action_items : ["Deep action item: Update the project timeline (mock)", "Deep action item: Schedule follow-up review (mock)"];
      mdp.key_decisions = mdp.key_decisions && mdp.key_decisions.length ? mdp.key_decisions : ["Deep decision: Adopt the proposed architecture (mock)"];
      mdp.open_questions = mdp.open_questions && mdp.open_questions.length ? mdp.open_questions : ["Deep question: Should we allocate more resources to this initiative? (mock)"];
      mdp.has_summary = true;
      mdp.action_item_count = mdp.action_items.length;
      mdp.key_decision_count = mdp.key_decisions.length;
      mdp.open_question_count = mdp.open_questions.length;
      mdp.processing_mode = "deep";
      return { ok: true, summary: mdp.summary, actions: mdp.action_items, decisions: mdp.key_decisions, questions: mdp.open_questions };
    }
    case "meeting_export": {
      var mex = (MOCK.meetings || []).find(function (x) { return x.id === args[0]; });
      if (!mex) return { ok: false, message: "Meeting not found" };
      var fmt = args[1] || "txt";
      var content = "", mime = "text/plain";
      if (fmt === "txt") {
        content = "# " + (mex.title || "Meeting") + "\nDuration: " + (mex.duration_display || "0:00") + "\n\n";
        (mex.segments || []).forEach(function (seg) {
          var min = Math.floor((seg.start_sec || 0) / 60);
          var sec = String(Math.floor((seg.start_sec || 0) % 60)).padStart(2, "0");
          content += "[" + min + ":" + sec + "] " + (seg.speaker || "Speaker") + ": " + (seg.text || "") + "\n";
        });
        if (mex.summary) { content += "\n--- Summary ---\n" + mex.summary + "\n"; }
        if (mex.action_items && mex.action_items.length) { content += "\n--- Action Items ---\n" + mex.action_items.map(function (a) { return "- " + (typeof a === "string" ? a : a.text || ""); }).join("\n") + "\n"; }
        if (mex.key_decisions && mex.key_decisions.length) { content += "\n--- Key Decisions ---\n" + mex.key_decisions.map(function (d) { return "- " + d; }).join("\n") + "\n"; }
        if (mex.open_questions && mex.open_questions.length) { content += "\n--- Open Questions ---\n" + mex.open_questions.map(function (q) { return "- " + q; }).join("\n") + "\n"; }
      } else if (fmt === "markdown" || fmt === "md") {
        content = "# " + (mex.title || "Meeting") + "\n\n**Duration:** " + (mex.duration_display || "0:00") + "\n\n## Transcript\n\n";
        (mex.segments || []).forEach(function (seg) {
          var min = Math.floor((seg.start_sec || 0) / 60);
          var sec = String(Math.floor((seg.start_sec || 0) % 60)).padStart(2, "0");
          content += "**[" + min + ":" + sec + "]** **" + (seg.speaker || "Speaker") + ":** " + (seg.text || "") + "\n\n";
        });
        if (mex.summary) { content += "## Summary\n\n" + mex.summary + "\n\n"; }
        if (mex.action_items && mex.action_items.length) { content += "## Action Items\n\n" + mex.action_items.map(function (a) { return "- " + (typeof a === "string" ? a : a.text || ""); }).join("\n") + "\n\n"; }
        if (mex.key_decisions && mex.key_decisions.length) { content += "## Key Decisions\n\n" + mex.key_decisions.map(function (d) { return "- " + d; }).join("\n") + "\n\n"; }
        if (mex.open_questions && mex.open_questions.length) { content += "## Open Questions\n\n" + mex.open_questions.map(function (q) { return "- " + q; }).join("\n") + "\n\n"; }
        mime = "text/markdown";
      } else if (fmt === "json") {
        content = JSON.stringify(mex, null, 2);
        mime = "application/json";
      } else if (fmt === "html") {
        content = "<!DOCTYPE html>\n<html><head><meta charset=\"UTF-8\"><title>" + esc(mex.title || "Meeting") + "</title><style>body{font-family:system-ui,sans-serif;max-width:800px;margin:2rem auto;padding:1rem;color:#222;background:#fff}h1{color:#333}h2{color:#555;margin-top:1.5em}.seg{margin:0.5em 0;padding:0.5em;border-left:3px solid #ccc}.sp{font-weight:600}.ts{color:#888;font-size:0.85em}</style></head><body>";
        content += "<h1>" + esc(mex.title || "Meeting") + "</h1><p><strong>Duration:</strong> " + esc(mex.duration_display || "0:00") + "</p>";
        if (mex.summary) { content += "<h2>Summary</h2><p>" + esc(mex.summary).replace(/\n/g, "<br>") + "</p>"; }
        if (mex.action_items && mex.action_items.length) { content += "<h2>Action Items</h2><ul>" + mex.action_items.map(function (a) { return "<li>" + esc(typeof a === "string" ? a : a.text || "") + "</li>"; }).join("") + "</ul>"; }
        if (mex.key_decisions && mex.key_decisions.length) { content += "<h2>Key Decisions</h2><ul>" + mex.key_decisions.map(function (d) { return "<li>" + esc(d) + "</li>"; }).join("") + "</ul>"; }
        if (mex.open_questions && mex.open_questions.length) { content += "<h2>Open Questions</h2><ul>" + mex.open_questions.map(function (q) { return "<li>" + esc(q) + "</li>"; }).join("") + "</ul>"; }
        content += "<h2>Transcript</h2>";
        (mex.segments || []).forEach(function (seg) {
          var min = Math.floor((seg.start_sec || 0) / 60);
          var sec = String(Math.floor((seg.start_sec || 0) % 60)).padStart(2, "0");
          var color = ""; var spk = (mex.speakers || []).find(function (s) { return s.label === seg.speaker; });
          if (spk && spk.color) color = " style=\"border-left-color:" + esc(spk.color) + "\"";
          content += "<div class=\"seg\"" + color + "><span class=\"ts\">[" + min + ":" + sec + "]</span> <span class=\"sp\">" + esc(seg.speaker || "Speaker") + ":</span> " + esc(seg.text || "") + "</div>";
        });
        content += "</body></html>";
        mime = "text/html";
      } else if (fmt === "clipboard") {
        content = "# " + (mex.title || "Meeting") + "\nDuration: " + (mex.duration_display || "0:00") + "\n\n";
        (mex.segments || []).forEach(function (seg) {
          var min = Math.floor((seg.start_sec || 0) / 60);
          var sec = String(Math.floor((seg.start_sec || 0) % 60)).padStart(2, "0");
          content += "[" + min + ":" + sec + "] " + (seg.speaker || "Speaker") + ": " + (seg.text || "") + "\n";
        });
        if (mex.summary) { content += "\n--- Summary ---\n" + mex.summary + "\n"; }
        if (mex.action_items && mex.action_items.length) { content += "\n--- Action Items ---\n" + mex.action_items.map(function (a) { return "- " + (typeof a === "string" ? a : a.text || ""); }).join("\n") + "\n"; }
      }
      return { ok: true, content: content, mime: mime };
    }
    case "meeting_set_processing_mode": {
      var mode = args[0];
      if (mode === "lightweight" || mode === "deep") {
        MOCK.settings.meeting_processing_mode = mode;
        return { ok: true, mode: mode };
      }
      return { ok: false, message: "Invalid mode — choose 'lightweight' or 'deep'" };
    }
    case "meeting_start_recording":
      return { ok: false, message: "Preview — recording runs in the full app" };
    case "meeting_stop_recording":
      return { ok: false, message: "Preview — stop runs in the full app" };
    case "meeting_pause_recording":
      return { ok: true };
    case "meeting_resume_recording":
      return { ok: true };
    case "meeting_import_audio":
      return { ok: false, message: "Preview — import runs in the full app" };
    case "get_meeting_stats": {
      var gms = MOCK.meetingStats || { meeting_count: 0, total_meeting_minutes: 0, total_meeting_segments: 0 };
      if (MOCK.meetings) { gms.meeting_count = MOCK.meetings.length; }
      return gms;
    }
    default:
      return null;
  }
}
/* Convert an ArrayBuffer to base64 without passing the whole file as function
   arguments. WebView2/Chromium throws RangeError once apply()/spread receives a
   moderately large Uint8Array, which made ordinary DOCX/PDF imports fail before
   they ever reached Python. Keep each call well below the engine argument cap. */
const MAX_READER_IMPORT_BYTES = 32 * 1024 * 1024;
const MAX_READER_TEXT_CHARS = 600000;

function arrayBufferToBase64(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode.apply(
      null,
      bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length)),
    );
  }
  return btoa(binary);
}

/* keep mock settings coherent across set/get during a browser preview */
function dbgSet(k, v) {
  try {
    MOCK.settings[k] = v;
  } catch (e) {}
}

/* ============================================================================
   TOAST / MODAL
   ========================================================================== */
function toast(msg, kind = "info", ms = 2600) {
  const wrap = $("#toast-wrap");
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.innerHTML =
    svg(kind === "ok" ? "check" : kind === "err" ? "x" : "info") +
    "<span></span>";
  $("span", el).textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .3s";
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 300);
  }, ms);
}

async function copyTextReliable(text) {
  try {
    if (!navigator.clipboard || !navigator.clipboard.writeText)
      throw new Error("Clipboard API unavailable");
    await navigator.clipboard.writeText(String(text || ""));
    return true;
  } catch (e) {
    try {
      const r = await call("copy_text", String(text || ""));
      return !!(r && r.ok);
    } catch (_e) {
      return false;
    }
  }
}

function activateDialog(overlay, onCancel) {
  const dialog = overlay.querySelector(".modal,.uc") || overlay.firstElementChild;
  const previous = document.activeElement;
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("tabindex", "-1");
  const heading = dialog.querySelector("h1,h2,h3");
  if (heading) {
    heading.id = heading.id || `dialog-title-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    dialog.setAttribute("aria-labelledby", heading.id);
  }
  const focusable = () => [...dialog.querySelectorAll(
    'button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'
  )].filter((el) => !el.hidden);
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); onCancel(); return; }
    if (e.key !== "Tab") return;
    const items = focusable();
    if (!items.length) { e.preventDefault(); dialog.focus(); return; }
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  overlay.addEventListener("keydown", onKey);
  (focusable()[0] || dialog).focus();
  return () => {
    overlay.removeEventListener("keydown", onKey);
    if (previous && document.contains(previous)) previous.focus();
  };
}

/* update-available dialog — deliberately minimal (spec: no channels/progress
   theatre); the actual download+swap runs from the tray/controller flow */
function showUpdateModal(info) {
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `<div class="modal">
    <div class="m-icon">${svg("download")}</div>
    <h3>Version ${esc(info.version)} is available</h3>
    <p>${esc(info.notes || "A new version of Mumble is ready.")}<br><br>
    Mumble will offer to install it from the tray icon — your settings and history are kept.</p>
    <div class="m-actions"><button class="btn btn-gold" data-ok>Got it</button></div></div>`;
  document.body.appendChild(ov);
  let deactivate;
  const close = () => { if (deactivate) deactivate(); ov.remove(); };
  $("[data-ok]", ov).onclick = close;
  ov.onclick = (e) => {
    if (e.target === ov) close();
  };
  deactivate = activateDialog(ov, close);
}

function confirmModal({
  icon = "trash",
  title,
  body,
  confirmText = "Confirm",
  danger = true,
}) {
  return new Promise((resolve) => {
    const ov = document.createElement("div");
    ov.className = "overlay";
    ov.innerHTML = `<div class="modal">
      <div class="m-icon">${svg(icon)}</div>
      <h3>${esc(title)}</h3><p>${esc(body)}</p>
      <div class="m-actions">
        <button class="btn btn-ghost" data-no>Cancel</button>
        <button class="btn ${danger ? "btn-danger" : "btn-gold"}" data-yes>${esc(confirmText)}</button>
      </div></div>`;
    document.body.appendChild(ov);
    let deactivate;
    const close = (v) => {
      if (deactivate) deactivate();
      ov.remove();
      resolve(v);
    };
    $("[data-no]", ov).onclick = () => close(false);
    $("[data-yes]", ov).onclick = () => close(true);
    ov.onclick = (e) => {
      if (e.target === ov) close(false);
    };
    deactivate = activateDialog(ov, () => close(false));
  });
}

/* Convert (History → Transcripts/Clipboard rows): pop up the Smart Modes and run
   the chosen one over THIS item, via the same Deck-job pipeline that powers the
   Deck (run_deck_job with a mode + the one item). Owner: "a Convert button that
   pops up all the modes so you can convert a certain mode." */
function openConvertMenu(text) {
  const modes = [
    ["prompt", "Prompt"],
    ["email", "Email"],
    ["foreign", "Foreign"],
  ];
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `<div class="modal convert-modal">
    <div class="m-icon">${svg("rotate")}</div>
    <h3>Convert to a Smart Mode</h3>
    <p>Run a Smart Mode over this item — the shaped result pastes at your cursor.</p>
    <div class="convert-modes">${modes
      .map(
        ([m, l]) =>
          `<button class="mode-chip convert-chip" data-cmode="${m}" title="${esc(MODE_DESCRIPTIONS[m] || "")}" style="color:${MODE_COLORS[m]};border-color:color-mix(in srgb,${MODE_COLORS[m]} 45%,transparent)">${l}</button>`,
      )
      .join("")}</div>
    <div class="m-actions"><button class="btn btn-ghost" data-no>Cancel</button></div>
  </div>`;
  document.body.appendChild(ov);
  let deactivate;
  const close = () => { if (deactivate) deactivate(); ov.remove(); };
  $("[data-no]", ov).onclick = close;
  ov.onclick = (e) => {
    if (e.target === ov) close();
  };
  $$("[data-cmode]", ov).forEach(
    (b) =>
      (b.onclick = () => {
        runConvert(text, b.dataset.cmode);
        close();
      }),
  );
  deactivate = activateDialog(ov, close);
}
async function runConvert(text, mode) {
  const label = MODE_LABELS[mode] || mode;
  // preset_slot=null + a mode → the controller runs the mode directive over the
  // item and pastes (its _run_hub_job uses the default instruction + the mode form).
  const r = await call("run_deck_job", null, mode, [
    { kind: "TEXT", time: "", text },
  ]);
  if (r && r.ok && r.live)
    toast(
      `Converting to ${label} — the result will paste at your cursor`,
      "ok",
      3000,
    );
  else if (r && r.ok) toast(`Converted to ${label}`, "ok");
  else
    toast(
      (r && r.message) ||
        "Convert needs the full app running (and your AI key)",
      "info",
      3400,
    );
}

/* Ctrl+H on a hovered History row (owner 2026-06-20): hovering acts as implicit
   selection. If a Smart Mode or preset is armed in the History tools, run it on
   the hovered item; otherwise open the Smart Mode menu so the user can pick one.
   Restores the "hover, Ctrl+H, run a Smart Mode/preset on this item" affordance. */
function runHoverJob() {
  const h = HX.hover;
  if (!h || !h.text) {
    toast("Hover over a Deck item first, then press Ctrl+H", "info", 2600);
    return;
  }
  if (!HX.runPreset && !HX.runMode) {
    // Nothing armed → expose the Smart Modes for this item (same menu as Convert).
    openConvertMenu(h.text);
    return;
  }
  if (_deckJobBusy) return;
  _deckJobBusy = true;
  const items = [{ kind: "TEXT", time: "", text: h.text }];
  Promise.resolve(call("run_deck_job", HX.runPreset, HX.runMode, items))
    .then((r) => {
      if (r && r.ok && r.live)
        toast("Working — the result will paste at your cursor", "ok", 3000);
      else if (r && r.ok) toast("Done — result pasted", "ok");
      else
        toast(
          (r && r.message) ||
            "Running a preset/mode needs the full app running (and your AI key)",
          "info",
          3400,
        );
    })
    .finally(() => setTimeout(() => (_deckJobBusy = false), 1200));
}

/* ============================================================================
   NAVIGATION
   ========================================================================== */
let CURRENT = "home";
function navTo(view) {
  // Leaving the Reader → pause its speech so it doesn't read on in the background.
  if (CURRENT === "reader" && view !== "reader") readerStopForNav();
  CURRENT = view;
  $$("[data-view]").forEach((v) => (v.hidden = v.dataset.view !== view));
  $$(".nav-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.nav === view),
  );
  const sv = document.querySelector(`[data-view="${view}"]`);
  if (sv) sv.scrollTop = 0;
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  // re-read live status on every return Home — the key-configured chip must
  // never show stale state after the user just saved a key in Settings
  if (view === "home") bootHome();
  if (view === "history") renderHistory();
  if (view === "stats") renderStats();
  if (view === "reader") {
    initReader();
    // On re-entry (already inited), refresh the library unless mid-read.
    if (READER.inited && READER.hasKey && $("#reader-player")?.hidden !== false)
      readerRefreshLibrary();
    syncShimmer(); // phase-lock the Reader cards to the global shimmer cycle
  }
  if (view === "meetings") renderMeetings();
  if (view === "settings") hydrateSettings();
  syncDeckWindow(); // palette (non-activating) behaviour is Deck-page-only
}

// Keep the live Deck WINDOW behaviour in sync with the UI. The non-activating
// "palette" mode (clicking the Deck never steals focus, so a highlighted selection
// survives a Capture) must be ON only while the Deck page is showing AND it's
// pinned — otherwise it would block typing in onboarding / Settings / the Deck's
// own search box. Pin-on-top itself is handled by set_pinned; this layers the
// focus behaviour on top, re-evaluated on every page change and pin toggle.
function syncDeckWindow() {
  if (!HAS_PY()) return;
  try {
    call("set_deck_palette", CURRENT === "history" && !!HX.pinned);
  } catch (e) {}
}

/* ============================================================================
   HOME (dynamic bits)
   ========================================================================== */
/* visual experience tiers: lite (flat, low-GPU) · standard (full glass) ·
   enhanced (real glass everywhere, ambient gold dust, richer motion — enhanced.css
   gates everything behind body.enhanced). RESOURCE SAVER MODE (owner v8) sits on
   top: when on it FORCES the lite tier (so enhanced.css is inactive) and adds
   body.saver, a hard global override (app.css) that strips every animation,
   transition, shadow, blur and glass-transparency — the "works everywhere" config. */
let CHOSEN_FX = "enhanced"; // the user's selected tier (Settings → Visual effects)
let SAVER = false; // Resource Saver Mode
function applyVisual() {
  const fx = SAVER ? "lite" : CHOSEN_FX; // saver always wins → lite
  document.body.classList.toggle("saver", SAVER);
  document.body.classList.toggle("lite", fx === "lite");
  document.body.classList.toggle("enhanced", fx === "enhanced");
}
function applyEffects(fx) {
  if (fx) CHOSEN_FX = fx;
  applyVisual();
}
function applySaver(on) {
  SAVER = !!on;
  applyVisual();
}

function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

async function bootHome() {
  const [o, hk, liveStatus] = await Promise.all([
    call("get_overview"), call("get_hotkeys"), call("get_app_status")
  ]);
  // Hotkey labels are plain text, and every visible binding updates after a rebind.
  setText("#hk-record", hk.hotkey);
  setText("#hk-record-btn", hk.hotkey);
  setText("#hk-paste-latest", hk.quick_paste_hotkey);
  setText("#hk-history", hk.history_hotkey);
  setText("#hk-history-btn", hk.history_hotkey);
  setText("#hk-search", hk.search_hotkey);
  // live version everywhere it appears
  setText("#about-version", o.version);
  setText("#update-sub", "You're on v" + o.version);
  const updateButton = $("#set-check-updates");
  if (updateButton) {
    updateButton.disabled = !o.update_enabled;
    updateButton.title = o.update_enabled ? "Check the signed release feed" :
      "Signed update checks are not configured yet";
  }
  if (!o.update_enabled) setText("#update-sub", "Signed update channel not configured");
  const cloudStt = o.transcription_mode === "cloud";
  const cloudProvider = o.cloud_transcription_provider || "your provider";
  const dictationLimit = $("#home-dictation-limit");
  if (dictationLimit) dictationLimit.textContent =
    `Normal dictation records for up to ${o.dictation_max_display || "10:00"} at a time.`;
  const hero = $("#home-hero-copy");
  if (hero) hero.innerHTML = cloudStt
    ? `Dictate into any app, shape rough thoughts into useful output, capture meetings, and listen to documents. Voice clips currently go to <span class="t-gold fw6">${esc(cloudProvider)}</span> for transcription.`
    : "Dictate into any app, shape rough thoughts into useful output, capture meetings, and listen to documents — with voice transcription kept on this device.";
  const audioBadge = $("#home-audio-badge");
  if (audioBadge) audioBadge.lastChild.textContent = cloudStt
    ? "Audio sent for cloud transcription" : "Audio transcribed locally";
  const proCopy = $("#home-pro-copy");
  if (proCopy) proCopy.innerHTML = cloudStt
    ? `Use Smart Modes and presets in the <span class="t-gold fw6">Deck</span> to polish, summarise, and repurpose your words. AI shaping sends transcript text only; cloud transcription separately sends audio to ${esc(cloudProvider)}.`
    : `Use Smart Modes and presets in the <span class="t-gold fw6">Deck</span> to polish, summarise, and repurpose your words. AI shaping sends transcript text only; your audio stays on this device.`;
  setText("#home-audio-detail", cloudStt
    ? `Cloud transcription sends audio to ${cloudProvider}; AI shaping receives transcript text.`
    : "Local transcription keeps audio on this device; AI shaping receives transcript text only.");
  // pro key status
  const pk = $("#pro-status"),
    pb = $("#pro-btn");
  if (!o.pro_mode && o.key_configured) {
    pk.textContent = "Pro Mode off — key saved (" + o.provider + ")";
    pk.className = "flex-1 fs12";
    pk.style.color = "var(--amber)";
    pb.textContent = "Turn on";
  } else if (!o.pro_mode) {
    pk.textContent = "Pro Mode off — using the offline engine";
    pk.className = "flex-1 fs12 t-mute";
    pb.textContent = "Turn on";
  } else if (o.key_configured) {
    pk.textContent = "Connected — " + o.provider;
    pk.className = "flex-1 fs12 t-green";
    pb.textContent = "Manage";
  } else {
    pk.textContent = "API key not configured";
    pk.className = "flex-1 fs12 t-mute";
    pb.textContent = "Add Key";
  }
  // "Switch to Mumble Lite" only when Lite actually ships with this install
  const liteBtn = $("#set-switch-lite");
  if (liteBtn) liteBtn.hidden = o.lite_available === false;
  pb.onclick = () => {
    navTo("settings");
    setTimeout(
      () =>
        $("#card-ai")?.scrollIntoView({ behavior: "smooth", block: "center" }),
      60,
    );
  };
  // Never overwrite an authoritative active state with a transient Ready label.
  if (liveStatus && liveStatus.live) reflectStatus(liveStatus);
  else setText("#status-text", "Ready");
}
function setText(sel, t) {
  const el = $(sel);
  if (el) el.textContent = t;
}

/* record button — in the live app this IS the activation hotkey (the
   controller runs the exact on_hotkey pipeline); in the browser preview it
   demos the states. */
let recPreview = false;
async function toggleRecordPreview() {
  if (HAS_PY()) {
    const r = await call("start_recording");
    if (r && r.live) {
      reflectStatus({
        live: true,
        recording: r.recording,
        state: r.recording ? "listening" : "idle",
        text: r.recording ? "Recording…" : "Ready",
      });
      pollStatus();
      return;
    }
    toast(
      (r && r.message) || "Start Mumble from the tray to record",
      "info",
      3000,
    );
    return;
  }
  recPreview = !recPreview;
  reflectStatus({
    live: false,
    recording: recPreview,
    state: recPreview ? "listening" : "idle",
    text: recPreview ? "Recording…" : "Ready",
  });
  if (recPreview)
    toast("Preview — in the app this records for real", "info", 2200);
}

/* one place that maps live controller state onto the chip/button/waveform/island */
let LIVE_REC = false; // last-known recording state — paces the status poll below
function reflectStatus(st) {
  const wf = $("#hero-waveform"),
    lab = $("#record-label"),
    chip = $("#status-chip");
  const rec = !!st.recording;
  LIVE_REC = rec;
  wf.classList.toggle("recording", rec);
  lab.textContent = rec ? "Stop Recording" : "Start Recording";
  chip.classList.toggle("is-recording", rec || st.state === "transcribing");
  chip.classList.toggle("is-error", st.state === "error");
  setText("#status-text", st.text || (rec ? "Recording…" : "Ready"));
}

/* light live polling: keeps the chip honest while you dictate via the hotkey.
   Runs only in the live app, only while the window is visible. */
let statusTimer = null;
async function pollStatus() {
  if (!HAS_PY()) return;
  clearTimeout(statusTimer);
  // Hidden/minimized window: STOP polling entirely (no reschedule). The chip
  // isn't visible, so pinging the controller is pure waste; the visibilitychange
  // handler restarts pollStatus the moment the window returns.
  if (document.hidden) return;
  try {
    const st = await call("get_app_status");
    if (st && st.live) reflectStatus(st);
  } catch (e) {}
  // Poll briskly while recording (the chip should track each state change), but
  // ease off when idle — the island, not this poll, is the primary feedback, so
  // there's no need to chatter at the controller twice a second at rest.
  // Idle cadence eased back (owner perf, v0.9): the island is the primary live
  // feedback, so polling the controller socket every 3.5s at rest was needless
  // connection churn. Brisk only while actually recording.
  statusTimer = setTimeout(pollStatus, LIVE_REC ? 1500 : SAVER ? 12000 : 6000);
}

/* ============================================================================
   HISTORY (Transcripts / Clipboard / Prompts)
   ========================================================================== */
let HX = {
  transcripts: [],
  clipboard: [],
  prompts: [],
  favorites: [],
  // Content-type filter dropdown: empty = "All types" (show everything).
  activeFilters: new Set(),
  q: "",
  sort: "new",
  pre: false,
  // The absorbed Deck (owner v4): ordered multi-select + a chosen Smart Mode /
  // Preset to run on the selection, plus which entries are expanded + the pin.
  selected: [],
  expanded: new Set(),
  runMode: null,
  runPreset: null,
  presets: [],
  pinned: false,
  // The row the cursor is over (owner 2026-06-20): hovering acts as implicit
  // selection so Ctrl+H runs the armed Smart Mode / preset on it.
  hover: null,
  // Selections captured from another app when the Deck was opened with text
  // highlighted (owner 2026-06-23) — rendered as temporary items at the top of
  // the Deck, dismissible, never persisted. See addDeckSelection / drawHistory.
  injected: [],
};
// selection key for an entry = its text (stable across re-renders, unique enough)
function selKeyOf(text) {
  return String(text || "");
}

/* ---- Filter chips (deck-filtering-overhaul, owner v7) ---- */
/* ---- Content-type filter dropdown (replaced filterchips, owner v1.0) ---- */

function setContentFilter(name) {
  if (!name) {
    HX.activeFilters.clear();
  } else {
    HX.activeFilters.clear();
    HX.activeFilters.add(name);
  }
  drawHistory();
}

/* Synchronised passive shimmer (owner v6). A CSS animation's phase is anchored to
   each element's CREATION time, so persistent cards and History rows (rebuilt on
   every refresh) drift apart and "shimmer at different times". We stamp every
   shimmer element with a shared --shim-delay derived from ONE clock (SHIM_EPOCH),
   so they all sit at the same point in the 6.5s cycle no matter when they were
   built — one deliberate, global wave. Cheap: only un-stamped (new) elements are
   touched; hover intensifies via CSS and drops straight back onto this cycle. */
const SHIM_PERIOD = 6500;
const SHIM_EPOCH =
  typeof performance !== "undefined" && performance.now ? performance.now() : 0;
function syncShimmer(scope) {
  const now =
    typeof performance !== "undefined" && performance.now
      ? performance.now()
      : 0;
  const delay = "-" + ((now - SHIM_EPOCH) % SHIM_PERIOD).toFixed(0) + "ms";
  (scope || document).querySelectorAll(".card, .tile, .row").forEach((el) => {
    if (el.dataset.shim) return; // already phase-locked → don't re-stamp (no jump)
    el.dataset.shim = "1";
    el.style.setProperty("--shim-delay", delay);
  });
}

function wordCountOf(x) {
  return (
    x._words || x.words || (x.text || x.prompt || "").split(/\s+/).filter(Boolean).length
  );
}
function sortItems(arr) {
  // Sort unified items (owner v7): items have _stamp for date-based sorting.
  // "old" reverses the incoming order (which is already newest-first from sources).
  if (HX.sort === "old") return arr.slice().reverse();
  // "words" (grouped by day) and "words_all" (flat, all-time) both rank by word
  // count; renderGrouped() decides whether to keep the date sections (owner v6).
  if (HX.sort === "words" || HX.sort === "words_all")
    return arr.slice().sort((a, b) => wordCountOf(b) - wordCountOf(a));
  return arr;
}

async function renderHistory() {
  // ALWAYS re-read from disk on entry/refresh — the stale-page bug was users
  // dictating, opening History, and seeing yesterday. (Filter keystrokes go
  // through drawHistory directly, so typing never refetches.)
  let presets;
  [HX.transcripts, HX.clipboard, HX.prompts, HX.favorites, presets] =
    await Promise.all([
      call("get_transcripts", 200),
      call("get_clipboard", 200),
      call("get_prompts"),
      call("get_favorites"),
      call("get_presets"),
    ]);
  HX.presets = presets || [];
  HX._loaded = true;
  drawHistTools(); // the absorbed-Deck Smart Mode + Presets sections
  drawHistory();
}

/* The most recent transcript by timestamp (the canonical "latest"), fetched fresh
   so it's correct regardless of the current sort/filter. */
async function latestTranscript() {
  const r = await call("get_transcripts", 50);
  const arr = Array.isArray(r) ? r : [];
  if (!arr.length) return null;
  let best = arr[0];
  for (const it of arr) {
    const a = it.stamp || it.time || "",
      b = best.stamp || best.time || "";
    if (a > b) best = it;
  }
  return best;
}
async function pasteLatest() {
  const it = await latestTranscript();
  if (!it) {
    toast("No transcripts yet", "err", 1500);
    return;
  }
  const r = await call("deck_paste", it.text || "");
  toast(
    r && r.ok
      ? "Pasted latest transcript"
      : "Couldn’t paste — click a text field first",
    r && r.ok ? "ok" : "err",
    1600,
  );
}
async function copyLatest() {
  const it = await latestTranscript();
  if (!it) {
    toast("No transcripts yet", "err", 1500);
    return;
  }
  const ok = await copyTextReliable(it.text || "");
  toast(ok ? "Copied latest transcript" : "Couldn't copy to the clipboard",
        ok ? "ok" : "err", 1500);
}

/* push channel: the controller announces fresh data (new transcript, new
   clipboard capture) the moment it lands — whatever page is open re-renders.
   No focus steal, no manual leave-and-return. */
window.pyRefresh = function (what) {
  if (what === "meeting_limit" || what === "meeting_capture_error") {
    const wasRecording = MEET.recording;
    clearInterval(MEET.recTimer);
    MEET.recording = false;
    MEET.paused = false;
    MEET.pausedElapsed = 0;
    MEET.autoStopping = false;
    $("#meeting-recording")?.setAttribute("hidden", "");
    setText("#meeting-record-label", "Record meeting");
    setText("#meeting-pause-label", "Pause");
    if (wasRecording && what === "meeting_limit") {
      toast("Four-hour limit reached · audio saved and transcription started", "info", 4500);
    } else if (wasRecording) {
      toast("Recording stopped early · captured audio saved and transcription started", "err", 5000);
    }
    if (CURRENT === "meetings") renderMeetings();
    return;
  }
  // The History hub live-updates the instant new data lands — but never clobber
  // an in-progress selection (the user mid-merge/run): if entries are ticked,
  // skip the auto-refresh (it would renumber/reset their ticks); they can hit
  // Refresh manually.
  //
  // CRITICAL: this function must NEVER change the active view (CURRENT). It only
  // refreshes data within the already-open page. Dictation, paste, and clipboard
  // events fire this handler — the user's current tab (Settings, Reader, etc.)
  // must stay exactly where it is.
  if (CURRENT === "history") {
    if (HX.selected.length === 0) {
      // Selective refresh: only re-fetch the data source that actually changed.
      // A full renderHistory() pulls 5 API calls every time; a clipboard-only
      // bump doesn't need to re-fetch transcripts/prompts/favorites/presets.
      if (what === "clipboard") {
        call("get_clipboard", 200).then(function (data) {
          if (CURRENT !== "history") return; // tab switched away — discard
          HX.clipboard = data || [];
          drawHistory();
        }).catch(function () {});
      } else if (what === "prompts") {
        call("get_prompts").then(function (data) {
          if (CURRENT !== "history") return;
          HX.prompts = data || [];
          drawHistory();
        }).catch(function () {});
      } else {
        // "history" (new transcript), "favorites", or unknown → full refresh
        renderHistory();
      }
    }
  } else if (CURRENT === "stats") {
    renderStats();
  } else if (CURRENT === "home") {
    bootHome();
  } else if (CURRENT === "meetings" && what === "meetings") {
    if (MEET.openId) openMeeting(MEET.openId);
    else renderMeetings();
  }
  // Settings, Reader, or any other view: do nothing. These pages are
  // self-contained; their data refreshes on explicit re-entry via navTo().
};
function setSub(s) {
  // Legacy stub — filter chips have replaced subtabs (owner v7).
  // Map old subtab names to the corresponding filter chip toggle.
  HX.activeFilters.clear();
  if (s === "transcripts") {
    HX.activeFilters.add("transcripts").add("emails");
  } else if (s === "favorites") {
    HX.activeFilters.add("favorites");
  } else {
    HX.activeFilters.add(s);
  }
  drawHistory();
}

const SRC_COLOR = {
  transcript: "var(--gold)",
  clipboard: "var(--mode-context)",
  prompt: "var(--mode-prompt)",
};

/* Date grouping (UX-Pilot history): Today / Yesterday / Older sections with a
   labelled rule and an entry count — immediate temporal orientation. */
function dateGroup(stamp) {
  const d = (stamp || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return "Older";
  const pad = (n) => String(n).padStart(2, "0");
  const now = new Date();
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const y = new Date(now.getTime() - 86400000);
  const yest = `${y.getFullYear()}-${pad(y.getMonth() + 1)}-${pad(y.getDate())}`;
  return d === today ? "Today" : d === yest ? "Yesterday" : "Older";
}
function renderGrouped(items, rowFn, stampOf) {
  // "Most words (all time)" (owner v6): ignore date grouping entirely — entries
  // are already ranked by word count across ALL dates (sortItems), so render them
  // as ONE flat list, highest word count first regardless of when it was said.
  // This is an ADDITIONAL option; "Most words (by day)" keeps the date sections.
  if (HX.sort === "words_all") {
    if (!items.length) return "";
    return (
      `<div class="hist-group"><span class="hg-name">Most words</span><div class="hg-rule"></div><span class="hg-count">${items.length} ${items.length === 1 ? "entry" : "entries"} · all time</span></div>` +
      items.map(rowFn).join("")
    );
  }
  const order =
    HX.sort === "old"
      ? ["Older", "Yesterday", "Today"]
      : ["Today", "Yesterday", "Older"];
  let html = "";
  for (const g of order) {
    const sub = items.filter((i) => dateGroup(stampOf(i)) === g);
    if (!sub.length) continue;
    html += `<div class="hist-group"><span class="hg-name">${g}</span><div class="hg-rule"></div><span class="hg-count">${sub.length} ${sub.length === 1 ? "entry" : "entries"}</span></div>`;
    html += sub.map(rowFn).join("");
  }
  return html;
}

function drawHistory() {
  const q = HX.q.toLowerCase();
  const list = $("#hist-list");
  const cleanBtn = $("#hist-clear");

  // ---- Build unified item list from all sources, each tagged with a _type ----
  const unified = [];

  // Transcript items: split into "transcripts" (non-email) and "emails" (mode==="email")
  (HX.transcripts || []).forEach((i, idx) => {
    const isEmail = (i.mode || "") === "email";
    unified.push({
      _type: isEmail ? "emails" : "transcripts",
      _idx: idx,
      _src: "transcript",
      _render: () => {
        const showRaw = HX.pre && i.raw;
        return rowHTML({
          tag: MODE_LABELS[i.mode] || i.mode,
          color: MODE_COLORS[i.mode] || "var(--gold)",
          meta:
            `${i.time} · ${i.words || 0} words` +
            wpmMeta(i) +
            (HX.pre
              ? i.raw
                ? " · raw transcript"
                : " · no raw kept (same as output)"
              : ""),
          text: showRaw ? i.raw : i.text,
          fav: i.fav,
          idx,
          kind: "transcript",
          src: "transcript",
          favtext: i.text,
          mode: i.mode,
          quality: i.quality,
          via: i.via,
          stamp: i.stamp,
        });
      },
      _qtext: (i.text || "") + " " + (i.raw || ""),
      _stamp: i.stamp,
      _words: i.words || 0,
    });
  });

  // Clipboard items
  (HX.clipboard || []).forEach((i, idx) => {
    const newest = idx === 0;
    unified.push({
      _type: "clipboard",
      _idx: idx,
      _src: "clipboard",
      _isImage: i.type === "image",
      _render: () => {
        if (i.type === "image") return imageRowHTML(i, idx, newest);
        return rowHTML({
          tag: "clip",
          color: "var(--mode-context)",
          meta: i.time,
          text: i.text,
          fav: i.fav,
          idx,
          kind: "clip",
          src: "clipboard",
          favtext: i.text,
          latest: newest,
          stamp: i.stamp,
        });
      },
      _qtext: i.text || "",
      _stamp: i.stamp,
      _words: (i.text || "").split(/\s+/).filter(Boolean).length,
    });
  });

  // Prompt items
  (HX.prompts || []).forEach((i, idx) => {
    unified.push({
      _type: "prompts",
      _idx: idx,
      _src: "prompt",
      _render: () =>
        rowHTML({
          tag: "Prompt",
          color: "var(--mode-prompt)",
          meta:
            i.time + (i.request ? " · \u201C" + i.request.slice(0, 48) + "\u201D" : ""),
          text: i.prompt,
          fav: i.fav,
          idx,
          kind: "prompt",
          src: "prompt",
          favtext: i.prompt,
          mode: "prompt",
        }),
      _qtext: (i.prompt || "") + " " + (i.request || ""),
      _stamp: i.time,
      _words: (i.prompt || "").split(/\s+/).filter(Boolean).length,
    });
  });

  // Favourite items
  (HX.favorites || []).forEach((i, idx) => {
    const fmode = i.mode || "";
    const isMode = fmode && MODE_LABELS[fmode];
    const label = isMode
      ? MODE_LABELS[fmode]
      : i.source === "prompt"
        ? "Prompt"
        : i.source === "transcript"
          ? "Text"
          : i.source || "item";
    const color = isMode
      ? MODE_COLORS[fmode] || "var(--gold)"
      : SRC_COLOR[i.source] || "var(--gold)";
    unified.push({
      _type: "favorites",
      _idx: idx,
      _src: "favorite",
      _render: () =>
        rowHTML({
          tag: "\u2605 " + label,
          color,
          meta:
            (i.added ? "starred " + i.added : "") +
            (i.time ? " \u00b7 from " + i.time : ""),
          text: i.text,
          fav: true,
          idx: 0,
          kind: "favorite",
          src: i.source || "clipboard",
          favtext: i.text,
          mode: fmode,
          quality: i.quality,
          via: i.via,
        }),
      _qtext: i.text || "",
      _stamp: i.added,
      _words: (i.text || "").split(/\s+/).filter(Boolean).length,
    });
  });

  // ---- Filter by active filter chips (union semantics) ----
  const activeTypes = HX.activeFilters.size > 0
    ? HX.activeFilters
    : new Set(["transcripts", "clipboard", "prompts", "favorites", "emails"]);

  let items = unified.filter((u) => activeTypes.has(u._type));

  // Text search (case-insensitive, across all visible content types)
  if (q) {
    items = items.filter((u) => u._qtext.toLowerCase().includes(q));
  }

  // Sort
  items = sortItems(items);

  // Render grouped
  let html = renderGrouped(
    items,
    (u) => u._render(),
    (u) => u._stamp,
  );

  // Clear button: only show if there are visible items from clearable sources
  const hasClearable = items.some(
    (u) => u._src === "transcript" || u._src === "clipboard" || u._src === "prompt",
  );
  cleanBtn.hidden = !hasClearable;

  if (hasClearable) {
    const counts = { transcripts: 0, clipboard: 0, prompts: 0 };
    items.forEach((u) => {
      if (u._src === "transcript") counts.transcripts++;
      else if (u._src === "clipboard") counts.clipboard++;
      else if (u._src === "prompt") counts.prompts++;
    });
    let best = "transcripts";
    if (counts.clipboard > counts[best]) best = "clipboard";
    if (counts.prompts > counts[best]) best = "prompts";
    setText("#hist-clear-label", `Clear ${best}`);
    cleanBtn.title = `Clear the visible ${best} entries`;
    cleanBtn.onclick = () => clearList(best);
  }

  const EMPTY = {
    transcripts: "Nothing here yet \u2014 press Ctrl+Option+D and dictate something.",
    clipboard: "Nothing captured yet \u2014 copy any text or image and it appears here.",
    prompts: "No prompts yet \u2014 switch Prompt on (the island or Settings) and dictate an idea.",
    favorites: "No favourites yet \u2014 click the \u2605 on any item to pin it here.",
    emails: "No email transcripts yet \u2014 dictate with Email mode selected.",
  };

  // Captured selections render above the main list
  const inj = (HX.injected || []).filter(
    (s) => !q || s.text.toLowerCase().includes(q),
  );
  const injGroup = inj.length
    ? `<div class="hist-group"><span class="hg-name">Captured selection</span><div class="hg-rule"></div><span class="hg-count">${inj.length} ${inj.length === 1 ? "item" : "items"}</span></div>` +
      inj
        .map((s) =>
          rowHTML({
            tag: "Selection",
            color: "var(--mode-convert)",
            meta: "highlighted in another app",
            text: s.text,
            fav: false,
            idx: 0,
            kind: "selection",
            src: "clipboard",
            favtext: s.text,
          }),
        )
        .join("")
    : "";

  // Context-aware empty message
  let emptyMsg = "Nothing here yet.";
  if (!items.length) {
    if (HX.activeFilters.size === 1) {
      const onlyType = [...HX.activeFilters][0];
      emptyMsg = EMPTY[onlyType] || emptyMsg;
    } else if (HX.activeFilters.size === 0) {
      const allEmpty =
        (!HX.transcripts || !HX.transcripts.length) &&
        (!HX.clipboard || !HX.clipboard.length) &&
        (!HX.prompts || !HX.prompts.length) &&
        (!HX.favorites || !HX.favorites.length);
      if (allEmpty) {
        emptyMsg = "Nothing here yet \u2014 press Ctrl+Option+D and dictate to get started.";
      } else {
        emptyMsg = "No items match your current search or filters.";
      }
    } else {
      emptyMsg = "No items match your current search or filters.";
    }
  }

  list.innerHTML =
    injGroup +
    (html ||
      (injGroup ? "" : `<div class="empty">${emptyMsg}</div>`));
  paintIcons(list);
  wireRows(list);
  loadThumbs(list);
  setText(
    "#hist-count",
    items.length + " item" + (items.length === 1 ? "" : "s"),
  );

  // Post/Pre-AI toggle: visible only when transcripts or emails are active
  const hasTranscriptContent =
    HX.activeFilters.size === 0 ||
    HX.activeFilters.has("transcripts") ||
    HX.activeFilters.has("emails");
  $("#hist-prepost").style.visibility = hasTranscriptContent ? "visible" : "hidden";

  // Hide mode-chips container (obsolete with filter chips)
  const modeChipsHost = $("#hist-modechips");
  if (modeChipsHost) modeChipsHost.hidden = true;

  refreshSelectionUI(list);
  syncShimmer(list);
}

/* clipboard image rows: fetch tiny base64 thumbnails from the backend
   (the webview can't read %APPDATA% paths directly) */
function loadThumbs(root) {
  $$("[data-thumb-path]", root).forEach(async (el) => {
    const p = el.dataset.thumbPath;
    if (!p || el.dataset.thumbed) return;
    el.dataset.thumbed = "1";
    const uri = await call("get_clip_thumb", p);
    if (uri)
      el.innerHTML = `<img class="thumb" src="${uri}" alt="clipboard image">`;
  });
}

// Words-per-minute for a transcript row: words ÷ minutes spoken. Only shown
// when we have both a word count and a sane duration (≥1s) — old entries that
// predate duration tracking simply omit it rather than show a wrong number.
function wpmMeta(i) {
  const w = i.words || 0,
    d = i.duration || 0;
  if (!w || d < 1) return "";
  const wpm = Math.round(w / (d / 60));
  if (!isFinite(wpm) || wpm <= 0 || wpm > 400) return ""; // guard nonsense
  return ` · ${fmtDur(d)} · ${wpm} wpm`;
}
function fmtDur(s) {
  s = Math.round(s || 0);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

function rowHTML({
  tag,
  color,
  meta,
  text,
  fav,
  idx,
  kind,
  src,
  favtext,
  quality,
  via,
  mode,
  latest,
  stamp,
}) {
  // UX-Pilot row design: a glowing mode dot + the mode name in its colour
  // lead the meta line; actions reveal on hover; locally derived audio
  // quality and the "via Convert" provenance render as small chips.
  // per-row delete only where the backend supports it: transcripts + clips
  // delete by index; favourites "delete" = unstar; prompts are clear-all only.
  const delBtn =
    kind === "transcript" || kind === "clip" || kind === "favorite"
      ? `<button class="btn-icon btn-ghost" data-del title="${kind === "favorite" ? "Remove favourite" : "Delete"}" aria-label="Delete">${svg("trash")}</button>`
      : kind === "selection"
        ? // A captured selection is transient — its ✕ just dismisses it from the
          // Deck (no store to delete from); handled by the data-del path in wireRows.
          `<button class="btn-icon btn-ghost" data-del title="Dismiss this captured selection" aria-label="Dismiss">${svg("x")}</button>`
        : "";
  // Convert: run a Smart Mode over this item (Transcripts + Clipboard, and a
  // captured Selection) — a quick pop-up of the modes, right from the row.
  const convertBtn =
    kind === "transcript" || kind === "clip" || kind === "selection"
      ? `<button class="btn-icon btn-ghost" data-convert="${esc(text)}" title="Convert to a Smart Mode" aria-label="Convert">${svg("rotate")}</button>`
      : "";
  const qchip = quality
    ? `<span class="qchip q-${esc(quality)}" title="Local audio-quality estimate from the recogniser's own per-word confidence — Good = clear &amp; confident, Fair = some uncertainty, Bad = noisy or unclear. Computed on-device; no audio is kept or sent.">${esc(quality)} audio</span>`
    : "";
  const viaChip =
    via === "convert"
      ? `<span class="via-chip" title="Produced by the Convert router">via Convert</span>`
      : "";
  // The newest clipboard item == the LIVE clipboard (what Ctrl+V pastes right
  // now). Mark it unmistakably so the active item is obvious at a glance.
  const liveChip = latest
    ? `<span class="latest-chip" title="The most recent clipboard item — what Ctrl+V would paste right now">● Current</span>`
    : "";
  // selection checkbox — numbered in the order you tick (owner v4 §6b); expand
  // affordance on the text so the FULL transcript can be read on demand (§D).
  const sk = selKeyOf(text);
  const ord = HX.selected.indexOf(sk);
  const checked = ord >= 0;
  const expanded = HX.expanded.has(sk);
  const longText = (text || "").length > 220;
  const check = `<button class="hist-check ${checked ? "on" : ""}" data-selcheck="${esc(sk)}" title="${checked ? "Selected #" + (ord + 1) + " — click to deselect" : "Select for merge / run"}" aria-pressed="${checked}">${checked ? ord + 1 : ""}</button>`;
  const expandBtn = longText
    ? `<button class="row-expand btn-icon btn-ghost" data-expand title="${expanded ? "Collapse" : "Read full"}" aria-label="Expand">${svg("expand")}</button>`
    : "";
  // ROW LAYOUT (owner 2026-06-23): the action cluster lives INSIDE the meta line
  // (right-aligned) — NOT in a right-hand column — so the transcript text below
  // runs the full width of the row. `.row-metatext` is the shrinkable part.
  return `<div class="row${latest ? " is-latest" : ""}${checked ? " selrow" : ""}${expanded ? " expanded" : ""}" data-idx="${idx}" data-stamp="${esc(stamp || "")}" data-kind="${kind}" data-selkey="${esc(sk)}">
    ${check}
    <div class="row-main">
      <div class="row-meta">
        <span class="mode-dot" style="background:${color};box-shadow:0 0 6px color-mix(in srgb, ${color} 55%, transparent)"></span>
        <span class="row-mode" style="color:${color}">${esc(tag)}</span>
        <span class="row-metatext">· ${esc(meta)}</span>${liveChip}${qchip}${viaChip}
        <div class="row-actions">
          ${expandBtn}
          <button class="star ${fav ? "on" : ""} btn-icon btn-ghost" data-star data-src="${src}" data-mode="${esc(mode || "")}" data-fav="${esc(favtext)}" title="${fav ? "In favourites — click to remove" : "Add to favourites"}" aria-label="Favourite" aria-pressed="${fav ? "true" : "false"}">${svg("star")}</button>
          <button class="btn-icon btn-ghost" data-copybtn title="Copy" aria-label="Copy">${svg("copy")}</button>
          <button class="btn-icon reader-send" data-reader title="Send to Reader — read this aloud" aria-label="Send to Reader">${svg("volume")}</button>
          ${convertBtn}
          ${delBtn}
        </div>
      </div>
      <div class="row-text${longText ? " expandable" : ""}" data-copy="${esc(text)}"${longText ? ' title="Click to expand / collapse"' : ""}>${esc(text || "")}</div>
    </div></div>`;
}
function imageRowHTML(i, idx, latest) {
  const liveChip = latest
    ? `<span class="latest-chip" title="The most recent clipboard item — what Ctrl+V would paste right now">● Current</span>`
    : "";
  return `<div class="row${latest ? " is-latest" : ""}" data-idx="${idx}" data-stamp="${esc(i.stamp || "")}" data-kind="clip">
    <div class="thumb flex items-center justify-center t-mute" data-thumb-path="${esc(i.path || "")}">${svg("layers")}</div>
    <div class="row-main"><div class="row-meta"><span class="tag" style="color:var(--mode-context);background:color-mix(in srgb,var(--mode-context) 16%,transparent)">image</span><span class="row-metatext">${esc(i.time)} · ${esc(i.size || "")}</span>${liveChip}
      <div class="row-actions">
        <button class="btn-icon btn-ghost" data-del title="Delete" aria-label="Delete">${svg("trash")}</button>
      </div></div>
      <div class="row-text t-mute">Image copied to clipboard</div></div></div>`;
}
// Re-fetch the ★ store from the backend (the single source of truth) so the
// local cache can never drift out of step — the old index-based mutation is
// exactly what removed the wrong / multiple favourites.
async function refreshFavorites() {
  if (!HAS_PY()) return;
  try {
    HX.favorites = (await call("get_favorites")) || [];
  } catch (e) {}
}
// Mirror a fav state change onto every cached list so the ★ shows correctly in
// Transcripts / Clipboard / Prompts too — favourite state is never invisible.
function setFavFlag(text, on) {
  [HX.transcripts, HX.clipboard, HX.prompts].forEach((arr) =>
    (arr || []).forEach((it) => {
      if (it.text === text || it.prompt === text) it.fav = on;
    }),
  );
}
function wireRows(root) {
  $$("[data-star]", root).forEach(
    (b) =>
      (b.onclick = async () => {
        const on = !b.classList.contains("on");
        b.classList.toggle("on", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
        b.title = on ? "In favourites — click to remove" : "Add to favourites";
        await call(
          "toggle_favorite",
          b.dataset.fav,
          b.dataset.src,
          "",
          b.dataset.mode || "",
        );
        setFavFlag(b.dataset.fav, on);
        if (HAS_PY()) await refreshFavorites();
        else if (on)
          HX.favorites.unshift({
            text: b.dataset.fav,
            source: b.dataset.src,
            mode: b.dataset.mode || "",
            time: "",
            added: "just now",
            fav: true,
          });
        else
          HX.favorites = HX.favorites.filter((f) => f.text !== b.dataset.fav);
        // mirror onto any other visible row with the same text (no full redraw → no scroll jump)
        $$("[data-star]", root).forEach((s) => {
          if (s.dataset.fav === b.dataset.fav) {
            s.classList.toggle("on", on);
            s.setAttribute("aria-pressed", on ? "true" : "false");
          }
        });
        if (HX.activeFilters.size === 0 || HX.activeFilters.has("favorites")) drawHistory();
        toast(
          on ? "Added to favourites" : "Removed from favourites",
          "ok",
          1500,
        );
      }),
  );
  $$("[data-copybtn]", root).forEach(
    (b) =>
      (b.onclick = () => {
        const t = $(".row-text", b.closest(".row"))?.dataset.copy || "";
        copyTextReliable(t).then((ok) =>
          toast(ok ? "Copied" : "Couldn't copy", ok ? "ok" : "err", 1200));
      }),
  );
  $$("[data-reader]", root).forEach(
    (b) =>
      (b.onclick = () => {
        const t = $(".row-text", b.closest(".row"))?.dataset.copy || "";
        sendToReader(t);
      }),
  );
  $$("[data-convert]", root).forEach(
    (b) => (b.onclick = () => openConvertMenu(b.dataset.convert)),
  );
  // Hover = implicit selection (owner 2026-06-20): track the row under the cursor
  // so Ctrl+H can run the armed Smart Mode / preset on it (or open the Smart Mode
  // menu if nothing is armed). Restores the "hover, Ctrl+H, run" affordance.
  $$(".row", root).forEach((row) => {
    row.addEventListener("mouseenter", () => {
      HX.hover = {
        text: row.dataset.selkey || "",
        kind: row.dataset.kind || "",
      };
      $$(".row.hover-target", root).forEach((r) =>
        r.classList.remove("hover-target"),
      );
      row.classList.add("hover-target");
    });
    row.addEventListener("mouseleave", () => {
      row.classList.remove("hover-target");
      if (HX.hover && HX.hover.text === (row.dataset.selkey || ""))
        HX.hover = null;
    });
  });
  // selection checkbox — toggle ordered multi-select (no full redraw → no scroll jump)
  $$("[data-selcheck]", root).forEach(
    (b) =>
      (b.onclick = (e) => {
        e.stopPropagation();
        const sk = b.dataset.selcheck;
        const i = HX.selected.indexOf(sk);
        if (i >= 0) HX.selected.splice(i, 1);
        else HX.selected.push(sk);
        refreshSelectionUI(root);
      }),
  );
  // expand / collapse a transcript to read it in full (owner v4 §D)
  const toggleExpand = (row) => {
    const sk = row.dataset.selkey;
    if (HX.expanded.has(sk)) HX.expanded.delete(sk);
    else HX.expanded.add(sk);
    row.classList.toggle("expanded", HX.expanded.has(sk));
    const eb = $("[data-expand]", row);
    if (eb)
      eb.title = row.classList.contains("expanded") ? "Collapse" : "Read full";
  };
  $$(".row-text.expandable", root).forEach(
    (t) => (t.onclick = () => toggleExpand(t.closest(".row"))),
  );
  $$("[data-expand]", root).forEach(
    (b) =>
      (b.onclick = (e) => {
        e.stopPropagation();
        toggleExpand(b.closest(".row"));
      }),
  );
  $$("[data-del]", root).forEach(
    (b) =>
      (b.onclick = async () => {
        const row = b.closest(".row");
        const kind = row.dataset.kind;
        // Delete by STABLE identity (stamp [+ text]), never array index. data-selkey
        // holds the row's text (selKeyOf); image rows have none → text '' → matched
        // by stamp. Index deletion sent a stale position when the list shifted under
        // it (a prior delete, a refresh, or a controller insert) → wrong row removed.
        const stamp = row.dataset.stamp || "";
        const text = row.dataset.selkey || "";
        row.style.transition = "opacity .2s";
        row.style.opacity = "0";
        setTimeout(() => row.remove(), 200);
        if (kind === "selection") {
          // Transient capture — just drop it from the in-memory list; no backend.
          HX.injected = (HX.injected || []).filter((s) => s.text !== text);
        } else if (kind === "transcript") {
          HX.transcripts = HX.transcripts.filter(
            (i) => !((i.stamp || "") === stamp && (i.text || "") === text),
          );
          await call("delete_transcript", stamp, text);
        } else if (kind === "clip") {
          HX.clipboard = HX.clipboard.filter(
            (i) => !((i.stamp || "") === stamp && (i.text || "") === text),
          );
          await call("delete_clip", stamp, text);
        } else if (kind === "favorite") {
          // remove by the favourite's unique TEXT KEY, never by array index, so one
          // removal can never take others with it
          const star = $("[data-star]", row);
          const ft = star ? star.dataset.fav : "";
          if (ft) {
            await call(
              "toggle_favorite",
              ft,
              star.dataset.src || "clipboard",
              "",
              star.dataset.mode || "",
            );
            setFavFlag(ft, false);
            if (HAS_PY()) await refreshFavorites();
            else HX.favorites = HX.favorites.filter((f) => f.text !== ft);
          }
        }
        setTimeout(drawHistory, 220);
      }),
  );
}
async function clearList(which) {
  const ok = await confirmModal({
    title: "Clear all " + which + "?",
    body:
      "This removes every item from " +
      which +
      ". Favourites you starred are kept. This cannot be undone.",
    confirmText: "Clear all",
  });
  if (!ok) return;
  if (which === "transcripts") {
    HX.transcripts = [];
    await call("clear_transcripts");
  }
  if (which === "clipboard") {
    HX.clipboard = [];
    await call("clear_clipboard");
  }
  if (which === "prompts") {
    HX.prompts = [];
    await call("clear_prompts");
  }
  drawHistory();
  toast(which.charAt(0).toUpperCase() + which.slice(1) + " cleared", "ok");
}

/* ============================================================================
   HISTORY HUB — the absorbed Deck (owner v4): Smart Mode + Presets sections, an
   ordered multi-select, Merge → Copy/Paste, Run preset/mode over the selection,
   and a Pin-on-top "always-open" workflow. The standalone Deck overlay is gone;
   this is the one unified place.
   ========================================================================== */
// re-number every visible checkbox by selection order; refresh the action bar
function refreshSelectionUI(root) {
  root = root || $("#hist-list");
  $$("[data-selcheck]", root).forEach((b) => {
    const ord = HX.selected.indexOf(b.dataset.selcheck);
    const on = ord >= 0;
    b.classList.toggle("on", on);
    b.textContent = on ? ord + 1 : "";
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.title = on
      ? `Selected #${ord + 1} — click to deselect`
      : "Select for merge / run";
    const row = b.closest(".row");
    if (row) row.classList.toggle("selrow", on);
  });
  updateHistActionbar();
}
function updateHistActionbar() {
  const bar = $("#hist-actionbar");
  if (!bar) return;
  const n = HX.selected.length;
  bar.hidden = n === 0;
  const single = n === 1;
  // ONE selected → a plain "Copy"/"Paste" (merging a single item is just copying
  // it), and that Copy becomes the gold hero the linked-copy connector lands on.
  // TWO+ → "Merge & Copy"/"Merge & Paste" and the gold accent returns to Run.
  setText("#hist-mc-label", single ? "Copy" : "Merge & Copy");
  setText("#hist-mp-label", single ? "Paste" : "Merge & Paste");
  setText(
    "#hist-sel-info",
    n + " selected" + (n > 1 ? " · merged in tick order" : ""),
  );
  const mc = $("#hist-merge-copy"),
    run = $("#hist-run");
  if (mc) {
    mc.classList.toggle("btn-gold", single);
    mc.classList.toggle("linked-copy", single);
  }
  if (run) run.classList.toggle("btn-gold", !single);
  const presetName = HX.runPreset
    ? (HX.presets.find((p) => p[0] === HX.runPreset) || [])[1] || ""
    : "";
  const what = `${presetName}${presetName && HX.runMode ? " + " : ""}${HX.runMode || ""}`;
  setText("#hist-run-label", what ? `Run · ${what}` : "Run on selection");
  syncLinkedCopy(single);
}
function clearHistSelection() {
  HX.selected = [];
  refreshSelectionUI();
}

/* LINKED-COPY connector REMOVED (owner: the full-viewport gold line was brittle
   — a position:fixed SVG appended to <body> that bled across the Stats / Reader /
   Settings views when switching tabs, since nothing tore it down on nav). The
   copy affordance now lives at the ROW level: hovering a row turns its inline
   Copy button gold + bold and gives it a brief vertical pulse (see
   `.row .btn-icon[data-copybtn]` in app.css), teaching that the button copies the
   row. syncLinkedCopy is kept as a no-op so callers don't change; it also tears
   down any stray connector element a previous build may have left in the DOM. */
function syncLinkedCopy() {
  const stray = document.getElementById("hist-connector");
  if (stray) stray.remove();
}

// Smart Mode chips + Presets, rendered into the History toolbox sections
function drawHistTools() {
  // List is no longer an independent Smart Mode (the AI polish bullets dictated
  // lists natively); Convert is a per-row router (the rotate button), not a mode
  // you arm over a selection. Both dropped from the armable roster.
  const modes = ["prompt", "email", "foreign"];
  const mh = $("#hist-runmodes");
  if (mh) {
    mh.innerHTML = modes
      .map(
        (m) =>
          `<button class="mode-chip ${HX.runMode === m ? "on" : ""}" data-runmode="${m}" title="${esc(MODE_DESCRIPTIONS[m] || "")}" style="${HX.runMode === m ? `background:${MODE_COLORS[m]};border-color:${MODE_COLORS[m]}` : `color:${MODE_COLORS[m]}`}">${m}</button>`,
      )
      .join("");
    $$("[data-runmode]", mh).forEach(
      (b) =>
        (b.onclick = () => {
          HX.runMode =
            HX.runMode === b.dataset.runmode ? null : b.dataset.runmode;
          drawHistTools();
          updateHistActionbar();
        }),
    );
  }
  const ph = $("#hist-runpresets");
  if (ph) {
    // Custom presets carry a "Custom N" tag in the description line so they're
    // identifiable in History (owner 2026-06-20). N is derived from the slot, so
    // nothing pollutes the user's stored description (which the Preset Adder edits).
    const nBuiltin = (HX.presets || []).filter((p) => p[3]).length;
    ph.innerHTML =
      (HX.presets || [])
        .map((p) => {
          const tag = p[3] ? "" : "Custom " + (p[0] - nBuiltin);
          const desc = [tag, p[2] || ""].filter(Boolean).join(" · ");
          return `<button class="preset ${HX.runPreset === p[0] ? "on" : ""}" data-runpreset="${p[0]}"><div class="p-title">${esc(p[1])}</div><div class="p-desc">${esc(desc)}</div></button>`;
        })
        .join("") || '<div class="empty">No presets yet.</div>';
    $$("[data-runpreset]", ph).forEach(
      (b) =>
        (b.onclick = () => {
          const s = +b.dataset.runpreset;
          HX.runPreset = HX.runPreset === s ? null : s;
          drawHistTools();
          updateHistActionbar();
        }),
    );
  }
}

// the selected entries (in tick order) as {kind, time, text} for merge / run
function selectedItems() {
  const all = [
    ...(HX.transcripts || []).map((x) => ({
      kind: "TEXT",
      time: x.time,
      text: x.text,
    })),
    ...(HX.clipboard || [])
      .filter((x) => x.type !== "image")
      .map((x) => ({ kind: "CLIP", time: x.time, text: x.text })),
    ...(HX.prompts || []).map((x) => ({
      kind: "PROMPT",
      time: (x.time || "").slice(-5),
      text: x.prompt,
    })),
    ...(HX.favorites || []).map((x) => ({
      kind: "TEXT",
      time: x.time,
      text: x.text,
    })),
  ];
  const byText = new Map();
  all.forEach((it) => {
    if (it.text && !byText.has(it.text)) byText.set(it.text, it);
  });
  return HX.selected.map((sk) => byText.get(sk)).filter(Boolean);
}
async function histMerge(paste) {
  const items = selectedItems();
  if (!items.length) {
    toast("Tick some entries first", "info");
    return;
  }
  const combined = items
    .map((i) => i.text || "")
    .filter(Boolean)
    .join("\n\n");
  if (paste && HAS_PY()) {
    const r = await call("deck_paste", combined);
    if (r && r.live) {
      toast(
        `Pasting ${items.length} merged entr${items.length === 1 ? "y" : "ies"} at your cursor…`,
        "ok",
        2000,
      );
      return;
    }
  }
  if (!(await copyTextReliable(combined))) {
    toast("Merged, but couldn't copy to the clipboard", "err", 2200);
    return;
  }
  toast(
    `Merged ${items.length} entr${items.length === 1 ? "y" : "ies"} — copied (Ctrl+V to paste)`,
    "ok",
    2200,
  );
}
let _deckJobBusy = false;
async function histRun() {
  const items = selectedItems();
  if (!items.length) {
    toast("Tick some entries first", "info");
    return;
  }
  if (!HX.runPreset && !HX.runMode) {
    toast("Pick a Smart Mode or Preset above first", "info", 2800);
    return;
  }
  if (_deckJobBusy) return; // ignore a double-click while a job is in flight (the
  _deckJobBusy = true; // backend also guards, but don't even send the 2nd call)
  try {
    const r = await call("run_deck_job", HX.runPreset, HX.runMode, items);
    if (r && r.ok && r.live)
      toast("Working — the result will paste at your cursor", "ok", 3000);
    else if (r && r.ok) toast("Done — result pasted", "ok");
    else
      toast(
        (r && r.message) ||
          "Running a preset/mode needs the full app running (and your AI key)",
        "info",
        3400,
      );
  } finally {
    setTimeout(() => {
      _deckJobBusy = false;
    }, 1200);
  }
}

// Pin History on top of every other app — the always-open workflow (§F/§6a).
async function setHistPinned(on) {
  HX.pinned = !!on;
  const btn = $("#hist-pin");
  if (btn) {
    btn.classList.toggle("btn-gold", HX.pinned);
    btn.classList.toggle("btn-ghost", !HX.pinned);
  }
  setText("#hist-pin-label", HX.pinned ? "Pinned" : "Pin");
  if (HAS_PY()) {
    try {
      const applied = await call("set_pinned", HX.pinned);
      if (!applied) {
        HX.pinned = !HX.pinned;
        if (btn) {
          btn.classList.toggle("btn-gold", HX.pinned);
          btn.classList.toggle("btn-ghost", !HX.pinned);
        }
        setText("#hist-pin-label", HX.pinned ? "Pinned" : "Pin");
        toast("This window could not change its always-on-top state", "err", 3000);
      }
    } catch (e) {
      HX.pinned = !HX.pinned;
      if (btn) {
        btn.classList.toggle("btn-gold", HX.pinned);
        btn.classList.toggle("btn-ghost", !HX.pinned);
      }
      setText("#hist-pin-label", HX.pinned ? "Pinned" : "Pin");
      toast("This window could not change its always-on-top state", "err", 3000);
    }
  }
  // Re-evaluate the non-activating palette behaviour now the pin state changed
  // (on while pinned + on the Deck; off otherwise so typing/search still works).
  syncDeckWindow();
}
// A selection captured from another app when the Deck was opened with text
// highlighted (owner 2026-06-23). The controller grabs it BEFORE the window
// steals focus (which clears the highlight) and hands it to openHistory(); it
// lands as a temporary item at the TOP of the Deck so you can Convert it, tick
// it alongside history items, or run a preset — then dismiss it. Deduped
// (re-grabbing the same text just floats it back to the top) and capped so
// repeated captures can't pile up.
function addDeckSelection(text) {
  const t = (text || "").trim();
  if (!t) return;
  HX.injected = (HX.injected || []).filter((s) => s.text !== t);
  HX.injected.unshift({ text: t });
  if (HX.injected.length > 5) HX.injected.length = 5;
}

// Ctrl+Option+H / the homepage button → open History as the hub (and pin on top).
// With toggle=true (the global hotkey), re-pressing it while History is already
// up + pinned hides it again — "re-using the shortcut to toggle" (owner §6a).
// `selection` is the highlighted text the controller grabbed when the Deck
// hotkey fired (see addDeckSelection): when present we NEVER toggle-hide — even
// if the Deck was already up — we surface it and drop the capture in on top.
function openHistory(toggle, selection) {
  if (selection) {
    addDeckSelection(selection);
    navTo("history"); // navTo always re-renders History, so the capture shows
    setHistPinned(true);
    return;
  }
  if (toggle && CURRENT === "history" && HX.pinned) {
    setHistPinned(false);
    if (HAS_PY()) call("minimize_window");
    return;
  }
  navTo("history");
  setHistPinned(true);
}

// "Capture" — pull whatever text the user has highlighted in ANOTHER app into the
// open Deck on demand (the always-pinned companion-window flow). The controller
// does the real grab (it owns the keyboard + clipboard); this works because a
// pinned Deck is non-activating, so clicking Capture leaves the highlight live in
// the source app and the Ctrl+C grab succeeds. The text lands as a "Captured
// selection" item at the top — exactly like opening the Deck over a selection.
// Honest feedback when there is nothing to grab (and a nudge to pin first, since
// an un-pinned Deck steals focus and clears the selection on click).
/* Capture a whole AI conversation (focused chat) as prompting context. The
   controller does Select-All -> Copy so it reaches every message, even ones
   scrolled out of view; the result is parsed into turns and stored. */
async function captureConversation() {
  if (!HAS_PY()) {
    toast("Open an AI chat, click into it, then use Capture chat in the app", "info");
    return;
  }
  toast("Capturing the conversation…", "busy", 1400);
  let r;
  try {
    r = await call("capture_conversation");
  } catch (e) {
    console.error("capture_conversation failed:", e);
    toast("Couldn't capture the conversation", "err");
    return;
  }
  if (r && r.ok) {
    const t = r.turns || 0;
    toast(`Captured the conversation · ${t} turn${t === 1 ? "" : "s"} now feed your next Prompt`, "ok", 2600);
  } else {
    toast((r && r.message) || "No conversation found — click into the chat first", "err", 3200);
  }
}

async function captureSelection() {
  if (!HAS_PY()) {
    toast("Capture works in the app — highlight text, then click Capture", "info");
    return;
  }
  let r;
  try {
    r = await call("capture_selection");
  } catch (e) {
    console.error("capture_selection failed:", e);
    toast("Couldn't capture the selection", "err");
    return;
  }
  const sel = (r && r.selection ? r.selection : "").trim();
  if (sel) {
    addDeckSelection(sel);
    if (CURRENT !== "history") navTo("history");
    else renderHistory();
    toast("Captured your highlighted text", "ok", 1800);
  } else if (r && r.ok) {
    toast(
      HX.pinned
        ? "No text highlighted — select some text first, then click Capture"
        : "Pin the Deck first so Capture doesn’t steal your selection",
      "info",
      2800,
    );
  } else {
    toast(
      (r && r.message) || "Start Mumble to capture highlighted text",
      "err",
      3000,
    );
  }
}

/* ============================================================================
   STATS
   ========================================================================== */
let STATS_RANGE = 7;
/* Growth indicator contract (§8): positive growth is ALWAYS visible and GREEN.
   A real % needs a previous period — when the user is too new for one, show
   the absolute gain in green instead of hiding the indicator. Never fabricate
   a percentage. */
function growthChip(pct, absolute) {
  if (pct != null) {
    // Exactly zero is "no change" — not growth. Showing it as a green ▲ up-arrow
    // (the old `pct >= 0` lumped 0 in with positive) falsely reads as progress.
    if (pct === 0) return `<span class="delta flat">± 0%</span>`;
    const up = pct > 0;
    return `<span class="delta ${up ? "up" : "down"}">${up ? "▲" : "▼"} ${Math.abs(pct)}%</span>`;
  }
  if (absolute > 0)
    return `<span class="delta up">▲ +${fmtNum(absolute)}</span>`;
  return "";
}

function readerEmptyTiles() {
  // Four empty/placeholder tiles for the Reader stats section when no data exists yet
  const tiles = [
    ["book",  "var(--mode-email)", "Reading time", "—", "lifetime"],
    ["type",  "var(--gold)",       "Words read",   "0", "lifetime"],
    ["check", "var(--green)",      "Completed",    "0", "documents"],
    ["fire",  "var(--amber)",      "Read streak",  "0d", "best 0 days"],
  ];
  return tiles
    .map(([ic, col, lab, val, trend]) =>
      `<div class="tile">
        <div class="tile-head"><span class="tile-ic" style="color:${col};background:color-mix(in srgb, ${col} 12%, transparent)">${svg(ic)}</span><span class="tile-lab">${esc(lab)}</span></div>
        <div class="num t-mute">${esc(val)}</div>
        <div class="tile-trend"><span class="tile-sub">${esc(trend)}</span></div>
      </div>`)
    .join("");
}

// Obvious, confirmed "Reset stats" (wired once). The button lives in
// Settings now, so this is callable from both the Settings and Stats render
// paths — the dataset guard keeps it idempotent.
function wireStatsReset() {
  const resetBtn = $("#stats-reset");
  if (!resetBtn || resetBtn.dataset.wired) return;
  resetBtn.dataset.wired = "1";
  resetBtn.addEventListener("click", async () => {
    const yes = await confirmModal({
      icon: "trash",
      title: "Reset all statistics?",
      body: "This permanently clears every dictation and reading statistic — totals, streaks, charts and history. This cannot be undone.",
      confirmText: "Reset everything",
    });
    if (!yes) return;
    const r = await call("reset_stats", "all");
    if (r && r.ok) { toast("Statistics reset", "ok"); renderStats(); }
    else toast("Couldn't reset statistics", "err");
  });
}

async function renderStats() {
  wireStatsReset();
  const [o, ins] = await Promise.all([
    call("get_overview"),
    call("get_insights"),
  ]);
  // Six tiles + meeting metric, UX-Pilot presentation: a coloured icon chip + uppercase label
  // above the value, with a trend line beneath. Every value real
  // (stats.json): no invented metrics, no "confidence"/accuracy %.
  var meetMinutes = o.total_meeting_minutes || 0;
  var meetMinDisplay = meetMinutes >= 60 ? (meetMinutes / 60).toFixed(1) + " h" : meetMinutes + " min";
  const tiles = [
    [
      "pen",
      "var(--gold)",
      "Total words",
      fmtNum(o.total_words),
      '<span class="tile-sub">lifetime</span>',
    ],
    [
      "mic",
      "var(--mode-email)",
      "Dictations",
      fmtNum(o.total_transcripts),
      '<span class="tile-sub">lifetime</span>',
    ],
    [
      "clock",
      "var(--green)",
      "Time saved",
      o.time_saved || "—",
      '<span class="tile-sub" title="Estimate: your total words ÷ 45 wpm (a typical typing speed). A rough guide, not a measurement.">≈ estimated vs typing <span style="opacity:.7">ⓘ</span></span>',
    ],
    [
      "bolt",
      "var(--gold)",
      "Words today",
      fmtNum(ins.today_words),
      growthChip(ins.today_delta_pct, ins.today_words) +
        '<span class="tile-sub"> vs yesterday</span>',
    ],
    [
      "gauge",
      "var(--mode-prompt)",
      "Avg speed",
      ins.avg_wpm ? ins.avg_wpm + " wpm" : "—",
      '<span class="tile-sub">speaking pace</span>',
    ],
    [
      "fire",
      "var(--amber)",
      "Streak",
      (o.current_streak || 0) + "d",
      `<span class="tile-sub">best ${o.best_streak || 0} days</span>`,
    ],
    [
      "mic",
      "var(--mode-context)",
      "Meetings",
      fmtNum(o.meeting_count || 0),
      '<span class="tile-sub">' + meetMinDisplay + ' recorded</span>',
    ],
  ];
  $("#stat-tiles").innerHTML = tiles
    .map(
      ([ic, col, lab, val, trend]) => `
    <div class="tile">
      <div class="tile-head"><span class="tile-ic" style="color:${col};background:color-mix(in srgb, ${col} 12%, transparent)">${svg(ic)}</span><span class="tile-lab">${esc(lab)}</span></div>
      <div class="num">${esc(val)}</div>
      <div class="tile-trend">${trend || ""}</div>
    </div>`,
    )
    .join("");
  // "When you dictate" — 24-hour buckets with peak hour callout.
  const hours = ins.tod_hours || new Array(24).fill(0);
  const hMax = Math.max(1, ...hours);
  const hTotal = hours.reduce((a, b) => a + b, 0);
  const peakHour = ins.tod_peak_hour != null ? ins.tod_peak_hour : (hTotal > 0 ? hours.indexOf(hMax) : -1);
  const hLabels = ["12a","","","3a","","","6a","","","9a","","","12p","","","3p","","","6p","","","9p","",""];
  const todChart = $("#tod-chart");
  todChart.style.gap = "2px";
  todChart.innerHTML = hours
    .map((w, i) => {
      const isPeak = i === peakHour;
      const h = Math.max(4, Math.round((w / hMax) * 66));
      const hourFmt = (i % 12 || 12) + (i < 12 ? "a" : "p");
      return `<div class="tod-col" title="${hourFmt}: ${fmtNum(w)} words">
      ${isPeak && w > 0 ? `<span class="tod-val">${fmtNum(w)}</span>` : `<span class="tod-val"></span>`}
      <div class="tod-bar ${isPeak ? "peak" : ""}" style="height:${h}px"></div>
      <span class="tod-name">${hLabels[i] || ""}</span></div>`;
    })
    .join("");
  if (hTotal > 0 && ins.tod_peak_label) {
    setText("#tod-peak", `peak ${ins.tod_peak_label}`);
    setText(
      "#tod-note",
      `You dictate most around ${ins.tod_peak_label} — ${ins.tod_peak_share}% of your words.`,
    );
  } else {
    setText("#tod-peak", "");
    setText(
      "#tod-note",
      "Dictate through the day and your rhythm appears here.",
    );
  }
  // This week — % when a previous week exists, green absolute gain when not
  const t = ins.trend_pct;
  const tEl = $("#ins-trend");
  if (t == null) {
    if (ins.this_week > 0) {
      tEl.textContent = "+" + fmtNum(ins.this_week);
      tEl.style.color = "var(--green)";
      setText(
        "#ins-trend-sub",
        "words this week — % trends unlock after your first full week",
      );
    } else {
      tEl.textContent = "—";
      tEl.style.color = "var(--text-dim)";
      setText("#ins-trend-sub", "dictate something to start your history");
    }
  } else {
    tEl.textContent = (t > 0 ? "+" : "") + t + "%";
    tEl.style.color = t >= 0 ? "var(--green)" : "var(--amber)";
    setText(
      "#ins-trend-sub",
      `${fmtNum(ins.this_week)} words vs ${fmtNum(ins.prev_week)} the week before`,
    );
  }
  // This month — same contract on the 30-day window
  const mEl = $("#ins-month");
  const mt = ins.month_trend_pct;
  if (mt == null) {
    if ((ins.month_this || 0) > 0) {
      mEl.textContent = "+" + fmtNum(ins.month_this);
      mEl.style.color = "var(--green)";
      setText(
        "#ins-month-sub",
        "words these 30 days — comparison unlocks next month",
      );
    } else {
      mEl.textContent = "—";
      mEl.style.color = "var(--text-dim)";
      setText("#ins-month-sub", "vs the previous 30 days");
    }
  } else {
    mEl.textContent = (mt > 0 ? "+" : "") + mt + "%";
    mEl.style.color = mt >= 0 ? "var(--green)" : "var(--amber)";
    setText(
      "#ins-month-sub",
      `${fmtNum(ins.month_this)} words vs ${fmtNum(ins.month_prev)} the 30 days before`,
    );
  }
  // Activity panel (right of the heatmap) — all real aggregates
  setText(
    "#act-busiest",
    ins.busiest_day
      ? `${ins.busiest_day} · ${fmtNum(ins.busiest_words || 0)}w`
      : "—",
  );
  setText(
    "#act-active",
    fmtNum(ins.active_days) + (ins.active_days === 1 ? " day" : " days"),
  );
  setText("#act-avg", fmtNum(ins.avg_per_active_day) + " words");
  const mins = ins.spoken_minutes || 0;
  setText(
    "#act-minutes",
    mins >= 90 ? (mins / 60).toFixed(1) + " h" : Math.round(mins) + " min",
  );
  setText(
    "#act-pace",
    ins.avg_wpm
      ? `${ins.avg_wpm} wpm · best ${Math.round(ins.best_wpm || ins.avg_wpm)}`
      : "—",
  );
  // weekday rhythm
  setText("#ins-peakday", ins.peak_weekday ? `peak: ${ins.peak_weekday}` : "");
  const wmax = Math.max(1, ...ins.weekday_words);
  $("#weekday-chart").innerHTML = ins.weekday_words
    .map((w, i) => {
      const h = Math.max(3, Math.round((w / wmax) * 54));
      const peak = ins.weekday_names[i] === ins.peak_weekday;
      return `<div class="wd-col" title="${esc(ins.weekday_names[i])}: ${fmtNum(w)} words">
      <div class="wd-bar ${peak ? "peak" : ""}" style="height:${h}px"></div>
      <span class="wd-name">${esc(ins.weekday_names[i][0])}</span></div>`;
    })
    .join("");
  await drawChart();
  await drawHeatmap();
  await drawModeBreakdown();

  // ── Reader statistics (reader-redesign milestone) ──────────────────────
  try {
    const rs = await call("get_reader_stats");
    if (rs && rs.total_sessions > 0) {
      const readerTiles = [
        ["book",  "var(--mode-email)", "Reading time", rs.total_reading_display || "—",
         '<span class="tile-sub">lifetime</span>'],
        ["type",  "var(--gold)",       "Words read",   fmtNum(rs.words_read || 0),
         '<span class="tile-sub">lifetime</span>'],
        ["check", "var(--green)",      "Completed",    fmtNum(rs.docs_completed || 0),
         '<span class="tile-sub">documents</span>'],
        ["fire",  "var(--amber)",      "Read streak",  (rs.current_streak || 0) + "d",
         '<span class="tile-sub">best ' + (rs.best_streak || 0) + ' days</span>'],
      ];
      $("#reader-stat-tiles").innerHTML = readerTiles
        .map(([ic, col, lab, val, trend]) =>
          `<div class="tile">
            <div class="tile-head"><span class="tile-ic" style="color:${col};background:color-mix(in srgb, ${col} 12%, transparent)">${svg(ic)}</span><span class="tile-lab">${esc(lab)}</span></div>
            <div class="num">${esc(val)}</div>
            <div class="tile-trend">${trend || ""}</div>
          </div>`)
        .join("");
      // Reading streak detail
      const streakLabel = (rs.current_streak || 0) > 0
        ? `You've read ${rs.current_streak} day${rs.current_streak === 1 ? "" : "s"} in a row`
        : "Start a reading streak — open a document and listen for a bit";
      setText("#reader-streak-label", streakLabel);
      setText("#reader-stats-help",
        "Reading stats are tracked independently from dictation — your dictation numbers are never affected by listening sessions.");
    } else {
      // No reader data yet — show empty / encouragement state
      $("#reader-stat-tiles").innerHTML = readerEmptyTiles();
      setText("#reader-streak-label", "Open a document in the Reader and listen for a few minutes to see your stats here.");
      setText("#reader-stats-help",
        "Reading stats are tracked independently from dictation — your dictation numbers are never affected by listening sessions.");
    }
  } catch (e) {
    // Reader stats unavailable (e.g. older stats.json without reader keys)
    $("#reader-stat-tiles").innerHTML = readerEmptyTiles();
    setText("#reader-streak-label", "Reading stats will appear after your first reading session.");
    setText("#reader-stats-help",
      "Reading stats are tracked independently from dictation — your dictation numbers are never affected by listening sessions.");
  }

  syncShimmer(); // phase-lock the Stats tiles/cards to the global shimmer cycle
}

/* 13-week activity heatmap — every cell is a real day from stats.json.
   CLEAN GRID contract (owner): fetch a little extra history and start the
   render on a Monday, so every column is a full Mon–Sun week — no offset
   ghost cells shifting the first rows sideways. Only the current, in-progress
   week may be short, at the far right where a partial column reads naturally. */
async function drawHeatmap() {
  const days = await call("get_daily_stats", 98);
  let start = 0;
  try {
    while (
      start < days.length &&
      new Date(days[start].day + "T00:00:00").getDay() !== 1
    )
      start++;
  } catch (e) {
    start = 0;
  }
  const aligned = days.slice(start);
  const max = Math.max(1, ...aligned.map((d) => d.words || 0));
  const weeks = [];
  for (let i = 0; i < aligned.length; i += 7)
    weeks.push(aligned.slice(i, i + 7));
  $("#heatmap").innerHTML = weeks
    .map((w) => {
      let cells = w
        .map((d) => {
          const r = (d.words || 0) / max;
          const lvl = d.words
            ? r > 0.75
              ? 4
              : r > 0.5
                ? 3
                : r > 0.25
                  ? 2
                  : 1
            : 0;
          return `<span class="hm-cell ${lvl ? "hm" + lvl : ""}" title="${esc(d.day)} — ${fmtNum(d.words)} words"></span>`;
        })
        .join("");
      // CLEAN GRID: pad the final, in-progress week up to a full 7 cells so every
      // column is the same height — no bottom-right hole (the reported "missing
      // square"). Pads are UPCOMING days this week, rendered as faint placeholders
      // (clearly "not yet", never mistaken for missing data).
      for (let k = w.length; k < 7; k++) {
        cells += `<span class="hm-cell hm-future" title="Upcoming"></span>`;
      }
      return `<div class="hm-col">${cells}</div>`;
    })
    .join("");
}

async function drawChart() {
  const days = await call("get_daily_stats", STATS_RANGE);
  const max = Math.max(1, ...days.map((d) => d.words || 0));
  const today = days.length - 1;
  // Y-axis: three real gridline values (max / half / 0) so the scale is
  // readable at a glance — the chart used to have no vertical reference at all
  const axis = `<div class="chart-axis">
      <span>${fmtNum(max)}</span><span>${fmtNum(Math.round(max / 2))}</span><span>0</span>
    </div>`;
  $("#stat-chart").innerHTML =
    axis +
    `<div class="chart-grid"><div></div><div></div><div></div></div>` +
    days
      .map((d, i) => {
        const h = Math.max(3, Math.round(((d.words || 0) / max) * 130));
        return `<div class="bar ${i === today ? "today" : ""}" style="height:${h}px" data-tip="${esc(d.day)} · ${fmtNum(d.words)} words · ${fmtNum(d.transcripts)} dictations"></div>`;
      })
      .join("");
  // INSTANT hover values — native title tooltips take ~1s to appear; this
  // tracks the pointer and shows the value with zero delay
  const chart = $("#stat-chart");
  let tip = $("#chart-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "chart-tip";
    tip.hidden = true;
    chart.parentElement.appendChild(tip);
  }
  $$("#stat-chart .bar").forEach((b) => {
    b.onmouseenter = () => {
      tip.textContent = b.dataset.tip;
      tip.hidden = false;
      const cr = chart.parentElement.getBoundingClientRect(),
        br = b.getBoundingClientRect();
      tip.style.left =
        Math.max(
          6,
          Math.min(cr.width - 150, br.left - cr.left + br.width / 2 - 70),
        ) + "px";
      tip.style.top = br.top - cr.top - 34 + "px";
    };
    b.onmouseleave = () => {
      tip.hidden = true;
    };
  });
  // axis labels: weekday letters at 7d, sparse dates beyond
  $("#chart-labels").innerHTML =
    '<span class="cl" style="flex:0 0 34px"></span>' +
    days
      .map((d, i) => {
        let lab = "";
        if (STATS_RANGE === 7) {
          try {
            lab = ["M", "T", "W", "T", "F", "S", "S"][
              new Date(d.day + "T00:00:00").getDay() === 0
                ? 6
                : new Date(d.day + "T00:00:00").getDay() - 1
            ];
          } catch (e) {}
        } else if (i % Math.ceil(days.length / 6) === 0)
          lab = (d.day || "").slice(5);
        return `<span class="cl">${esc(lab)}</span>`;
      })
      .join("");
  setText("#chart-max", fmtNum(max) + " max");
}
async function drawModeBreakdown() {
  // UX-Pilot presentation: glow dot + mode name, words + SHARE % on the
  // right, bars animate in. Plain Text still gets its own scale (it dwarfs
  // the Smart Modes), and the % is each mode's share of ALL dictations.
  const modes = (await call("get_mode_stats")).filter(
    (m) => m.mode !== "convert" && m.mode !== "reply",
  );
  const total = Math.max(
    1,
    modes.reduce((a, m) => a + (m.count || 0), 0),
  );
  const bar = (m, max, di) => {
    const c = MODE_COLORS[m.mode] || "var(--gold)";
    const w = Math.round(((m.count || 0) / max) * 100);
    const share = Math.round(((m.count || 0) / total) * 100);
    return `<div class="modebar-row">
      <span class="mode-dot" style="background:${c};box-shadow:0 0 5px color-mix(in srgb, ${c} 60%, transparent)"></span>
      <span class="name" style="color:${c}">${esc(m.mode)}</span>
      <div class="meter"><div class="fill grow" style="--bar-w:${w}%;background:${c};animation-delay:${di * 60}ms"></div></div>
      <span class="val">${fmtNum(m.words)}w · <b class="t-dim">${share}%</b></span></div>`;
  };
  const text = modes.filter((m) => m.mode === "text");
  const smart = modes.filter((m) => m.mode !== "text");
  let html = "",
    di = 0;
  if (text.length) {
    html += text
      .map((m) => bar(m, Math.max(1, text[0].count || 0), di++))
      .join("");
    if (smart.length)
      html +=
        '<div class="flex items-center gap8 mt4 mb4"><div class="divider flex-1"></div><span class="t-mute fs10 uppercase">Smart Modes</span><div class="divider flex-1"></div></div>';
  }
  const smax = Math.max(1, ...smart.map((m) => m.count || 0));
  html += smart.map((m) => bar(m, smax, di++)).join("");
  $("#mode-breakdown").innerHTML =
    html || '<div class="empty">Dictate something to see your mode mix.</div>';
}
function setStatsRange(r) {
  STATS_RANGE = r;
  $$("#stat-range button").forEach((b) =>
    b.classList.toggle("active", +b.dataset.r === r),
  );
  drawChart();
}

/* ============================================================================
   SETTINGS
   ========================================================================== */
let SET = {};
/* API key fields you can actually inspect: each *_api_key input gets an eye toggle
   that reveals the real stored key on demand (fetched only on click), and a masked
   already-saved field clears itself the moment you type a replacement — so a saved
   key is verifiable AND never silently overwritten by its own mask. */
function wireKeyFields() {
  $$('input[data-setting$="_api_key"]').forEach((inp) => {
    if (inp.dataset.keywired) return;
    inp.dataset.keywired = "1";
    const field = inp.closest(".field") || inp.parentElement;
    if (field) field.classList.add("has-reveal");
    inp.addEventListener("focus", () => {
      if (inp.dataset.masked === "1") { inp.value = ""; inp.dataset.masked = ""; }
    });
    inp.addEventListener("input", () => { inp.dataset.masked = ""; });
    const eye = document.createElement("button");
    eye.type = "button";
    eye.className = "key-reveal";
    eye.title = "Show / hide the saved key";
    eye.innerHTML = svg("eye");
    eye.addEventListener("click", async () => {
      if (inp.type === "password") {
        if (inp.dataset.masked === "1") {
          const real = await call("reveal_setting", inp.dataset.setting);
          if (typeof real === "string" && real) inp.value = real;
          inp.dataset.masked = "";
        }
        inp.type = "text";
        eye.innerHTML = svg("eye-off");
      } else {
        inp.type = "password";
        eye.innerHTML = svg("eye");
      }
    });
    (field || inp.parentElement).appendChild(eye);
  });
}

let SETTINGS_HYDRATION_VERSION = 0;
const SETTINGS_MUTATION_VERSION = Object.create(null);

function setSettingsHydrationState(state, message) {
  const view = $('[data-view="settings"]');
  if (!view) return;
  view.dataset.settingsState = state;
  view.setAttribute("aria-busy", state === "loading" ? "true" : "false");
  const title = $("#settings-hydration-title");
  const copy = $("#settings-hydration-copy");
  const retry = $("#settings-hydration-retry");
  if (state === "loading") {
    if (title) title.textContent = "Loading your saved settings…";
    if (copy) copy.textContent = "Controls will appear when Mumble has confirmed the saved and effective setup.";
  } else if (state === "error") {
    if (title) title.textContent = "Settings could not be read";
    if (copy) copy.textContent = message || "Your existing configuration was not changed. Try again when the app is ready.";
  }
  if (retry) {
    retry.hidden = state !== "error";
    if (!retry.dataset.wired) {
      retry.dataset.wired = "1";
      retry.addEventListener("click", () => hydrateSettings());
    }
  }
  const selector = 'button,input,select,textarea,a[href],[role="button"],[tabindex]';
  if (!view.dataset.hydrationGuardWired) {
    view.dataset.hydrationGuardWired = "1";
    const block = (event) => {
      const action = event.target.closest?.(selector);
      if (view.dataset.settingsState !== "ready" && action && action !== retry) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    };
    ["click", "change", "input", "keydown"].forEach((type) => view.addEventListener(type, block, true));
  }
  $$(selector, view).forEach((control) => {
    if (control === retry) return;
    if (state !== "ready" && control.dataset.hydrationGuarded !== "1") {
      control.dataset.hydrationGuarded = "1";
      control.dataset.hydrationTabIndex = control.getAttribute("tabindex") ?? "";
      control.dataset.hydrationAriaDisabled = control.getAttribute("aria-disabled") ?? "";
      if ("disabled" in control) {
        control.dataset.hydrationWasDisabled = control.disabled ? "1" : "0";
        control.disabled = true;
      }
      control.setAttribute("aria-disabled", "true");
      control.tabIndex = -1;
    } else if (state === "ready" && control.dataset.hydrationGuarded === "1") {
      if ("disabled" in control && control.dataset.hydrationWasDisabled === "0") control.disabled = false;
      const oldTab = control.dataset.hydrationTabIndex;
      if (oldTab === "") control.removeAttribute("tabindex"); else control.setAttribute("tabindex", oldTab);
      const oldAria = control.dataset.hydrationAriaDisabled;
      if (oldAria === "") control.removeAttribute("aria-disabled"); else control.setAttribute("aria-disabled", oldAria);
      ["hydrationGuarded", "hydrationTabIndex", "hydrationAriaDisabled", "hydrationWasDisabled"].forEach((key) => delete control.dataset[key]);
    }
  });
}

async function hydrateSettings() {
  const requestId = ++SETTINGS_HYDRATION_VERSION;
  setSettingsHydrationState("loading");
  try {
    const loaded = await call("get_settings");
    if (requestId !== SETTINGS_HYDRATION_VERSION) return false;
    if (!loaded || typeof loaded !== "object") throw new Error("No settings returned");
    SET = loaded;
  } catch (e) {
    if (requestId !== SETTINGS_HYDRATION_VERSION) return false;
    setSettingsHydrationState(
      "error",
      "Your existing configuration was not changed. Try again when the app is ready.",
    );
    toast("Settings could not be loaded. Your existing configuration was not changed.", "err", 4200);
    return false;
  }
  // generic [data-setting] binding
  $$("[data-setting]").forEach((el) => {
    const key = el.dataset.setting;
    const val = nested(SET, key);
    if (el.type === "checkbox") el.checked = !!val;
    else if (
      el.tagName === "SELECT" ||
      el.tagName === "INPUT" ||
      el.tagName === "TEXTAREA"
    )
      el.value = val == null ? "" : val;
    // API keys arrive from get_settings as a MASKED preview (bullets), never the
    // real key. Flag the field so an untouched mask is never re-saved over the real
    // key and the reveal toggle knows to fetch the real bytes on demand.
    if (key && key.endsWith("_api_key")) el.dataset.masked = val ? "1" : "";
    if (!el.dataset.wired) {
      el.dataset.wired = "1";
      // 'change' for EVERY control (the old no-op ternary resolved to 'change'
      // both ways). This is deliberate, not a leftover: text inputs (name, API
      // keys, models, URLs, numbers) must save on blur — NOT 'input', which
      // would persist + ping the controller on every keystroke and store
      // half-typed keys. Checkboxes/selects have no 'input' event anyway.
      const ev = "change";
      el.addEventListener(ev, async () => {
        const previous = nested(SET, key);
        let v = el.type === "checkbox" ? el.checked : el.value;
        const mutationId = (SETTINGS_MUTATION_VERSION[key] || 0) + 1;
        SETTINGS_MUTATION_VERSION[key] = mutationId;
        if (el.dataset.type === "number") v = Number(v);
        // Never write an untouched masked key back over the real one.
        if (el.dataset.masked === "1" || (typeof v === "string" && v.indexOf("•") >= 0)) return;
        let r;
        try {
          r = await call("set_setting", key, v);
        } catch (e) {
          r = { ok: false, message: (e && e.message) || "Couldn't save this setting" };
        }
        if (mutationId !== SETTINGS_MUTATION_VERSION[key]) return;
        if (!r || r.ok === false) {
          // Keep the control and in-memory settings honest when validation or
          // persistence fails. Previously the UI flashed "Saved" and adopted a
          // value the backend had explicitly rejected.
          if (el.type === "checkbox") el.checked = !!previous;
          else el.value = previous == null ? "" : previous;
          const message = (r && r.message) || "Couldn't save this setting";
          if (el.dataset.feedback) flash($(el.dataset.feedback), message, "err");
          else toast(message, "err", 2600);
          return;
        }
        setNested(SET, key, v);
        confirmSaved(el, key, v, r);
        if (el.dataset.feedback) flash($(el.dataset.feedback), "Saved", "ok");
        if (key === "llm_provider") { reflectProvider(); updateSetupSummary(); }
        if (
          key === "transcription_mode" ||
          key === "cloud_transcription_provider"
        )
          reflectCloudStt();
        if (key === "ui_effects") applyEffects(v);
        if (key === "resource_saver") applySaver(v);
        // Update the at-a-glance summary when relevant hardware / AI settings change.
        if (["pro_mode", "english_only", "model", "device"].includes(key)) {
          updateSetupSummary();
        }
        // A freshly-entered API key → fetch THAT provider's real model list so the
        // dropdown fills with valid ids (the core "enter key, then pick a model" flow).
        if (key.endsWith("_api_key")) {
          const pr = key.slice(0, -8);
          populateModels(pr, true);
          // One OpenRouter key, shared everywhere OpenRouter is used.
          if (key === "openrouter_api_key") syncOpenRouterKey(v);
        }
        if ([
          "transcription_mode", "cloud_transcription_provider",
          "local_only_mode", "pro_mode", "llm_provider", "instant_text",
          "cerebras_api_key", "openrouter_api_key", "groq_api_key",
          "openai_api_key", "anthropic_api_key", "deepseek_api_key",
          "local_llm_enabled", "local_llm_model",
        ].includes(key)) await refreshRouteState();
      });
    }
  });
  wireKeyFields();
  // mic list (populated separately from the generic binding)
  let mics;
  try {
    mics = await call("list_microphones");
    if (!Array.isArray(mics)) throw new Error("No microphone list returned");
  } catch (e) {
    if (requestId !== SETTINGS_HYDRATION_VERSION) return false;
    setSettingsHydrationState(
      "error",
      "Your saved settings were read, but microphones could not be checked. Try again when audio devices are ready.",
    );
    toast("Microphones could not be checked. Your settings were not changed.", "err", 4200);
    return false;
  }
  if (requestId !== SETTINGS_HYDRATION_VERSION) return false;
  const msel = $("#set-mic");
  if (msel) {
    msel.innerHTML = mics
      .map((m) => `<option value="${m.index}">${esc(m.name)}</option>`)
      .join("");
    msel.value = SET.mic_device == null ? -1 : SET.mic_device;
    if (!msel.dataset.wired) {
      msel.dataset.wired = "1";
      msel.addEventListener("change", () => {
        const v = +msel.value;
        call("set_setting", "mic_device", v < 0 ? null : v);
        SET.mic_device = v < 0 ? null : v;
      });
    }
  }
  // vocabulary
  const vt = $("#set-vocab");
  if (vt) {
    const lines = [].concat(SET.vocabulary_terms || []);
    Object.entries(SET.vocabulary || {}).forEach(([k, v]) =>
      lines.push(k + " = " + v),
    );
    vt.value = lines.join("\n");
  }
  // model options follow the chosen language (en ⇄ multilingual sets)
  syncModelOptions(false);
  // Foreign-mode language pills (owner §6)
  hydrateForeignLangs();
  // On-device (offline) AI engine status badge + help
  hydrateLocalLlm();
  // prompt key field + provider reflect
  reflectProvider();
  // advanced cloud-transcription section (mode + provider field visibility)
  reflectCloudStt();
  // presets adder
  renderPresetAdder();
  // pretty hotkey labels
  [
    "hotkey",
    "quick_paste_hotkey",
    "history_hotkey",
    "search_hotkey",
  ].forEach(async (k) => {
    const lab = $(`[data-keylabel="${k}"]`);
    if (lab) {
      const p = await call("pretty_binding", SET[k] || "");
      lab.textContent = (p && p.pretty) || SET[k] || "—";
    }
  });
  // Account card (cloud sync)
  hydrateAccountCard();
  if (requestId !== SETTINGS_HYDRATION_VERSION) return false;
  setSettingsHydrationState("ready");
  return true;
}
function nested(o, path) {
  return path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
}
function setNested(o, path, v) {
  const ks = path.split(".");
  const last = ks.pop();
  let t = o;
  ks.forEach((k) => {
    t[k] = t[k] || {};
    t = t[k];
  });
  t[last] = v;
}
function flash(el, msg, kind) {
  if (!el) return;
  el.textContent = msg;
  el.className = "feedback " + kind;
  setTimeout(() => {
    el.textContent = "";
  }, 2200);
}

/* §11 — every saved setting confirms itself: the row flashes a green ring,
   and consequential choices also get a toast that says whether the running
   app took the change live (set_setting → controller reload) or it waits for
   the next start. The user never has to wonder "did that apply?". */
const BIG_SETTINGS = {
  model: (v) => "Model → " + v,
  llm_provider: (v) => "AI provider → " + v,
  search_engine: (v) => "Search engine → " + v,
  browser: (v) => "Browser → " + (v === "default" ? "system default" : v),
  language: (v) => "Transcription language → " + v,
  ui_effects: (v) => "Visual effects → " + v,
};
function confirmSaved(el, key, v, r) {
  // ring the CONTROL itself (select/input/textarea, or the switch capsule for
  // checkboxes) — ringing the whole row floated above the target and looked
  // broken (owner report)
  const tag = el.tagName;
  const ring =
    tag === "SELECT" || tag === "INPUT" || tag === "TEXTAREA"
      ? el.closest(".switch") || el
      : el;
  if (ring) {
    ring.classList.remove("just-saved");
    void ring.offsetWidth;
    ring.classList.add("just-saved");
    setTimeout(() => ring.classList.remove("just-saved"), 1400);
  }
  const big = BIG_SETTINGS[key];
  if (big) {
    const live = r && r.applied;
    const tail =
      key === "model"
        ? live
          ? " — loading in the background, ready in a moment"
          : " — applies when Mumble starts"
        : live
          ? " — applied"
          : " — saved";
    toast(big(v) + tail, "ok", 2400);
  }
  // the model list follows the language (owner spec: never expose irrelevant
  // options) — English shows the fast .en models, anything else shows the
  // multilingual ones, and an invalid combination auto-corrects
  if (key === "language") syncModelOptions(true);
  // Show/hide the "high-end CPU" warning the moment a Maximum Accuracy tier is
  // picked (or cleared).
  if (key === "model") updateMaxAccuracyWarn(v);
}

/* faster-whisper genuinely supports multilingual transcription — but only on
   the multilingual models; the ".en" builds are English-only and IGNORE the
   language setting. So the model dropdown adapts to the language and a stale
   choice is swapped for its closest equivalent automatically. */
const MODEL_SETS = {
  en: [
    ["tiny.en", "Fast — Tiny"],
    ["base.en", "Balanced — Base"],
    ["small.en", "Accurate — Small"],
    ["medium.en", "Maximum Accuracy — Medium"],
  ],
  multi: [
    ["base", "Balanced — Base (multilingual)"],
    ["small", "Accurate — Small (multilingual)"],
    ["large-v3", "Maximum Accuracy — Large v3 (multilingual)"],
  ],
};
// The "Maximum Accuracy" tiers are heavy (≈1.5 GB+) and slow on a weak CPU, so
// surface a warning whenever one is selected (owner 2026-06-20).
const MAX_ACCURACY_MODELS = ["medium.en", "large-v3", "large-v2", "large"];
function updateMaxAccuracyWarn(model) {
  const warn = document.getElementById("max-accuracy-warn");
  if (warn) warn.hidden = !MAX_ACCURACY_MODELS.includes(model);
}
function syncModelOptions(announce) {
  const sel = $('[data-setting="model"]');
  if (!sel) return;
  const en = (SET.language || "en") === "en";
  const set = en ? MODEL_SETS.en : MODEL_SETS.multi;
  sel.innerHTML = set
    .map(([v, l]) => `<option value="${v}">${esc(l)}</option>`)
    .join("");
  let m = SET.model || (en ? "small.en" : "small");
  if (!set.some(([v]) => v === m)) {
    m = en ? m.replace(/\.en$/, "") + ".en" : m.replace(/\.en$/, "");
    if (!set.some(([v]) => v === m)) m = en ? "small.en" : "small";
    SET.model = m;
    call("set_setting", "model", m);
    if (announce)
      toast(
        "Model switched to “" + m + "” to match the language",
        "info",
        3200,
      );
  }
  sel.value = m;
  updateMaxAccuracyWarn(m);
}

// Foreign-mode languages (owner §6): multi-select pills, Arabic default. Saved
// as a list to settings.foreign_languages; the foreign AI lane prioritises them.
// On-device (offline) AI engine: fill the status badge + help line from the
// independently-computed backend status (a GGUF in the models folder + the
// bundled binary). Pure status read — never blocks the settings render.
async function hydrateLocalLlm() {
  const badge = document.getElementById("local-llm-status");
  const help = document.getElementById("local-llm-help");
  if (!badge && !help) return;
  let st = null;
  try {
    st = await call("local_llm_status");
  } catch (e) {
    st = null;
  }
  if (!st) {
    if (badge) badge.textContent = "—";
    return;
  }
  if (badge) {
    if (!st.enabled) {
      badge.textContent = "Off";
      badge.style.color = "var(--text-mute)";
    } else if (st.ready) {
      badge.textContent = "Active";
      badge.style.color = "var(--green)";
    } else {
      badge.textContent = "No model";
      badge.style.color = "var(--text-mute)";
    }
  }
  if (help) {
    if (st.ready && st.model) {
      help.innerHTML =
        "Running on-device — model: <code>" +
        esc(st.model.split(/[\\/]/).pop()) +
        "</code>.";
    } else if (st.models_dir) {
      const bin = st.has_binary
        ? ""
        : " <span class=\"t-amber\">(bundled engine not found in this build)</span>";
      help.innerHTML =
        "Drop a <code>.gguf</code> model into <code>" +
        esc(st.models_dir) +
        "</code> to enable offline AI shaping." +
        bin;
    }
  }
}

function hydrateForeignLangs() {
  const wrap = document.getElementById("foreign-langs");
  if (!wrap) return;
  const sel = new Set(
    (SET.foreign_languages || ["arabic"]).map((x) => String(x).toLowerCase()),
  );
  Array.from(wrap.querySelectorAll(".lang-chip")).forEach((b) => {
    b.classList.toggle("on", sel.has(b.dataset.lang));
    b.onclick = () => {
      b.classList.toggle("on");
      const picked = Array.from(wrap.querySelectorAll(".lang-chip.on")).map(
        (x) => x.dataset.lang,
      );
      SET.foreign_languages = picked;
      call("set_setting", "foreign_languages", picked);
    };
  });
}

/* Cloud Account card — Supabase auth (register / sign in / sign out).
   Session tokens are stored in a separate .session file, NEVER in
   plaintext settings.json. The Account card lives in the Settings view. */
async function hydrateAccountCard() {
  const form = $("#account-auth-form");
  const signedIn = $("#account-signed-in");
  const loading = $("#account-loading");
  const credsBlock = $("#account-credentials-block");
  const statusLabel = $("#account-status-label");
  if (!form || !signedIn || !statusLabel) return;

  // 1. Check session state
  const sess = await call("cloud_session");
  const isSignedIn = sess && sess.user;

  if (isSignedIn) {
    form.hidden = true;
    signedIn.hidden = false;
    if (credsBlock) credsBlock.hidden = true;
    if (statusLabel) { statusLabel.textContent = "signed in"; statusLabel.className = "t-green fs11 ml-auto"; }
    const emailEl = $("#account-user-email");
    if (emailEl) emailEl.textContent = String(sess.user && (sess.user.email || sess.user.id || "\u2014"));
    const expires = $("#account-session-expires");
    if (expires && sess.session && sess.session.expires_at) {
      try {
        expires.textContent = "Session expires " + new Date(sess.session.expires_at * 1000).toLocaleString();
      } catch (_) { expires.textContent = ""; }
    } else if (expires) {
      expires.textContent = "";
    }
    hydrateSyncToggles();
    return;
  }

  // 2. Not signed in \u2014 check credentials
  const hasUrl = (SET.supabase_url || "").trim();
  const hasKey = (SET.supabase_anon_key || "").trim();

  if (!hasUrl || !hasKey) {
    // Show credentials block, hide auth form
    if (credsBlock) credsBlock.hidden = false;
    form.hidden = true;
    signedIn.hidden = true;
    if (statusLabel) { statusLabel.textContent = "not configured"; statusLabel.className = "t-mute fs11 ml-auto"; }
    return;
  }

  // 3. Credentials set but not signed in \u2014 show auth form
  if (credsBlock) credsBlock.hidden = true;
  form.hidden = false;
  signedIn.hidden = true;
  if (statusLabel) { statusLabel.textContent = "not signed in"; statusLabel.className = "t-dim fs11 ml-auto"; }
}

// ---- Account card event handlers (wired once on first load) ----
function wireAccountCard() {
  const saveBtn = $("#account-save-creds");
  const loginBtn = $("#account-login-btn");
  const registerBtn = $("#account-register-btn");
  const logoutBtn = $("#account-logout-btn");

  if (saveBtn && !saveBtn.dataset.wired) {
    saveBtn.dataset.wired = "1";
    saveBtn.addEventListener("click", async () => {
      const url = ($('[data-setting="supabase_url"]')?.value || "").trim();
      const key = ($('[data-setting="supabase_anon_key"]')?.value || "").trim();
      if (!url || !key) {
        flash($("#account-creds-fb"), "Both fields are required.", "err");
        return;
      }
      const r = await call("cloud_configure", url, key);
      if (r && r.ok) {
        flash($("#account-creds-fb"), "Saved. You can now sign in.", "ok");
        await hydrateAccountCard();
      } else {
        flash($("#account-creds-fb"), (r && r.message) || "Failed to save.", "err");
      }
    });
  }

  if (loginBtn && !loginBtn.dataset.wired) {
    loginBtn.dataset.wired = "1";
    loginBtn.addEventListener("click", async () => {
      const email = ($("#account-email")?.value || "").trim();
      const password = ($("#account-password")?.value || "").trim();
      if (!email || !password) {
        flash($("#account-auth-fb"), "Email and password are required.", "err");
        return;
      }
      const r = await call("cloud_sign_in", email, password);
      if (r && r.ok) {
        flash($("#account-auth-fb"), "Signed in.", "ok");
        // Auto-start background sync
        call("cloud_sync_start_bg", 30);
        await hydrateAccountCard();
      } else {
        flash($("#account-auth-fb"), (r && r.message) || "Sign in failed.", "err");
      }
    });
  }

  if (registerBtn && !registerBtn.dataset.wired) {
    registerBtn.dataset.wired = "1";
    registerBtn.addEventListener("click", async () => {
      const email = ($("#account-email")?.value || "").trim();
      const password = ($("#account-password")?.value || "").trim();
      if (!email || !password) {
        flash($("#account-auth-fb"), "Email and password are required.", "err");
        return;
      }
      const r = await call("cloud_sign_up", email, password);
      if (r && r.ok) {
        if (r.message) {
          flash($("#account-auth-fb"), r.message, "ok");
        } else {
          flash($("#account-auth-fb"), "Account created. You can now sign in.", "ok");
        }
        await hydrateAccountCard();
      } else {
        flash($("#account-auth-fb"), (r && r.message) || "Registration failed.", "err");
      }
    });
  }

  if (logoutBtn && !logoutBtn.dataset.wired) {
    logoutBtn.dataset.wired = "1";
    logoutBtn.addEventListener("click", async () => {
      // Stop background sync before sign-out (Python side also stops, this is belt-and-suspenders)
      await call("cloud_sync_stop_bg");
      const r = await call("cloud_sign_out");
      if (r && r.ok) {
        flash($("#account-auth-fb"), "Signed out.", "ok");
        const em = $("#account-email");
        const pw = $("#account-password");
        if (em) em.value = "";
        if (pw) pw.value = "";
        await hydrateAccountCard();
      } else {
        flash($("#account-auth-fb"), (r && r.message) || "Sign out failed.", "err");
      }
    });
  }
  // Sync-now button
  const syncNowBtn = $("#account-sync-now");
  if (syncNowBtn && !syncNowBtn.dataset.wired) {
    syncNowBtn.dataset.wired = "1";
    syncNowBtn.addEventListener("click", syncNow);
  }
}

/* ── Sync toggles: per-data-type enable/disable + Sync-now button ──── */
const SYNC_TYPES = [
  { key: "settings", label: "Settings", desc: "App preferences (API keys stay local)" },
  { key: "stats", label: "Statistics", desc: "Dictation & reading stats" },
  { key: "history", label: "History", desc: "Recent 100 transcripts" },
  { key: "reader", label: "Reader library", desc: "Documents & reading progress" },
  { key: "favorites", label: "Favorites", desc: "Starred items" },
  { key: "presets", label: "Custom presets", desc: "Your 5 custom presets" },
];

function hydrateSyncToggles() {
  const list = $("#sync-toggles-list");
  if (!list) return;
  let html = "";
  SYNC_TYPES.forEach(function (st) {
    const checked = SET["sync_" + st.key] !== false ? " checked" : "";
    html += '<label class="flex items-center gap8" style="cursor:pointer;padding:4px 0">' +
      '<input type="checkbox" data-sync-toggle="' + st.key + '"' + checked + ' style="accent-color:var(--gold)" />' +
      '<span class="fs12 fw5">' + esc(st.label) + '</span>' +
      '<span class="t-mute fs10 ml-auto">' + esc(st.desc) + '</span>' +
      '</label>';
  });
  list.innerHTML = html;
  wireSyncToggles();
}

function wireSyncToggles() {
  $$("[data-sync-toggle]").forEach(function (el) {
    if (el.dataset.syncWired) return;
    el.dataset.syncWired = "1";
    el.addEventListener("change", async function () {
      const key = "sync_" + el.dataset.syncToggle;
      SET[key] = el.checked;
      if (HAS_PY()) await call("set_setting", key, el.checked);
    });
  });
}

async function syncNow() {
  const fb = $("#account-sync-fb");
  if (!HAS_PY()) {
    flash(fb, "Sync works in the live app.", "info");
    return;
  }
  flash(fb, "Syncing…", "busy");
  try {
    const r = await call("cloud_sync_all");
    if (r && r.ok) {
      flash(fb, "Synced.", "ok");
    } else {
      flash(fb, (r && r.message) || "Sync failed.", "err");
    }
  } catch (e) {
    flash(fb, "Sync error: " + (e.message || e), "err");
  }
}

/* ── End sync toggles ──────────────────────────────────────────────── */

/* Known-good model ids per provider (verified 2026-06-14). These power the model
   fields' autocomplete (a <datalist> each) so users pick a valid id instead of
   typing a dash-laden name from memory — the #1 cause of "it says connected but
   nothing works". Free text is still allowed (local models, brand-new ids), so the
   list is a guide, not a cage. OpenRouter ids are namespaced (provider/model) —
   the full live list lives at openrouter.ai/models. */
const MODELS = {
  cerebras: ["gpt-oss-120b", "zai-glm-4.7"],
  openrouter: [
    "openai/gpt-5.4-mini",
    "anthropic/claude-opus-4-8",
    "deepseek/deepseek-v4-flash",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
  ],
};
/* Short, CONSISTENT one-line provider descriptions (owner v10 — they used to be
   inconsistent: only OpenRouter had a tagline). Shown under the provider picker. */
const PROVIDER_DESC = {
  cerebras: "Fastest, free tier — the recommended default.",
  openrouter: "One key, many models.",
};

/* Live model DROPDOWN (owner v9/v10 — replaces free-text model entry). When a key
   is saved we fetch the provider's real model list (once per session, or on demand)
   so the user PICKS a valid id; otherwise we fall back to the curated MODELS list.
   The saved value is always kept selectable so nothing is ever lost. */
const MODELS_FETCHED = {};
async function fillModelSelect(sel, provider, force) {
  if (!sel) return false;
  provider = (provider || "").toLowerCase();
  const saved = nested(SET, provider + "_model") || "";
  let models = (MODELS[provider] || []).slice();
  let live = false;
  const hasKey = !!String(nested(SET, provider + "_api_key") || "").trim();
  if (HAS_PY() && hasKey && (force || !MODELS_FETCHED[provider])) {
    try {
      const r = await call("list_models", provider);
      if (r && r.ok && Array.isArray(r.models) && r.models.length) {
        models = r.models;
        live = true;
        MODELS_FETCHED[provider] = true;
      }
    } catch (_) {
      /* keep the fallback list */
    }
  }
  if (saved && !models.includes(saved)) models.unshift(saved);
  sel.innerHTML = models
    .map((m) => `<option value="${esc(m)}">${esc(m)}</option>`)
    .join("");
  // If the saved value is not in the curated/live list, prefer the first model
  // so old/deprecated ids (e.g. deepseek-chat) never silently stay selected.
  sel.value = models.includes(saved) ? saved : models[0] || "";
  return live;
}
/* Populate the MAIN AI Provider card's model select for a provider. */
function populateModels(provider, force) {
  return fillModelSelect(
    document.querySelector(`select[data-model-select="${provider}"]`),
    provider,
    force,
  );
}

/* Curated CLOUD TRANSCRIPTION (STT) models per provider — a small set of the
   best ids, recommended one first (owner 2026-06-20: STT providers should offer
   a curated choice like the LLM dropdowns, not a blank text box). There is no
   /audio/models discovery endpoint, so these are static; the user's saved value
   is always kept selectable so an advanced override is never lost. [id, label]. */
const STT_MODELS = {
  groq: [
    ["whisper-large-v3-turbo", "Turbo — fastest (recommended)"],
    ["whisper-large-v3", "Large v3 — most accurate"],
    ["distil-whisper-large-v3-en", "Distil — fastest, English only"],
  ],
  openai: [
    ["gpt-4o-mini-transcribe", "4o-mini — fast (recommended)"],
    ["gpt-4o-transcribe", "4o — most accurate"],
    ["whisper-1", "Whisper-1 — legacy"],
  ],
  openrouter: [
    ["openai/gpt-4o-mini-transcribe", "4o-mini (recommended)"],
    ["openai/gpt-4o-transcribe", "4o — most accurate"],
    ["groq/whisper-large-v3-turbo", "Groq Turbo — fastest"],
  ],
};
/* Fill one STT model <select data-stt-select="provider">, keeping any saved
   custom value selectable. */
function fillSttSelect(provider) {
  const sel = document.querySelector(`select[data-stt-select="${provider}"]`);
  if (!sel) return;
  const saved = String(SET[provider + "_transcription_model"] || "").trim();
  const list = (STT_MODELS[provider] || []).slice();
  if (saved && !list.some(([v]) => v === saved))
    list.unshift([saved, saved + " (custom)"]);
  sel.innerHTML = list
    .map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`)
    .join("");
  sel.value = list.some(([v]) => v === saved) ? saved : list[0]?.[0] || "";
}

function reflectProvider() {
  const p = SET.llm_provider || "cerebras";
  $$("[data-provider-field]").forEach(
    (f) => (f.hidden = f.dataset.providerField !== p),
  );
  $$("[data-provider-only]").forEach((f) => {
    f.hidden = !f.dataset.providerOnly.split(",").includes(p);
  });
  const sel = $("#set-provider");
  if (sel) sel.value = p;
  const desc = $("#provider-desc");
  if (desc) desc.textContent = PROVIDER_DESC[p] || "";
  populateModels(p); // fill (and, if a key is saved, live-fetch) the model list
}

/* Advanced cloud-transcription section: show the provider/key/model fields only
   when Cloud mode is selected, and only the chosen provider's key+model row. */
function reflectCloudStt() {
  const mode = SET.transcription_mode || "local";
  const fields = $("#cloud-stt-fields");
  if (fields) fields.hidden = mode !== "cloud";
  const p = SET.cloud_transcription_provider || "groq";
  $$("[data-tx-field]").forEach(
    (el) => (el.hidden = el.dataset.txField !== p),
  );
  // Fill the curated STT model dropdowns (saved value kept selectable).
  ["groq", "openai", "openrouter"].forEach(fillSttSelect);
  const provSel = $("#set-tx-provider");
  if (provSel) provSel.value = p;
  // Show the right explanation text for the selected mode.
  const localDesc = $("#tx-mode-local-desc");
  const cloudDesc = $("#tx-mode-cloud-desc");
  if (localDesc) localDesc.hidden = mode !== "local";
  if (cloudDesc) cloudDesc.hidden = mode !== "cloud";
  // Update the at-a-glance summary.
  updateSetupSummary();
}

/* At-a-glance "Your Processing Setup" card — shows the user exactly what is
   running and where, without expanding any Advanced/details elements. Called
   from reflectCloudStt (on settings load + transcription-mode change) and from
   the main settings change handler for AI provider / hardware / model changes. */
function updateSetupSummary() {
  const route = SET._route_state || {};
  const transcription = route.transcription || {};
  const action = route.action_processing || {};
  const provNames = { cerebras: "Cerebras", openai: "OpenAI", anthropic: "Claude",
                      openrouter: "OpenRouter", deepseek: "DeepSeek", groq: "Groq", local: "Local model" };
  // Transcription mode
  const sumTx = $("#sum-tx-mode");
  if (sumTx) {
    const mode = transcription.effective || SET.transcription_mode || "local";
    sumTx.textContent = mode === "cloud" ? "Cloud" : "Local (private)";
    sumTx.className = "setup-value " + (mode === "cloud" ? "t-amber" : "t-green");
  }
  // Saved provider and effective text-shaping route
  const sumProv = $("#sum-provider");
  if (sumProv) {
    const prov = action.provider || SET.llm_provider || "cerebras";
    const effective = action.effective === "cloud" ? "Hosted" : "On this device";
    sumProv.textContent = effective + " · " + (provNames[prov] || prov);
    sumProv.className = "setup-value " + (action.effective === "cloud" ? "t-gold" : "t-mute");
  }
  // Loaded model
  const sumModel = $("#sum-model");
  if (sumModel) {
    sumModel.textContent = SET.model || "—";
    sumModel.className = "setup-value t-dim fs11";
  }
  const decision = action.decision || {};
  const provider = provNames[action.provider] || action.provider || "No provider";
  const effectiveHosted = action.effective === "cloud";
  const reason = action.reason || "checking";
  setText("#processing-route-title", effectiveHosted ? `Hosted text shaping · ${provider}` : `On-device text shaping · ${reason.replaceAll("_", " ")}`);
  setText("#processing-route-copy", effectiveHosted
    ? `Transcript text may be sent to ${provider}; microphone audio never uses this route.`
    : "Transcript text stays on this Mac for shaping. The saved hosted choice remains visible below.");
  setText("#route-fact-saved", SET.pro_mode ? `Hosted text processing · ${provider}${decision.model ? ` · ${decision.model}` : ""}` : "On-device text shaping");
  setText("#route-fact-effective", effectiveHosted ? `Hosted · ready (${provider})` : `On this Mac · ${reason.replaceAll("_", " ")}`);
  setText("#route-fact-engine", effectiveHosted ? `Transcript text · ${provider}${decision.model ? ` · ${decision.model}` : ""}` : "Transcript text · local shaping pipeline");
  setText("#route-fact-location", effectiveHosted ? `${provider} hosted service` : "This Mac");
  setText("#route-fact-egress", effectiveHosted ? "Transcript text and selected context; never microphone audio on this route" : "Nothing for text shaping");
  setText("#route-fact-tradeoff", effectiveHosted ? "Network and provider affect speed and quality. Provider use may cost money." : "No hosted-provider charge. Speed and quality depend on this Mac and its local engine.");
  const routeStatus = $("#processing-route-status");
  if (routeStatus) routeStatus.dataset.route = effectiveHosted ? "cloud" : (reason === "no_key" ? "warning" : "local");
}

async function refreshRouteState() {
  try {
    const fresh = await call("get_settings");
    if (fresh && fresh._route_state) SET._route_state = fresh._route_state;
  } catch (_) {
    // Keep the last confirmed route; the save path already reports failures.
  }
  updateSetupSummary();
}

/* ONE OpenRouter key, shared everywhere OpenRouter is selected (main provider,
   prompting provider, Reader). Enter it once and mirror it into every OpenRouter
   key input in the DOM + SET, so the user never re-types it. The backend already
   stores a single openrouter_api_key — this is presentation sync. */
function syncOpenRouterKey(value) {
  const v = (value || "").trim();
  SET.openrouter_api_key = v;
  document
    .querySelectorAll(
      "input[data-setting='openrouter_api_key'], #reader-key-input",
    )
    .forEach((el) => {
      if (el.value !== v) el.value = v;
    });
}

/* capture a key/mouse binding */
async function captureBinding(key, labelSel, fb) {
  flash($(fb), "Press a key or mouse button…", "busy");
  const res = await call("capture_binding", 15);
  if (res && res.spec) {
    // Every remaining binding is a single-PRESS hotkey where a lone modifier
    // (Ctrl / Win / Alt / Shift) would fire on every press — the 'Ctrl alone
    // starts Mumble' bug. Reject it here with a clear message rather than saving
    // a value the controller will just heal away. (The Big Shift removed the only
    // HOLD binding, the mode key.)
    {
      const v = await call("validate_binding", res.spec, false);
      if (v && v.ok === false) {
        flash($(fb), v.message || "That key can't be used on its own.", "err");
        return;
      }
    }
    const r = await call("set_setting", key, res.spec);
    SET[key] = res.spec;
    const lab = $(labelSel);
    if (lab) lab.textContent = res.pretty || res.spec;
    // honest confirmation: did the running controller re-register it live?
    flash(
      $(fb),
      r && r.applied
        ? "Saved & active now — " + (res.pretty || res.spec)
        : "Saved — " + (res.pretty || res.spec),
      "ok",
    );
    bootHome(); // the Home widget + record button show the new binding at once
  } else flash($(fb), "Nothing captured", "err");
}

/* test API key */
async function testKey(provider, keySel, fb) {
  const key = $(keySel).value.trim();
  flash($(fb), "Testing…", "busy");
  const r = await call("test_key", provider, key);
  flash($(fb), r.message, r.ok ? "ok" : "err");
  await refreshRouteState();
}
async function testMic(fb) {
  const idx = +($("#set-mic")?.value ?? -1);
  flash($(fb), "Listening 5s…", "busy");
  const r = await call("test_mic", idx);
  flash($(fb), r.message, r.ok ? "ok" : "err");
}

/* vocabulary save */
async function saveVocab(fb) {
  const raw = $("#set-vocab").value;
  const terms = [],
    pairs = {};
  raw
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .forEach((l) => {
      if (l.includes("=")) {
        const [a, b] = l.split("=");
        pairs[a.trim()] = b.trim();
      } else terms.push(l);
    });
  const result = await call(
    "save_vocabulary",
    terms,
    pairs,
    Array.isArray(SET.vocabulary_terms) ? SET.vocabulary_terms.slice() : [],
    Object.assign({}, SET.vocabulary || {}),
  );
  if (!result || !result.ok) {
    flash($(fb), (result && result.message) || "Vocabulary could not be saved", "err");
    return;
  }
  const savedTerms = Array.isArray(result.vocabulary_terms)
    ? result.vocabulary_terms : terms;
  const savedPairs = result.vocabulary || pairs;
  SET.vocabulary_terms = savedTerms;
  SET.vocabulary = savedPairs;
  $("#set-vocab").value = savedTerms
    .concat(Object.keys(savedPairs).map((key) => key + " = " + savedPairs[key]))
    .join("\n");
  flash(
    $(fb),
    savedTerms.length +
      " terms + " +
      Object.keys(savedPairs).length +
      " exact pairs saved",
    "ok",
  );
}

/* preset adder — FIVE custom slots after the built-ins (owner 2026-06-20: 20
   built-in + 5 custom = 25). The built-in count + slot base are DERIVED from
   get_presets (the is-builtin flag), so this never hard-codes a slot number. */
const N_CUSTOM = 5;
let N_BUILTIN = 20;
let CUSTOM = [];
async function renderPresetAdder() {
  const all = await call("get_presets");
  N_BUILTIN = all.filter((p) => p[3]).length || N_BUILTIN;
  CUSTOM = all
    .filter((p) => !p[3])
    .map((p) => ({
      slot: p[0],
      title: p[1],
      description: p[2],
      instruction: p[4] || "",
    }));
  while (CUSTOM.length < N_CUSTOM)
    CUSTOM.push({
      slot: N_BUILTIN + 1 + CUSTOM.length,
      title: "",
      description: "",
      instruction: "",
    });
  const host = $("#preset-adder");
  if (!host) return;
  host.innerHTML = CUSTOM.slice(0, N_CUSTOM)
    .map(
      (c, i) => `
    <div class="flex gap8 items-start" data-prow="${i}">
      <input class="input" style="flex:0 0 130px" placeholder="Custom ${i + 1} title" data-pf="title" value="${esc(c.title)}">
      <input class="input" style="flex:0 0 180px" placeholder="One-line description" data-pf="description" value="${esc(c.description)}">
      <input class="input flex-1" placeholder="Instruction sent to the AI" data-pf="instruction" value="${esc(c.instruction)}">
      <button class="btn-icon btn-ghost t-mute" data-pclear title="Clear">${svg("x")}</button>
    </div>`,
    )
    .join("");
  paintIcons(host);
  $$("[data-pclear]", host).forEach(
    (b, i) =>
      (b.onclick = () => {
        $$(`[data-prow="${i}"] input`, host).forEach((inp) => (inp.value = ""));
      }),
  );
  setText(
    "#preset-count",
    CUSTOM.filter((c) => c.title).length + " / " + N_CUSTOM + " custom presets",
  );
}
async function savePresets(fb) {
  const rows = $$("#preset-adder [data-prow]")
    .map((row, i) => ({
      slot: N_BUILTIN + 1 + i,
      title: $('[data-pf="title"]', row).value.trim(),
      description: $('[data-pf="description"]', row).value.trim(),
      instruction: $('[data-pf="instruction"]', row).value.trim(),
    }))
    .filter((r) => r.title && r.instruction);
  await call("save_presets", rows);
  flash(
    $(fb),
    rows.length +
      " custom preset" +
      (rows.length === 1 ? "" : "s") +
      " saved — now in the Deck",
    "ok",
  );
  setText("#preset-count", rows.length + " / " + N_CUSTOM + " custom presets");
}

/* THE DECK was absorbed into History (owner v4): its Smart Mode + Presets
   + ordered multi-select + Merge/Run now live in the HISTORY HUB section
   above. The standalone Deck overlay and its JS were removed. */

/* ============================================================================
   UPDATE CENTER — UX-Pilot port: version block, live check stages, honest
   result states, release notes when the feed provides them
   ========================================================================== */
let updateDialogDeactivate = null;
async function openUpdateCenter() {
  const ov = $("#update-center");
  if (!ov) return;
  ov.hidden = false;
  if (updateDialogDeactivate) updateDialogDeactivate();
  updateDialogDeactivate = activateDialog(ov, closeUpdateCenter);
  paintIcons(ov);
  const o = await call("get_overview");
  setText("#uc-current", "v" + o.version);
  setText("#uc-status-title", "Checking for updates…");
  setText("#uc-status-sub", "Contacting the release feed");
  $("#uc-stage-check").className = "uc-stage busy";
  $("#uc-stage-result").className = "uc-stage";
  $("#uc-notes").hidden = true;
  $("#uc-spinner").hidden = false;
  const t0 = Date.now();
  const r = await call("check_updates");
  // a check that resolves instantly reads as fake — let the stage breathe
  await new Promise((res) => {
    setTimeout(res, Math.max(0, 700 - (Date.now() - t0)));
  });
  $("#uc-spinner").hidden = true;
  $("#uc-stage-check").className = "uc-stage done";
  const stamp = new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  setText("#uc-checked", "Last checked " + stamp);
  if (r.status === "available") {
    $("#uc-stage-result").className = "uc-stage done";
    setText("#uc-status-title", "Version " + r.version + " is available");
    setText(
      "#uc-status-sub",
      "Mumble will offer to install it from the tray — your settings and history are kept.",
    );
    setText("#uc-available", "v" + r.version);
    $("#uc-available-row").hidden = false;
    if (r.notes) {
      $("#uc-notes").hidden = false;
      setText("#uc-notes-body", r.notes);
    }
  } else if (r.status === "disabled") {
    $("#uc-stage-result").className = "uc-stage";
    setText("#uc-status-title", "Signed updates are not available yet");
    setText("#uc-status-sub", r.message || "The update channel is disabled.");
    $("#uc-available-row").hidden = true;
  } else if (r.status === "uptodate") {
    $("#uc-stage-result").className = "uc-stage done";
    setText("#uc-status-title", "You're up to date");
    setText(
      "#uc-status-sub",
      r.message || "This is the latest version of Mumble.",
    );
    $("#uc-available-row").hidden = true;
  } else {
    $("#uc-stage-result").className = "uc-stage fail";
    setText("#uc-status-title", "Couldn't check for updates");
    setText(
      "#uc-status-sub",
      r.message ||
        "The release feed did not answer — try again later. Mumble keeps working normally.",
    );
    $("#uc-available-row").hidden = true;
  }
}
function closeUpdateCenter() {
  const ov = $("#update-center");
  if (ov) ov.hidden = true;
  if (updateDialogDeactivate) updateDialogDeactivate();
  updateDialogDeactivate = null;
}

/* ============================================================================
   ONBOARDING WIZARD  (§5.0)
   ========================================================================== */
let OB = { step: 1, total: 9, lang: "en" };
function obShow(n) {
  OB.step = Math.max(1, Math.min(OB.total, n));
  $$("#onboarding .ob-panel").forEach(
    (p) => (p.hidden = +p.dataset.step !== OB.step),
  );
  // progress
  $("#ob-progress").innerHTML = Array.from({ length: OB.total }, (_, i) => {
    const s = i + 1;
    const cls = s === OB.step ? "active" : s < OB.step ? "done" : "";
    const line =
      i < OB.total - 1
        ? `<div class="ob-step-line ${s < OB.step ? "done" : ""}"></div>`
        : "";
    return `<div class="ob-step-dot ${cls}">${s < OB.step ? svg("check") : s}</div>${line}`;
  }).join("");
  paintIcons($("#ob-progress"));
  // nav buttons
  const last = OB.step === OB.total;
  $("#ob-back").style.visibility = OB.step === 1 ? "hidden" : "visible";
  $("#ob-next").hidden = last;
  $("#ob-finish").hidden = !last;
  setText("#ob-next-label", OB.step === 1 ? "Get started" : "Next");
}
function obNext() {
  if (OB.step >= OB.total) return finishOnboarding();
  obShow(OB.step + 1);
}
function obBack() {
  obShow(OB.step - 1);
}
async function obTestKey() {
  const key = $("#ob-key").value.trim();
  const prov = $("#ob-provider")?.value || OB.provider || "cerebras";
  flash($("#ob-key-fb"), "Testing…", "busy");
  // test_key SAVES the key first, then validates
  const keySetting = prov + "_api_key";
  await call("set_setting", keySetting, key);
  const r = await call("test_key", prov, key);
  if (r.ok) {
    OB.keyOk = true;
    OB.keyProv = prov;
    const recap = $("#ob-key-recap");
    if (recap)
      recap.innerHTML =
        '<span class="dot none" style="background:var(--green)"></span>' +
        (prov === "cerebras" ? "Cerebras" : "OpenRouter") + " connected";
  }
  flash($("#ob-key-fb"), r.message, r.ok ? "ok" : "err");
  await refreshRouteState();
}
async function finishOnboarding() {
  const fx = $("#ob-effects .active")?.dataset.fx || "enhanced";
  // Save name
  const name = $("#ob-name")?.value?.trim() || "";
  if (name) {
    await call("set_setting", "user_name", name);
    SET.user_name = name;
  }
  // Save transcription mode
  const txMode = $("#ob-tx-mode .active")?.dataset.tx || "local";
  await call("set_setting", "transcription_mode", txMode);
  SET.transcription_mode = txMode;
  // Save provider choice (key was already saved by obTestKey)
  const provider = $("#ob-provider")?.value || "cerebras";
  await call("set_setting", "llm_provider", provider);
  SET.llm_provider = provider;
  // Persist the language choice; local model sizing is automatic.
  const lang = $("#ob-lang .active")?.dataset.lang || OB.lang || "en";
  const englishOnly = lang === "en";
  // Primary language drives the model swap (branding.model_for_language): "en" →
  // the lean .en model; any other → multilingual. "other" → blank (multilingual,
  // no specific id). English-only always records "en".
  let primary = "en";
  if (!englishOnly) {
    primary = $("#ob-primary-lang")?.value || "es";
    if (primary === "other") primary = "";
  }
  await call("set_setting", "english_only", englishOnly);
  await call("set_setting", "primary_language", primary);
  await call("finish_onboarding", {
    autostart: !!$("#ob-autostart")?.checked,
    ui_effects: fx,
    english_only: englishOnly,
  });
  await refreshRouteState();
  // The app bundle already lives in Applications on macOS; only the
  // LaunchAgent choice needs applying here.
  call("apply_shortcuts", {
    autostart: !!$("#ob-autostart")?.checked, // apply the run-at-login choice NOW, not only next launch
  });
  applyEffects(fx);
  $("#onboarding").hidden = true;
  try { call("set_onboarding_mode", false); } catch (e) {}  // restore sticky Deck pin
  toast("Welcome to Mumble — press Ctrl + Option + D to dictate", "ok", 3600);
  navTo("home");
}

/* ============================================================================
   GLOBAL KEYS + BOOT
   ========================================================================== */
function wireGlobalKeys() {
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const uc = $("#update-center");
      if (uc && !uc.hidden) closeUpdateCenter();
    }
    // F11 — fullscreen (native pywebview toggle in the app; browser API in preview)
    if (e.key === "F11") {
      e.preventDefault();
      if (HAS_PY())
        call("toggle_fullscreen").catch((e) =>
          console.error("F11 toggle failed:", e),
        );
      else
        (document.fullscreenElement
          ? document.exitFullscreen()
          : document.documentElement.requestFullscreen()
        ).catch(() => {});
    }
    // Ctrl+Option+H opens HISTORY — in the browser preview only; in the live app the
    // OS-global History hotkey routes through the controller → {"cmd":"history"} →
    // openHistory() (intercepting here too would double-fire). Ctrl+Option+V is now a
    // pure paste-latest action handled entirely by the controller, so the preview
    // no longer maps it to anything.
    if (
      !HAS_PY() &&
      e.ctrlKey &&
      e.altKey &&
      (e.key === "h" || e.key === "H")
    ) {
      e.preventDefault();
      openHistory(true);
    }
    // Ctrl+H (no Alt), inside the History view, runs a Smart Mode / preset on the
    // HOVERED item — hover acts as implicit selection (owner 2026-06-20). Distinct
    // from the global Ctrl+Option+H (open History).
    if (
      e.ctrlKey &&
      !e.altKey &&
      !e.shiftKey &&
      (e.key === "h" || e.key === "H") &&
      CURRENT === "history"
    ) {
      e.preventDefault();
      runHoverJob();
    }
  });
}

/* Responsive layout is CSS-driven. This compatibility hook remains because boot
   and resize already call it, and it also clears zoom persisted by an older UI. */
function applyResponsiveScale() {
  // The remastered interface reflows through CSS breakpoints. Scaling the entire
  // document made 11–13px copy physically unreadable in narrow windows and kept
  // desktop column counts long after they stopped fitting.
  document.documentElement.style.zoom = "";
}

/* ============================================================================
   READER — document-to-audio via cloud TTS (owner 2026-06-20, rebuilt)
   Speaks through the configured OpenRouter or OpenAI voice provider. Keeps a
   LIBRARY of opened documents and resumes
   exactly where you left off. Text is synthesized chunk-by-chunk (sentence-
   aligned), played through an <audio> element, and the current word is
   highlighted by interpolating the chunk's audio time across its words. Speed is
   the browser playbackRate (instant, free, lets chunk audio be cached).
   ========================================================================== */
const READER = {
  inited: false,
  docId: null,
  title: "",
  plain: "",
  words: [], // [{start,end}] char offsets of each word in `plain`
  chunks: [], // [{wStart,wEnd,text}] sentence-aligned TTS chunks
  chunkIdx: 0,
  idx: 0, // current word index = the reading position (persisted)
  state: "idle", // idle | playing | paused
  playerOpen: false, // document pane visibility is independent of playback state
  audio: null,
  gen: 0, // bumped on every play/pause/stop/jump/reload/open — async chunk
          // loads that started under an older gen abort instead of clobbering
          // the new playback (prevents two clips playing at once).
  cache: {}, // chunkIdx -> { url } | null
  fetching: {}, // chunkIdx -> one shared in-flight synthesis Promise
  provider: "openrouter", // active TTS provider id
  ttsProviders: [], // [{id, label, auth_setting, has_key, default_model}]
  model: "google/gemini-3.1-flash-tts-preview",
  effectiveProvider: null, // session-only fallback selection
  effectiveModel: null,
  effectiveVoice: null,
  ttsModels: [],
  ttsVoices: [], // flat voice catalogue (male-first, high-quality, with tags)
  hasKey: false,
  bookmarks: [], // [{pos,label,ts}] for the currently-open document
  starred: false, // is the open document starred
  libQuery: "", // library search filter text
  libStarred: false, // library "starred only" filter
  libTab: "all", // all | collections | history
  libAll: null, // cached doc list from reader_list()
  collectionsCache: null, // cached collection list
  historyCache: null, // cached reading history
  continueDoc: null, // continue-reading doc
  fontScale: 1, // reading-pane font scale (A- / A+)
  sleepTimer: null, // setTimeout handle for the sleep timer
  // variables for reading session tracking
  sessionStart: 0, // timestamp when current play session began
  sessionStartPos: 0, // word index when current play session began
  sessionElapsedMs: 0, // actual playing time; excludes synthesis/buffering gaps
  playStartedAt: 0, // wall-clock start of the current audio-playing segment
  // TTS synthesis progress tracking
  synthStart: 0, // timestamp when synthesis began (for wait estimate)
  synthDone: 0, // chunks synthesised so far in this generation
  synthTotal: 0, // total chunks in the document
  synthTimes: [], // recent chunk fetch durations (ms) for wait estimate
  fallbackUsed: false, // true if a fallback provider was transparently used
  disclosure: { tts: false, summary: false },
};

function readerCurrentVoice() {
  // First try the dropdown for models with a known voice list.
  const vsel = $("#reader-voice");
  if (vsel && !vsel.hidden && vsel.value) return vsel.value;
  // For models without a known voice list, use the free-text field.
  const custom = $("#reader-voice-custom");
  if (custom && !custom.hidden) return (custom.value || "").trim();
  // Fallback: pick the first voice for the current model.
  const modelVoices = READER.ttsVoices.filter(function (v) {
    return v.model === READER.model;
  });
  if (modelVoices.length) return modelVoices[0].id;
  return "";
}

async function confirmReaderCloudUse(kind) {
  if (READER.disclosure[kind]) return true;
  const provider = kind === "tts" ? (READER.provider || "the voice provider") :
    ((SET && SET.llm_provider) || "your AI provider");
  const body = kind === "tts"
    ? `Reader voice sends each short passage to ${provider} to create audio. If one voice model is unavailable, another compatible model from that same provider may be tried. Reader Sync, when enabled, also stores your library in your account.`
    : `Summarize sends the document text to ${provider}. Do not continue with confidential material unless you are comfortable sharing it with that provider.`;
  const ok = await confirmModal({ icon: "shield", title: "Send document text?",
    body, confirmText: "Continue", danger: false });
  if (ok) READER.disclosure[kind] = true;
  return ok;
}

function readerBuildPane(text) {
  // Hard cap on document size. The Reader keeps one <span> per word in the DOM
  // (for highlight sync) and one ~400-char TTS chunk per passage; CSS
  // content-visibility keeps scrolling smooth, but a truly unbounded document
  // would still balloon the node count and the chunk list. Cap at ~100k words
  // (roughly 400 pages / 11 hours at 150 wpm). New imports are rejected above
  // this limit; this fallback keeps legacy oversized library rows safe to open.
  let plain = String(text || "");
  READER.truncated = false;
  if (plain.length > MAX_READER_TEXT_CHARS) {
    // Cut back to the last space before the cap so a word is never split.
    let cut = plain.lastIndexOf(" ", MAX_READER_TEXT_CHARS);
    if (cut < MAX_READER_TEXT_CHARS * 0.9) cut = MAX_READER_TEXT_CHARS; // no nearby space — hard cut
    plain = plain.slice(0, cut);
    READER.truncated = true;
  }
  READER.plain = plain;
  READER.words = [];
  const pane = $("#reader-pane");
  if (!pane) return;
  // Build spans in chunks of ~300 words each wrapped in <div class="rw-chunk">.
  // CSS content-visibility:auto on each chunk lets the browser skip layout and
  // paint for off-screen chunks so 100k+ word documents scroll without jank,
  // while all spans stay in the DOM for TTS word highlighting.
  const CHUNK_WORDS = 300;
  let html = "",
    last = 0,
    wi = 0,
    chunkOpen = false,
    wordsInChunk = 0;
  const re = /\S+/g;
  let m;
  while ((m = re.exec(READER.plain))) {
    if (!chunkOpen) { html += '<div class="rw-chunk">'; chunkOpen = true; }
    html += esc(READER.plain.slice(last, m.index));
    READER.words.push({ start: m.index, end: m.index + m[0].length });
    html += `<span class="rw" data-wi="${wi}">${esc(m[0])}</span>`;
    last = m.index + m[0].length;
    wi++;
    wordsInChunk++;
    if (wordsInChunk >= CHUNK_WORDS) { html += '</div>'; chunkOpen = false; wordsInChunk = 0; }
  }
  html += esc(READER.plain.slice(last));
  if (chunkOpen) html += '</div>';
  pane.innerHTML = html;
  // Click any word to jump there.
  $$(".rw", pane).forEach(
    (el) => (el.onclick = () => readerJumpTo(+el.dataset.wi)),
  );
}

/* Render parsed document blocks without changing the canonical speech stream.
   Every visible source word still gets its global data-wi, so headings, lists,
   quotes, code, and table cells retain click-to-seek and live highlighting. */
function readerBuildBlocks(blocks, canonicalText) {
  var pane = $("#reader-pane");
  if (!pane || !blocks || !blocks.length) return;
  // Python stores this exact block-derived stream and computes resume/bookmark
  // positions from it. Never independently rebuild a punctuation-heavy TTS
  // variant here or the browser and store will count different words.
  readerBuildPane(canonicalText || "");
  var wordIndex = 0;

  function wordHtml(value) {
    if (wordIndex >= READER.words.length) return "";
    var source = String(value == null ? "" : value);
    var out = "";
    var last = 0;
    var re = /\S+/g;
    var match;
    while ((match = re.exec(source))) {
      if (wordIndex >= READER.words.length) break;
      out += esc(source.slice(last, match.index));
      // Use the canonical token, not the display source token. They are equal
      // for current documents; this also keeps a hard 600k-char truncation from
      // ever rendering/speaking a partial word differently.
      var word = READER.words[wordIndex];
      var visible = READER.plain.slice(word.start, word.end);
      out += '<span class="rw" data-wi="' + wordIndex + '">' + esc(visible) + '</span>';
      wordIndex++;
      last = match.index + match[0].length;
    }
    if (wordIndex < READER.words.length) out += esc(source.slice(last));
    return out;
  }

  var html = "";
  blocks.forEach(function (block) {
    var type = (block.type || "").toLowerCase();
    var text = block.text || "";
    if (type === "table") {
      var meta = block.meta || {};
      var headers = meta.headers || [];
      var rows = meta.rows || [];
      var tbl = '<div class="rw-table-wrap"><table class="rw-table" role="table">';
      if (headers.length) {
        tbl += '<thead><tr>';
        headers.forEach(function (h) { tbl += '<th scope="col">' + wordHtml(h) + '</th>'; });
        tbl += '</tr></thead>';
      }
      tbl += '<tbody>';
      rows.forEach(function (row) {
        tbl += '<tr>';
        (row || []).forEach(function (cell) {
          tbl += '<td>' + wordHtml(cell) + '</td>';
        });
        tbl += '</tr>';
      });
      tbl += '</tbody></table>';
      if (meta.truncated) {
        tbl += '<p class="rw-truncated">Showing first ' + rows.length + ' rows — data was truncated.</p>';
      }
      tbl += '</div>';
      html += tbl;
    } else if (type === "heading") {
      var level = Math.max(1, Math.min(6, parseInt(block.level, 10) || 1));
      html += "<h" + level + ' class="rw-heading">' + wordHtml(text) + "</h" + level + ">";
    } else if (type === "list_item") {
      var indent = Math.max(0, (parseInt(block.level, 10) || 1) - 1);
      var marker = block.meta && block.meta.ordered
        ? String(parseInt(block.meta.order, 10) || 1) + "."
        : "•";
      html += '<div class="rw-list-item" style="--rw-list-level:' + indent + '"><span class="rw-list-marker" aria-hidden="true">' + marker + '</span><span>' + wordHtml(text) + "</span></div>";
    } else if (type === "blockquote") {
      html += '<blockquote class="rw-blockquote">' + wordHtml(text) + "</blockquote>";
    } else if (type === "code") {
      html += '<pre class="rw-code"><code>' + wordHtml(text) + "</code></pre>";
    } else if (type === "horizontal_rule" || type === "break") {
      html += '<hr class="rw-break" />';
    } else if (type !== "image") {
      var body = wordHtml(text);
      if (body) html += '<p class="rw-paragraph">' + body + "</p>";
    }
  });

  pane.innerHTML = html;
  $$(".rw", pane).forEach(
    (el) => (el.onclick = () => readerJumpTo(+el.dataset.wi)),
  );
}

function readerBuildChunks() {
  // Group words into ~400-char chunks, ending at sentence punctuation where
  // possible. Smaller chunks = finer resume granularity, tighter word-highlight
  // sync, and faster first audio. (Cost is per-character, so more chunks ≈ same
  // spend.)
  READER.chunks = [];
  const w = READER.words;
  const MAXC = 400;
  let start = 0;
  while (start < w.length) {
    const cstart = w[start].start;
    let j = start;
    while (j < w.length) {
      const charlen = w[j].end - cstart;
      const wtext = READER.plain.slice(w[j].start, w[j].end);
      const sentenceEnd = /[.!?]["')\]]?$/.test(wtext);
      j++;
      if (charlen >= MAXC && (sentenceEnd || charlen >= MAXC * 1.5)) break;
    }
    READER.chunks.push({
      wStart: start,
      wEnd: j,
      text: READER.plain.slice(w[start].start, w[j - 1].end),
    });
    start = j;
  }
}

function readerChunkForWord(wi) {
  if (!READER.chunks.length) return 0;
  for (let i = 0; i < READER.chunks.length; i++) {
    if (wi >= READER.chunks[i].wStart && wi < READER.chunks[i].wEnd) return i;
  }
  // Out of range: a negative index → first chunk; anything at/past the end
  // (e.g. idx === words.length, the "finished" sentinel) → the LAST chunk.
  // Never fall back to 0 for a past-end index, or resuming a finished/boundary
  // position would silently restart the document from the top.
  return wi < 0 ? 0 : READER.chunks.length - 1;
}

function readerHighlight(wi) {
  const pane = $("#reader-pane");
  if (!pane) return;
  const prev = $(".rw.current", pane);
  if (prev) prev.classList.remove("current");
  const el = pane.querySelector(`.rw[data-wi="${wi}"]`);
  if (el) {
    el.classList.add("current");
    // PERF: keep the spoken word in view WITHOUT thrashing layout. We use
    // getBoundingClientRect to get viewport-relative positions (robust across
    // nested .rw-chunk wrappers), but only scroll when the word drifts out of
    // a comfort band to avoid re-triggering a smooth scroll mid-animation.
    const paneRect = pane.getBoundingClientRect();
    const elRect = el.getBoundingClientRect();
    const top = elRect.top - paneRect.top + pane.scrollTop;
    const band = pane.clientHeight;
    const rel = elRect.top - paneRect.top;
    if (rel < band * 0.15 || rel > band * 0.78)
      pane.scrollTo({ top: Math.max(0, top - band / 2), behavior: "smooth" });
  }
  READER.idx = wi;
  readerUpdateProgress();
}

function readerUpdateProgress() {
  const n = READER.words.length;
  // idx is the 0-based current word; progress is "words completed so far" =
  // idx + 1, so the bar reaches 100% on the final word instead of stalling at
  // (n-1)/n (e.g. "97% • word 32 of 32"). Matches the 1-based word counter.
  const completed = n
    ? Math.min(n, Math.max(0, READER.idx >= n ? n : READER.idx + 1))
    : 0;
  const pct = n ? Math.round((100 * completed) / n) : 0;
  setText("#reader-progress", n ? `${pct}% • word ${completed} of ${n}` : "");
}

function readerUpdateSynthProgress() {
  // Show synthesis progress: how many chunks have been fetched and an expected
  // wait estimate for the remaining chunks. Only updates while the Reader is
  // playing/paused (not idle).
  if (READER.state === "idle") return;
  var done = READER.synthDone;
  var total = READER.synthTotal;
  if (!total || done > total) return;
  // Estimate wait from the rolling average chunk time.
  var waitSec = 0;
  if (READER.synthTimes.length) {
    var sum = 0;
    for (var i = 0; i < READER.synthTimes.length; i++) sum += READER.synthTimes[i];
    var avgMs = sum / READER.synthTimes.length;
    var remaining = Math.max(0, total - done);
    waitSec = Math.round((remaining * avgMs) / 1000);
  }
  var waitStr =
    waitSec > 0 ? " \u2022 ~" + waitSec + "s left" : "";
  setText(
    "#reader-status",
    "\u25b6 Synthesising voice\u2026 (" +
      done +
      " of " +
      total +
      " parts)" +
      waitStr,
  );
}

function readerSetPlayIcon() {
  const btn = $("#reader-playpause");
  if (btn) btn.innerHTML = svg(READER.state === "playing" ? "pause" : "play");
}
function readerSetStatus(msg) {
  setText("#reader-status", msg || "");
}

function readerSavePosition(wi) {
  if (READER.docId && HAS_PY())
    call("reader_position", READER.docId, wi).catch(() => {});
}

const MAX_READER_AUDIO_CACHE_CHUNKS = 24;

function readerPruneAudioCache(around) {
  var keys = Object.keys(READER.cache).map(Number).filter(Number.isFinite);
  if (keys.length <= MAX_READER_AUDIO_CACHE_CHUNKS) return;
  keys.sort(function (a, b) {
    return Math.abs(a - around) - Math.abs(b - around);
  });
  keys.slice(MAX_READER_AUDIO_CACHE_CHUNKS).forEach(function (key) {
    delete READER.cache[key];
  });
}

async function readerFetchChunk(ci) {
  if (ci < 0 || ci >= READER.chunks.length) return null;
  if (READER.cache[ci]) return READER.cache[ci];
  // Prefetch and the onended path often ask for the same next chunk at the
  // same time. Share one request so a race cannot double the provider charge.
  if (READER.fetching[ci]) return READER.fetching[ci];

  var cacheEpoch = READER.cache;
  var fetchingEpoch = READER.fetching;
  var request = (async function () {
    var t0 = Date.now();
    try {
      const r = await call(
      "reader_tts",
      READER.chunks[ci].text,
      READER.effectiveModel || READER.model,
      READER.effectiveVoice || readerCurrentVoice(),
      READER.effectiveProvider || READER.provider,
      );
    // Track synthesis timing for wait estimates.
    var dt = Date.now() - t0;
    READER.synthTimes.push(dt);
    if (READER.synthTimes.length > 8) READER.synthTimes.shift(); // rolling window
    var active = READER.cache === cacheEpoch;
    if (active && r && r.fallback) {
      READER.fallbackUsed = true;
      READER.effectiveProvider = r.provider || READER.provider;
      READER.effectiveModel = r.model || null;
      READER.effectiveVoice = r.voice || null;
      // Light toast the FIRST time we fall back so the user knows why the
      // voice sounds different.
      if (!READER._fallbackToasted) {
        READER._fallbackToasted = true;
        var fbLabel = r.fallback_provider || "another provider";
        toast(
          "The primary voice provider didn\u2019t respond \u2014 using " +
            fbLabel + " instead.",
          "warn",
          4000,
        );
      }
    }
    if (!r || !r.ok)
      return { error: (r && r.message) || "The voice service didn't respond." };
    const obj = { url: `data:${r.mime || "audio/mpeg"};base64,${r.audio}` };
    // A voice/model/document change replaces the cache object. Never let an
    // older in-flight request repopulate the new cache with stale audio.
    if (active) {
      READER.cache[ci] = obj;
      readerPruneAudioCache(READER.chunkIdx);
      READER.synthDone = Math.min(READER.synthTotal, READER.synthDone + 1);
      readerUpdateSynthProgress();
    }
      return obj;
    } catch (e) {
      if (READER.cache === cacheEpoch) {
        var dt = Date.now() - t0;
        READER.synthTimes.push(dt);
        if (READER.synthTimes.length > 8) READER.synthTimes.shift();
      }
      return { error: "Couldn't reach the voice service." };
    }
  })();
  fetchingEpoch[ci] = request;
  try {
    return await request;
  } finally {
    if (fetchingEpoch[ci] === request) delete fetchingEpoch[ci];
  }
}

function readerTick(ch, audio) {
  const dur = audio.duration;
  if (!dur || !isFinite(dur)) return;
  const frac = Math.min(1, audio.currentTime / dur);
  // Map audio time → a character position inside the chunk using the words'
  // REAL offsets in `plain` (which already include the inter-word whitespace),
  // then highlight the word that position falls in. Using real offsets instead
  // of a flat "+1 char per word" removes the systematic skew, and picking the
  // word whose START is at/before `target` (rather than testing a cumulative
  // END) stops the highlight from running one word ahead of the audio.
  const base = READER.words[ch.wStart].start;
  const span = READER.words[ch.wEnd - 1].end - base;
  if (span <= 0) return;
  const target = base + frac * span;
  let wi = ch.wStart;
  for (let k = ch.wStart; k < ch.wEnd; k++) {
    if (READER.words[k].start <= target) wi = k;
    else break;
  }
  if (wi !== READER.idx) readerHighlight(wi);
}

function readerChunkOffset(ch, word) {
  // Fraction of a chunk's audio at which `word` begins (by real char offset) —
  // used to SEEK into the resumed chunk so resume is word-accurate, not just
  // chunk-accurate. Mirrors readerTick's mapping so seek and highlight agree.
  if (word <= ch.wStart) return 0;
  const base = READER.words[ch.wStart].start;
  const span = READER.words[ch.wEnd - 1].end - base;
  if (span <= 0) return 0;
  const w = Math.min(word, ch.wEnd - 1);
  return Math.max(0, Math.min(1, (READER.words[w].start - base) / span));
}

function readerStartListeningClock(startWord) {
  if (!READER.sessionStart) {
    READER.sessionStart = Date.now();
    READER.sessionStartPos = startWord;
    READER.sessionElapsedMs = 0;
  }
  if (!READER.playStartedAt) READER.playStartedAt = Date.now();
}

function readerStopListeningClock() {
  if (!READER.playStartedAt) return;
  READER.sessionElapsedMs += Math.max(0, Date.now() - READER.playStartedAt);
  READER.playStartedAt = 0;
}

async function readerPlayChunk(ci, seekWord) {
  // Claim this playback generation. Any state change (pause/stop/jump/reload/
  // open) bumps READER.gen; if it changes while we're awaiting the TTS fetch
  // below, this invocation is stale and must abort so it can't start a second
  // <audio> on top of the new one.
  const gen = ++READER.gen;
  if (ci >= READER.chunks.length) {
    // finished the document
    _readerLogSession();
    READER.state = "idle";
    READER.audio = null;
    readerSetPlayIcon();
    readerSetStatus("Finished.");
    readerSavePosition(READER.words.length); // 100%
    return;
  }
  const ch = READER.chunks[ci];
  READER.chunkIdx = ci;
  const startWord = seekWord != null && seekWord > ch.wStart ? seekWord : ch.wStart;
  readerHighlight(startWord);
  readerSavePosition(startWord);
  // Show synthesis progress (the readerUpdateSynthProgress inside
  // readerFetchChunk keeps it current as chunks arrive).
  readerUpdateSynthProgress();
  const got = await readerFetchChunk(ci);
  // bail if the user paused/stopped or switched chunk/document while we awaited
  if (gen !== READER.gen || READER.state !== "playing" || READER.chunkIdx !== ci)
    return;
  if (!got || got.error) {
    _readerLogSession();
    READER.state = "paused";
    readerSavePosition(READER.idx);
    readerSetPlayIcon();
    const msg = got && got.error ? got.error : "Couldn't get audio.";
    readerSetStatus(msg);
    toast(msg, "err", 4200);
    return;
  }
  readerSetStatus("");
  const audio = new Audio(got.url);
  audio.playbackRate = parseFloat($("#reader-speed")?.value) || 1;
  // Resume mid-chunk: seek to the saved word's offset once duration is known.
  const frac = readerChunkOffset(ch, startWord);
  if (frac > 0) {
    audio.addEventListener("loadedmetadata", () => {
      if (audio.duration && isFinite(audio.duration))
        audio.currentTime = Math.min(audio.duration * 0.99, audio.duration * frac);
    });
  }
  READER.audio = audio;
  audio.ontimeupdate = () => {
    READER.errStreak = 0; // real playback progress clears the decode-failure run
    readerTick(ch, audio);
  };
  audio.onended = () => {
    if (gen !== READER.gen || READER.state !== "playing") return; // superseded clip
    readerStopListeningClock();
    // wEnd is the first unread word (or words.length at EOF). Keep that
    // sentinel in state so completion stats and the persisted 100% position
    // are exact instead of losing the final word.
    READER.idx = ch.wEnd;
    readerUpdateProgress();
    readerSavePosition(ch.wEnd);
    readerPlayChunk(ci + 1);
  };
  audio.onerror = () => {
    if (gen !== READER.gen || READER.state !== "playing") return; // superseded clip
    // Never skip unread text. A single undecodable part is a hard pause at the
    // same word; silently advancing made apparently successful reads omit whole
    // passages.
    _readerLogSession();
    READER.state = "paused";
    READER.audio = null;
    readerSavePosition(READER.idx);
    readerSetPlayIcon();
    const msg = "Couldn't decode this audio part. Check the selected voice provider, then press play to retry.";
    readerSetStatus(msg);
    toast(msg, "err", 5000);
  };
  try {
    await audio.play();
    if (gen === READER.gen && READER.state === "playing") {
      // Listening time starts when audio actually starts, not while the cloud
      // provider is synthesising the first chunk.
      readerStartListeningClock(startWord);
    }
  } catch (e) {
    // Autoplay can be blocked when a document was opened programmatically.
    // Present a real Play state so the next user click retries this loaded clip.
    if (gen !== READER.gen) return;
    const blocked = e && e.name === "NotAllowedError";
    READER.state = "paused";
    READER.sessionStart = 0;
    READER.sessionStartPos = 0;
    READER.sessionElapsedMs = 0;
    READER.playStartedAt = 0;
    if (!blocked) READER.audio = null;
    readerSetPlayIcon();
    readerSetStatus(blocked
      ? "Press play to start (the browser blocked autoplay)."
      : "Couldn't start this audio part. Check the selected voice and try again.");
    return;
  }
  // Prefetch the next two chunks for gapless playback — the second one is often
  // the one that matters when the current chunk is short.
  readerFetchChunk(ci + 1);
  readerFetchChunk(ci + 2);
}

async function readerPlay() {
  if (!READER.words.length) return;
  if (!(await confirmReaderCloudUse("tts"))) return;
  // resume a paused mid-chunk clip without re-fetching
  if (
    READER.state === "paused" &&
    READER.audio &&
    !READER.audio.ended
  ) {
    READER.state = "playing";
    readerSetPlayIcon();
    READER.audio.play().then(() => {
      if (READER.state === "playing") {
        readerStartListeningClock(READER.idx);
      }
    }).catch(() => {
      if (READER.state === "playing") {
        READER.state = "paused";
        READER.sessionStart = 0;
        READER.sessionStartPos = 0;
        READER.sessionElapsedMs = 0;
        READER.playStartedAt = 0;
        READER.audio = null;
        readerSetPlayIcon();
        readerSetStatus("Couldn't start audio. Check the selected voice and try again.");
      }
    });
    return;
  }
  READER.state = "playing";
  readerSetPlayIcon();
  READER.sessionStart = 0;
  READER.sessionStartPos = READER.idx;
  READER.sessionElapsedMs = 0;
  READER.playStartedAt = 0;
  // Initialise synthesis progress tracking for this play-through.
  READER.synthStart = Date.now();
  READER.synthDone = 0;
  READER.synthTotal = READER.chunks.length;
  READER.fallbackUsed = false;
  READER._fallbackToasted = false;
  READER.effectiveProvider = null;
  READER.effectiveModel = null;
  READER.effectiveVoice = null;
  readerPlayChunk(readerChunkForWord(READER.idx), READER.idx);
}
function readerPause() {
  // NOTE: do NOT bump READER.gen here. The in-flight prefetch for the NEXT
  // chunk is harmless (it just warms the cache), and bumping gen would
  // invalidate the CURRENT audio's onended handler — after a pause→resume
  // cycle the chunk would play to the end but never advance to the next
  // chunk, silently stalling playback. The state change to "paused" already
  // prevents readerPlayChunk from starting a new clip via the
  // READER.state !== "playing" guard.
  _readerLogSession();
  READER.state = "paused";
  if (READER.audio) READER.audio.pause();
  readerSavePosition(READER.idx);
  readerSetPlayIcon();
}
function readerToggle() {
  READER.state === "playing" ? readerPause() : readerPlay();
}
function readerStop() {
  READER.gen++; // abort any in-flight chunk
  _readerLogSession();
  if (READER.audio) {
    READER.audio.pause();
    READER.audio = null;
  }
  READER.state = "idle";
  READER.idx = 0;
  READER.chunkIdx = 0;
  READER.synthDone = 0;
  READER.synthTotal = 0;
  readerSavePosition(0);
  readerHighlight(0);
  readerSetStatus("");
  readerSetPlayIcon();
}
function readerJumpTo(wi) {
  READER.gen++; // abort any in-flight chunk before we move the cursor
  const wasPlaying = READER.state === "playing";
  if (wasPlaying) _readerLogSession();
  READER.idx = Math.max(0, Math.min(wi, READER.words.length - 1));
  if (READER.audio) READER.audio.pause();
  READER.audio = null;
  readerHighlight(READER.idx);
  if (wasPlaying) readerPlay();
  else readerSavePosition(READER.idx);
}
function readerReload() {
  // voice/model changed → cached audio is invalid; re-synthesize from here.
  READER.gen++; // abort any in-flight chunk fetched under the old voice
  const wasPlaying = READER.state === "playing";
  if (wasPlaying) _readerLogSession();
  READER.cache = {};
  READER.fetching = {};
  // Drop the loaded clip in ALL states. If we only did this while playing, a
  // change made while PAUSED would leave the old-voice <audio> in place and the
  // next Play would resume it via the fast path — silently ignoring the change.
  if (READER.audio) {
    READER.audio.pause();
    READER.audio = null;
  }
  // Reset synthesis progress since all cached audio is now invalid.
  READER.synthDone = 0;
  READER.synthTotal = READER.chunks.length;
  READER.synthTimes = [];
  READER.fallbackUsed = false;
  READER._fallbackToasted = false;
  READER.effectiveProvider = null;
  READER.effectiveModel = null;
  READER.effectiveVoice = null;
  if (wasPlaying) {
    READER.sessionStart = 0;
    READER.sessionStartPos = READER.idx;
    readerPlayChunk(READER.chunkIdx, READER.idx);
  }
}
function readerStopForNav() {
  if (READER.state === "playing") readerPause();
  // readerPause already logs the session via _readerLogSession
}

function _readerLogSession() {
  // Log a reading session if we have a valid doc and actually read something
  readerStopListeningClock();
  const hadPlayback = !!READER.sessionStart;
  const startPos = READER.sessionStartPos || 0;
  const elapsedMs = READER.sessionElapsedMs || 0;
  READER.sessionStart = 0;
  READER.sessionStartPos = 0;
  READER.sessionElapsedMs = 0;
  READER.playStartedAt = 0;
  if (!READER.docId || !HAS_PY()) return;
  const dur = hadPlayback ? elapsedMs / 1000.0 : 0;
  if (dur < 1) return; // ignore sub-second "sessions"
  const endPos = READER.idx;
  const wordsRead = Math.max(0, endPos - startPos);
  const docLen = READER.words ? READER.words.length : 0;
  const completed = docLen > 0 && endPos >= docLen;
  call("reader_log_session", READER.docId, startPos, endPos, dur).catch(
    () => {});
  // Also persist into the independent stats store (Reader metrics on Stats page)
  call("reader_record_stats", wordsRead, dur, completed).catch(() => {});
}

/* ---- library + open/resume ---- */
async function readerRefreshLibrary(useCache) {
  // Show/hide the correct sub-views based on the active tab
  const tab = READER.libTab;
  const allList = $("#reader-lib-list");
  const colView = $("#reader-collections-view");
  const histView = $("#reader-history-view");
  const filterRow = $("#reader-lib-filter-row");
  const contCard = $("#reader-continue");
  if (allList) allList.hidden = tab !== "all";
  if (colView) colView.hidden = tab !== "collections";
  if (histView) histView.hidden = tab !== "history";
  if (filterRow) filterRow.hidden = tab !== "all";
  if (contCard) contCard.hidden = true; // will be shown by the all-tab renderer

  // Update subtab buttons
  $$("#reader-lib-tabs .subtab").forEach(function (b) {
    b.classList.toggle("active", b.dataset.rsub === tab);
  });

  if (tab === "all") await readerRenderAllTab(useCache);
  else if (tab === "collections") await readerRenderCollectionsTab();
  else if (tab === "history") await readerRenderHistoryTab();
}

async function readerRenderAllTab(useCache) {
  const host = $("#reader-lib-list");
  if (!host) return;
  // PERF: the document list lives in READER.libAll. Search filters that cached
  // copy in memory (useCache=true) instead of hitting the disk-backed bridge on
  // every keystroke; open/delete/star pass nothing, so they refetch.
  const all =
    useCache && READER.libAll
      ? READER.libAll
      : (READER.libAll = (await call("reader_list")) || []);
  const q = (READER.libQuery || "").toLowerCase();
  const docs = all
    .filter(
      (d) =>
        (!READER.libStarred || d.starred) &&
        (!q ||
          (d.title || "").toLowerCase().includes(q) ||
          (d.preview || "").toLowerCase().includes(q)),
    )
    .sort((a, b) => (b.opened || 0) - (a.opened || 0));
  const starFilter = $("#reader-lib-starred");
  if (starFilter) starFilter.classList.toggle("on", READER.libStarred);
  setText("#reader-lib-count", all.length ? `${all.length} saved` : "");

  // Continue Reading card
  await readerRenderContinueCard(all);

  if (!all.length) {
    host.innerHTML =
      '<div class="empty">No documents yet — paste or open one below.</div>';
    return;
  }
  if (!docs.length) {
    host.innerHTML = '<div class="empty">Nothing matches your filter.</div>';
    return;
  }
  host.innerHTML = docs
    .map(
      (d) => {
        const colls = (d.collections || []).map(function (c) {
          return `<span class="rl-coll-pill" data-coll="${esc(c)}" data-docid="${esc(d.id)}" title="Remove from ${esc(c)}">${esc(c)}&nbsp;<span style="font-size:9px;opacity:0.7">×</span></span>`;
        }).join("");
        const addBtn = `<button class="btn-icon btn-ghost t-mute rl-add-col-btn" data-docid="${esc(d.id)}" title="Add to collection" style="padding:2px 5px" aria-label="Add to collection">+</button>`;
        var fmtBadge = "";
        if (d.format) {
          var fmtLabel = d.format.toUpperCase();
          if (fmtLabel === "MARKDOWN") fmtLabel = "MD";
          fmtBadge = `<span class="format-badge" aria-label="Format: ${fmtLabel}">${fmtLabel}</span>`;
        }
        return `
    <div class="rl-item" role="listitem" data-open="${esc(d.id)}" tabindex="0" aria-label="${esc(d.title)} — ${d.format ? d.format.toUpperCase() : 'TXT'} document">
      <button class="star ${d.starred ? "on" : ""} btn-icon btn-ghost" data-starid="${esc(d.id)}" data-starred="${d.starred ? "1" : "0"}" title="${d.starred ? "Starred — click to unstar" : "Star this document"}" aria-pressed="${d.starred ? "true" : "false"}" aria-label="${d.starred ? "Starred document" : "Star this document"}">${svg("star")}</button>
      <div class="rl-main">
        <div class="rl-title truncate">${esc(d.title)}${fmtBadge ? ' ' + fmtBadge : ""}</div>
        <div class="rl-sub">${d.percent}% read • ${d.length} words${d.bookmark_count ? ` • ${d.bookmark_count} bookmark${d.bookmark_count > 1 ? "s" : ""}` : ""}${d.total_minutes ? ` • ${d.total_minutes}m read` : ""}</div>
        <div class="rl-bar"><span style="width:${d.percent}%"></span></div>
        <div class="rl-collections">${colls}${addBtn}</div>
      </div>
      <button class="btn btn-sm btn-gold rl-resume" data-open="${esc(d.id)}" aria-label="${d.percent >= 100 ? 'Read again' : d.percent > 0 ? 'Resume reading' : 'Read'} ${esc(d.title)}">${d.percent >= 100 ? "Read again" : d.percent > 0 ? "Resume" : "Read"}</button>
      <button class="btn-icon btn-ghost t-mute" data-del="${esc(d.id)}" title="Remove" aria-label="Remove ${esc(d.title)} from library">${svg("x")}</button>
    </div>`;
      },
    )
    .join("");

  // wire collection pill clicks (remove from collection)
  $$(".rl-coll-pill", host).forEach(function (el) {
    el.onclick = async function (e) {
      e.stopPropagation();
      await call("reader_remove_from_collection", el.dataset.docid, el.dataset.coll);
      READER.collectionsCache = null;
      readerRefreshLibrary();
    };
  });
  // wire "+" add-to-collection buttons
  $$(".rl-add-col-btn", host).forEach(function (el) {
    el.onclick = function (e) {
      e.stopPropagation();
      readerShowAddToCollection(el.dataset.docid, el);
    };
  });

  $$("[data-starid]", host).forEach(
    (el) =>
      (el.onclick = async (e) => {
        e.stopPropagation();
        await call("reader_star", el.dataset.starid, el.dataset.starred !== "1");
        readerRefreshLibrary();
      }),
  );
  $$("[data-open]", host).forEach(
    (el) =>
      (el.onclick = (e) => {
        e.stopPropagation();
        readerOpenDoc(el.dataset.open);
      }),
  );
  $$("[data-del]", host).forEach(
    (el) =>
      (el.onclick = async (e) => {
        e.stopPropagation();
        await call("reader_delete", el.dataset.del);
        readerRefreshLibrary();
      }),
  );

  // Keyboard navigation for library list (arrow keys, Enter/Space)
  // Wire keydown on each rl-item directly for arrow navigation between items
  $$(".rl-item", host).forEach(function (item, idx) {
    item.addEventListener("keydown", function (e) {
      var items = $$(".rl-item", host);
      var curIdx = items.indexOf(item);
      if (e.key === "ArrowDown") {
        e.preventDefault();
        var next = items[Math.min(curIdx + 1, items.length - 1)];
        if (next) next.focus();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        var prev = items[Math.max(curIdx - 1, 0)];
        if (prev) prev.focus();
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        readerOpenDoc(item.dataset.open);
      }
    });
  });
}

async function readerRenderContinueCard(allDocs) {
  var cont = $("#reader-continue");
  if (!cont) return;
  var doc = await call("reader_continue_reading");
  if (!doc || !doc.id) {
    cont.hidden = true;
    return;
  }
  // Check this doc is still in the library
  var inLib = (allDocs || []).some(function (d) { return d.id === doc.id; });
  if (!inLib) {
    cont.hidden = true;
    return;
  }
  cont.hidden = false;
  setText("#reader-cont-title", doc.title);
  setText("#reader-cont-sub", doc.percent + "% read • " + doc.position + " of " + (doc.length || 0) + " words");
  $("#reader-cont-btn").onclick = function () { readerOpenDoc(doc.id); };
}

async function readerRenderCollectionsTab() {
  var cols = READER.collectionsCache = (await call("reader_list_collections")) || [];
  var host = $("#reader-collections-list");
  if (!host) return;
  setText("#reader-lib-count", cols.length ? cols.length + " collection" + (cols.length === 1 ? "" : "s") : "");
  if (!cols.length) {
    host.innerHTML = '<div class="empty">No collections yet. Create one above, then add documents to it from the All tab with the + button.</div>';
    return;
  }
  host.innerHTML = cols.map(function (c) {
    return `<div class="rl-collection-item" data-col="${esc(c.name)}">
      <span data-icon="folder" style="width:15px;height:15px;display:inline-flex;color:var(--gold);flex:none"></span>
      <span class="rl-col-name">${esc(c.name)}</span>
      <span class="rl-col-count">${c.count} doc${c.count === 1 ? "" : "s"}</span>
      <button class="btn btn-sm btn-ghost" data-col-open="${esc(c.name)}">View</button>
      <button class="btn-icon btn-ghost t-mute" data-col-del="${esc(c.name)}" title="Delete collection">${svg("x")}</button>
    </div>`;
  }).join("");
  // View collection
  $$("[data-col-open]", host).forEach(function (el) {
    el.onclick = function () { readerOpenCollection(el.dataset.colOpen); };
  });
  // Delete collection
  $$("[data-col-del]", host).forEach(function (el) {
    el.onclick = async function (e) {
      e.stopPropagation();
      var ok = await confirmModal({
        icon: "trash", title: "Delete collection?",
        body: 'This removes the "' + el.dataset.colDel + '" label from all documents — your documents stay in the library.',
        confirmText: "Delete",
      });
      if (!ok) return;
      await call("reader_delete_collection", el.dataset.colDel);
      READER.collectionsCache = null;
      readerRefreshLibrary();
    };
  });
}

async function readerOpenCollection(name) {
  var docs = await call("reader_list_collection_docs", name, 50);
  // Show docs in a simple overlay/modal-like section - we reuse the All tab
  // by switching to it and showing the collection-filtered list
  READER.libTab = "all";
  READER.libQuery = "collection:" + name; // not real search — we'll overlay
  // Instead, let's just show them in a sub-view
  var host = $("#reader-lib-list");
  if (!host) return;
  $("#reader-collections-view").hidden = true;
  $("#reader-lib-filter-row").hidden = true;
  $("#reader-continue").hidden = true;
  host.hidden = false;
  host.innerHTML =
    '<div class="flex items-center gap8 mb8"><button class="btn-icon btn-ghost t-mute" id="reader-col-back" title="Back to collections">' +
    svg("arrowRight") +
    '</button><span class="fs13 fw6">' + esc(name) + '</span><span class="t-mute fs11">' + docs.length + ' doc' + (docs.length === 1 ? "" : "s") + '</span></div>' +
    (docs.length ? docs.map(function (d) {
      return `<div class="rl-item" data-open="${esc(d.id)}">
        <div class="rl-main">
          <div class="rl-title truncate">${esc(d.title)}</div>
          <div class="rl-sub">${d.percent}% read • ${d.length} words</div>
          <div class="rl-bar"><span style="width:${d.percent}%"></span></div>
        </div>
        <button class="btn btn-sm btn-gold rl-resume" data-open="${esc(d.id)}">${d.percent >= 100 ? "Read again" : d.percent > 0 ? "Resume" : "Read"}</button>
      </div>`;
    }).join("") : '<div class="empty">This collection is empty.</div>');
  $$("[data-open]", host).forEach(function (el) {
    el.onclick = function (e) {
      e.stopPropagation();
      readerOpenDoc(el.dataset.open);
    };
  });
  $("#reader-col-back").onclick = function () {
    READER.libTab = "collections";
    readerRefreshLibrary();
  };
}

async function readerRenderHistoryTab() {
  var sessions = READER.historyCache = (await call("reader_reading_history", 100)) || [];
  var host = $("#reader-history-list");
  if (!host) return;
  setText("#reader-lib-count", sessions.length ? sessions.length + " session" + (sessions.length === 1 ? "" : "s") : "");
  if (!sessions.length) {
    host.innerHTML = '<div class="empty">No reading sessions yet. Open a document and listen for a bit — sessions are logged automatically when you pause or stop.</div>';
    return;
  }
  host.innerHTML = sessions.map(function (s) {
    var durMin = Math.round((s.duration_sec || 0) / 60 * 10) / 10;
    var durStr = durMin >= 1 ? durMin + "m" : Math.round(s.duration_sec || 0) + "s";
    var when = s.start ? new Date(s.start * 1000).toLocaleString() : "";
    var progress = s.doc_length
      ? Math.round((100 * (s.end_pos || 0)) / s.doc_length) + "%"
      : (s.end_pos || 0) + "w";
    return `<div class="rh-item" data-open="${esc(s.doc_id)}">
      <span data-icon="clock" style="width:14px;height:14px;display:inline-flex;color:var(--gold);flex:none"></span>
      <div class="rh-main">
        <div class="rh-title">${esc(s.doc_title)}</div>
        <div class="rh-meta">${when} · ${progress}</div>
      </div>
      <span class="rh-duration">${durStr}</span>
      <button class="btn btn-sm btn-ghost" data-open="${esc(s.doc_id)}">Open</button>
    </div>`;
  }).join("");
  $$("[data-open]", host).forEach(function (el) {
    el.onclick = function () { readerOpenDoc(el.dataset.open); };
  });
}

/* ---- collection add-to dropdown ---- */
async function readerShowAddToCollection(docId, anchorEl) {
  // Remove any existing dropdown
  var old = document.querySelector(".rl-add-col-dropdown");
  if (old) old.remove();
  // Build dropdown with existing collections + new input
  var cols = READER.collectionsCache;
  if (!cols) {
    cols = (await call("reader_list_collections")) || [];
    READER.collectionsCache = cols;
  }
  var wrap = document.createElement("div");
  wrap.className = "rl-add-col-dropdown";
  wrap.innerHTML =
    '<input type="text" placeholder="Type a collection name…" id="rl-add-col-input" />' +
    (cols.length
      ? cols
          .map(function (c) {
            return '<button class="rl-col-opt" data-col="' +
              esc(c.name) +
              '">' +
              esc(c.name) +
              ' <span style="font-size:9px;color:var(--text-mute)">' +
              c.count +
              "</span></button>";
          })
          .join("")
      : '<div class="help" style="padding:6px 10px">No collections yet — type a name above and press Enter</div>');
  document.body.appendChild(wrap);
  // Position near the anchor
  var rect = anchorEl.getBoundingClientRect();
  wrap.style.position = "fixed";
  wrap.style.left = Math.min(rect.left, window.innerWidth - 200) + "px";
  wrap.style.top = Math.max(0, rect.top - 200) + "px";
  // Wire events
  var input = wrap.querySelector("#rl-add-col-input");
  var addDoc = async function (name) {
    name = (name || "").trim();
    if (!name) return;
    await call("reader_add_to_collection", docId, name);
    READER.collectionsCache = null;
    wrap.remove();
    readerRefreshLibrary();
  };
  if (input) {
    input.focus();
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        addDoc(input.value);
      }
    });
  }
  wrap.querySelectorAll(".rl-col-opt").forEach(function (b) {
    b.onclick = function () { addDoc(b.dataset.col); };
  });
  // Click outside to close
  var closeHandler = function (e) {
    if (!wrap.contains(e.target)) {
      wrap.remove();
      document.removeEventListener("click", closeHandler);
      document.removeEventListener("keydown", escHandler);
    }
  };
  var escHandler = function (e) {
    if (e.key === "Escape") {
      wrap.remove();
      document.removeEventListener("click", closeHandler);
      document.removeEventListener("keydown", escHandler);
    }
  };
  setTimeout(function () {
    document.addEventListener("click", closeHandler);
    document.addEventListener("keydown", escHandler);
  }, 50);
}

function readerShowPlayer(on) {
  const open = !!on;
  READER.playerOpen = open;
  $("#reader-main")?.classList.toggle("is-reading", open);
  $("#reader-player").hidden = !open;
  const wide = window.innerWidth >= 1440;
  $("#reader-library").hidden = open && !wide;
  $("#reader-input").hidden = open;
}

async function readerOpenDoc(id) {
  const openGen = ++READER.gen; // abort the previous document/fetch generation
  if (READER.state === "playing") _readerLogSession();
  if (READER.audio) READER.audio.pause();
  READER.audio = null;
  READER.state = "idle";
  readerSetPlayIcon();
  const doc = await call("reader_open", id);
  // Two rapid library clicks can resolve out of order. Only the newest open is
  // allowed to replace the player.
  if (openGen !== READER.gen) return;
  if (!doc) {
    // The row is stale (e.g. deleted in another action between list and click).
    // Refresh so it disappears instead of sitting there as a dead button.
    toast("That document is no longer available", "info");
    readerRefreshLibrary();
    return;
  }
  READER.docId = doc.id;
  READER.title = doc.title;
  READER.starred = !!doc.starred;
  READER.bookmarks = doc.bookmarks || [];
  READER.docFormat = doc.format || "txt";
  READER.docBlocks = doc.blocks || null;
  // If the document has structured blocks (parsed document), render them
  // instead of building a plain-text pane. Table blocks render as HTML tables.
  if (doc.blocks && doc.blocks.length > 0) {
    readerBuildBlocks(doc.blocks, doc.text);
  } else {
    readerBuildPane(doc.text);
  }
  readerBuildChunks();
  if (READER.truncated) {
    toast(
      "This document is very long — the Reader loaded the first part. " +
        "Split it into shorter documents to read the rest.",
      "info",
      4500,
    );
  }
  READER.cache = {};
  READER.fetching = {};
  READER.audio = null;
  READER.state = "idle";
  const savedPosition = Math.max(0, parseInt(doc.position, 10) || 0);
  // A completed document stores words.length. Opening it means "read again",
  // not replay only the final word.
  READER.idx = savedPosition >= READER.words.length
    ? 0
    : Math.max(0, Math.min(savedPosition, READER.words.length - 1));
  READER.chunkIdx = readerChunkForWord(READER.idx);
  setText("#reader-title", doc.title);
  readerShowPlayer(true);
  if (READER.fontScale !== 1) readerSetFontScale(0);
  readerRenderBookmarks();
  readerHighlight(READER.idx);
  readerSetStatus("");
  // Focus management: move focus to the reading pane after opening a document
  setTimeout(function () {
    var pane = $("#reader-pane");
    if (pane) pane.focus();
  }, 100);
  const confirmed = await confirmReaderCloudUse("tts");
  if (openGen !== READER.gen || READER.docId !== doc.id) return;
  if (confirmed)
    readerPlay(); // resume right away (falls back to "press play" if autoplay is blocked)
}

async function readerAddAndRead() {
  const text = ($("#reader-text")?.value || "").trim();
  if (!text) {
    toast("Paste some text or open a .txt file first", "info");
    return;
  }
  let r;
  try {
    r = await call("reader_save", "", text);
  } catch (e) {
    console.error("reader_save bridge failed", e);
    toast("Couldn't save — the document may be too large. Try a shorter text.", "err", 3000);
    return;
  }
  if (!r || !r.ok || !r.id) {
    toast((r && r.message) || "Couldn't save the document", "info");
    return;
  }
  $("#reader-text").value = "";
  readerRefreshLibrary();
  readerOpenDoc(r.id);
}

/* ---- send-to-reader (from History) + AI summary ---- */
async function sendToReader(text) {
  text = (text || "").trim();
  if (!text) {
    toast("Nothing to read", "info");
    return;
  }
  const r = await call("reader_save", "", text);
  if (!r || !r.ok || !r.id) {
    toast((r && r.message) || "Couldn't send to the Reader", "info");
    return;
  }
  navTo("reader");
  await initReader();
  if (READER.hasKey) {
    readerOpenDoc(r.id);
    toast("Sent to the Reader", "ok", 1400);
  } else {
    readerRefreshLibrary();
    toast("Saved to the Reader library — add an OpenRouter key to listen", "info", 2800);
  }
}

async function readerSummarize() {
  const text = (READER.plain || "").trim();
  if (!text) {
    toast("Open a document first", "info");
    return;
  }
  if (!(await confirmReaderCloudUse("summary"))) return;
  const panel = $("#reader-summary");
  const out = $("#reader-summary-text");
  const btn = $("#reader-summarize");
  if (panel) panel.hidden = false;
  if (out) out.textContent = "Summarizing…";
  if (btn) btn.disabled = true;
  try {
    const r = await call("reader_summarize", text);
    if (r && r.ok && r.summary) {
      READER.lastSummary = r.summary;
      if (out) out.textContent = r.summary;
    } else if (out) {
      out.textContent = (r && r.message) || "Couldn't summarize this document.";
    }
  } catch (e) {
    if (out) out.textContent = "Couldn't summarize this document.";
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ---- bookmarks + reading options ---- */
function readerRenderBookmarks() {
  const host = $("#reader-bm-list");
  if (!host) return;
  const marks = READER.bookmarks || [];
  if (!marks.length) {
    host.innerHTML = "";
    host.hidden = true;
    return;
  }
  host.hidden = false;
  host.innerHTML = marks
    .map((b) => {
      const n = READER.words.length;
      const pct = n ? Math.round((100 * b.pos) / n) : 0;
      const w = READER.words[b.pos];
      const label =
        (b.label || "").trim() ||
        (w ? READER.plain.slice(w.start, w.start + 28).trim() : `${pct}%`);
      return `<span class="reader-bm" data-bm="${b.pos}" title="Jump to ${pct}%">${svg("pin")}<span class="truncate">${esc(label || pct + "%")}</span><button class="reader-bm-x" data-bmx="${b.pos}" title="Remove bookmark">${svg("x")}</button></span>`;
    })
    .join("");
  $$("[data-bm]", host).forEach(
    (el) =>
      (el.onclick = (e) => {
        if (e.target.closest("[data-bmx]")) return;
        readerJumpTo(parseInt(el.dataset.bm, 10) || 0);
      }),
  );
  $$("[data-bmx]", host).forEach(
    (el) =>
      (el.onclick = async (e) => {
        e.stopPropagation();
        const r = await call(
          "reader_remove_bookmark",
          READER.docId,
          parseInt(el.dataset.bmx, 10),
        );
        if (r && r.ok) {
          READER.bookmarks = r.bookmarks || [];
          readerRenderBookmarks();
        } else {
          toast("Couldn't remove the bookmark", "info");
        }
      }),
  );
}

async function readerAddBookmarkHere() {
  if (!READER.docId || !READER.words.length) return;
  // Clamp to a real word: at the "finished" sentinel READER.idx === words.length,
  // which has no backing word.
  const pos = Math.max(0, Math.min(READER.idx, READER.words.length - 1));
  const r = await call("reader_add_bookmark", READER.docId, pos, "");
  if (!r || !r.ok) {
    // Don't wipe the existing list on failure (a missing `bookmarks` would set
    // it to []) and don't claim success.
    toast("Couldn't save the bookmark", "info");
    return;
  }
  READER.bookmarks = r.bookmarks || [];
  readerRenderBookmarks();
  toast("Bookmarked this spot", "ok", 1200);
}

function readerSetFontScale(delta) {
  READER.fontScale = Math.max(0.8, Math.min(1.8, (READER.fontScale || 1) + delta));
  const pane = $("#reader-pane");
  if (pane) pane.style.fontSize = READER.fontScale.toFixed(2) + "em";
}

function readerSetSleepTimer(mins) {
  if (READER.sleepTimer) {
    clearTimeout(READER.sleepTimer);
    READER.sleepTimer = null;
  }
  mins = parseInt(mins, 10) || 0;
  if (mins > 0) {
    READER.sleepTimer = setTimeout(
      () => {
        if (READER.state === "playing") readerPause();
        readerSetStatus("Sleep timer reached — paused.");
        const sel = $("#reader-sleep");
        if (sel) sel.value = "0";
        READER.sleepTimer = null;
      },
      mins * 60000,
    );
  }
}

function readerBack() {
  if (READER.state === "playing") readerPause();
  if (READER.sleepTimer) {
    clearTimeout(READER.sleepTimer);
    READER.sleepTimer = null;
  }
  readerShowPlayer(false);
  readerRefreshLibrary();
}

/* ---- model / voice dropdowns ---- */
function readerPopulateVoices() {
  // Filter voices to current provider + model.
  var modelVoices = READER.ttsVoices.filter(function (v) {
    return v.model === READER.model;
  });
  var vsel = $("#reader-voice");
  var custom = $("#reader-voice-custom");
  var count = $("#reader-voice-count");
  var tagsRow = $("#reader-voice-tags");

  if (modelVoices.length && modelVoices[0].id) {
    // Has known voice ids — populate the dropdown with tagged entries.
    if (vsel) {
      vsel.hidden = false;
      vsel.innerHTML = modelVoices
        .map(function (v) {
          var genderIcon = v.gender === "male" ? " ♂" : v.gender === "female" ? " ♀" : "";
          return '<option value="' + esc(v.id) + '">' + esc(v.name) + genderIcon + '</option>';
        })
        .join("");
      // Select the saved voice or first voice.
      var savedVoice = (READER._savedVoice || modelVoices[0].id);
      if (Array.from(vsel.options).some(function (o) { return o.value === savedVoice; })) {
        vsel.value = savedVoice;
      }
    }
    if (custom) custom.hidden = true;
    if (count) count.textContent = modelVoices.length + " voices";
  } else if (modelVoices.length) {
    // Model with no known voice list — show free-text field.
    if (vsel) vsel.hidden = true;
    if (custom) {
      custom.hidden = false;
      if (!custom.value) custom.value = "";
    }
    if (count) count.textContent = "type a voice name";
  } else {
    // No voices at all for this model — show free-text field.
    if (vsel) vsel.hidden = true;
    if (custom) {
      custom.hidden = false;
      if (!custom.value) custom.value = "";
    }
    if (count) count.textContent = "type a voice name";
  }

  // Update the voice tags row.
  if (tagsRow) {
    readerUpdateVoiceTags();
  }
}

function readerUpdateVoiceTags() {
  var tagsRow = $("#reader-voice-tags");
  if (!tagsRow) return;
  var voiceId = readerCurrentVoice();
  var voiceObj = null;
  for (var i = 0; i < READER.ttsVoices.length; i++) {
    if (READER.ttsVoices[i].id === voiceId && READER.ttsVoices[i].model === READER.model) {
      voiceObj = READER.ttsVoices[i];
      break;
    }
  }
  var providerTag = tagsRow.querySelector(".tag-provider");
  var genderTag = tagsRow.querySelector(".tag-gender");
  var qualityTag = tagsRow.querySelector(".tag-quality");
  var personaTag = tagsRow.querySelector(".tag-persona");

  if (voiceObj) {
    if (providerTag) {
      providerTag.hidden = false;
      providerTag.textContent = voiceObj.provider_label || voiceObj.provider || "";
    }
    if (genderTag) {
      genderTag.hidden = false;
      genderTag.textContent = voiceObj.gender === "male" ? "♂ Male" : voiceObj.gender === "female" ? "♀ Female" : "Neutral";
    }
    if (qualityTag) {
      qualityTag.hidden = false;
      qualityTag.textContent = voiceObj.quality === "high" ? "✦ HD" : (voiceObj.quality || "");
    }
    if (personaTag) {
      if (voiceObj.persona) {
        personaTag.hidden = false;
        personaTag.textContent = voiceObj.persona.charAt(0).toUpperCase() + voiceObj.persona.slice(1);
      } else {
        personaTag.hidden = true;
      }
    }
  } else {
    if (providerTag) providerTag.hidden = true;
    if (genderTag) genderTag.hidden = true;
    if (qualityTag) qualityTag.hidden = true;
    if (personaTag) personaTag.hidden = true;
  }
}

function readerPopulateProviders(providers) {
  var psel = $("#reader-provider");
  if (!psel) return;
  psel.innerHTML = (providers || []).map(function (p) {
    var label = p.label;
    if (!p.has_key) label += " (key needed)";
    return '<option value="' + esc(p.id) + '">' + esc(label) + '</option>';
  }).join("");
  if (READER.provider) {
    var opts = Array.from(psel.options);
    if (opts.some(function (o) { return o.value === READER.provider; })) {
      psel.value = READER.provider;
    }
  }
}

function readerPopulateModels() {
  var msel = $("#reader-model");
  if (!msel) return;
  msel.innerHTML = READER.ttsModels
    .map(function (m) { return '<option value="' + esc(m[0]) + '">' + esc(m[1]) + '</option>'; })
    .join("");
  // Restore saved model or use default.
  var modelIds = READER.ttsModels.map(function (m) { return m[0]; });
  if (modelIds.indexOf(READER.model) >= 0) {
    msel.value = READER.model;
  } else if (modelIds.length) {
    READER.model = modelIds[0];
    msel.value = READER.model;
  }
}

async function readerSaveKey() {
  // Save the OpenRouter key straight from the Reader's no-key card. The Reader
  // needs an OpenRouter key for TTS regardless of which LLM provider is set for
  // polishing, and the Settings key field is tucked inside the OpenRouter
  // provider row — so let the user paste it right here.
  const inp = $("#reader-key-input");
  const msg = $("#reader-key-msg");
  const key = (inp?.value || "").trim();
  if (!key) {
    if (msg) msg.textContent = 'Paste your OpenRouter key first (it starts with "sk-or-").';
    return;
  }
  if (!HAS_PY()) {
    if (msg) msg.textContent = "Open the Reader in the Mumble app to save your key.";
    return;
  }
  if (msg) msg.textContent = "Saving…";
  try {
    await call("set_setting", "openrouter_api_key", key);
    syncOpenRouterKey(key); // fill the Settings OpenRouter fields too (one shared key)
    await refreshRouteState();
    if (inp) inp.value = "";
    if (msg) msg.textContent = "Connected — loading voices…";
    await initReader(); // re-check has_key → reveal the Reader + populate models
    if (msg) msg.textContent = READER.hasKey ? "" : "Saved, but the key didn't take — double-check it.";
    if (READER.hasKey) toast("OpenRouter key saved — AI voices ready", "ok", 2400);
  } catch (e) {
    if (msg) msg.textContent = "Couldn't save the key — is the app running?";
  }
}

/* ============================================================================
   MEETINGS — record/import → transcribe (local) → summarise → action items.
   Wired to the meeting_* Api bridge (meeting.py / meeting_store.py). The page was
   originally added externally and lost in a UI rollback; rebuilt here on baseline.
   ========================================================================== */
const MEET = { recording: false, paused: false, recTimer: null, recStart: 0,
  maxSeconds: 14400, autoStopping: false,
  openId: null, openStarred: false, wired: false };
const _ell = "white-space:nowrap;overflow:hidden;text-overflow:ellipsis";

function meetingWire() {
  if (MEET.wired) return;
  MEET.wired = true;
  $("#meeting-record")?.addEventListener("click", meetingToggleRecord);
  $("#meeting-pause")?.addEventListener("click", meetingTogglePause);
  $("#meeting-stop")?.addEventListener("click", meetingStopRecord);
  $("#meeting-import")?.addEventListener("click", meetingImport);
  $("#meeting-back")?.addEventListener("click", () => { MEET.openId = null; renderMeetings(); });
  $("#meeting-play")?.addEventListener("click", meetingPlayAudio);
  $("#meeting-retry")?.addEventListener("click", meetingRetry);
  $("#meeting-summarize")?.addEventListener("click", meetingSummarize);
  $("#meeting-actions")?.addEventListener("click", meetingActions);
  $("#meeting-decisions")?.addEventListener("click", meetingExtractDecisions);
  $("#meeting-questions")?.addEventListener("click", meetingExtractQuestions);
  $("#meeting-export")?.addEventListener("click", meetingExport);
  $("#meeting-deep-process")?.addEventListener("click", meetingDeepProcess);
  $("#meeting-proc-mode")?.addEventListener("change", async (e) => {
    await call("meeting_set_processing_mode", e.target.value);
    toast("Processing mode set to " + e.target.value, "ok", 1500);
  });
  $("#meeting-title")?.addEventListener("change", async (e) => {
    if (MEET.openId) await call("meeting_update_title", MEET.openId, e.target.value.trim());
  });
  // Star / delete in detail view
  $("#meeting-star-detail")?.addEventListener("click", async () => {
    if (!MEET.openId) return;
    const next = !MEET.openStarred;
    const r = await call("meeting_star", MEET.openId, next);
    if (!r || !r.ok) { toast("Couldn't update the star", "err"); return; }
    openMeeting(MEET.openId);
    toast(next ? "Starred" : "Unstarred", "ok", 1200);
  });
  $("#meeting-del-detail")?.addEventListener("click", async () => {
    if (!MEET.openId) return;
    const yes = await confirmModal({ icon: "trash", title: "Delete this meeting?", body: "The transcript and recording are permanently removed. This cannot be undone.", confirmText: "Delete" });
    if (!yes) return;
    const r = await call("meeting_delete", MEET.openId);
    if (!r || !r.ok) { toast((r && r.message) || "Couldn't delete the meeting", "err"); return; }
    MEET.openId = null;
    renderMeetings();
    toast("Meeting deleted", "ok", 1500);
  });
}

async function renderMeetings() {
  meetingWire();
  if (MEET.openId) return; // a meeting detail is open — leave it
  const [listResult, settings] = await Promise.all([
    call("meeting_list"),
    call("get_settings"),
  ]);
  const list = listResult || [];
  const processing = settings && settings.meeting_processing_mode;
  const processingSelect = $("#meeting-proc-mode");
  if (processingSelect) {
    processingSelect.value = processing === "deep" ? "deep" : "lightweight";
  }
  const wrap = $("#meetings-list");
  const empty = $("#meetings-empty");
  if ($("#meeting-detail")) $("#meeting-detail").hidden = true;
  if (!list.length) {
    if (empty) empty.hidden = false;
    if (wrap) wrap.innerHTML = "";
    return;
  }
  if (empty) empty.hidden = true;
  const meetingsById = new Map(list.map((m) => [String(m.id), m]));
  wrap.innerHTML = list.map((m) => {
    var modeBadge = "";
    if (m.processing_mode === "deep" &&
        (m.key_decision_count > 0 || m.open_question_count > 0)) {
      modeBadge = '<span class="meeting-badge deep">deep</span>';
    }
    var status = String(m.status || "ready");
    var statusBadge = status === "processing"
      ? '<span class="meeting-badge">transcribing</span>'
      : status === "interrupted"
        ? '<span class="meeting-badge">retry needed</span>'
        : status === "failed"
          ? '<span class="meeting-badge">failed</span>' : "";
    var captureBadge = m.capture_warning
      ? '<span class="meeting-badge">ended early</span>' : "";
    var summarisedBadge = m.has_summary ? ' · <span class="t-gold fs11">summarised</span>' : "";
    return `
    <div class="card lift meeting-item" data-id="${esc(m.id)}" role="listitem">
      <button type="button" class="meeting-item-open" data-id="${esc(m.id)}" aria-label="Open ${esc(m.title)}">
          <span class="meeting-item-title-row">
            ${m.starred ? '<span class="t-gold" style="width:13px;height:13px;display:inline-flex;flex:none">'+svg("star")+'</span>' : ''}
            <span class="meeting-item-title">${esc(m.title)}</span>
            ${modeBadge}
            ${statusBadge}
            ${captureBadge}
          </span>
          <span class="meeting-item-meta">${esc(m.duration_display)} · ${m.speaker_count} speaker${m.speaker_count===1?"":"s"} · ${m.segment_count} segments${summarisedBadge}</span>
          ${status === "processing" ? `<span class="meeting-item-preview">Audio saved · transcription in progress…</span>` :
            m.preview ? `<span class="meeting-item-preview">${esc(m.preview)}</span>` : ""}
      </button>
        <div class="meeting-item-actions">
          <button class="btn btn-icon meeting-star${m.starred ? " on" : ""}" data-id="${esc(m.id)}" data-starred="${m.starred ? "1" : "0"}" aria-label="${m.starred ? "Unstar" : "Star"} ${esc(m.title)}">${svg("star")}</button>
          <button class="btn btn-icon meeting-del" data-id="${esc(m.id)}" aria-label="Delete ${esc(m.title)}">${svg("trash")}</button>
        </div>
    </div>`;
  }).join("");
  wrap.querySelectorAll(".meeting-item-open").forEach((el) =>
    el.addEventListener("click", () => openMeeting(el.dataset.id)),
  );
  wrap.querySelectorAll(".meeting-star").forEach((b) => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    const next = b.dataset.starred !== "1";
    const r = await call("meeting_star", b.dataset.id, next);
    if (!r || !r.ok) toast("Couldn't update the star", "err");
    renderMeetings();
  }));
  wrap.querySelectorAll(".meeting-del").forEach((b) => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    var mid = b.dataset.id;
    // Use the list returned by the live bridge. Looking only in MOCK.meetings
    // made every real-app confirmation fall back to "this meeting".
    var m = meetingsById.get(String(mid)) || {};
    var title = m.title || "this meeting";
    // confirmModal escapes its inputs; pre-escaping here displayed literal
    // entities (for example "R&amp;D") in the dialog.
    const yes = await confirmModal({ icon: "trash", title: "Delete " + title + "?", body: "The transcript and recording are permanently removed. This cannot be undone.", confirmText: "Delete" });
    if (!yes) return;
    const r = await call("meeting_delete", mid);
    if (!r || !r.ok) { toast((r && r.message) || "Couldn't delete the meeting", "err"); return; }
    renderMeetings();
  }));
  paintIcons(wrap);
}

async function openMeeting(id) {
  const m = await call("meeting_open", id);
  if (!m) { toast("Couldn't open that meeting", "err"); return; }
  MEET.openId = id;
  MEET.openStarred = !!m.starred;
  $("#meetings-list").innerHTML = "";
  if ($("#meetings-empty")) $("#meetings-empty").hidden = true;
  $("#meeting-detail").hidden = false;
  $("#meeting-title").value = m.title || "";
  // Meta line
  var dateStr = "";
  try { dateStr = new Date((m.created || 0) * 1000).toLocaleDateString(); } catch (e) {}
  setText("#meeting-meta-date", dateStr);
  setText("#meeting-meta-duration", (m.duration_display || "0:00"));
  setText("#meeting-meta-speakers", (m.speaker_count || 0) + " speaker" + ((m.speaker_count || 0) === 1 ? "" : "s"));
  setText("#meeting-meta-segments", (m.segment_count || 0) + " segments");
  var meetingReady = (m.status || "ready") === "ready";
  var retry = $("#meeting-retry");
  if (retry) retry.hidden = !["interrupted", "failed"].includes(m.status);
  ["#meeting-summarize", "#meeting-actions", "#meeting-decisions",
   "#meeting-questions", "#meeting-deep-process", "#meeting-export"].forEach(function (sel) {
    var control = $(sel);
    if (control) {
      control.disabled = !meetingReady;
      control.title = meetingReady ? "" : "Available when transcription is complete";
    }
  });
  // Star button state
  var sd = $("#meeting-star-detail");
  if (sd) sd.classList.toggle("on", !!m.starred);
  // Summary section
  var sumEl = $("#meeting-summary"), sumTextEl = $("#meeting-summary-text");
  if (m.summary) {
    sumEl.hidden = false;
    if (sumTextEl) sumTextEl.innerHTML = esc(m.summary).replace(/\n/g, "<br>");
  } else {
    sumEl.hidden = true;
  }
  // Action items section
  var aiEl = $("#meeting-actionitems"), aiListEl = $("#meeting-actionitems-list");
  if (m.action_items && m.action_items.length) {
    aiEl.hidden = false;
    if (aiListEl) aiListEl.innerHTML = m.action_items.map(function (a) {
      return '<div class="flex items-start gap8" style="margin-bottom:6px"><span class="t-green" style="width:13px;height:13px;display:inline-flex;margin-top:2px;flex:none">' + svg("check") + '</span><span class="fs12">' + esc(typeof a === "string" ? a : (a.text || "")) + '</span></div>';
    }).join("");
  } else {
    aiEl.hidden = true;
  }
  // Key decisions section
  var kdEl = $("#meeting-keydecisions"), kdListEl = $("#meeting-keydecisions-list");
  if (m.key_decisions && m.key_decisions.length) {
    kdEl.hidden = false;
    if (kdListEl) kdListEl.innerHTML = m.key_decisions.map(function (d) {
      return '<div class="flex items-start gap8" style="margin-bottom:6px"><span class="t-gold" style="width:13px;height:13px;display:inline-flex;margin-top:2px;flex:none">' + svg("star") + '</span><span class="fs12">' + esc(d) + '</span></div>';
    }).join("");
  } else {
    kdEl.hidden = true;
  }
  // Open questions section
  var oqEl = $("#meeting-openquestions"), oqListEl = $("#meeting-openquestions-list");
  if (m.open_questions && m.open_questions.length) {
    oqEl.hidden = false;
    if (oqListEl) oqListEl.innerHTML = m.open_questions.map(function (q) {
      return '<div class="flex items-start gap8" style="margin-bottom:6px"><span class="t-gold" style="width:13px;height:13px;display:inline-flex;margin-top:2px;flex:none">' + svg("comment") + '</span><span class="fs12">' + esc(q) + '</span></div>';
    }).join("");
  } else {
    oqEl.hidden = true;
  }
  // Speaker rename controls
  var spEdit = $("#meeting-speakers-edit"), spList = $("#meeting-speakers-list");
  if (m.speakers && m.speakers.length) {
    spEdit.hidden = false;
    if (spList) {
      spList.innerHTML = m.speakers.map(function (s) {
        // Names inferred from the transcript ("Hey Ralph…" / "I'm Ralph") offered
        // as one-tap pills — never auto-applied; the user's own label always wins.
        var cur = (s.name || "").toLowerCase();
        var sug = (s.suggested_names || []).filter(function (n) {
          return n && n.toLowerCase() !== cur;
        });
        var pills = sug.length
          ? '<div class="meeting-speaker-suggest"><span class="t-mute fs10">Preferred speaker:</span>' +
            sug.map(function (n) {
              return '<button type="button" class="suggestion-pill" data-speaker="' +
                esc(s.label) + '" data-name="' + esc(n) + '">' + esc(n) + '</button>';
            }).join("") + '</div>'
          : '';
        return '<div class="meeting-speaker-block"><div class="meeting-speaker-row">' +
          '<span class="speaker-color-dot" style="background:' + esc(s.color || "var(--gold)") + '"></span>' +
          '<span class="speaker-label">' + esc(s.label) + '</span>' +
          '<input class="input selectable speaker-name-input" data-speaker="' + esc(s.label) +
          '" value="' + esc(s.name || "") + '" placeholder="Name this speaker…" /></div>' +
          pills + '</div>';
      }).join("");
      spList.querySelectorAll(".speaker-name-input").forEach(function (inp) {
        inp.addEventListener("change", async function () {
          var label = inp.dataset.speaker;
          var name = inp.value.trim();
          await call("meeting_rename_speaker", MEET.openId, label, name);
          // Refresh transcript labels
          openMeeting(MEET.openId);
          toast(name ? "Speaker renamed to " + esc(name) : "Speaker name cleared", "ok", 1500);
        });
      });
      spList.querySelectorAll(".suggestion-pill").forEach(function (pill) {
        pill.addEventListener("click", async function () {
          var label = pill.dataset.speaker, name = pill.dataset.name;
          await call("meeting_rename_speaker", MEET.openId, label, name);
          openMeeting(MEET.openId);
          toast("Speaker named " + esc(name), "ok", 1500);
        });
      });
    }
  } else {
    spEdit.hidden = true;
  }
  // Transcript
  var speakersMap = {};
  (m.speakers || []).forEach(function (s) { speakersMap[s.label] = s; });
  var tx = $("#meeting-transcript");
  var segCount = (m.segments || []).length;
  setText("#meeting-transcript-count", segCount + " segment" + (segCount === 1 ? "" : "s"));
  var captureWarning = m.capture_warning
    ? '<p class="help" style="color:var(--amber);margin-bottom:10px"><strong>Recording ended early.</strong> ' + esc(m.capture_warning) + '</p>'
    : '';
  var transcriptBody = (m.segments || []).map(function (seg) {
    var sp = speakersMap[seg.speaker] || {};
    var name = sp.name || seg.speaker || "Speaker";
    var color = sp.color || "var(--gold)";
    var min = Math.floor((seg.start_sec || 0) / 60);
    var sec = String(Math.floor((seg.start_sec || 0) % 60)).padStart(2, "0");
    return '<div class="meeting-seg"><span class="meeting-ts t-mute fs10">' + min + ':' + sec + '</span><span class="meeting-speaker" style="color:' + esc(color) + '">' + esc(name) + '</span><span class="meeting-text">' + esc(seg.text || "") + '</span></div>';
  }).join("") || (m.status === "processing"
    ? '<p class="help text-center">Audio is safely saved. Transcription is still running…</p>'
    : m.status === "interrupted"
      ? '<p class="help text-center">Transcription was interrupted and will retry on the next launch. ' + esc(m.error || "") + '</p>'
      : m.status === "failed"
        ? '<p class="help text-center">Transcription failed. The saved recording is still available. ' + esc(m.error || "") + '</p>'
        : '<p class="help text-center">No transcript for this meeting.</p>');
  tx.innerHTML = captureWarning + transcriptBody;
  paintIcons($("#meeting-detail"));
  var v = document.querySelector('[data-view="meetings"]'); if (v) v.scrollTop = 0;
}

async function meetingToggleRecord() {
  if (MEET.recording) return meetingStopRecord();
  if (!HAS_PY()) { toast("Recording runs in the app (the tray controller owns the mic)", "info"); return; }
  const r = await call("meeting_start_recording");
  if (r && r.ok === false) { toast(r.message || "Couldn't start recording", "err"); return; }
  MEET.maxSeconds = Number(r && r.max_seconds) || 14400;
  MEET.autoStopping = false;
  MEET.recording = true; MEET.paused = false; MEET.recStart = Date.now();
  $("#meeting-recording").hidden = false;
  $("#meeting-record-label").textContent = "Recording…";
  $("#meeting-pause-label").textContent = "Pause";
  startMeetingTimer();
}

function startMeetingTimer() {
  clearInterval(MEET.recTimer);
  MEET.recTimer = setInterval(function () {
    var s = Math.floor((Date.now() - MEET.recStart) / 1000);
    var el = $("#meeting-rec-timer");
    if (el) el.textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
    if (s >= MEET.maxSeconds && !MEET.autoStopping) {
      MEET.autoStopping = true;
      toast("Four-hour meeting limit reached · stopping safely…", "info", 4000);
      meetingStopRecord();
    }
  }, 1000);
}

async function meetingTogglePause() {
  if (!MEET.recording) return;
  if (MEET.paused) {
    // Resume
    const r = await call("meeting_resume_recording");
    if (!r || !r.ok) { toast((r && r.message) || "Couldn't resume recording", "err", 3000); return; }
    MEET.paused = false;
    MEET.recStart = Date.now() - (MEET.pausedElapsed || 0);
    $("#meeting-pause-label").textContent = "Pause";
    $("#meeting-record-label").textContent = "Recording…";
    startMeetingTimer();
    toast("Resumed recording", "ok", 1200);
  } else {
    // Pause
    var s = Math.floor((Date.now() - MEET.recStart) / 1000);
    MEET.pausedElapsed = s * 1000;
    clearInterval(MEET.recTimer);
    const r = await call("meeting_pause_recording");
    if (!r || !r.ok) { startMeetingTimer(); toast((r && r.message) || "Couldn't pause recording", "err", 3000); return; }
    MEET.paused = true;
    $("#meeting-pause-label").textContent = "Resume";
    $("#meeting-record-label").textContent = "Paused";
    toast("Recording paused", "info", 1200);
  }
}

async function meetingStopRecord() {
  if (!MEET.recording) return;
  clearInterval(MEET.recTimer);
  MEET.recording = false;
  MEET.paused = false;
  MEET.pausedElapsed = 0;
  MEET.autoStopping = false;
  $("#meeting-record-label").textContent = "Record meeting";
  $("#meeting-pause-label").textContent = "Pause";
  $("#meeting-recording").hidden = true;
  toast("Saving audio and starting transcription…", "busy", 2200);
  var r = await call("meeting_stop_recording", "");
  if (r && r.ok === false) { toast(r.message || "Couldn't save the meeting", "err"); return; }
  if (r && r.meeting_id) {
    toast(r.processing ? "Meeting saved · transcription is running" : "Meeting saved", r.processing ? "info" : "ok", 3200);
    renderMeetings();
  }
  else { toast("Meeting saved (preview — refresh to see it)", "ok", 3000); renderMeetings(); }
}

async function meetingImport() {
  if (!HAS_PY()) { toast("Importing runs in the app", "info"); return; }
  var r = await call("meeting_import_audio", "");
  if (r && r.ok) { toast("Audio import started", "ok"); renderMeetings(); }
  else if (r && r.cancelled) return;
  else toast((r && r.message) || "Choose an audio file in the app to import", "info", 3000);
}

async function meetingSummarize() {
  if (!MEET.openId) return;
  toast("Summarising…", "busy", 2200);
  var r = await call("meeting_summarize", MEET.openId);
  if (r && r.ok) { toast("Summary ready", "ok"); openMeeting(MEET.openId); }
  else toast((r && r.message) || "Couldn't summarise — add an AI key in Settings", "err", 3200);
}

async function meetingPlayAudio() {
  if (!MEET.openId) return;
  var r = await call("meeting_play_audio", MEET.openId);
  if (r && r.ok) toast("Opened recording in your audio player", "ok", 1800);
  else toast((r && r.message) || "Couldn't open the recording", "err", 3000);
}

async function meetingRetry() {
  if (!MEET.openId) return;
  toast("Retrying transcription…", "busy", 2200);
  var r = await call("meeting_retry", MEET.openId);
  if (r && r.ok) {
    toast(r.processing ? "Transcription restarted" : "Transcript is already ready", "info", 2500);
    openMeeting(MEET.openId);
  } else {
    toast((r && r.message) || "Couldn't retry transcription", "err", 3000);
  }
}

async function meetingActions() {
  if (!MEET.openId) return;
  toast("Finding action items…", "busy", 2200);
  var r = await call("meeting_extract_actions", MEET.openId);
  if (r && r.ok) { toast("Action items ready", "ok"); openMeeting(MEET.openId); }
  else toast((r && r.message) || "Couldn't extract action items — add an AI key in Settings", "err", 3200);
}

async function meetingExtractDecisions() {
  if (!MEET.openId) return;
  toast("Finding key decisions…", "busy", 2200);
  var r = await call("meeting_extract_decisions", MEET.openId);
  if (r && r.ok) { toast("Key decisions ready", "ok"); openMeeting(MEET.openId); }
  else toast((r && r.message) || "Couldn't extract key decisions — add an AI key in Settings", "err", 3200);
}

async function meetingExtractQuestions() {
  if (!MEET.openId) return;
  toast("Finding open questions…", "busy", 2200);
  var r = await call("meeting_extract_questions", MEET.openId);
  if (r && r.ok) { toast("Open questions ready", "ok"); openMeeting(MEET.openId); }
  else toast((r && r.message) || "Couldn't extract open questions — add an AI key in Settings", "err", 3200);
}

async function meetingDeepProcess() {
  if (!MEET.openId) return;
  toast("Deep processing — this may take a moment…", "busy", 3500);
  var r = await call("meeting_extract_deep", MEET.openId);
  if (r && r.ok) {
    toast("Deep processing complete — summary, actions, decisions & questions extracted", "ok");
    openMeeting(MEET.openId);
  } else {
    toast((r && r.message) || "Couldn't deep-process — add an AI key in Settings", "err", 3200);
  }
}

async function meetingExport() {
  if (!MEET.openId) return;
  var fmtEl = $("#meeting-export-fmt");
  var fmt = fmtEl ? fmtEl.value : "txt";
  var r = await call(fmt === "clipboard" ? "meeting_export" : "meeting_save_export",
                     MEET.openId, fmt);
  if (r && r.ok) {
    if (fmt === "clipboard" && r.content) {
      const copied = await copyTextReliable(r.content);
      toast(copied ? "Copied to clipboard (" + fmt + ")" : "Couldn't copy export",
            copied ? "ok" : "err", 2000);
    } else {
      toast("Saved " + fmt.toUpperCase() + " export", "ok", 2000);
    }
  } else if (r && r.cancelled) {
    return;
  } else {
    toast((r && r.message) || "Couldn't export", "err");
  }
}

async function initReader() {
  const info = (await call("reader_tts_models")) || {};
  READER.hasKey = !!info.has_key;
  // Capture the model catalogue + default ONCE. initReader runs on every Reader
  // open (to re-check the key for the inline-key card), so re-assigning these on
  // re-entry would reset READER.model back to the default while the model dropdown
  // still shows the user's pick — a silent desync that synthesizes with the wrong
  // model. The list never changes at runtime, so only set it on first init.
  if (!READER.inited) {
    READER.ttsProviders = info.providers || [];
    READER.provider = info.provider || "openrouter";
    READER.ttsModels = info.models || [];
    READER.ttsVoices = info.voices || [];  // flat array with tags
    READER.model = info.default_model || "google/gemini-3.1-flash-tts-preview";
    READER._savedVoice = info.default_voice || "";
  }

  // Strictly TTS. The no-key card lets the user paste the key INLINE
  // (it's the same openrouter_api_key Settings uses), so re-run this on every
  // Reader open to re-check the key; wire the key controls only once.
  $("#reader-nokey").hidden = READER.hasKey;
  $("#reader-card").hidden = !READER.hasKey;
  if (!READER.wiredKeyUI) {
    READER.wiredKeyUI = true;
    $("#reader-gokey")?.addEventListener("click", () => navTo("settings"));
    $("#reader-key-save")?.addEventListener("click", readerSaveKey);
    $("#reader-key-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        readerSaveKey();
      }
    });
  }
  if (!READER.hasKey) return;

  // The player UI (provider/model/voice dropdowns + listeners + initial library
  // load) is wired ONCE, and only after a key exists.
  if (READER.inited) return;
  READER.inited = true;

  // Provider selector
  readerPopulateProviders(READER.ttsProviders);
  const psel = $("#reader-provider");
  if (psel) {
    psel.addEventListener("change", async () => {
      READER.provider = psel.value;
      // Persist provider choice.
      if (HAS_PY()) await call("set_setting", "reader_tts_provider", READER.provider);
      // Re-fetch voice catalogue for the new provider.
      const info2 = await call("reader_tts_models");
      if (info2 && info2.voices) {
        READER.ttsVoices = info2.voices;
        READER.ttsModels = info2.models || [];
        READER.model = info2.default_model || READER.model;
        READER._savedVoice = info2.default_voice || "";
      }
      readerPopulateModels();
      readerPopulateVoices();
      readerReload();
    });
  }

  // Model selector
  readerPopulateModels();

  const msel = $("#reader-model");
  if (msel) {
    msel.innerHTML = READER.ttsModels
      .map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`)
      .join("");
    msel.value = READER.model;
    msel.addEventListener("change", () => {
      READER.model = msel.value;
      readerPopulateVoices();
      readerReload();
    });
  }
  readerPopulateVoices();

  $("#reader-start")?.addEventListener("click", readerAddAndRead);
  $("#reader-playpause")?.addEventListener("click", readerToggle);
  $("#reader-stop")?.addEventListener("click", readerStop);
  $("#reader-back")?.addEventListener("click", readerBack);
  $("#reader-voice")?.addEventListener("change", () => {
    readerUpdateVoiceTags();
    readerReload();
  });
  $("#reader-voice-custom")?.addEventListener("change", () => {
    readerUpdateVoiceTags();
    readerReload();
  });
  $("#reader-speed")?.addEventListener("change", () => {
    if (READER.audio)
      READER.audio.playbackRate = parseFloat($("#reader-speed").value) || 1;
  });
  $("#reader-bookmark")?.addEventListener("click", readerAddBookmarkHere);
  $("#reader-font-dn")?.addEventListener("click", () => readerSetFontScale(-0.1));
  $("#reader-font-up")?.addEventListener("click", () => readerSetFontScale(0.1));
  $("#reader-sleep")?.addEventListener("change", (e) =>
    readerSetSleepTimer(e.target.value),
  );
  $("#reader-lib-search")?.addEventListener(
    "input",
    debounce((e) => {
      READER.libQuery = e.target.value || "";
      readerRefreshLibrary(true); // filter the cached list — no per-keystroke disk read
    }, 120),
  );
  $("#reader-lib-starred")?.addEventListener("click", () => {
    READER.libStarred = !READER.libStarred;
    readerRefreshLibrary(true);
  });
  // Library subtabs (All | Collections | History)
  $$("#reader-lib-tabs .subtab").forEach((b) => {
    b.addEventListener("click", () => {
      READER.libTab = b.dataset.rsub || "all";
      readerRefreshLibrary();
    });
  });
  // Collection creation
  $("#reader-create-collection")?.addEventListener("click", async () => {
    var inp = $("#reader-new-collection");
    var name = (inp?.value || "").trim();
    if (!name) { toast("Enter a collection name", "info"); return; }
    var result = await call("reader_create_collection", name);
    if (!result || !result.ok) {
      toast((result && result.message) || "Couldn't create the collection", "err");
      return;
    }
    if (inp) inp.value = "";
    READER.libTab = "collections";
    await readerRefreshLibrary();
    toast(result.created === false ? "Collection already exists" : "Collection created", result.created === false ? "info" : "ok", 1600);
  });
  // Enter key on collection input
  $("#reader-new-collection")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      $("#reader-create-collection")?.click();
    }
  });
  $("#reader-summarize")?.addEventListener("click", readerSummarize);
  $("#reader-summary-read")?.addEventListener("click", () => {
    if (READER.lastSummary) sendToReader(READER.lastSummary);
  });
  $("#reader-summary-close")?.addEventListener("click", () => {
    const p = $("#reader-summary");
    if (p) p.hidden = true;
  });
  $("#reader-file")?.addEventListener("change", (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    if (f.size > MAX_READER_IMPORT_BYTES) {
      toast("That document is larger than 32 MB. Split or compress it before importing.", "err", 4000);
      e.target.value = "";
      return;
    }
    const name = (f.name || "").toLowerCase();
    const textExtensions = /\.(txt|text|md|markdown|mdown|log|csv|tsv|rtf|json|xml|html?|tex)$/;
    const isText = (f.type && f.type.startsWith("text/")) || textExtensions.test(name);
    if (HAS_PY()) {
      // Let the Python bridge handle all formats via reader_import_bytes
      const reader = new FileReader();
      reader.onload = async () => {
        try {
          const b64 = arrayBufferToBase64(reader.result);
          const r = await call("reader_import_bytes", b64, f.name);
          if (r && r.ok) {
            toast("Imported — " + (r.format || "").toUpperCase() + " document", "ok", 2000);
            readerRefreshLibrary();
            readerOpenDoc(r.id);
          } else {
            toast((r && r.message) || "Couldn't import file", "err", 3000);
          }
        } catch (err) {
          toast("Couldn't read that file", "err");
        }
        e.target.value = "";
      };
      reader.onerror = () => {
        toast("Couldn't read that file", "err");
        e.target.value = "";
      };
      reader.readAsArrayBuffer(f);
      return;
    }
    // Mock fallback: only decode plain-text files directly
    if (!isText) {
      toast("Binary formats (XLSX, PPTX, DOCX, PDF, EPUB, ODT) need the full app for parsing. Plain-text formats like TXT, CSV, MD, HTML work here.", "info", 4000);
      e.target.value = "";
      return;
    }
    const fr = new FileReader();
    fr.onload = () => {
      const text = String(fr.result || "");
      const txtArea = $("#reader-text");
      if (txtArea) txtArea.value = text;
      toast("Loaded — press Add & read", "ok", 1800);
      e.target.value = "";
      if (text.length > 50000) {
        toast(
          "Large document loaded — you can also press Add & read directly",
          "info",
          3000,
        );
      }
    };
    fr.onerror = () => toast("Couldn't read that file — try pasting the text instead", "info");
    fr.readAsText(f);
  });
  readerRefreshLibrary();
}

async function boot() {
  // Guard against a double boot: boot is wired to BOTH pywebviewready and
  // DOMContentLoaded(!HAS_PY), and on some WebView2 timings both fire — a second
  // run would re-add document listeners and stack timer chains.
  if (window.__mumbleBooted) return;
  window.__mumbleBooted = true;
  paintIcons();
  wireGlobalKeys();
  applyResponsiveScale();
  window.addEventListener("resize", debounce(applyResponsiveScale, 80));
  // Re-layout the Reader when the viewport crosses the 1440px wide-layout
  // breakpoint (library sidebar visible beside the reading pane).
  var _rdrWide = window.innerWidth >= 1440;
  window.addEventListener("resize", debounce(function () {
    var wideNow = window.innerWidth >= 1440;
    if (wideNow !== _rdrWide) {
      _rdrWide = wideNow;
      // Only re-invoke if a document is open and the Reader is inited.
      if (READER.docId && READER.inited) {
        // readerShowPlayer's logic is keyed off window.innerWidth,
        // so calling it again updates the library/player layout.
        readerShowPlayer(READER.playerOpen);
      }
    }
  }, 120));
  initReader();
  // Enhanced is now a real tier (Settings → Visual effects). The ?enhanced
  // flag remains for the localhost demo bat — it just forces the class.
  if (location.search.includes("enhanced"))
    document.body.classList.add("enhanced");
  // nav
  $$(".nav-btn").forEach((b) => (b.onclick = () => navTo(b.dataset.nav)));
  // home
  $("#record-btn").onclick = toggleRecordPreview;
  $("#open-history-btn")?.addEventListener("click", () => openHistory());
  // Pause ambient animation when the window can't be seen OR isn't focused.
  // `visibilitychange` only fires on minimize / hidden tab — but a Mumble window
  // left OPEN behind other apps stays document.visible, so its ~35 infinite
  // Enhanced animations kept compositing for nothing (measured: a WebView2 renderer
  // burning ~60% of a core for 20 min while the user worked in another app). Adding
  // blur/focus closes that gap: unfocused → paused, focused/returned → instantly
  // back, so the look is unchanged whenever you're actually looking at it. (Owner:
  // resources should rise only while you're interacting.)
  // VAL-PERF-005: also relays focus state to the controller so the Python island
  // animation loop pauses when the user can't see it — no GPU waste on unseen frames.
  const setAmbient = () => {
    const off = document.hidden || !document.hasFocus();
    document.body.classList.toggle("paused", off);
    if (!off) pollStatus(); // resume the status sync the moment the window returns
    try { call("set_app_focused", !off); } catch (e) {}
  };
  document.addEventListener("visibilitychange", setAmbient);
  window.addEventListener("blur", setAmbient);
  window.addEventListener("focus", setAmbient);
  // visual experience (standard / lite) from settings
  try {
    const st = await call("get_settings");
    applyEffects(st.ui_effects);
    applySaver(st.resource_saver);
  } catch (e) {}
  // Content-type filter dropdown (replaced filterchips, owner v1.0)
  $("#hist-content-filter")?.addEventListener("change", (e) => {
    setContentFilter(e.target.value);
  });
  $("#hist-filter").addEventListener(
    "input",
    debounce((e) => {
      HX.q = e.target.value;
      drawHistory();
    }, 120),
  );
  // Type in the Deck search even while pinned: a non-activating palette can't take
  // keyboard focus from a click, so focusing the filter momentarily exits palette
  // mode (activates the window) and blurring it restores the palette — so clicking
  // items / Capture still never steals your selection. No need to un-pin to search.
  const histFilter = $("#hist-filter");
  if (histFilter) {
    const wakeForTyping = () => {
      if (HAS_PY() && HX.pinned && CURRENT === "history") {
        try {
          call("set_deck_palette", false); // activate so keystrokes land here
        } catch (e) {}
      }
    };
    histFilter.addEventListener("pointerdown", wakeForTyping);
    histFilter.addEventListener("focus", wakeForTyping);
    histFilter.addEventListener("blur", () => syncDeckWindow()); // re-pin as palette
  }
  // Pre-AI ⇄ Post-AI: actually swaps the rendered text to the stored raw
  // transcript (kept only when it differs from the final output).
  $("#hist-prepost").onclick = () => {
    HX.pre = !HX.pre;
    setText("#prepost-label", HX.pre ? "Showing: Pre-AI" : "Showing: Post-AI");
    drawHistory();
  };
  $("#hist-sort")?.addEventListener("change", (e) => {
    HX.sort = e.target.value;
    drawHistory();
  });
  $("#hist-refresh")?.addEventListener("click", async () => {
    await renderHistory();
    toast("Deck refreshed", "ok", 1200);
  });
  // Paste / copy the most recent transcript — manual fallbacks for the paste-latest
  // hotkey (owner v9), so you never need to remember the bind.
  $("#hist-paste-latest")?.addEventListener("click", pasteLatest);
  $("#hist-copy-latest")?.addEventListener("click", copyLatest);
  // History hub — the absorbed Deck. Smart Mode + Presets are now ONE always-open
  // merged widget (v0.9), so there are no collapsible headers to wire here; the
  // merge/run action bar, clear-selection and pin-on-top controls follow.
  $("#hist-merge-copy")?.addEventListener("click", () => histMerge(false));
  $("#hist-merge-paste")?.addEventListener("click", () => histMerge(true));
  $("#hist-run")?.addEventListener("click", histRun);
  $("#hist-clear-sel")?.addEventListener("click", clearHistSelection);
  $("#hist-pin")?.addEventListener("click", () => setHistPinned(!HX.pinned));
  $("#hist-capture")?.addEventListener("click", captureSelection);
  $("#hist-capture-chat")?.addEventListener("click", captureConversation);
  // Deck search button: search selected text in the browser
  $("#hist-search")?.addEventListener("click", () => {
    const sel = HX.selected.length > 0 ? HX.selected[0].text : "";
    if (sel && sel.trim()) {
      const engine = SET.search_engine || "google";
      const templates = { google: "https://www.google.com/search?q=", perplexity: "https://www.perplexity.ai/search?q=", brave: "https://search.brave.com/search?q=" };
      const url = (templates[engine] || templates.google) + encodeURIComponent(sel.trim());
      call("open_url", url);
    } else {
      toast("Highlight text then press Ctrl+Option+S for voice search, or select an item first", "info", 3500);
    }
  });
  // stats range
  $$("#stat-range button").forEach(
    (b) => (b.onclick = () => setStatsRange(+b.dataset.r)),
  );
  // settings special controls
  wireSettingsControls();
  // onboarding controls
  $("#ob-next").onclick = obNext;
  $("#ob-back").onclick = obBack;
  // Skipping the Cerebras key is NEVER silent (owner §8): the first click reveals
  // the consequence; only a deliberate second click proceeds.
  $$("#onboarding [data-ob-skip]").forEach(
    (b) =>
      (b.onclick = () => {
        const warn = $("#ob-skip-warn");
        if (warn && warn.hidden) {
          warn.hidden = false;
          b.textContent = "Skip anyway — AI features stay off";
          b.classList.remove("t-mute");
          b.style.color = "var(--amber)";
          return;
        }
        obNext();
      }),
  );
  $("#ob-get-key").onclick = () => {
    const prov = $("#ob-provider")?.value || OB.provider || "cerebras";
    const url = prov === "openrouter" ? "https://openrouter.ai/keys" : "https://cloud.cerebras.ai/";
    call("open_url", url);
  };
  $("#ob-test-key").onclick = obTestKey;
  $("#ob-finish").onclick = finishOnboarding;
  $$("#ob-lang .ob-choice").forEach(
    (b) =>
      (b.onclick = () => {
        $$("#ob-lang .ob-choice").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        OB.lang = b.dataset.lang;
        // Reveal the "which language most?" picker only for multilingual.
        const wrap = $("#ob-primary-wrap");
        if (wrap) wrap.hidden = b.dataset.lang !== "multi";
      }),
  );
  // Transcription mode step (local vs cloud)
  $$("#ob-tx-mode .ob-choice").forEach(
    (b) =>
      (b.onclick = () => {
        $$("#ob-tx-mode .ob-choice").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        OB.txMode = b.dataset.tx;
      }),
  );
  // Name input: save on blur so it persists even if they skip ahead
  $("#ob-name")?.addEventListener("change", () => {
    const v = $("#ob-name").value.trim();
    OB.userName = v;
    call("set_setting", "user_name", v);
    SET.user_name = v;
  });
  // Provider choice in onboarding: save the chosen LLM provider
  $("#ob-provider")?.addEventListener("change", async () => {
    const v = $("#ob-provider").value;
    OB.provider = v;
    await call("set_setting", "llm_provider", v);
    SET.llm_provider = v;
    await refreshRouteState();
    // Update the get-key button URL
    const openUrl = v === "openrouter" ? "https://openrouter.ai/keys" : "https://cloud.cerebras.ai/";
    const getKeyBtn = $("#ob-get-key");
    if (getKeyBtn) getKeyBtn.onclick = () => call("open_url", openUrl);
  });
  $("#ob-mic-test")?.addEventListener("click", async () => {
    flash($("#ob-mic-fb"), "Listening 5s…", "busy");
    const r = await call("test_mic", +($("#ob-mic")?.value ?? -1));
    flash($("#ob-mic-fb"), r.message, r.ok ? "ok" : "err");
  });
  // populate onboarding mic dropdown; picking one saves it for real
  call("list_microphones").then((mics) => {
    const s = $("#ob-mic");
    if (s && mics) {
      s.innerHTML = mics
        .map((m) => `<option value="${m.index}">${esc(m.name)}</option>`)
        .join("");
      s.addEventListener("change", () => {
        const v = +s.value;
        call("set_setting", "mic_device", v < 0 ? null : v);
      });
    }
  });
  // visual experience choice (Standard / Lite) — applies live as you pick
  $$("#ob-effects button").forEach(
    (b) =>
      (b.onclick = () => {
        $$("#ob-effects button").forEach((x) =>
          x.classList.toggle("active", x === b),
        );
        applyEffects(b.dataset.fx);
      }),
  );

  await bootHome();
  syncShimmer(); // phase-lock Home's cards to the global shimmer cycle

  // THE BIG SHIFT: the Deck is pinned by default. Reflect the persisted choice on
  // the Deck's Pin button label (the window itself is pinned on launch by the
  // shell). Don't re-call set_pinned here — just sync the UI state.
  try {
    const s = await call("get_settings");
    HX.pinned = s && s.deck_pinned !== false;
    const pinBtn = $("#hist-pin");
    if (pinBtn) {
      pinBtn.classList.toggle("btn-gold", HX.pinned);
      pinBtn.classList.toggle("btn-ghost", !HX.pinned);
    }
    setText("#hist-pin-label", HX.pinned ? "Pinned" : "Pin");
  } catch (e) {}

  // first run → onboarding
  const o = await call("get_overview");
  if (o.first_run) {
    $("#onboarding").hidden = false;
    try { call("set_onboarding_mode", true); } catch (e) {}  // drop always-on-top during setup
    obShow(1);
  }
  pollStatus(); // live chip sync with the controller (no-op in preview)
}

let SETTINGS_CATEGORY = "general";

function showSettingsCategory(category, focusTab) {
  const tabs = $$("[data-settings-tab]");
  if (!tabs.some((tab) => tab.dataset.settingsTab === category))
    category = "general";
  SETTINGS_CATEGORY = category;

  tabs.forEach((tab) => {
    const selected = tab.dataset.settingsTab === category;
    tab.setAttribute("aria-selected", selected ? "true" : "false");
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focusTab) tab.focus();
  });

  $$('[data-view="settings"] [data-settings-category]').forEach((panel) => {
    const categories = (panel.dataset.settingsCategory || "").split(/\s+/);
    panel.hidden = !categories.includes(category);
  });
}

function wireSettingsCategoryNav() {
  const tabs = $$("[data-settings-tab]");
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => showSettingsCategory(tab.dataset.settingsTab));
    tab.addEventListener("keydown", (event) => {
      let next = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown")
        next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp")
        next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      if (next == null) return;
      event.preventDefault();
      showSettingsCategory(tabs[next].dataset.settingsTab, true);
    });
  });
  showSettingsCategory(SETTINGS_CATEGORY);
}

function wireSettingsControls() {
  wireSettingsCategoryNav();
  // model dropdowns are filled per-provider by reflectProvider() → populateModels()
  // binding captures
  $$("[data-capture]").forEach(
    (b) =>
      (b.onclick = () =>
        captureBinding(b.dataset.capture, b.dataset.label, b.dataset.fb)),
  );
  // key tests
  $$("[data-testkey]").forEach(
    (b) =>
      (b.onclick = () =>
        testKey(b.dataset.testkey, b.dataset.keyfield, b.dataset.fb)),
  );
  // OpenRouter balance readout (owner 2026-06-20: surface credits inside Mumble).
  $("#or-credits-btn")?.addEventListener("click", async () => {
    const out = $("#or-credits");
    const btn = $("#or-credits-btn");
    if (out) {
      out.hidden = false;
      out.style.color = "";
      out.textContent = "Checking balance…";
    }
    if (btn) btn.disabled = true;
    try {
      const r = await call("get_openrouter_credits");
      if (r && r.ok) {
        out.textContent = `Balance: $${r.remaining.toFixed(2)} remaining (of $${r.total.toFixed(2)}; $${r.used.toFixed(2)} used).`;
      } else {
        out.style.color = "var(--amber)";
        out.textContent =
          (r && r.message) ||
          "Couldn't fetch balance — save your OpenRouter key first.";
      }
    } catch (e) {
      out.style.color = "var(--amber)";
      out.textContent = "Couldn't fetch balance right now.";
    } finally {
      if (btn) btn.disabled = false;
    }
  });
  $("#set-mic-test")?.addEventListener("click", () => testMic("#set-mic-fb"));
  // Cloud transcription: record a short clip and prove the key/provider/model
  // works (and how fast it is). Disable the button while it records+uploads.
  $("#set-tx-test")?.addEventListener("click", async () => {
    const btn = $("#set-tx-test");
    const prov = SET.cloud_transcription_provider || "groq";
    flash($("#set-tx-fb"), "Listening 3s — speak now…", "busy");
    if (btn) btn.disabled = true;
    try {
      const r = await call("test_transcription", prov, 3);
      flash($("#set-tx-fb"), r.message, r.ok ? "ok" : "err");
    } finally {
      if (btn) btn.disabled = false;
    }
  });
  // Open the right key page for whichever cloud-STT provider is selected.
  $("#set-tx-get-key")?.addEventListener("click", () => {
    const urls = {
      groq: "https://console.groq.com/keys",
      openai: "https://platform.openai.com/api-keys",
      openrouter: "https://openrouter.ai/keys",
    };
    call("open_url", urls[SET.cloud_transcription_provider || "groq"]);
  });
  $("#set-vocab-save")?.addEventListener("click", () =>
    saveVocab("#set-vocab-fb"),
  );
  $("#set-presets-save")?.addEventListener("click", () =>
    savePresets("#set-presets-fb"),
  );
  $("#set-open-data")?.addEventListener("click", () =>
    call("open_data_folder"),
  );
  $("#set-get-key")?.addEventListener("click", () =>
    call("open_url", "https://cloud.cerebras.ai/"),
  );
  $("#set-replay-tour")?.addEventListener("click", () => {
    $("#onboarding").hidden = false;
    try { call("set_onboarding_mode", true); } catch (e) {}
    obShow(1);
  });
  $("#uc-close")?.addEventListener("click", closeUpdateCenter);
  $("#update-center")?.addEventListener("click", (e) => {
    if (e.target.id === "update-center") closeUpdateCenter();
  });
  $("#set-switch-lite")?.addEventListener("click", async () => {
    const ok = await call("switch_to_lite");
    toast(
      ok
        ? "Opening Mumble Lite…"
        : "Mumble Lite isn’t included with this install",
      ok ? "info" : "err",
    );
  });
  $("#set-reset-window")?.addEventListener("click", async () => {
    const ok = await call("reset_window_size");
    toast(
      ok
        ? "Window reset to the design size"
        : "Resize works in the live app window",
      ok ? "ok" : "info",
    );
  });
  // Reset statistics lives in Settings now (owner) — wire it here so it works
  // even if the Stats view was never opened this session.
  wireStatsReset();
  // UPDATE CENTER (UX-Pilot port) — "Check now" opens a dedicated view with
  // the current version, live check progress and the HONEST result
  // (available / up to date / couldn't check — never a faked "latest").
  $("#set-check-updates")?.addEventListener("click", openUpdateCenter);
  // Share Mumble — a real pitch someone can read, with the GitHub placeholder
  const SHARE_TEXT = [
    "🎙️ Mumble — Speak. It types.",
    "",
    "I dictate instead of typing now. Mumble turns your voice into clean,",
    "polished text anywhere on your Mac — press one hotkey, talk, and it",
    "pastes at your cursor. Local transcription keeps audio on-device;",
    "optional Cloud transcription sends clips to your provider. AI polish shapes emails, lists",
    "and prompts using your own free API key.",
    "",
    "Distribution link not published yet.",
  ].join("\n");
  const prev = $("#share-preview");
  if (prev) prev.textContent = SHARE_TEXT;
  $("#set-share-copy")?.addEventListener("click", async () => {
    const ok = await copyTextReliable(SHARE_TEXT);
    flash($("#set-share-fb"), ok ? "Copied — paste it anywhere" : "Couldn't copy",
          ok ? "ok" : "err");
  });
  // Account card (cloud sync — Supabase auth)
  wireAccountCard();
  // provider dropdown change handled via generic binding + reflectProvider
}

window.addEventListener("pywebviewready", boot);
window.addEventListener("DOMContentLoaded", () => {
  if (!HAS_PY()) boot();
});
