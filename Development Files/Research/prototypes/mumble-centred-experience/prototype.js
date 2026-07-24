(function () {
  "use strict";

  const variants = [
    { key: "A", name: "Focus Stage", render: renderA },
    { key: "B", name: "Centred Workbench", render: renderB },
    { key: "C", name: "Guided Spine", render: renderC },
  ];
  const surfaces = ["home", "deck", "stats", "meetings", "reader", "settings", "find"];
  const states = ["ready", "loading", "empty", "error", "recording", "processing"];
  const params = new URLSearchParams(location.search);
  const model = {
    variant: normalise(params.get("variant"), variants.map((item) => item.key), "A"),
    surface: normalise(params.get("surface"), surfaces, "home"),
    state: normalise(params.get("state"), states, "ready"),
    reduceMotion: params.get("motion") === "reduce",
    island: params.get("island") !== "hidden",
    clean: params.get("clean") === "1",
  };

  const shortcutRows = [
    ["Dictate", "Start or stop anywhere", "Ctrl Win"],
    ["Paste latest", "Use the newest finished result", "Ctrl Alt V"],
    ["Open Deck", "Shape, reuse, and finish", "Ctrl Alt D"],
    ["Mumble Find", "Find apps, files, and folders", "Ctrl Alt F"],
    ["Web Search", "Search selected words online", "Ctrl Alt S"],
  ];

  const deckItems = [
    { type: "Transcript", title: "Project handoff notes", meta: "Today · 184 words", copy: "Keep release claims separate from verified installation evidence." },
    { type: "Image", title: "interface-reference.png", meta: "1440 × 900 · 286 KB", image: true },
    { type: "Clipboard", title: "Meeting follow-up", meta: "12 minutes ago · 42 words", copy: "Send the revised plan after the architecture review." },
  ];

  const routeFacts = [
    ["Receives", "Microphone audio", "Transcript text"],
    ["Runs", "This device", "This device"],
    ["Leaves device", "Nothing", "Nothing"],
    ["Speed", "Fast after model is ready", "Immediate rules"],
    ["Quality", "Local speech model", "Reliable cleanup; limited rewriting"],
    ["Cost", "Free", "Free"],
  ];

  function normalise(value, allowed, fallback) {
    return allowed.includes(value) ? value : fallback;
  }

  function esc(value) {
    return String(value).replace(/[&<>"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[char]);
  }

  function icon(name) {
    const symbols = {
      mic: "●", deck: "▤", find: "⌕", web: "↗", image: "▧", copy: "▣",
      meeting: "◉", settings: "≡", pause: "Ⅱ", stop: "■", folder: "▰",
      file: "▥", app: "▦", reader: "¶", stats: "▥", check: "✓", alert: "!",
    };
    return `<span class="ui-icon" aria-hidden="true">${symbols[name] || "•"}</span>`;
  }

  function button(label, kind, extra) {
    return `<button type="button" class="button ${kind || "quiet"}" ${extra || ""}>${esc(label)}</button>`;
  }

  function routeBadge() {
    return `<span class="route-badge"><i aria-hidden="true"></i>Local · audio stays on this device</span>`;
  }

  function durationTruth() {
    return `<p class="duration-truth">Short dictation currently stops after 10 minutes. Use Meetings for longer recordings.</p>`;
  }

  function shortcutList(className) {
    return `<div class="${className || "shortcut-list"}" aria-label="Global shortcuts">${shortcutRows.map((row) => `
      <div class="shortcut-row"><span>${esc(row[0])}<small>${esc(row[1])}</small></span><kbd>${esc(row[2])}</kbd></div>`).join("")}</div>`;
  }

  function deckRows(compact) {
    return `<div class="deck-items ${compact ? "compact" : ""}" role="list" aria-label="Deck items">${deckItems.map((item, index) => `
      <article class="deck-item ${item.image ? "image-item" : ""}" role="listitem" tabindex="0" ${index === 0 ? 'aria-selected="true"' : ""}>
        <div class="item-anchor ${item.image ? "image-thumb" : "text-thumb"}" aria-hidden="true">${item.image ? `<span class="mountain"></span>` : icon("copy")}</div>
        <div class="item-copy"><span class="item-type">${esc(item.type)}</span><strong>${esc(item.title)}</strong><small>${esc(item.meta)}</small>${item.copy ? `<p>${esc(item.copy)}</p>` : ""}</div>
        <div class="item-actions">
          ${item.image ? `<button type="button" class="button gold paste-image" aria-label="Paste image into previous app">${icon("image")}Paste image</button>` : `<button type="button" class="icon-button" aria-label="Paste ${esc(item.title)} into previous app">${icon("copy")}</button>`}
          <button type="button" class="icon-button danger" aria-label="Delete ${esc(item.title)}">×</button>
        </div>
      </article>`).join("")}</div>`;
  }

  function deckCommands(layout) {
    return `<section class="deck-command-bar ${layout || ""}" aria-label="Deck commands">
      <div class="command-scope" role="tablist" aria-label="Content type">
        <button class="active" role="tab" aria-selected="true">All <b>24</b></button>
        <button role="tab" aria-selected="false">Transcripts <b>9</b></button>
        <button role="tab" aria-selected="false">Clipboard <b>8</b></button>
        <button role="tab" aria-selected="false">Prompts <b>7</b></button>
      </div>
      <label class="search-field"><span class="sr-only">Filter Deck</span>${icon("find")}<input type="search" placeholder="Filter Deck"></label>
      <select aria-label="Sort Deck"><option>Newest first</option><option>Oldest first</option></select>
      <button type="button" class="button quiet" aria-pressed="false">Starred</button>
      <button type="button" class="button quiet" aria-pressed="false">Pin</button>
      <button type="button" class="button quiet">More</button>
    </section>`;
  }

  function shapeControls(layout) {
    return `<section class="shape-controls ${layout || ""}" aria-label="Shape selected Deck items">
      <span class="selection-count">1 selected</span>
      <label>Mode<select><option>Prompt</option><option>Email</option><option>Reply</option></select></label>
      <label>Preset<select><option>Professional</option><option>Summarise</option><option>Extract actions</option></select></label>
      <button type="button" class="button gold">Run shaping</button>
      <button type="button" class="button quiet">Search the web</button>
    </section>`;
  }

  function meetingState(state, variantClass) {
    if (state === "loading") return statePanel("loading", "Preparing the microphone", "Checking the selected input and local transcription route.");
    if (state === "error") return statePanel("error", "Microphone needs attention", "Mumble could not open the selected microphone. Choose another device in Settings.", "Open Settings");
    if (state === "empty") return statePanel("empty", "No saved meetings yet", "Record a meeting or import audio to begin your private library.", "Import audio");
    if (state === "recording" || state === "processing") {
      const processing = state === "processing";
      return `<section class="meeting-live ${variantClass || ""}" aria-live="polite" data-primary-focus tabindex="-1">
        <div class="meeting-aura" aria-hidden="true"><span></span><span></span><span></span></div>
        <div class="meeting-live-copy"><span class="eyebrow">During the meeting</span><h2>${processing ? "Saving and transcribing" : "Recording locally"}</h2>
          <time>${processing ? "42% complete" : "18:42"}</time><p>${processing ? "The recording is safe on this device. You can keep working." : "Audio is being written safely to this device."}</p></div>
        <div class="level-meter" aria-label="Microphone level"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
        <div class="meeting-live-actions">${processing ? button("Cancel processing", "quiet") : `${button("Pause", "quiet")}${button("Stop & save", "gold")}`}</div>
      </section>`;
    }
    return `<section class="meeting-before ${variantClass || ""}" data-primary-focus tabindex="-1">
      <div><span class="eyebrow">Before the meeting</span><h2>Start with a recording</h2><p>Capture a conversation or bring in audio you already have.</p>${routeBadge()}</div>
      <div class="meeting-choice primary">${icon("meeting")}<span><small>Live conversation</small><strong>Record meeting</strong><em>Use the selected microphone</em></span><button type="button" aria-label="Record meeting">→</button></div>
      <div class="meeting-choice">${icon("folder")}<span><small>Already recorded</small><strong>Import audio</strong><em>MP3, WAV, M4A, or FLAC</em></span><button type="button" aria-label="Import audio">→</button></div>
    </section>`;
  }

  function statePanel(kind, title, copy, action) {
    const rows = kind === "loading" ? `<div class="skeleton-stack" aria-hidden="true"><i></i><i></i><i></i></div>` : "";
    return `<section class="state-panel ${kind}" role="status" ${kind === "loading" ? 'aria-busy="true"' : ""}>
      <span class="state-symbol" aria-hidden="true">${kind === "error" ? "!" : kind === "empty" ? "○" : ""}</span>
      <h2>${esc(title)}</h2><p>${esc(copy)}</p>${rows}${action ? button(action, kind === "error" ? "gold" : "quiet") : ""}
    </section>`;
  }

  function statsContent(layout) {
    return `<section class="stats-preserve ${layout || ""}" data-prototype-surface="stats">
      <header><span class="eyebrow">Preservation pass</span><h1>Your work, made understandable</h1><p>The hierarchy stays calm; definitions and states become clearer.</p></header>
      <div class="stat-metrics"><article><strong>12,480</strong><span>Output words</span><small>Words Mumble finished and returned</small></article><article><strong>18</strong><span>Active days</span><small>Days with at least one finished result</small></article><article><strong>121</strong><span>Estimated WPM</span><small>Speech rate, not typing speed</small></article></div>
      <div class="chart-card"><div><strong>Daily output</strong><small>Last seven days</small></div><div class="bars" aria-label="Daily output values: 980, 1420, 1100, 1720, 1260, 1950, 1640"><i style="--v:45%"></i><i style="--v:66%"></i><i style="--v:52%"></i><i style="--v:80%"></i><i style="--v:59%"></i><i style="--v:92%" class="today"></i><i style="--v:75%"></i></div><p class="chart-note"><b>Today:</b> 1,950 words · Gold plus the “Today” label identifies the current value.</p></div>
      <div class="preserve-note"><strong>Preserve</strong><span>Reader and Meetings activity, mode mix, trend summaries, accessible data tables, and explicit loading/partial/reset states.</span></div>
    </section>`;
  }

  function readerContent(layout) {
    return `<section class="reader-preserve ${layout || ""}" data-prototype-surface="reader">
      <header><span class="eyebrow">Preservation pass</span><h1>Continue where you left off</h1><p>The library stays first; provider detail stays secondary to voice and reading.</p></header>
      <article class="continue-card"><div class="book-mark" aria-hidden="true">¶</div><div><small>Continue reading</small><strong>Designing Calm Technology</strong><span>Chapter 4 · 38% complete</span></div><button class="button gold">Continue</button></article>
      <div class="reader-grid"><aside><label>Voice<select><option>Warm narrator · Local</option></select></label><label>Speed<select><option>1.0×</option></select></label><p>No paid credits for this route.</p></aside><div class="library-list"><label class="search-field">${icon("find")}<input type="search" placeholder="Filter library"></label><article tabindex="0"><strong>Designing Calm Technology</strong><small>PDF · 38% · last opened today</small></article><article tabindex="0"><strong>Voice Interfaces That Work</strong><small>EPUB · 12% · local voice available</small></article><article tabindex="0"><strong>Meeting notes collection</strong><small>Text · not started</small></article></div></div>
      <div class="preserve-note"><strong>Preserve</strong><span>Library filters, document find, bookmarks, summary disclosure, reading position, progress, and reduced-motion highlighting.</span></div>
    </section>`;
  }

  function settingsRouteTable(layout) {
    return `<section class="route-truth ${layout || ""}" data-prototype-surface="settings">
      <header><span class="eyebrow">Effective routes</span><h1>Know what happens to your words</h1><p>Two stages, named in ordinary language. Requested and effective routes remain distinct.</p></header>
      <div class="route-headings"><article>${icon("mic")}<span><small>Stage 1</small><strong>Speech to text</strong><em>On this device · faster-whisper</em></span></article><b aria-hidden="true">→</b><article>${icon("deck")}<span><small>Stage 2</small><strong>Text shaping</strong><em>On this device · rules</em></span></article></div>
      <div class="route-grid" role="table" aria-label="Speech and shaping route comparison">${routeFacts.map((row) => `<div role="row"><strong role="rowheader">${esc(row[0])}</strong><span role="cell">${esc(row[1])}</span><span role="cell">${esc(row[2])}</span></div>`).join("")}</div>
      <div class="route-choice"><div><strong>Text shaping</strong><p>Choose where transcript text is shaped.</p></div><label><input type="radio" name="shape-route" checked> On this device</label><label><input type="radio" name="shape-route"> Hosted provider</label><button class="button quiet">Advanced provider details</button></div>
    </section>`;
  }

  function homeSharedIntro() {
    return `<span class="eyebrow">Private voice workspace</span><h1>Speak naturally.<br>Finish clearly.</h1><p>Local speech to text, thoughtful shaping, and your work returned exactly where it belongs.</p>`;
  }

  function renderA(surface, state) {
    if (surface === "stats") return statsContent("a-preserve");
    if (surface === "reader") return readerContent("a-preserve");
    if (surface === "deck") return `<section class="surface-shell variant-a deck-a" data-prototype-surface="deck">
      <header class="centred-intro"><span class="eyebrow">Deck</span><h1>Browse first. Shape when ready.</h1><p>One calm command bar keeps scope and search visible. Selection reveals shaping.</p></header>
      ${deckCommands("a-command")}${deckRows(false)}${shapeControls("a-shape")}</section>`;
    if (surface === "meetings") return `<section class="surface-shell variant-a meetings-a" data-prototype-surface="meetings"><header class="centred-intro"><span class="eyebrow">Meetings</span><h1>One shell, from capture to outcome.</h1><p>The recording surface transforms without losing its Mumble identity.</p></header>${meetingState(state, "a-meeting")}<div class="meeting-after"><strong>After the meeting</strong><span>Searchable recordings and transcripts will appear here.</span></div></section>`;
    if (surface === "settings") return `<section class="surface-shell variant-a settings-a">${settingsRouteTable("a-settings")}<nav class="settings-sections" aria-label="Settings sections"><button class="active">Overview</button><button>Speech to text</button><button>Text shaping</button><button>Deck &amp; data</button><button>System</button></nav></section>`;
    return `<section class="surface-shell variant-a home-a" data-prototype-surface="home">
      <div class="focus-stage"><div class="stage-copy">${homeSharedIntro()}<div class="stage-actions"><button class="record-orb" data-primary-focus aria-label="Start dictation">${icon("mic")}<span>Start dictation</span><kbd>Ctrl Win</kbd></button><button class="button quiet">Open Deck</button></div>${routeBadge()}${durationTruth()}</div>
      <aside class="shortcut-vault"><div><span class="eyebrow">Everywhere</span><h2>Five clear commands</h2></div>${shortcutList("shortcut-list")}</aside></div>
      <div class="discovery-pair"><button>${icon("find")}<span><strong>Mumble Find</strong><small>Private apps, files, and folders</small></span><kbd>Ctrl Alt F</kbd></button><button>${icon("web")}<span><strong>Web Search</strong><small>Selected words sent to your chosen provider</small></span><kbd>Ctrl Alt S</kbd></button></div>
    </section>`;
  }

  function renderB(surface, state) {
    if (surface === "stats") return statsContent("b-preserve");
    if (surface === "reader") return readerContent("b-preserve");
    if (surface === "deck") return `<section class="surface-shell variant-b deck-b" data-prototype-surface="deck">
      <div class="editorial-head"><div><span class="eyebrow">Deck workspace</span><h1>Everything in view.</h1></div><p>Persistent browse controls above; selected-item actions stay beside the content.</p></div>
      ${deckCommands("b-command")}<div class="deck-workbench"><div>${deckRows(true)}</div><aside class="context-inspector"><span class="eyebrow">Selected item</span><h2>Project handoff notes</h2><p>184 words · Transcript · Today</p><div class="inspector-actions">${button("Paste latest", "gold")}${button("Copy", "quiet")}${button("Search the web", "quiet")}</div>${shapeControls("b-shape")}</aside></div></section>`;
    if (surface === "meetings") return `<section class="surface-shell variant-b meetings-b" data-prototype-surface="meetings"><div class="editorial-head"><div><span class="eyebrow">Meetings</span><h1>Capture control room</h1></div>${routeBadge()}</div><div class="meeting-workbench">${meetingState(state, "b-meeting")}<aside class="meeting-context"><strong>Before</strong><span class="active">During</span><span>After</span><hr><p>Recording remains visible while library context stays available.</p><div><small>Input</small><b>Default microphone</b></div><div><small>Route</small><b>Local · faster-whisper</b></div></aside></div></section>`;
    if (surface === "settings") return `<section class="surface-shell variant-b settings-b"><div class="settings-workbench"><nav aria-label="Settings sections"><span>Settings</span><button class="active">Overview</button><button>Speech to text</button><button>Text shaping</button><button>Deck &amp; data</button><button>System</button></nav>${settingsRouteTable("b-settings")}</div></section>`;
    return `<section class="surface-shell variant-b home-b" data-prototype-surface="home">
      <div class="home-dashboard"><header>${homeSharedIntro()}${routeBadge()}</header><div class="dashboard-command"><button class="wide-dictate" data-primary-focus>${icon("mic")}<span><strong>Start dictation</strong><small>Ready · local speech model warm</small></span><kbd>Ctrl Win</kbd></button><button class="button quiet">Paste latest</button><button class="button quiet">Open Deck</button></div>
      <section class="dashboard-grid"><article class="command-gallery"><div><span class="eyebrow">Command gallery</span><h2>Move without losing context</h2></div><button data-open-find>${icon("find")}<span><strong>Mumble Find</strong><small>Apps, files &amp; folders · private</small></span></button><button>${icon("web")}<span><strong>Web Search</strong><small>Uses your chosen provider</small></span></button></article><aside>${shortcutList("shortcut-ledger")}</aside></section>${durationTruth()}</div>
    </section>`;
  }

  function renderC(surface, state) {
    if (surface === "stats") return statsContent("c-preserve");
    if (surface === "reader") return readerContent("c-preserve");
    if (surface === "deck") return `<section class="surface-shell variant-c deck-c" data-prototype-surface="deck">
      <header class="spine-intro"><span class="eyebrow">Deck</span><h1>Browse → choose → shape</h1><p>Commands follow the work instead of surrounding it.</p></header>
      <div class="workflow-spine"><section data-step="1"><span class="step-dot">1</span><div><h2>Browse</h2>${deckCommands("c-command")}</div></section><section data-step="2"><span class="step-dot">2</span><div><h2>Choose</h2>${deckRows(true)}</div></section><section data-step="3"><span class="step-dot">3</span><div><h2>Shape</h2>${shapeControls("c-shape")}</div></section></div></section>`;
    if (surface === "meetings") return `<section class="surface-shell variant-c meetings-c" data-prototype-surface="meetings"><header class="spine-intro"><span class="eyebrow">Meetings</span><h1>Before → during → after</h1><p>One vertical state path keeps the current moment unmistakable.</p></header><div class="meeting-spine"><ol aria-label="Meeting stages"><li class="done">Before</li><li class="active">${state === "recording" ? "During" : state === "processing" ? "Saving" : "Ready"}</li><li>After</li></ol>${meetingState(state, "c-meeting")}</div></section>`;
    if (surface === "settings") return `<section class="surface-shell variant-c settings-c"><header class="spine-intro"><span class="eyebrow">Settings</span><h1>Follow the route</h1><p>Speech becomes text, then text may be shaped. Each stage declares its truth.</p></header><div class="settings-spine"><nav aria-label="Settings sections"><button class="active">Overview</button><button>Speech to text</button><button>Text shaping</button><button>Deck &amp; data</button><button>System</button></nav>${settingsRouteTable("c-settings")}</div></section>`;
    return `<section class="surface-shell variant-c home-c" data-prototype-surface="home">
      <header class="spine-intro">${homeSharedIntro()}${routeBadge()}</header><div class="home-spine">
        <section><span class="step-dot">1</span><div><small>Speak</small><h2>Start local dictation</h2><button class="spine-record" data-primary-focus>${icon("mic")}<span>Start dictation</span><kbd>Ctrl Win</kbd></button></div><aside>${durationTruth()}</aside></section>
        <section><span class="step-dot">2</span><div><small>Shape</small><h2>Choose only when needed</h2><p>Plain text stays local. Explicit modes make the next action clear.</p></div><aside><button class="button quiet">Prompt</button><button class="button quiet">Email</button><button class="button quiet">Reply</button></aside></section>
        <section><span class="step-dot">3</span><div><small>Use</small><h2>Return to your work</h2><p>Paste, reuse in Deck, find locally, or search the web.</p></div><aside><button data-open-find>${icon("find")}Mumble Find</button><button>${icon("web")}Web Search</button></aside></section>
      </div><div class="spine-shortcuts">${shortcutList("shortcut-strip")}</div></section>`;
  }

  function findDialog(variant, state) {
    const resultRows = [
      ["app", "Mumble", "Application · this device"],
      ["folder", "Development Files", "Folder · C:\\Mumble"],
      ["file", "mumble-interface-contract-research.md", "Markdown · Research"],
      ["app", "Settings", "System application"],
    ];
    let body = resultRows.map((row, index) => `<div class="find-result ${index === 0 ? "selected" : ""}" role="option" aria-selected="${index === 0}"><span class="drag-grip" aria-label="Drag ${esc(row[1])}">⠿</span>${icon(row[0])}<span><strong>${esc(row[1])}</strong><small>${esc(row[2])}</small></span><kbd>${index + 1}</kbd></div>`).join("");
    if (state === "loading") body = `<div class="find-loading" role="status" aria-busy="true"><div class="skeleton-line"></div><div class="skeleton-line"></div><div class="skeleton-line"></div><p>Loading your app catalogue. Files continue in the background.</p></div>`;
    if (state === "empty") body = `<div class="find-empty" role="status"><span aria-hidden="true">○</span><h3>No local matches</h3><p>Try fewer words or another category. Web Search stays separate.</p></div>`;
    if (state === "error") body = `<div class="find-empty error" role="alert"><span aria-hidden="true">!</span><h3>File index unavailable</h3><p>Your apps still work. File coverage is incomplete until the system index returns.</p><button class="button gold">Retry local index</button></div>`;
    const modeClass = variant.key.toLowerCase();
    return `<div class="find-overlay open" role="presentation"><div class="find-backdrop" data-close-find></div><section class="find-dialog find-${modeClass}" role="dialog" aria-modal="true" aria-labelledby="findTitle" data-prototype-surface="find">
      <header class="find-header"><div><span class="eyebrow">Mumble Find · local</span><h2 id="findTitle">Find apps &amp; files</h2><p>Private on this device · ${state === "error" ? "partial coverage" : "7,263 indexed items"}</p></div><button class="icon-button" data-close-find aria-label="Close Mumble Find">×</button></header>
      <label class="find-query">${icon("find")}<span class="sr-only">Find apps, files, and folders</span><input id="findInput" data-primary-focus type="search" value="mumble" placeholder="Find apps, files, and folders"><kbd>Ctrl Alt F</kbd></label>
      <div class="find-layout"><nav class="find-filters" aria-label="Result type"><button class="active">Everything</button><button>Apps</button><button>Files</button><button>Folders</button></nav><div class="find-results" role="listbox" aria-label="Local results">${body}</div>${variant.key === "B" ? `<aside class="find-preview"><span class="app-preview">M</span><strong>Mumble</strong><p>Local voice workspace</p><button class="button gold">Open</button><button class="button quiet">Show in folder</button></aside>` : ""}</div>
      <footer><span><kbd>↑↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Drag</kbd> send file</span><span><kbd>Esc</kbd> close</span></footer>
    </section></div>`;
  }

  function islandMarkup(variant, state) {
    const processing = state === "processing";
    const recording = state === "recording" || (!processing && state === "ready");
    const action = processing ? "Cancel" : "Stop";
    return `<aside class="island-demo island-${variant.key.toLowerCase()} ${processing ? "processing" : "listening"}" aria-label="Mumble floating status and controls">
      <div class="primary-island" aria-live="polite"><i aria-hidden="true"></i><strong>${processing ? "Shaping text" : "Listening"}</strong><time>${processing ? "Result kept if cancelled" : "0:18"}</time><div class="mini-wave" aria-hidden="true"><b></b><b></b><b></b><b></b><b></b></div></div>
      <div class="island-rail" aria-label="Mumble controls"><button class="mode-chip" aria-pressed="true">Prompt</button><button>Deck</button><button class="stop-control" aria-label="${action} ${processing ? "text shaping" : "dictation"}">${icon(processing ? "alert" : "stop")}${action}</button></div>
    </aside>`;
  }

  function currentVariant() {
    return variants.find((item) => item.key === model.variant) || variants[0];
  }

  function render() {
    const variant = currentVariant();
    document.body.dataset.variant = variant.key;
    document.body.classList.toggle("reduce-motion", model.reduceMotion);
    document.body.classList.toggle("clean-capture", model.clean);
    const underlyingSurface = model.surface === "find" ? "home" : model.surface;
    document.getElementById("surfaceRoot").innerHTML = variant.render(underlyingSurface, model.state);
    document.getElementById("findRoot").innerHTML = model.surface === "find" ? findDialog(variant, model.state) : "";
    document.getElementById("islandRoot").innerHTML = model.island ? islandMarkup(variant, model.state) : "";
    document.getElementById("variantLabel").textContent = `${variant.key} — ${variant.name}`;
    document.getElementById("surfaceControl").value = model.surface;
    document.getElementById("stateControl").value = model.state;
    document.getElementById("motionControl").checked = model.reduceMotion;
    document.getElementById("islandControl").setAttribute("aria-pressed", model.island ? "true" : "false");
    document.getElementById("islandControl").textContent = model.island ? "Island shown" : "Island hidden";
    document.querySelectorAll(".primary-nav [data-surface]").forEach((button) => {
      const current = button.dataset.surface === underlyingSurface;
      if (current) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    document.getElementById("stateReadout").textContent = `Prototype state: direction ${variant.key}, ${model.surface}, ${model.state}, ${model.reduceMotion ? "reduced motion" : "standard motion"}.`;
    wireDynamicActions();
    if (params.get("focus") === "primary") window.setTimeout(() => document.querySelector("[data-primary-focus]")?.focus(), 0);
  }

  function updateUrl() {
    const next = new URLSearchParams(location.search);
    next.set("variant", model.variant);
    next.set("surface", model.surface);
    next.set("state", model.state);
    if (model.reduceMotion) next.set("motion", "reduce"); else next.delete("motion");
    if (!model.island) next.set("island", "hidden"); else next.delete("island");
    history.replaceState(null, "", `${location.pathname}?${next.toString()}`);
  }

  function setModel(key, value) {
    model[key] = value;
    updateUrl();
    render();
  }

  function cycleVariant(direction) {
    const index = variants.findIndex((item) => item.key === model.variant);
    model.variant = variants[(index + direction + variants.length) % variants.length].key;
    updateUrl();
    render();
  }

  function shouldIgnoreVariantKey(target) {
    return !!target?.closest?.("input, textarea, select, [contenteditable='true']");
  }

  function wireDynamicActions() {
    document.querySelectorAll("[data-open-find]").forEach((button) => button.addEventListener("click", () => setModel("surface", "find")));
    document.querySelectorAll("[data-close-find]").forEach((button) => button.addEventListener("click", () => setModel("surface", "home")));
  }

  document.querySelectorAll(".primary-nav [data-surface]").forEach((button) => button.addEventListener("click", () => setModel("surface", button.dataset.surface)));
  document.getElementById("surfaceControl").addEventListener("change", (event) => setModel("surface", event.target.value));
  document.getElementById("stateControl").addEventListener("change", (event) => setModel("state", event.target.value));
  document.getElementById("motionControl").addEventListener("change", (event) => setModel("reduceMotion", event.target.checked));
  document.getElementById("islandControl").addEventListener("click", () => setModel("island", !model.island));
  document.getElementById("previousVariant").addEventListener("click", () => cycleVariant(-1));
  document.getElementById("nextVariant").addEventListener("click", () => cycleVariant(1));
  document.querySelector("[data-open-find]").addEventListener("click", () => setModel("surface", "find"));
  window.addEventListener("keydown", (event) => {
    if (shouldIgnoreVariantKey(event.target)) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      cycleVariant(event.key === "ArrowRight" ? 1 : -1);
    }
  });

  function runSelfTest() {
    const checks = [];
    const assert = (condition, label) => checks.push({ label, pass: !!condition });
    assert(variants.length === 3, "three variants exist");
    assert(document.querySelectorAll(".primary-nav [data-surface]").length === 6, "exactly six primary destinations");
    assert(!document.querySelector('.primary-nav [data-surface="find"]'), "Mumble Find is not a destination");
    variants.forEach((variant) => {
      surfaces.filter((surface) => surface !== "find").forEach((surface) => {
        const html = variant.render(surface, surface === "meetings" ? "recording" : "ready");
        assert(html.includes(`data-prototype-surface="${surface}"`) || ["settings"].includes(surface), `${variant.key}/${surface} renders`);
      });
      const home = variant.render("home", "ready");
      assert(home.includes("Mumble Find") && home.includes("Web Search"), `${variant.key} separates discovery tools`);
      assert(home.includes("Short dictation currently stops after 10 minutes"), `${variant.key} carries truthful duration wording`);
      const deck = variant.render("deck", "ready");
      assert(deck.includes("Paste image") && deck.includes("Search the web") && deck.includes("Preset"), `${variant.key} covers Deck actions`);
      const settings = variant.render("settings", "ready");
      assert(settings.includes("Speech to text") && settings.includes("Text shaping") && settings.includes("Cost"), `${variant.key} carries route truth`);
      assert(findDialog(variant, "loading").includes('aria-busy="true"'), `${variant.key} Find loading state`);
      assert(findDialog(variant, "empty").includes("No local matches"), `${variant.key} Find empty state`);
      assert(findDialog(variant, "error").includes("partial coverage"), `${variant.key} Find error state`);
      assert(islandMarkup(variant, "recording").includes("Stop"), `${variant.key} Island Stop control`);
      assert(islandMarkup(variant, "processing").includes("Cancel"), `${variant.key} Island Cancel control`);
    });
    const fakeInput = document.createElement("input");
    assert(shouldIgnoreVariantKey(fakeInput), "arrow switching ignores focused inputs");
    assert(!shouldIgnoreVariantKey(document.body), "arrow switching works outside inputs");
    const output = document.getElementById("selfTestOutput");
    output.hidden = false;
    output.textContent = JSON.stringify({ ok: checks.every((check) => check.pass), checks }, null, 2);
    output.dataset.result = checks.every((check) => check.pass) ? "pass" : "fail";
  }

  render();
  if (params.get("selftest") === "1") runSelfTest();
})();
