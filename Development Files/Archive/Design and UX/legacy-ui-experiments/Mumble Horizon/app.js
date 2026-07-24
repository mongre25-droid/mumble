/*
 * Mumble Horizon — isolated UI experiment.
 * This file deliberately contains mock state only. It imports no Mumble code,
 * opens no command ports, and reads/writes no production data.
 */
(function () {
  "use strict";

  const $ = (q, root = document) => root.querySelector(q);
  const $$ = (q, root = document) => Array.from(root.querySelectorAll(q));
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

  const paths = {
    home:'<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v10h13V10"/><path d="M9.5 20v-6h5v6"/>',
    library:'<path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    meeting:'<path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM16 9a2.5 2.5 0 1 0 0-5"/><path d="M2.5 20v-2a5.5 5.5 0 0 1 11 0v2M14 13a4.5 4.5 0 0 1 7.5 3.3V20"/>',
    reader:'<path d="M4 5.5A3.5 3.5 0 0 1 7.5 2H12v18H7.5A3.5 3.5 0 0 0 4 23z"/><path d="M20 5.5A3.5 3.5 0 0 0 16.5 2H12v18h4.5A3.5 3.5 0 0 1 20 23z"/>',
    insights:'<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    settings:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.86 2.86-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21h-4v-.1A1.7 1.7 0 0 0 8.6 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.86-2.86.06-.06A1.7 1.7 0 0 0 4.2 15a1.7 1.7 0 0 0-1.6-1H2.5v-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.34-1.88l-.06-.06L6.66 4.2l.06.06A1.7 1.7 0 0 0 8.6 4a1.7 1.7 0 0 0 1-1.6v-.1h4v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.86 2.86-.06.06A1.7 1.7 0 0 0 19 8.4a1.7 1.7 0 0 0 1.6 1h.1v4h-.1a1.7 1.7 0 0 0-1.2 1.6Z"/>',
    search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    mic:'<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/>',
    spark:'<path d="m12 2 1.3 4.7L18 8l-4.7 1.3L12 14l-1.3-4.7L6 8l4.7-1.3zM19 15l.7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7zM5 14l.6 1.9 1.9.6-1.9.6L5 19l-.6-1.9-1.9-.6 1.9-.6z"/>',
    command:'<path d="M9 7h6a2 2 0 1 1 0 4H9a2 2 0 1 1 0-4ZM9 13h6a2 2 0 1 1 0 4H9a2 2 0 1 1 0-4Z"/><path d="M9 7v10M15 7v10"/>',
    more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    arrow:'<path d="m9 18 6-6-6-6"/>',
    waveform:'<path d="M3 12h2l2-7 4 14 3-11 3 8 2-4h2"/>',
    clipboard:'<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4.5V3h6v1.5M8 9h8M8 13h8M8 17h5"/>',
    brain:'<path d="M9.5 4A3.5 3.5 0 0 0 6 7.5c0 .3 0 .5.1.8A3.4 3.4 0 0 0 4 11.5c0 1.3.7 2.5 1.7 3.1A3.5 3.5 0 0 0 9.5 20c1 0 1.8-.4 2.5-1V5a3.4 3.4 0 0 0-2.5-1ZM14.5 4A3.5 3.5 0 0 1 18 7.5c0 .3 0 .5-.1.8a3.4 3.4 0 0 1 2.1 3.2c0 1.3-.7 2.5-1.7 3.1a3.5 3.5 0 0 1-3.8 5.4c-1 0-1.8-.4-2.5-1V5a3.4 3.4 0 0 1 2.5-1Z"/><path d="M8 9.5c1.6 0 3 1.3 3 3M16 14c-1.6 0-3 1.3-3 3"/>',
    star:'<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-2.9-5.6 2.9 1.1-6.2L3 9.6l6.2-.9z"/>',
    copy:'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
    mail:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    reply:'<path d="m9 17-6-5 6-5v3h3c5 0 8 2 9 7-2-2-4-3-8-3H9z"/>',
    globe:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    shuffle:'<path d="M16 3h5v5M4 20l7-7M21 3l-7 7M16 21h5v-5M15 15l6 6M4 4l5 5"/>',
    play:'<path d="m8 5 11 7-11 7z"/>',
    pause:'<path d="M8 5h3v14H8zM15 5h3v14h-3z"/>',
    stop:'<rect x="6" y="6" width="12" height="12" rx="2"/>',
    upload:'<path d="M12 16V4m0 0-5 5m5-5 5 5M5 20h14"/>',
    download:'<path d="M12 4v12m0 0 5-5m-5 5-5-5M5 20h14"/>',
    bolt:'<path d="m13 2-8 12h7l-1 8 8-12h-7z"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    flame:'<path d="M12 22c4 0 7-3 7-7 0-5-4-8-7-13 0 5-7 6-7 13 0 4 3 7 7 7Z"/><path d="M9 18c0-2 2-3 3-5 0 2 3 3 3 5a3 3 0 0 1-6 0Z"/>',
    gauge:'<path d="M4 18a9 9 0 1 1 16 0"/><path d="m12 13 4-4M8 18h8"/>',
    bookmark:'<path d="M6 4h12v17l-6-4-6 4z"/>',
    moon:'<path d="M20 15.5A8 8 0 0 1 8.5 4 8.5 8.5 0 1 0 20 15.5Z"/>',
    folder:'<path d="M3 6h7l2 2h9v11H3z"/>',
    lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M18.5 9A7 7 0 0 0 6 6.5L4 9M5.5 15A7 7 0 0 0 18 17.5l2-2.5"/>',
    close:'<path d="m6 6 12 12M18 6 6 18"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    external:'<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v6H5V6h6"/>'
  };
  function icon(name, cls = "") { return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.spark}</svg>`; }

  const nav = [
    ["home","Today","home"], ["library","Library","library"], ["meetings","Meetings","meeting"],
    ["reader","Reader","reader"], ["insights","Insights","insights"], ["settings","Settings","settings"]
  ];

  const deckItems = [
    {id:1,kind:"transcript",mode:"Text",time:"9:42 pm",words:38,text:"The new onboarding should feel less like setup and more like a quiet conversation that gets people to their first useful dictation."},
    {id:2,kind:"clipboard",mode:"Captured",time:"8:16 pm",words:74,text:"Customer research: people understand the privacy story immediately when the active processing route is visible beside the record control."},
    {id:3,kind:"prompt",mode:"Prompt",time:"6:08 pm",words:127,text:"Design a concise launch narrative for a private voice-to-text tool. Lead with the moment speech becomes finished writing, then prove local processing."},
    {id:4,kind:"transcript",mode:"Email",time:"4:31 pm",words:64,text:"Hi team, I have moved tomorrow’s design review to eleven so we can include the completed interaction prototype. The agenda remains the same."},
    {id:5,kind:"transcript",mode:"Foreign",time:"2:19 pm",words:22,text:"Please add barakah, tawakkul and the client’s Arabic name to my personal vocabulary before the call."},
    {id:6,kind:"clipboard",mode:"Image",time:"Yesterday",words:0,text:"Interface reference — meeting notes layout.png"},
    {id:7,kind:"transcript",mode:"Reply",time:"Yesterday",words:49,text:"That works for me. I’ll send the revised prototype before lunch and include a short explanation of the new navigation model."}
  ];
  const meetingData = [
    {id:"weekly",title:"Weekly product sync",meta:"Today · 34 min · 4 speakers",tag:"Product",active:true},
    {id:"research",title:"Reader research debrief",meta:"Yesterday · 52 min · 3 speakers",tag:"Research"},
    {id:"launch",title:"Launch readiness",meta:"8 July · 27 min · 5 speakers",tag:"Planning"},
    {id:"design",title:"Design critique",meta:"7 July · 1 hr 12 min · 6 speakers",tag:"Design"}
  ];
  const docs = [
    {id:"systems",title:"Thinking in Systems",meta:"PDF · 318 pages",progress:68},
    {id:"brief",title:"Mumble product brief",meta:"DOCX · 24 min left",progress:42},
    {id:"paper",title:"Voice Interfaces in Practice",meta:"EPUB · Not started",progress:0},
    {id:"notes",title:"Research notes — July",meta:"Markdown · 8 min left",progress:83}
  ];

  const state = {
    view:"home", deckTab:"all", deckQuery:"", selected:new Set([1,3]), mode:"prompt", workflow:"Turn into a clear project brief",
    meeting:"weekly", meetingRecording:false, readerDoc:"systems", readerPlaying:true, settingsSection:"appearance",
    theme:"horizon", recording:false, prompt:false, contextOpen:false
  };

  const titles = {
    home:["FRIDAY, 10 JULY","Good evening, Khaled"], library:["YOUR WORKSPACE","Library"], meetings:["CAPTURE & UNDERSTAND","Meetings"],
    reader:["LISTEN & LEARN","Reader"], insights:["YOUR MOMENTUM","Insights"], settings:["MAKE IT YOURS","Settings"]
  };

  function buildChrome() {
    $("#primary-nav").innerHTML = nav.map(([id,label,ic]) => `<button class="nav-button ${id===state.view?'active':''}" data-view="${id}" data-tooltip="${label}" aria-label="${label}">${icon(ic)}</button>`).join("");
    $("#command-trigger").innerHTML = icon("command");
    $("[data-action='notifications']").innerHTML = icon("bell");
    $("[data-action='new-dictation']").innerHTML = `${icon("mic")}<span>New dictation</span>`;
    $("[data-action='dock-library']").innerHTML = icon("library");
    $("[data-action='dock-more']").innerHTML = icon("more");
    $("#command-search-icon").innerHTML = icon("search");
    $("#dock-wave").innerHTML = Array.from({length:16},()=>"<i></i>").join("");
  }

  function navigate(view) {
    if (!titles[view]) return;
    state.view = view;
    const [eye,title] = titles[view];
    $("#view-eyebrow").textContent = eye;
    $("#view-title").textContent = title;
    $$(".nav-button").forEach(b=>b.classList.toggle("active",b.dataset.view===view));
    render();
  }

  function render() {
    const renderers = {home:renderHome,library:renderLibrary,meetings:renderMeetings,reader:renderReader,insights:renderInsights,settings:renderSettings};
    const view = $("#view");
    view.innerHTML = renderers[state.view]();
    view.classList.remove("view-enter"); void view.offsetWidth; view.classList.add("view-enter");
  }

  function renderHome() {
    return `
      <div class="home-hero card">
        <div class="hero-copy">
          <span class="pill green"><span class="privacy-dot"></span>ON-DEVICE & READY</span>
          <h2>Your thoughts,<br>already written.</h2>
          <p>Mumble listens when invited, shapes what you mean, and places finished words exactly where you need them.</p>
          <div class="hero-actions">
            <button class="action-button" data-action="new-dictation">${icon("mic")} Start speaking <span class="keycap">CTRL WIN</span></button>
            <button class="soft-button" data-nav="library">${icon("library")} Open library</button>
          </div>
        </div>
        <div class="hero-orbit" aria-hidden="true"><div class="voice-orb"><span></span><span></span><span></span><span></span></div></div>
      </div>
      <div class="section-head" style="margin-top:22px"><div><h2>Pick up where you left off</h2><p>Everything Mumble has kept ready for you.</p></div><button class="text-button" data-action="command">View all actions →</button></div>
      <div class="grid three">
        <button class="quick-card card interactive" data-nav="library"><span class="quick-icon">${icon("waveform")}</span><span class="arrow">${icon("arrow")}</span><h3>7 dictations today</h3><p>1,284 words · mostly Text mode</p></button>
        <button class="quick-card card interactive" data-nav="meetings"><span class="quick-icon purple">${icon("meeting")}</span><span class="arrow">${icon("arrow")}</span><h3>Weekly product sync</h3><p>Summary ready · 5 action items</p></button>
        <button class="quick-card card interactive" data-nav="reader"><span class="quick-icon green">${icon("reader")}</span><span class="arrow">${icon("arrow")}</span><h3>Continue reading</h3><p>Thinking in Systems · page 216</p></button>
      </div>
      <div class="grid two" style="margin-top:20px">
        <div>
          <div class="section-head"><div><h2>Recent flow</h2><p>Your latest words, captures, and prompts.</p></div><button class="text-button" data-nav="library">Library →</button></div>
          <div class="card pad activity-list">
            ${deckItems.slice(0,4).map(item=>`<div class="activity-row"><span class="activity-mark">${icon(item.kind==='clipboard'?'clipboard':item.kind==='prompt'?'spark':'waveform')}</span><div class="activity-copy"><b>${item.mode}</b><p>${esc(item.text)}</p></div><span class="activity-time">${item.time}</span></div>`).join("")}
          </div>
        </div>
        <div>
          <div class="section-head"><div><h2>This week</h2><p>Real activity, quietly summarised.</p></div><button class="text-button" data-nav="insights">Insights →</button></div>
          <div class="grid two">
            <div class="stat-tile card"><div class="stat-top"><span class="stat-icon">${icon("waveform")}</span><span class="delta">↑ 18%</span></div><strong>6,842</strong><small>words dictated</small></div>
            <div class="stat-tile card"><div class="stat-top"><span class="stat-icon">${icon("clock")}</span><span class="delta">+34 min</span></div><strong>2.5h</strong><small>estimated saved</small></div>
          </div>
          <div class="feature-note card" style="margin-top:12px"><span class="note-icon">${icon("spark")}</span><div><h3>Try Capture selection</h3><p>Highlight text in any app and bring it straight into your Library.</p></div><button class="soft-button" data-action="capture">Try it</button></div>
        </div>
      </div>`;
  }

  function renderLibrary() {
    const q = state.deckQuery.toLowerCase();
    const items = deckItems.filter(i => (state.deckTab==='all'||state.deckTab==='starred'||i.kind===state.deckTab) && (!q||i.text.toLowerCase().includes(q)||i.mode.toLowerCase().includes(q)));
    return `<div class="deck-layout">
      <div class="deck-main">
        <div class="toolbar"><label class="search-box">${icon("search")}<input id="deck-search" value="${esc(state.deckQuery)}" placeholder="Search everything you have said or saved…"></label><button class="soft-button" data-action="capture">${icon("clipboard")} Capture</button><button class="soft-button" data-action="pin">${icon("lock")} Pin</button></div>
        <div class="toolbar"><div class="segmented">${[["all","All"],["transcript","Dictations"],["clipboard","Captures"],["prompt","Prompts"],["starred","Starred"]].map(([id,label])=>`<button class="${state.deckTab===id?'active':''}" data-deck-tab="${id}">${label}</button>`).join("")}</div><span class="muted" style="font-size:9px;margin-left:auto">${items.length} items · Newest first</span></div>
        ${state.selected.size?`<div class="selection-bar"><strong>${state.selected.size} selected</strong><span>Ready to shape with Smart Canvas</span><span class="spacer"></span><button class="text-button" data-action="merge-copy">Merge & copy</button><button class="text-button" data-action="clear-selection">Clear</button></div>`:""}
        <div class="deck-list">${items.map(item=>{
          const selected=state.selected.has(item.id), kindIcon=item.kind==='clipboard'?'clipboard':item.kind==='prompt'?'spark':'waveform';
          return `<article class="deck-item ${selected?'selected':''}" data-item="${item.id}"><button class="check" data-select="${item.id}" aria-label="Select item">${selected?icon("check"):''}</button><span class="item-kind ${item.kind==='clipboard'?'clip':item.kind==='prompt'?'prompt':''}">${icon(kindIcon)}</span><div class="item-copy"><p>${esc(item.text)}</p><span class="item-meta"><b>${item.mode}</b><span>·</span><span>${item.time}</span>${item.words?`<span>·</span><span>${item.words} words</span>`:''}</span></div><div class="item-actions"><button class="mini-icon" data-action="star" aria-label="Star">${icon("star")}</button><button class="mini-icon" data-action="copy" aria-label="Copy">${icon("copy")}</button><button class="mini-icon" aria-label="More">${icon("more")}</button></div></article>`;
        }).join("")}</div>
      </div>
      <aside class="smart-canvas card">
        <div class="smart-head"><span class="smart-symbol">${icon("spark")}</span><div><h3>Smart Canvas</h3><p>Shape selected material into something useful.</p></div></div>
        <span class="kicker">OUTPUT MODE</span>
        <div class="mode-grid">${[["text","Text","waveform"],["prompt","Prompt","spark"],["email","Email","mail"],["reply","Reply","reply"],["foreign","Foreign","globe"],["convert","Auto shape","shuffle"]].map(([id,label,ic])=>`<button class="mode-button ${state.mode===id?'active':''}" data-mode="${id}">${icon(ic)}${label}</button>`).join("")}</div>
        <label class="kicker" for="workflow">WORKFLOW</label>
        <select class="select-field" id="workflow"><option>Turn into a clear project brief</option><option>Summarise the key points</option><option>Extract every action item</option><option>Compare the selected sources</option><option>Draft a thoughtful reply</option><option>Create my own workflow…</option></select>
        <button class="action-button" data-action="run-canvas">${icon("spark")} Run on ${state.selected.size||0} item${state.selected.size===1?'':'s'}</button>
        <div class="smart-hint">${icon("lock")}<span>Only the selected text is sent to your active AI provider. Audio never leaves this device.</span></div>
      </aside>
    </div>`;
  }

  function renderMeetings() {
    if (state.meetingRecording) return `<div class="recording-card card"><div class="recording-center"><div class="recording-pulse">${icon("mic")}</div><h2>Meeting in progress</h2><p id="meeting-timer">00:12:48 · Local recording · Deep processing</p><div class="recording-wave">${Array.from({length:21},()=>"<i></i>").join("")}</div><div class="hero-actions"><button class="soft-button" data-action="pause-meeting">${icon("pause")} Pause</button><button class="danger-button" data-action="stop-meeting">${icon("stop")} Stop & save</button></div></div></div>`;
    return `<div class="meeting-layout">
      <aside class="meeting-sidebar card"><div class="meeting-create card"><h3>Capture a conversation</h3><p>Record locally, identify speakers, then turn the discussion into decisions.</p><button class="action-button" data-action="start-meeting">${icon("mic")} Start a meeting</button><button class="text-button" style="width:100%;margin-top:8px" data-action="import-audio">${icon("upload")} Import audio instead</button></div><span class="kicker" style="display:block;padding:5px 10px">RECENT</span><div class="meeting-list">${meetingData.map(m=>`<button class="meeting-row ${state.meeting===m.id?'active':''}" data-meeting="${m.id}"><b>${esc(m.title)}</b><span>${m.meta}</span></button>`).join("")}</div></aside>
      <section class="meeting-detail card">
        <div class="meeting-title-row"><div><span class="pill purple">PRODUCT</span><h2>Weekly product sync</h2><p>Today at 10:00 · 34 minutes · 4 speakers · Deep processed</p></div><div class="meeting-tools"><button class="soft-button" data-action="meeting-star">${icon("star")}</button><button class="soft-button" data-action="meeting-export">${icon("download")} Export</button><button class="soft-button">${icon("more")}</button></div></div>
        <div class="summary-grid">
          <article class="summary-card"><h3>${icon("spark")} Summary</h3><p>The team aligned on a quieter onboarding flow built around the first successful dictation. The redesigned privacy indicator and local processing message tested clearly. The Reader import experience needs one final usability pass before release.</p></article>
          <article class="summary-card"><h3>${icon("check")} Action items</h3><ul><li>Khaled to share the interaction prototype by Friday.</li><li>Maya to test Reader imports with large PDFs.</li><li>Alex to prepare the local-processing copy.</li></ul></article>
          <article class="summary-card"><h3>${icon("star")} Key decisions</h3><ul><li>Show processing location beside every record control.</li><li>Keep advanced model choices out of onboarding.</li></ul></article>
          <article class="summary-card"><h3>${icon("brain")} Open questions</h3><ul><li>Should Prompt mode remember the previous request across sessions?</li><li>How should imported meeting audio be labelled?</li></ul></article>
        </div>
        <div class="transcript-head"><h3>Transcript</h3><div class="segmented"><button class="active">Conversation</button><button>Notes</button></div></div>
        <div class="transcript"><div class="utterance"><span class="speaker">KH</span><div><b>Khaled</b><time>10:04</time><p>The important thing is that the new interface makes the local privacy model visible without turning it into a warning. It should simply feel reassuring.</p></div></div><div class="utterance"><span class="speaker b">MY</span><div><b>Maya</b><time>10:05</time><p>That worked in the research sessions. People understood the green local badge before we explained it, especially when it stayed near the voice control.</p></div></div><div class="utterance"><span class="speaker">KH</span><div><b>Khaled</b><time>10:06</time><p>Great. Let’s use that as the anchor and move the detailed choices into settings.</p></div></div></div>
      </section></div>`;
  }

  function renderReader() {
    const doc = docs.find(d=>d.id===state.readerDoc)||docs[0];
    return `<div class="reader-layout">
      <aside class="reader-library card"><div class="reader-library-head"><h2>Your library</h2><button class="mini-icon" data-action="add-document">${icon("plus")}</button></div><label class="search-box" style="height:36px">${icon("search")}<input placeholder="Find a document…"></label><div class="segmented" style="margin-top:9px"><button class="active">All</button><button>Starred</button><button>Collections</button></div><div class="doc-list">${docs.map(d=>`<button class="doc-row ${d.id===state.readerDoc?'active':''}" data-doc="${d.id}"><b>${esc(d.title)}</b><small>${d.meta}</small><span class="progress-line"><i style="width:${d.progress}%"></i></span></button>`).join("")}</div><button class="soft-button" style="width:100%;margin-top:13px" data-action="add-document">${icon("upload")} Add PDF, DOCX, EPUB & more</button></aside>
      <section class="reader-pane">
        <div class="reader-toolbar card"><span class="doc-title"><b>${esc(doc.title)}</b><small>Collection: Ideas worth keeping · ${doc.progress}% complete</small></span><span class="pill">${icon("bookmark")} 4 bookmarks</span><select class="select-field" style="width:132px"><option>George · OpenAI</option><option>Matthew · OpenRouter</option><option>Onyx · OpenAI</option></select><button class="mini-icon">${icon("more")}</button></div>
        <article class="reader-page card"><span class="chapter">CHAPTER TWELVE</span><h2>Leverage points — places to intervene in a system</h2><p>People who are raised in the industrial world and who get enthused about systems thinking are likely to make a terrible mistake. They are likely to assume that here, in systems analysis, in interconnection and complication, in the power of the computer, here at last is the key to prediction and control.</p><p>The future can’t be predicted, but it can be envisioned and brought lovingly into being. <mark>Systems can’t be controlled, but they can be designed and redesigned.</mark> We can’t surge forward with certainty into a world of no surprises, but we can expect surprises and learn from them.</p><p>Before we disturb the system in any way, we watch how it behaves. If it is a piece of music or a whitewater rapid or a fluctuation in a commodity price, we study its beat, its rhythm, and its history.</p>
          <div class="player"><button data-action="reader-back">${icon("reply")}</button><button class="play" data-action="reader-play">${icon(state.readerPlaying?'pause':'play')}</button><button data-action="reader-forward">${icon("arrow")}</button><span class="player-time">18:42</span><span class="player-progress"><i></i></span><span class="player-time">42:08</span><button data-action="bookmark">${icon("bookmark")}</button><button data-action="sleep">${icon("moon")}</button></div>
        </article>
      </section></div>`;
  }

  function renderInsights() {
    const heights=[36,58,45,76,62,89,54,48,92,70,66,82,61,98];
    let heat=""; for(let w=0;w<13;w++){heat+='<span class="heat-week">';for(let d=0;d<7;d++)heat+=`<i data-level="${(w*7+d*3+2)%5}"></i>`;heat+='</span>';}
    return `<div class="grid four">
      ${[["6,842","Words this week","waveform","↑ 18%"],["42","Dictations","mic","↑ 6"],["132","Words per minute","gauge","Personal best"],["11 days","Current streak","flame","Best: 19"]].map(([v,l,ic,d])=>`<div class="stat-tile card"><div class="stat-top"><span class="stat-icon">${icon(ic)}</span><span class="delta">${d}</span></div><strong>${v}</strong><small>${l}</small></div>`).join("")}
    </div>
    <div class="grid two" style="grid-template-columns:1.35fr .65fr;margin-top:15px">
      <div class="chart-card card"><div class="chart-head"><div><h3>Words in flow</h3><p>Daily dictated words · last 14 days</p></div><div class="segmented"><button>7D</button><button class="active">30D</button><button>90D</button></div></div><div class="bar-chart">${heights.map((h,i)=>`<span class="bar-col"><i style="height:${h}%"></i><span>${i%2?'':'J '+(27+i)}</span></span>`).join("")}</div></div>
      <div class="chart-card card"><div class="chart-head"><div><h3>Mode mix</h3><p>2,418 shaped words</p></div></div><div style="display:flex;align-items:center;gap:22px;margin-top:31px"><div class="mode-donut"></div><div class="mode-legend">${[["Text","58%","var(--accent)"],["Prompt","16%","var(--purple)"],["Email","13%","var(--orange)"],["Other","13%","var(--good)"]].map(x=>`<div class="legend-row"><i style="background:${x[2]}"></i><span>${x[0]}</span><b>${x[1]}</b></div>`).join("")}</div></div></div>
    </div>
    <div class="grid two" style="margin-top:15px">
      <div class="chart-card card" style="min-height:190px"><div class="chart-head"><div><h3>Thirteen weeks of momentum</h3><p>Each square is a day you used Mumble.</p></div><span class="pill green">11 day streak</span></div><div class="heatmap">${heat}</div><p class="muted" style="font-size:9px;margin-top:18px">Quieter squares mean fewer words. Your strongest rhythm is Tuesday to Thursday, between 9 and 11 am.</p></div>
      <div class="chart-card card" style="min-height:190px"><div class="chart-head"><div><h3>Reader</h3><p>Independent reading activity</p></div><span class="pill purple">THIS MONTH</span></div><div class="grid three" style="margin-top:28px"><div><strong style="font:600 22px Manrope">4.2h</strong><small class="muted" style="display:block;font-size:8px">listened</small></div><div><strong style="font:600 22px Manrope">61</strong><small class="muted" style="display:block;font-size:8px">pages</small></div><div><strong style="font:600 22px Manrope">3</strong><small class="muted" style="display:block;font-size:8px">completed</small></div></div><div class="feature-note" style="margin-top:20px;padding:12px"><span class="note-icon">${icon("clock")}</span><div><h3>2 hours 32 minutes saved</h3><p>Estimated against a 45 WPM typing baseline.</p></div></div></div>
    </div>`;
  }

  const settingSections = [
    ["voice","Voice & shortcuts"],["intelligence","Intelligence"],["language","Language & vocabulary"],["privacy","Privacy & data"],["appearance","Appearance"],["sync","Account & sync"],["system","System"]
  ];
  function toggle(on, key) { return `<button class="toggle ${on?'on':''}" data-toggle="${key}" aria-pressed="${on}"><i></i></button>`; }
  function row(title,desc,control) { return `<div class="setting-row"><div class="setting-copy"><b>${title}</b><p>${desc}</p></div><div class="control">${control}</div></div>`; }
  function settingsContent() {
    const s=state.settingsSection;
    if(s==="appearance") return `<div class="settings-title"><h2>Appearance</h2><p>Choose the visual atmosphere for this experiment.</p></div>${row("Interface theme","Two complete expressions of the Horizon design system.",`<div class="theme-cards"><button class="theme-card ${state.theme==='horizon'?'active':''}" data-theme-choice="horizon"><span class="theme-preview"><i></i><span></span></span><b>Horizon · Blue</b></button><button class="theme-card ${state.theme==='noir'?'active':''}" data-theme-choice="noir"><span class="theme-preview noir"><i></i><span></span></span><b>Noir · Gold</b></button></div>`)}${row("Visual depth","Glass, blur, and subtle ambient colour.",`<select><option>Full atmosphere</option><option>Balanced</option><option>Reduced</option></select>`)}${row("Motion","Interface and island transitions.",toggle(true,"motion"))}${row("Reader type size","Default document reading size.",`<select><option>Comfortable</option><option>Compact</option><option>Large</option></select>`)}`;
    if(s==="voice") return `<div class="settings-title"><h2>Voice & shortcuts</h2><p>Control how Mumble listens and how quickly you can reach it.</p></div>${row("Dictation shortcut","Press once to record and again to finish.",`<span class="keycap">CTRL</span><span class="keycap">WIN</span><button class="soft-button">Change</button>`)}${row("Open Library","Bring your words and captures to the front.",`<span class="keycap">CTRL</span><span class="keycap">ALT</span><span class="keycap">D</span>`)}${row("Quick paste","Paste your latest dictation instantly.",`<span class="keycap">CTRL</span><span class="keycap">ALT</span><span class="keycap">V</span>`)}${row("Search selected text","Use highlighted text or your next dictation as a search.",`<select><option>Perplexity</option><option>Google</option><option>Brave</option></select>`)}${row("Microphone","Active input device and live test.",`<select><option>MacBook Microphone</option><option>Studio Display</option></select><button class="soft-button">Test</button>`)}`;
    if(s==="intelligence") return `<div class="settings-title"><h2>Intelligence</h2><p>Decide where and how Mumble shapes your words.</p></div>${row("Pro mode","Use AI for rich shaping, prompts, summaries, and workflows.",toggle(true,"pro"))}${row("AI provider","The main provider for text shaping.",`<select><option>Cerebras · gpt-oss-120b</option><option>OpenAI · GPT-5.4 mini</option><option>Anthropic · Claude Opus 4.8</option><option>DeepSeek V4 Flash</option><option>Groq · Llama 3.3</option><option>Local · Ollama</option></select>`)}${row("Dedicated prompt provider","Route Prompt mode to a separate reasoning model.",toggle(false,"prompt-provider"))}${row("Polish strength","How assertively Mumble cleans ordinary dictation.",`<select><option>Light</option><option>Standard</option><option>Thorough</option></select>`)}${row("Prompt preferences","Neutral tone · balanced detail · bullets and headings.",`<button class="soft-button">Edit preferences</button>`)}${row("Local model","Available for offline shaping and graceful fallback.",`<span class="pill green">READY · QWEN 3.5 4B</span>`)}`;
    if(s==="language") return `<div class="settings-title"><h2>Language & vocabulary</h2><p>Teach Mumble the words and languages that matter to you.</p></div>${row("Primary language","Used for transcription and punctuation.",`<select><option>English (UK)</option><option>English (US)</option><option>Auto detect</option></select>`)}${row("Foreign awareness","Preserve multilingual words inside English speech.",toggle(true,"foreign"))}${row("Active languages","Languages Foreign mode should expect.",`<div class="vocab-chips"><span class="vocab-chip">Arabic</span><span class="vocab-chip">French</span><span class="vocab-chip">Urdu</span><button class="mini-icon">${icon("plus")}</button></div>`)}${row("Personal vocabulary","Names, places, brands, and specialist terms.",`<div class="vocab-chips"><span class="vocab-chip">Mumble</span><span class="vocab-chip">Khaled</span><span class="vocab-chip">tawakkul</span><button class="soft-button">Manage 24</button></div>`)}${row("Cleanup","Remove fillers, loops, and common transcription artefacts.",toggle(true,"cleanup"))}`;
    if(s==="privacy") return `<div class="settings-title"><h2>Privacy & data</h2><p>Your audio is local by default. These controls make every boundary explicit.</p></div>${row("Transcription route","Where speech becomes raw text.",`<select><option>On this device · Local</option><option>Cloud · Groq</option><option>Cloud · OpenAI</option></select>`)}${row("Clipboard capture","Keep copied text and images in your Library.",toggle(true,"clipboard"))}${row("Conversation context","Recognise copied AI replies for intentional Reply mode context.",toggle(true,"context"))}${row("History limit","Maximum local items before the oldest are removed.",`<select><option>5,000 items</option><option>2,000 items</option><option>500 items</option></select>`)}${row("Data location","Stored locally in your Mumble application data folder.",`<button class="soft-button">${icon("folder")} Open folder</button>`)}${row("Clear local data","Choose transcripts, captures, Reader, meetings, or statistics.",`<button class="danger-button">Review & clear…</button>`)}`;
    if(s==="sync") return `<div class="settings-title"><h2>Account & sync</h2><p>Optional encrypted continuity across your own devices.</p></div>${row("Account","Signed in as khaled@example.com.",`<span class="pill green">CONNECTED</span><button class="soft-button">Sign out</button>`)}${row("Sync now","Last completed 2 minutes ago.",`<button class="soft-button" data-action="sync">${icon("refresh")} Sync now</button>`)}${row("Settings", "Keep preferences aligned.",toggle(true,"sync-settings"))}${row("History", "Sync text history. Audio is never uploaded.",toggle(false,"sync-history"))}${row("Reader library", "Keep documents and positions in sync.",toggle(true,"sync-reader"))}${row("Meetings", "Meeting transcripts remain local in this concept.",`<span class="pill">LOCAL ONLY</span>`)}`;
    return `<div class="settings-title"><h2>System</h2><p>Processing, performance, startup, and maintenance.</p></div>${row("Transcription model","Small is the recommended balance of speed and accuracy.",`<select><option>Small · Recommended</option><option>Medium · Accurate</option><option>Tiny · Fastest</option></select>`)}${row("Hardware strategy","Automatic uses the best available accelerator.",`<select><option>Automatic</option><option>CPU</option><option>DirectML</option></select>`)}${row("Resource saver","Use Tiny, pause streaming, and avoid AI warm-up.",toggle(false,"saver"))}${row("Start with computer","Keep Mumble ready in the background.",toggle(true,"autostart"))}${row("Updates","You’re on version 0.9 · checked today.",`<button class="soft-button" data-action="update">Check again</button>`)}${row("Window","Open full screen, reset size, or switch to Lite.",`<button class="soft-button">Reset</button><button class="soft-button">Mumble Lite</button>`)}`;
  }
  function renderSettings() {
    return `<div class="settings-layout"><aside class="settings-menu card">${settingSections.map(([id,label])=>`<button class="${state.settingsSection===id?'active':''}" data-settings-section="${id}">${label}</button>`).join("")}</aside><section class="settings-panel card">${settingsContent()}</section></div>`;
  }

  function setRecording(on) {
    state.recording=on;
    const dock=$("#voice-dock"); dock.classList.toggle("recording",on);
    $("#dock-title").textContent=on?(state.prompt?"Building a prompt…":"Listening…"):"Mumble is ready";
    $("#dock-subtitle").textContent=on?"Tap again when you’re finished":"Ctrl + Win to speak";
    $("#record-button").setAttribute("aria-label",on?"Stop dictation":"Start dictation");
    if(!on) toast("Dictation saved","38 words · Text mode · pasted at your cursor");
  }
  function setPrompt(on) {
    state.prompt=on; const b=$("#prompt-switch"); b.setAttribute("aria-pressed",String(on));
    if(state.recording) $("#dock-title").textContent=on?"Building a prompt…":"Listening…";
    toast(on?"Prompt mode on":"Prompt mode off",on?"Your next dictation will become a structured prompt.":"Your next dictation will produce clean text.");
  }
  function toast(title,subtitle="") {
    const el=document.createElement("div"); el.className="toast"; el.innerHTML=`${icon("check")}<span><b>${esc(title)}</b>${subtitle?`<small>${esc(subtitle)}</small>`:""}</span>`; $("#toast-region").appendChild(el); setTimeout(()=>el.remove(),3600);
  }
  function openCommand() {
    $("#scrim").hidden=false; $("#command-palette").hidden=false; renderCommands(""); setTimeout(()=>$("#command-input").focus(),0);
  }
  function closeCommand(){ $("#scrim").hidden=true; $("#command-palette").hidden=true; $("#command-input").value=""; }
  const commands=[
    ["Start a new dictation","Ctrl + Win","mic","record"],["Record a meeting","","meeting","meetings"],["Capture selected text","","clipboard","capture"],["Open the Library","Ctrl + Alt + D","library","library"],["Quick paste latest","Ctrl + Alt + V","copy","paste"],["Continue reading","Thinking in Systems","reader","reader"],["Search the web with voice","Ctrl + Alt + S","search","search"],["Open settings","","settings","settings"]
  ];
  function renderCommands(q) {
    q=q.toLowerCase(); const rows=commands.filter(c=>c[0].toLowerCase().includes(q)); $("#command-results").innerHTML=`<div class="command-group">QUICK ACTIONS</div>${rows.map((c,i)=>`<button class="command-row ${i===0?'active':''}" data-command="${c[3]}"><span>${icon(c[2])}</span><b>${c[0]}</b><small>${c[1]}</small></button>`).join("")}`;
  }
  function openContext() {
    state.contextOpen=!state.contextOpen; const panel=$("#context-panel"),shell=$("#app-shell"); panel.hidden=!state.contextOpen; shell.classList.toggle("with-context",state.contextOpen);
    if(state.contextOpen) panel.innerHTML=`<div style="display:flex;align-items:center;justify-content:space-between"><span class="kicker">VOICE CONTROL</span><button class="mini-icon" data-action="dock-more">${icon("close")}</button></div><h2 style="margin-top:18px">The new island</h2><p>A spatial voice control that stays quiet until you need it.</p><div class="context-option"><b>Input device</b><p>MacBook Microphone · level is healthy</p></div><div class="context-option"><b>Processing route</b><p>Local Small model · audio stays on this device</p></div><div class="context-option"><b>Current output</b><p>${state.prompt?'Structured Prompt':'Clean Text'} · Light polish</p></div><div class="context-option"><b>Quick modes</b><div class="mode-grid" style="margin-bottom:0"><button class="mode-button active">${icon("waveform")}Text</button><button class="mode-button">${icon("mail")}Email</button><button class="mode-button">${icon("reply")}Reply</button><button class="mode-button">${icon("globe")}Foreign</button></div></div><div class="context-actions"><button class="soft-button" data-nav="settings">Settings</button><button class="action-button" data-action="new-dictation">${icon("mic")} Speak</button></div>`;
  }

  document.addEventListener("click", (e) => {
    const b=e.target.closest("button"); if(!b) return;
    if(b.dataset.view) return navigate(b.dataset.view);
    if(b.dataset.nav) return navigate(b.dataset.nav);
    if(b.dataset.navTarget) return navigate(b.dataset.navTarget);
    if(b.dataset.deckTab){state.deckTab=b.dataset.deckTab;return render();}
    if(b.dataset.select){const id=+b.dataset.select;state.selected.has(id)?state.selected.delete(id):state.selected.add(id);return render();}
    if(b.dataset.mode){state.mode=b.dataset.mode;return render();}
    if(b.dataset.meeting){state.meeting=b.dataset.meeting;return render();}
    if(b.dataset.doc){state.readerDoc=b.dataset.doc;return render();}
    if(b.dataset.settingsSection){state.settingsSection=b.dataset.settingsSection;return render();}
    if(b.dataset.themeChoice){state.theme=b.dataset.themeChoice;document.documentElement.dataset.theme=state.theme;render();toast(`${state.theme==='horizon'?'Horizon':'Noir'} theme applied`,`This choice exists only inside the experiment.`);return;}
    if(b.dataset.toggle){b.classList.toggle("on");b.setAttribute("aria-pressed",String(b.classList.contains("on")));return;}
    if(b.dataset.command){const c=b.dataset.command;closeCommand();if(["library","reader","settings","meetings"].includes(c))navigate(c);else if(c==="record")setRecording(true);else toast(c==="capture"?"Selection captured":"Action ready","Prototype interaction — no production data was touched.");return;}
    switch(b.dataset.action){
      case "new-dictation": setRecording(!state.recording); break;
      case "dock-library": navigate("library"); break;
      case "dock-more": openContext(); break;
      case "command": openCommand(); break;
      case "capture": toast("Selection captured","Added to your Library as a local capture."); break;
      case "pin": toast("Library pinned","It would now stay above your other apps without taking focus."); break;
      case "copy": case "merge-copy": toast("Copied","Ready to paste anywhere."); break;
      case "star": case "meeting-star": toast("Saved to favourites"); break;
      case "clear-selection": state.selected.clear();render();break;
      case "run-canvas": if(!state.selected.size)toast("Choose something first","Select one or more Library items to shape.");else toast("Smart Canvas is working",`${state.selected.size} items · ${state.mode} mode`);break;
      case "start-meeting": state.meetingRecording=true;render();break;
      case "stop-meeting": state.meetingRecording=false;render();toast("Meeting saved","Transcription and speaker labelling are ready.");break;
      case "pause-meeting": toast("Meeting paused","No audio is being recorded.");break;
      case "import-audio": toast("Audio importer opened","Supports common local audio formats.");break;
      case "meeting-export": toast("Export options","TXT · Markdown · JSON · HTML · Clipboard");break;
      case "reader-play": state.readerPlaying=!state.readerPlaying;render();break;
      case "bookmark": toast("Bookmark added","Page 216 · 18:42");break;
      case "sleep": toast("Sleep timer","Playback will stop in 30 minutes.");break;
      case "add-document": toast("Document picker opened","PDF, DOCX, HTML, EPUB, RTF, Markdown, TXT, CSV, ODT, PPTX and XLSX.");break;
      case "sync": toast("Sync complete","Settings and Reader position are up to date.");break;
      case "update": toast("Mumble is up to date","Version 0.9 · checked just now.");break;
      case "privacy": state.settingsSection="privacy";navigate("settings");break;
      case "notifications": toast("You’re all caught up");break;
    }
  });
  $("#record-button").addEventListener("click",()=>setRecording(!state.recording));
  $("#prompt-switch").addEventListener("click",()=>setPrompt(!state.prompt));
  $("#command-trigger").addEventListener("click",openCommand);
  $("#scrim").addEventListener("click",closeCommand);
  $("#command-input").addEventListener("input",e=>renderCommands(e.target.value));
  $("#view").addEventListener("input",e=>{if(e.target.id==="deck-search"){state.deckQuery=e.target.value;const pos=e.target.selectionStart;render();setTimeout(()=>{const x=$("#deck-search");if(x){x.focus();x.setSelectionRange(pos,pos);}},0);}});
  document.addEventListener("keydown",e=>{
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){e.preventDefault();openCommand();}
    if(e.key==="Escape")closeCommand();
    if(e.key==="Enter"&&!$("#command-palette").hidden){const active=$(".command-row.active");if(active)active.click();}
  });

  buildChrome(); render();
})();
