/*
 * Mumble Aperture — sealed, static interface experiment.
 *
 * Everything in this file is local mock state. It deliberately does not read
 * or write Mumble data, call a production bridge, use storage, or use a network.
 */
(function () {
  'use strict';

  var doc = document;
  var body = doc.body;
  var sessionClock = null;
  var readerClock = null;
  var voiceClock = null;
  var lastCommandFocus = null;

  function one(selector, root) {
    return (root || doc).querySelector(selector);
  }

  function all(selector, root) {
    return Array.prototype.slice.call((root || doc).querySelectorAll(selector));
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (character) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[character];
    });
  }

  function icon(name, className) {
    var aliases = {
      aperture: 'spark',
      capture: 'copy',
      controls: 'settings',
      deck: 'layers',
      document: 'layers',
      export: 'download',
      folder: 'layers',
      gauge: 'cpu',
      reader: 'book',
      reply: 'arrow',
      sessions: 'mic',
      signals: 'chart',
      stop: 'close'
    };
    name = aliases[name] || name;
    return '<svg class="icon ' + (className || '') + '" aria-hidden="true" viewBox="0 0 24 24">' +
      '<use href="#i-' + name + '"></use></svg>';
  }

  function formatTime(seconds) {
    var value = Math.max(0, Math.floor(seconds));
    var hours = Math.floor(value / 3600);
    var minutes = Math.floor((value % 3600) / 60);
    var secs = value % 60;
    var padded = function (number) { return String(number).padStart(2, '0'); };
    return hours ? padded(hours) + ':' + padded(minutes) + ':' + padded(secs) : padded(minutes) + ':' + padded(secs);
  }

  var focusModes = [
    { id: 'clean', label: 'Clean text', glyph: 'wave', note: 'Light polish, faithful phrasing, ready at the cursor.' },
    { id: 'prompt', label: 'Command', glyph: 'spark', note: 'Turns an unfinished thought into a precise, structured prompt.' },
    { id: 'reply', label: 'Reply', glyph: 'reply', note: 'Uses selected context to draft a calm, direct response.' },
    { id: 'foreign', label: 'Multilingual', glyph: 'globe', note: 'Keeps names and foreign phrases intact while shaping the sentence.' }
  ];

  var deckItems = [
    {
      id: 'onboarding', type: 'dictation', mode: 'Clean text', time: '09:42', date: 'Today', words: 38, starred: true,
      route: 'Local · Small', title: 'A quieter first run',
      text: 'The new onboarding should feel less like configuration and more like a quiet conversation that takes someone directly to their first useful dictation.'
    },
    {
      id: 'privacy', type: 'capture', mode: 'Capture', time: '08:16', date: 'Today', words: 74, starred: false,
      route: 'Local capture', title: 'Privacy research note',
      text: 'People understand the privacy story immediately when the active processing route is visible beside the record control. The explanation should stay close to the moment of use.'
    },
    {
      id: 'launch', type: 'prompt', mode: 'Command', time: '18:08', date: 'Yesterday', words: 127, starred: true,
      route: 'Cerebras · Text only', title: 'Launch narrative prompt',
      text: 'Design a concise launch narrative for a private voice-to-text tool. Begin with the moment speech becomes finished writing, then prove that local processing is the default.'
    },
    {
      id: 'review', type: 'dictation', mode: 'Email', time: '16:31', date: 'Yesterday', words: 64, starred: false,
      route: 'Local · Small', title: 'Design review moved',
      text: 'Hi team, I have moved tomorrow’s design review to eleven so we can include the completed interaction prototype. The agenda remains the same.'
    },
    {
      id: 'vocabulary', type: 'dictation', mode: 'Multilingual', time: '14:19', date: 'Friday', words: 22, starred: true,
      route: 'Local · Medium', title: 'Personal vocabulary',
      text: 'Please add barakah, tawakkul and the client’s Arabic name to my personal vocabulary before the call.'
    },
    {
      id: 'reply', type: 'dictation', mode: 'Reply', time: '11:04', date: 'Thursday', words: 49, starred: false,
      route: 'Local · Small', title: 'Prototype follow-up',
      text: 'That works for me. I will send the revised prototype before lunch and include a short explanation of the new navigation model.'
    }
  ];

  var meetings = [
    {
      id: 'weekly', eyebrow: 'PRODUCT · TODAY', title: 'Weekly product sync', meta: '34 min · 4 speakers · Deep processed',
      summary: 'The team aligned on a quieter onboarding flow built around the first successful dictation. The redesigned privacy indicator tested clearly, while Reader import needs one final usability pass before release.',
      decisions: ['Keep the processing route beside every record control.', 'Remove advanced model choices from first-run setup.', 'Ship Reader import after the large-PDF pass.'],
      actions: [['Khaled', 'Share the interaction prototype', 'Friday'], ['Maya', 'Test Reader with large PDFs', 'Tomorrow'], ['Alex', 'Prepare local-processing copy', 'Friday']],
      transcript: [
        ['Maya', '00:42', 'The simpler entry point tested well. People wanted to speak before they wanted to configure anything.'],
        ['Khaled', '02:18', 'Then the voice control should be the centre of gravity, with settings available only when someone reaches for them.'],
        ['Alex', '05:07', 'The local indicator did more work than the paragraph of privacy copy. We should keep that signal visible.']
      ]
    },
    {
      id: 'reader', eyebrow: 'RESEARCH · YESTERDAY', title: 'Reader research debrief', meta: '52 min · 3 speakers · 8 notes',
      summary: 'Listeners preferred a restrained reading surface with one persistent progress line. Word highlighting was most useful at sentence level and distracting when the colour contrast was too high.',
      decisions: ['Use a low-contrast word highlight.', 'Keep playback controls inside the reading stage.', 'Remember speed separately for each document.'],
      actions: [['Maya', 'Test sentence-level tracking', 'Monday'], ['Khaled', 'Prototype the reading altar', 'Friday']],
      transcript: [
        ['Maya', '01:10', 'People described it as calmer when the library receded and the page stayed centred.'],
        ['Jon', '04:28', 'The active word can be gold, but completed text should not look disabled.'],
        ['Khaled', '08:51', 'I want the player to feel like part of the instrument, not a media bar attached afterwards.']
      ]
    },
    {
      id: 'launch', eyebrow: 'PLANNING · 8 JULY', title: 'Launch readiness', meta: '27 min · 5 speakers · 6 actions',
      summary: 'The launch checklist is on track. Documentation and the fallback transcription path remain the two items at risk, with owners confirmed for both.',
      decisions: ['Retain the current release window.', 'Document offline fallback before launch.', 'Use the short product film on the landing page.'],
      actions: [['Alex', 'Finish launch copy', 'Tuesday'], ['Jon', 'Record fallback demo', 'Monday'], ['Khaled', 'Approve final build', 'Wednesday']],
      transcript: [
        ['Alex', '00:31', 'The core story is holding together. We just need one proof point for the offline path.'],
        ['Jon', '03:12', 'I can record the fallback demo against the candidate build on Monday.'],
        ['Khaled', '06:45', 'Keep it short and show the route change without interrupting the dictation.']
      ]
    },
    {
      id: 'critique', eyebrow: 'DESIGN · 7 JULY', title: 'Aperture critique', meta: '1 hr 12 min · 6 speakers · 14 notes',
      summary: 'The precision-instrument direction felt distinct from the previous experiment. The group asked for stronger hierarchy in transcript detail and less decorative glow around secondary elements.',
      decisions: ['Centre the persistent voice aperture.', 'Use hairlines instead of container shadows.', 'Reserve glow for active recording only.'],
      actions: [['Khaled', 'Tighten transcript hierarchy', 'Done'], ['Maya', 'Review reduced motion', 'Today']],
      transcript: [
        ['Maya', '02:03', 'The centre feels intentional now. It reads like a tool rather than another dashboard.'],
        ['Khaled', '05:22', 'Gold needs to behave as a signal. If everything glows, nothing is actually active.'],
        ['Alex', '09:40', 'The editorial rows are much more useful than another set of floating cards.']
      ]
    }
  ];

  var documents = [
    {
      id: 'systems', title: 'Thinking in Systems', author: 'Donella H. Meadows', kind: 'PDF · 318 pages', progress: 68, remaining: '24 min left', chapter: 'Chapter 6 · Leverage Points',
      paragraphs: [
        'A system is more than the sum of its parts. It may exhibit adaptive, dynamic, goal-seeking, self-preserving, and sometimes evolutionary behaviour.',
        'Once we see the relationship between structure and behaviour, we can begin to understand how systems work, what makes them produce poor results, and how to shift them into better patterns.'
      ]
    },
    {
      id: 'brief', title: 'Mumble product brief', author: 'Product · July revision', kind: 'DOCX · 26 pages', progress: 42, remaining: '11 min left', chapter: 'Principle 02 · Quiet intelligence',
      paragraphs: [
        'Mumble should disappear at the moment it becomes useful. The interface creates trust before recording, provides a single clear signal while listening, and returns finished language without ceremony.',
        'Every processing boundary should be legible. Audio remains local by default, while optional text intelligence is described at the exact moment it is used.'
      ]
    },
    {
      id: 'voice', title: 'Voice Interfaces in Practice', author: 'Research collection', kind: 'EPUB · 184 pages', progress: 17, remaining: '1 hr 08 min left', chapter: 'Essay 04 · The shape of attention',
      paragraphs: [
        'A voice interface does not need to imitate conversation in order to feel natural. It needs to respect the rhythm of thinking and make its own state unambiguous.',
        'Visual feedback should reassure rather than entertain. Timing, restraint, and a coherent stopping point matter more than a literal picture of sound.'
      ]
    },
    {
      id: 'notes', title: 'Research notes — July', author: 'Personal notebook', kind: 'Markdown · 9 notes', progress: 83, remaining: '4 min left', chapter: 'Synthesis · First-use trust',
      paragraphs: [
        'The best first-run experience begins with permission and immediately demonstrates value. Configuration can follow after the first successful result.',
        'Keep language concrete: microphone active, audio local, text ready. Each phrase should correspond to a state someone can see.'
      ]
    }
  ];

  var signalRanges = {
    '7d': [38, 52, 44, 66, 58, 73, 81],
    '30d': [28, 34, 31, 46, 42, 55, 48, 62, 58, 70, 66, 79, 74, 86],
    '12w': [31, 42, 37, 54, 49, 63, 59, 68, 72, 77, 74, 89]
  };

  var controlSections = [
    { id: 'voice', label: 'Voice & trigger', icon: 'mic', note: 'Capture behaviour and hardware' },
    { id: 'intelligence', label: 'Intelligence', icon: 'spark', note: 'Shaping and provider routes' },
    { id: 'language', label: 'Language', icon: 'globe', note: 'Speech and vocabulary' },
    { id: 'privacy', label: 'Privacy & data', icon: 'shield', note: 'Local boundaries and history' },
    { id: 'appearance', label: 'Appearance', icon: 'aperture', note: 'Motion, contrast and light' },
    { id: 'system', label: 'System', icon: 'gauge', note: 'Models, startup and updates' }
  ];

  var state = {
    view: 'focus',
    focusMode: 'clean',
    deckFilter: 'all',
    deckItem: 'onboarding',
    transform: 'polish',
    sessionId: 'weekly',
    sessionTab: 'summary',
    sessionLive: false,
    sessionStarted: 0,
    sessionElapsed: 0,
    readerDoc: 'systems',
    readerPlaying: false,
    readerWord: 12,
    readerSpeed: 1,
    signalRange: '30d',
    controlSection: 'voice',
    voice: 'idle',
    commandQuery: '',
    commandIndex: 0,
    settings: {
      autoPaste: true,
      holdToTalk: false,
      inputChime: true,
      deepPolish: true,
      localFallback: true,
      providerContext: false,
      preserveTerms: true,
      fillers: true,
      multilingual: true,
      localAudio: true,
      clipboardHistory: false,
      telemetry: false,
      subtleMotion: true,
      ambientGlow: true,
      highContrast: false,
      launchAtLogin: true,
      automaticUpdates: true,
      resourceSaver: false
    },
    selects: {
      microphone: 'Studio microphone',
      model: 'Small · Recommended',
      provider: 'Cerebras · gpt-oss-120b',
      language: 'English (UK)',
      history: '5,000 items',
      density: 'Balanced'
    }
  };

  function modeButtons() {
    return focusModes.map(function (mode) {
      var active = mode.id === state.focusMode;
      return '<button type="button" class="mode-node ' + (active ? 'is-active' : '') + '" data-focus-mode="' + mode.id + '" role="radio" aria-checked="' + active + '">' +
        '<span class="mode-node__icon">' + icon(mode.glyph) + '</span>' +
        '<span><strong>' + mode.label + '</strong><small>0' + (focusModes.indexOf(mode) + 1) + '</small></span>' +
      '</button>';
    }).join('');
  }

  function renderFocus() {
    var mode = focusModes.filter(function (item) { return item.id === state.focusMode; })[0];
    return '<section class="view-panel focus-view" aria-labelledby="focus-title">' +
      '<header class="view-intro view-intro--center">' +
        '<p class="eyebrow"><span></span> ACTIVE APERTURE · 01 <span></span></p>' +
        '<h1 id="focus-title">Quietly turn thought<br>into finished language.</h1>' +
        '<p class="view-lede">One centred instrument for dictation, decisions, reading and recall.</p>' +
      '</header>' +
      '<section class="focus-console" aria-label="Output mode">' +
        '<div class="precision-rule" aria-hidden="true">' + Array.from({ length: 31 }, function (_, index) { return '<i class="' + (index % 5 === 0 ? 'major' : '') + '"></i>'; }).join('') + '</div>' +
        '<div class="mode-selector" role="radiogroup" aria-label="Choose dictation output">' + modeButtons() + '</div>' +
        '<div class="mode-readout"><span class="readout-index">MODE / ' + escapeHtml(mode.id.toUpperCase()) + '</span><p>' + escapeHtml(mode.note) + '</p><span class="local-seal">' + icon('lock') + ' Audio stays local</span></div>' +
      '</section>' +
      '<section class="recent-tape" aria-labelledby="recent-title">' +
        '<header class="section-heading"><div><p class="precision-label">RECENT SIGNAL</p><h2 id="recent-title">Last transmissions</h2></div><button type="button" class="text-button" data-view-target="deck">Open the full deck ' + icon('arrow') + '</button></header>' +
        '<div class="recent-rows">' + deckItems.slice(0, 3).map(function (item, index) {
          return '<button type="button" class="recent-row" data-open-deck="' + item.id + '">' +
            '<span class="row-number">0' + (index + 1) + '</span><span class="row-time">' + item.time + '<small>' + item.date + '</small></span>' +
            '<span class="row-copy"><strong>' + escapeHtml(item.title) + '</strong><span>' + escapeHtml(item.text) + '</span></span>' +
            '<span class="row-meta">' + item.words + ' W<br>' + escapeHtml(item.mode.toUpperCase()) + '</span><span class="row-arrow">' + icon('arrow') + '</span>' +
          '</button>';
        }).join('') + '</div>' +
      '</section>' +
      '<footer class="focus-footer"><span>CTRL + WIN</span><p>Speak from anywhere. The aperture remains ready without taking focus.</p><button type="button" class="ghost-button" data-action="core-toggle">Start a dictation</button></footer>' +
    '</section>';
  }

  function transformPreview(item) {
    if (state.transform === 'brief') {
      return '<p><strong>Objective</strong> Create an onboarding experience that feels like a quiet conversation.</p><p><strong>Success signal</strong> The user reaches a useful first dictation before configuration.</p>';
    }
    if (state.transform === 'email') {
      return '<p>Hi team,</p><p>' + escapeHtml(item.text) + '</p><p>I would appreciate your thoughts before the next review.</p>';
    }
    if (state.transform === 'actions') {
      return '<ul><li>Prototype the conversational first-run path.</li><li>Measure time to first useful dictation.</li><li>Move advanced configuration after the result.</li></ul>';
    }
    return '<p>' + escapeHtml(item.text) + '</p>';
  }

  function renderDeck() {
    var filtered = deckItems.filter(function (item) {
      if (state.deckFilter === 'all') return true;
      if (state.deckFilter === 'starred') return item.starred;
      return item.type === state.deckFilter;
    });
    var selected = deckItems.filter(function (item) { return item.id === state.deckItem; })[0] || deckItems[0];
    var filters = [['all', 'All'], ['dictation', 'Dictations'], ['capture', 'Captures'], ['prompt', 'Commands'], ['starred', 'Starred']];
    var transforms = [['polish', 'Polish', 'wave'], ['brief', 'Project brief', 'document'], ['email', 'Email', 'mail'], ['actions', 'Actions', 'check']];
    return '<section class="view-panel deck-view" aria-labelledby="deck-title">' +
      '<header class="view-heading"><div><p class="eyebrow">ARCHIVE · 1,284 WORDS TODAY</p><h1 id="deck-title">Transcript deck</h1><p>Every thought arranged as a precise, editable record.</p></div><div class="heading-actions"><button type="button" class="ghost-button" data-action="capture">' + icon('capture') + ' Capture selection</button><button type="button" class="gold-button" data-action="core-toggle">' + icon('mic') + ' New dictation</button></div></header>' +
      '<nav class="filter-ruler" aria-label="Filter transcript deck">' + filters.map(function (filter) {
        var active = filter[0] === state.deckFilter;
        return '<button type="button" class="' + (active ? 'is-active' : '') + '" data-deck-filter="' + filter[0] + '" aria-pressed="' + active + '"><span>' + filter[1] + '</span><i></i></button>';
      }).join('') + '<span class="filter-count">' + filtered.length + ' RECORDS</span></nav>' +
      '<div class="deck-workspace">' +
        '<aside class="tape-index" aria-label="Transcript records"><div class="tape-index__head"><span>INDEX</span><span>ROUTE / TIME</span></div>' +
          '<div class="tape-list">' + (filtered.length ? filtered.map(function (item, index) {
            var active = selected.id === item.id;
            return '<button type="button" class="transcript-row ' + (active ? 'is-active' : '') + '" data-deck-id="' + item.id + '" aria-pressed="' + active + '">' +
              '<span class="transcript-row__number">' + String(index + 1).padStart(2, '0') + '</span>' +
              '<span class="transcript-row__mark">' + icon(item.type === 'capture' ? 'capture' : item.type === 'prompt' ? 'spark' : 'wave') + '</span>' +
              '<span class="transcript-row__copy"><strong>' + escapeHtml(item.title) + '</strong><small>' + escapeHtml(item.text) + '</small></span>' +
              '<span class="transcript-row__data"><b>' + item.time + '</b><small>' + escapeHtml(item.mode) + '</small></span>' +
            '</button>';
          }).join('') : '<div class="empty-index">No records on this channel.</div>') + '</div>' +
        '</aside>' +
        '<article class="transcript-inspector" aria-labelledby="transcript-detail-title">' +
          '<header class="inspector-heading"><div><p class="precision-label">SELECTED RECORD · ' + escapeHtml(selected.date.toUpperCase()) + '</p><h2 id="transcript-detail-title">' + escapeHtml(selected.title) + '</h2></div><div class="inspector-actions"><button type="button" class="icon-button" data-action="star-record" aria-label="' + (selected.starred ? 'Remove star' : 'Star transcript') + '">' + icon('star') + '</button><button type="button" class="icon-button" data-action="copy-transcript" aria-label="Copy transcript">' + icon('copy') + '</button><button type="button" class="icon-button" data-action="export-transcript" aria-label="Export transcript">' + icon('export') + '</button></div></header>' +
          '<div class="record-calibration"><span><b>' + selected.words + '</b> words</span><span><b>' + selected.time + '</b> captured</span><span><b>' + escapeHtml(selected.mode) + '</b> output</span><span><b>' + escapeHtml(selected.route) + '</b> route</span></div>' +
          '<div class="transcript-paper"><span class="paper-line-number">01</span><p>' + escapeHtml(selected.text) + '</p><div class="paper-cursor" aria-hidden="true"></div></div>' +
          '<section class="transformation-bay" aria-labelledby="transform-title"><header><div><p class="precision-label">SMART TRANSFORM</p><h3 id="transform-title">Shape this record</h3></div><span>TEXT ONLY · NO AUDIO</span></header>' +
            '<div class="transform-rail" role="radiogroup" aria-label="Transformation type">' + transforms.map(function (item) {
              var active = item[0] === state.transform;
              return '<button type="button" class="' + (active ? 'is-active' : '') + '" data-transform="' + item[0] + '" role="radio" aria-checked="' + active + '">' + icon(item[2]) + '<span>' + item[1] + '</span></button>';
            }).join('') + '</div>' +
            '<div class="transform-preview"><span class="preview-label">OUTPUT PREVIEW</span><div>' + transformPreview(selected) + '</div><button type="button" class="apply-transform" data-action="apply-transform">Apply transformation ' + icon('arrow') + '</button></div>' +
          '</section>' +
        '</article>' +
      '</div>' +
    '</section>';
  }

  function renderSessionTab(meeting) {
    if (state.sessionTab === 'transcript') {
      return '<div class="session-transcript">' + meeting.transcript.map(function (line) {
        return '<div class="utterance"><span class="speaker-mark">' + escapeHtml(line[0].slice(0, 1)) + '</span><span class="utterance-time">' + line[1] + '</span><p><strong>' + escapeHtml(line[0]) + '</strong>' + escapeHtml(line[2]) + '</p><button type="button" class="utterance-copy" data-action="copy-line" aria-label="Copy this line">' + icon('copy') + '</button></div>';
      }).join('') + '</div>';
    }
    if (state.sessionTab === 'actions') {
      return '<div class="action-register"><div class="register-head"><span>OWNER</span><span>COMMITMENT</span><span>DUE</span><span>STATE</span></div>' + meeting.actions.map(function (action, index) {
        return '<div class="action-row"><span><i>' + escapeHtml(action[0].slice(0, 1)) + '</i>' + escapeHtml(action[0]) + '</span><strong>' + escapeHtml(action[1]) + '</strong><span>' + escapeHtml(action[2]) + '</span><button type="button" class="action-state ' + (index === 0 && meeting.id === 'critique' ? 'is-done' : '') + '" data-action="toggle-action">' + (index === 0 && meeting.id === 'critique' ? 'Complete' : 'Open') + '</button></div>';
      }).join('') + '</div>';
    }
    return '<div class="session-summary"><article class="summary-prose"><p class="precision-label">SYNTHESIS</p><p>' + escapeHtml(meeting.summary) + '</p></article><article class="decision-register"><p class="precision-label">DECISIONS · ' + meeting.decisions.length + '</p><ol>' + meeting.decisions.map(function (decision) { return '<li><span>' + icon('check') + '</span><p>' + escapeHtml(decision) + '</p></li>'; }).join('') + '</ol></article><aside class="session-pulse"><span class="pulse-score">92<small>%</small></span><p>Conversation clarity</p><div><i style="--value:92%"></i></div><small>Balanced participation · low overlap</small></aside></div>';
  }

  function renderSessions() {
    var meeting = meetings.filter(function (item) { return item.id === state.sessionId; })[0] || meetings[0];
    var tabs = [['summary', 'Summary'], ['transcript', 'Transcript'], ['actions', 'Actions ' + meeting.actions.length]];
    return '<section class="view-panel sessions-view" aria-labelledby="sessions-title">' +
      '<header class="view-heading"><div><p class="eyebrow">CONVERSATION INSTRUMENT · LOCAL CAPTURE</p><h1 id="sessions-title">Meetings</h1><p>From live conversation to decisions, without breaking the room.</p></div>' +
        (state.sessionLive ? '<button type="button" class="stop-button" data-action="stop-session">' + icon('stop') + '<span>Stop & save<small data-live-time>' + formatTime(state.sessionElapsed) + '</small></span></button>' : '<button type="button" class="gold-button" data-action="start-session">' + icon('mic') + ' Start live session</button>') +
      '</header>' +
      (state.sessionLive ? '<div class="live-session-strip"><div class="reels" aria-hidden="true"><i class="reel"></i><i class="reel"></i></div><span class="live-beacon"><i></i></span><div><p class="precision-label">LIVE · ON-DEVICE RECORDING</p><strong>New conversation</strong></div><div class="live-wave" aria-hidden="true">' + Array.from({ length: 24 }, function (_, i) { return '<i style="--n:' + ((i * 7) % 13 + 3) + ';--d:' + (i * 34) + 'ms"></i>'; }).join('') + '</div><time data-live-time>' + formatTime(state.sessionElapsed) + '</time><span>1 speaker detected</span></div>' : '') +
      '<div class="session-workspace">' +
        '<aside class="session-index" aria-label="Recorded sessions"><header><span>SESSION ARCHIVE</span><button type="button" class="icon-button" data-action="import-audio" aria-label="Import audio">' + icon('plus') + '</button></header>' +
          '<div class="session-list">' + meetings.map(function (item, index) {
            var active = meeting.id === item.id;
            return '<button type="button" class="session-row ' + (active ? 'is-active' : '') + '" data-session-id="' + item.id + '" aria-pressed="' + active + '"><span class="session-number">S-' + String(index + 1).padStart(2, '0') + '</span><span><strong>' + escapeHtml(item.title) + '</strong><small>' + escapeHtml(item.eyebrow) + '</small></span><span class="session-duration">' + escapeHtml(item.meta.split(' · ')[0]) + '</span></button>';
          }).join('') + '</div><footer><span>12 sessions this month</span><small>8 hr 14 min processed locally</small></footer>' +
        '</aside>' +
        '<article class="session-detail"><header class="session-detail__head"><div><p class="eyebrow">' + escapeHtml(meeting.eyebrow) + '</p><h2>' + escapeHtml(meeting.title) + '</h2><p>' + escapeHtml(meeting.meta) + '</p></div><div><button type="button" class="icon-button" data-action="star-session" aria-label="Star session">' + icon('star') + '</button><button type="button" class="ghost-button" data-action="export-session">' + icon('export') + ' Export</button></div></header>' +
          '<div class="session-tabs" role="tablist" aria-label="Session detail">' + tabs.map(function (tab) {
            var active = state.sessionTab === tab[0];
            return '<button type="button" role="tab" aria-selected="' + active + '" class="' + (active ? 'is-active' : '') + '" data-session-tab="' + tab[0] + '"><span>' + tab[1] + '</span><i></i></button>';
          }).join('') + '</div>' +
          '<div class="session-tab-panel" role="tabpanel">' + renderSessionTab(meeting) + '</div>' +
        '</article>' +
      '</div>' +
    '</section>';
  }

  function readerText(documentItem) {
    var count = 0;
    return documentItem.paragraphs.map(function (paragraph) {
      var words = paragraph.split(/\s+/).map(function (word) {
        var index = count++;
        return '<span class="reader-word" data-reader-word="' + index + '">' + escapeHtml(word) + '</span>';
      }).join(' ');
      return '<p>' + words + '</p>';
    }).join('');
  }

  function readerWordCount(documentItem) {
    return documentItem.paragraphs.join(' ').trim().split(/\s+/).length;
  }

  function renderReader() {
    var current = documents.filter(function (item) { return item.id === state.readerDoc; })[0] || documents[0];
    var words = readerWordCount(current);
    var progress = Math.min(100, Math.round((state.readerWord / Math.max(1, words - 1)) * 100));
    return '<section class="view-panel reader-view" aria-labelledby="reader-title">' +
      '<header class="view-heading"><div><p class="eyebrow">LISTENING LIBRARY · FOUR DOCUMENTS</p><h1 id="reader-title">Reader</h1><p>A measured place for long-form listening and close attention.</p></div><button type="button" class="ghost-button" data-action="add-document">' + icon('plus') + ' Add document</button></header>' +
      '<div class="reader-workspace">' +
        '<aside class="document-index" aria-label="Reading library"><header><span>LIBRARY / 04</span><button type="button" class="icon-button" data-action="reader-search" aria-label="Search documents">' + icon('search') + '</button></header>' + documents.map(function (item, index) {
          var active = item.id === current.id;
          return '<button type="button" class="document-row ' + (active ? 'is-active' : '') + '" data-reader-doc="' + item.id + '" aria-pressed="' + active + '"><span class="document-index-number">0' + (index + 1) + '</span><span class="document-copy"><strong>' + escapeHtml(item.title) + '</strong><small>' + escapeHtml(item.author) + '</small></span><span class="document-progress"><i style="--progress:' + item.progress + '%"></i><small>' + item.progress + '%</small></span></button>';
        }).join('') + '<footer><button type="button" data-action="open-reader-folder">' + icon('folder') + ' Open local library</button><span>Private · On this device</span></footer></aside>' +
        '<article class="reading-stage">' +
          '<div class="reading-ruler" aria-hidden="true"><span>PAGE 216</span><i></i><span>' + escapeHtml(current.kind.toUpperCase()) + '</span></div>' +
          '<header class="reading-heading"><p class="precision-label">' + escapeHtml(current.chapter.toUpperCase()) + '</p><h2>' + escapeHtml(current.title) + '</h2><p>' + escapeHtml(current.author) + '</p></header>' +
          '<div class="reading-copy" aria-label="Document text">' + readerText(current) + '</div>' +
          '<div class="reader-position"><span style="--progress:' + progress + '%"><i></i></span><small>PAGE 216 / 318</small><small>' + escapeHtml(current.remaining.toUpperCase()) + '</small></div>' +
          '<div class="reader-player" aria-label="Reader playback controls">' +
            '<button type="button" class="player-side" data-action="reader-back" aria-label="Go back 15 seconds"><span>−15</span></button>' +
            '<button type="button" class="player-main ' + (state.readerPlaying ? 'is-playing' : '') + '" data-action="reader-toggle" aria-label="' + (state.readerPlaying ? 'Pause reading' : 'Play reading') + '">' + icon(state.readerPlaying ? 'pause' : 'play') + '<span>' + (state.readerPlaying ? 'Pause' : 'Listen') + '</span></button>' +
            '<button type="button" class="player-side" data-action="reader-forward" aria-label="Go forward 15 seconds"><span>+15</span></button>' +
            '<div class="speed-control" aria-label="Playback speed">' + [0.8, 1, 1.25, 1.5].map(function (speed) { return '<button type="button" class="' + (speed === state.readerSpeed ? 'is-active' : '') + '" data-reader-speed="' + speed + '" aria-pressed="' + (speed === state.readerSpeed) + '">' + speed + '×</button>'; }).join('') + '</div>' +
            '<button type="button" class="player-tool" data-action="bookmark" aria-label="Bookmark position">' + icon('bookmark') + '</button><button type="button" class="player-tool" data-action="sleep-timer" aria-label="Set sleep timer">' + icon('moon') + '</button>' +
          '</div>' +
        '</article>' +
      '</div>' +
    '</section>';
  }

  function chartGeometry(values) {
    var width = 760;
    var height = 250;
    var minimum = Math.min.apply(Math, values) - 8;
    var maximum = Math.max.apply(Math, values) + 8;
    var span = Math.max(1, maximum - minimum);
    var points = values.map(function (value, index) {
      var x = 18 + index * ((width - 36) / Math.max(1, values.length - 1));
      var y = height - 20 - ((value - minimum) / span) * (height - 40);
      return { x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10, value: value };
    });
    var line = points.map(function (point) { return point.x + ',' + point.y; }).join(' ');
    var area = 'M ' + points[0].x + ' ' + (height - 18) + ' L ' + points.map(function (point) { return point.x + ' ' + point.y; }).join(' L ') + ' L ' + points[points.length - 1].x + ' ' + (height - 18) + ' Z';
    return { line: line, area: area, points: points };
  }

  function renderSignals() {
    var values = signalRanges[state.signalRange];
    var geometry = chartGeometry(values);
    return '<section class="view-panel signals-view" aria-labelledby="signals-title">' +
      '<header class="view-heading"><div><p class="eyebrow">PERSONAL TELEMETRY · LOCAL ONLY</p><h1 id="signals-title">Stats</h1><p>A quiet reading of pace, clarity and time returned to you.</p></div><div class="range-switch" role="radiogroup" aria-label="Signal range">' + [['7d', '7 days'], ['30d', '30 days'], ['12w', '12 weeks']].map(function (range) { var active = range[0] === state.signalRange; return '<button type="button" class="' + (active ? 'is-active' : '') + '" data-signal-range="' + range[0] + '" role="radio" aria-checked="' + active + '">' + range[1] + '</button>'; }).join('') + '</div></header>' +
      '<div class="metric-grid">' +
        '<article class="metric-block metric-block--primary"><span class="metric-index">01 / WORDS</span><strong>12,480</strong><p>words shaped this month</p><small>↑ 18.4% from your previous period</small><div class="metric-ticks">' + values.slice(-9).map(function (value, i) { return '<i style="--h:' + value + '%;--d:' + (i * 40) + 'ms"></i>'; }).join('') + '</div></article>' +
        '<article class="metric-block"><span class="metric-index">02 / TIME</span><strong>4<small>h</small> 38<small>m</small></strong><p>estimated typing time returned</p><span class="metric-seal">+42 min this week</span></article>' +
        '<article class="metric-block"><span class="metric-index">03 / ACCURACY</span><div class="radial-metric" style="--value:98.2"><strong>98.2<small>%</small></strong></div><p>accepted without correction</p></article>' +
        '<article class="metric-block"><span class="metric-index">04 / SESSIONS</span><strong>21</strong><p>focused voice sessions</p><small>Longest uninterrupted flow · 18 min</small></article>' +
      '</div>' +
      '<div class="signals-lower">' +
        '<article class="signal-chart"><header><div><p class="precision-label">OUTPUT VELOCITY</p><h2>Words moved into flow</h2></div><span class="chart-legend"><i></i> Words per active minute</span></header>' +
          '<div class="chart-shell"><svg viewBox="0 0 760 250" role="img" aria-label="Output velocity across the selected period" preserveAspectRatio="none"><defs><linearGradient id="signal-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity=".28"></stop><stop offset="1" stop-color="currentColor" stop-opacity="0"></stop></linearGradient></defs><g class="chart-grid"><line x1="18" y1="28" x2="742" y2="28"></line><line x1="18" y1="91" x2="742" y2="91"></line><line x1="18" y1="154" x2="742" y2="154"></line><line x1="18" y1="217" x2="742" y2="217"></line></g><path class="chart-area" d="' + geometry.area + '"></path><polyline class="chart-line" points="' + geometry.line + '"></polyline>' + geometry.points.map(function (point, index) { return '<circle class="chart-point ' + (index === geometry.points.length - 1 ? 'is-current' : '') + '" cx="' + point.x + '" cy="' + point.y + '" r="' + (index === geometry.points.length - 1 ? 5 : 3) + '"><title>' + point.value + ' words per active minute</title></circle>'; }).join('') + '</svg><div class="chart-axis"><span>START</span><span>MIDPOINT</span><span>NOW</span></div></div>' +
        '</article>' +
        '<aside class="signal-detail"><header><p class="precision-label">PATTERN 03</p><h2>Your clearest window</h2></header><div class="clarity-time"><span>09</span><i>:</i><span>00</span><small>— 11:30</small></div><p>Morning sessions are 23% longer and require fewer corrections.</p><div class="day-spectrum">' + ['M', 'T', 'W', 'T', 'F', 'S', 'S'].map(function (day, index) { return '<span class="' + (index === 2 || index === 4 ? 'is-strong' : '') + '"><i style="--v:' + [54, 69, 92, 63, 88, 39, 27][index] + '%"></i><small>' + day + '</small></span>'; }).join('') + '</div><footer>' + icon('lock') + '<span>Calculated locally from usage events. Never synced.</span></footer></aside>' +
      '</div>' +
    '</section>';
  }

  function settingSwitch(key, label) {
    var on = Boolean(state.settings[key]);
    return '<button type="button" class="switch ' + (on ? 'is-on' : '') + '" data-setting-key="' + key + '" role="switch" aria-checked="' + on + '" aria-label="' + escapeHtml(label) + '"><span><i></i></span><small>' + (on ? 'ON' : 'OFF') + '</small></button>';
  }

  function settingSelect(key, options) {
    return '<label class="select-wrap"><span class="sr-only">Choose ' + escapeHtml(key) + '</span><select data-control-select="' + key + '">' + options.map(function (option) { return '<option ' + (state.selects[key] === option ? 'selected' : '') + '>' + escapeHtml(option) + '</option>'; }).join('') + '</select>' + icon('chevron') + '</label>';
  }

  function settingRow(index, title, description, control, badge) {
    return '<div class="setting-row"><span class="setting-number">' + String(index).padStart(2, '0') + '</span><div class="setting-copy"><div><h3>' + escapeHtml(title) + '</h3>' + (badge ? '<span class="setting-badge">' + escapeHtml(badge) + '</span>' : '') + '</div><p>' + escapeHtml(description) + '</p></div><div class="setting-control">' + control + '</div></div>';
  }

  function controlContent() {
    var section = state.controlSection;
    if (section === 'intelligence') {
      return settingRow(1, 'Text intelligence', 'Use deep shaping for commands, summaries and transformations.', settingSwitch('deepPolish', 'Text intelligence')) +
        settingRow(2, 'Primary provider', 'Only shaped text is sent when this cloud route is selected.', settingSelect('provider', ['Cerebras · gpt-oss-120b', 'OpenAI · GPT-5 mini', 'Anthropic · Claude Sonnet', 'Local · Ollama'])) +
        settingRow(3, 'Local fallback', 'Continue with on-device shaping if the active provider is unavailable.', settingSwitch('localFallback', 'Local fallback'), 'RECOMMENDED') +
        settingRow(4, 'Conversation context', 'Allow Reply mode to use intentionally selected nearby text.', settingSwitch('providerContext', 'Conversation context'));
    }
    if (section === 'language') {
      return settingRow(1, 'Primary language', 'Used for punctuation, spelling and speech model selection.', settingSelect('language', ['English (UK)', 'English (US)', 'Auto-detect', 'French', 'Arabic'])) +
        settingRow(2, 'Preserve personal terms', 'Prioritise names, brands and specialist vocabulary.', settingSwitch('preserveTerms', 'Preserve personal terms')) +
        settingRow(3, 'Multilingual awareness', 'Keep foreign phrases intact inside the primary language.', settingSwitch('multilingual', 'Multilingual awareness')) +
        settingRow(4, 'Remove fillers', 'Clean repeated starts and low-confidence filler words.', settingSwitch('fillers', 'Remove fillers')) +
        settingRow(5, 'Personal vocabulary', '24 words · Mumble, Khaled, barakah, tawakkul and 20 more.', '<button type="button" class="ghost-button" data-action="manage-vocabulary">Manage 24</button>');
    }
    if (section === 'privacy') {
      return '<div class="privacy-route"><span>' + icon('shield') + '</span><div><p class="precision-label">CURRENT ROUTE</p><h3>Audio never leaves this device</h3><p>Speech becomes raw text locally. Optional intelligence receives text only.</p></div><b>LOCAL</b></div>' +
        settingRow(1, 'Keep local audio', 'Retain session audio after a transcript is complete.', settingSwitch('localAudio', 'Keep local audio')) +
        settingRow(2, 'Clipboard history', 'Keep intentionally captured text in the transcript deck.', settingSwitch('clipboardHistory', 'Clipboard history')) +
        settingRow(3, 'Anonymous telemetry', 'Share non-content reliability signals. Never speech or text.', settingSwitch('telemetry', 'Anonymous telemetry')) +
        settingRow(4, 'History limit', 'Oldest local records are removed when this limit is reached.', settingSelect('history', ['5,000 items', '2,000 items', '500 items', 'Keep everything'])) +
        settingRow(5, 'Local data', 'Review transcripts, audio, documents and usage signals.', '<button type="button" class="ghost-button" data-action="open-local-data">Review & clear…</button>');
    }
    if (section === 'appearance') {
      return '<div class="appearance-sample"><div><span></span><i></i><i></i><i></i></div><p>APERTURE / OBSIDIAN + CHAMPAGNE</p></div>' +
        settingRow(1, 'Subtle motion', 'Use iris reveals, traced connectors and measured transitions.', settingSwitch('subtleMotion', 'Subtle motion')) +
        settingRow(2, 'Ambient signal', 'Show restrained gold light only around active states.', settingSwitch('ambientGlow', 'Ambient signal')) +
        settingRow(3, 'High contrast', 'Increase hairline and secondary text contrast.', settingSwitch('highContrast', 'High contrast')) +
        settingRow(4, 'Interface density', 'Adjust the spacing of indexes and precision readouts.', settingSelect('density', ['Airy', 'Balanced', 'Compact']));
    }
    if (section === 'system') {
      return settingRow(1, 'Transcription model', 'Small is the recommended balance of pace and accuracy.', settingSelect('model', ['Small · Recommended', 'Medium · Accurate', 'Tiny · Fastest'])) +
        settingRow(2, 'Launch with computer', 'Keep the voice aperture ready in the background.', settingSwitch('launchAtLogin', 'Launch with computer')) +
        settingRow(3, 'Automatic updates', 'Install stable Mumble updates when the app is idle.', settingSwitch('automaticUpdates', 'Automatic updates')) +
        settingRow(4, 'Resource saver', 'Use the fastest local model and pause warm providers.', settingSwitch('resourceSaver', 'Resource saver')) +
        settingRow(5, 'Version', 'Mumble Aperture experiment · sealed prototype.', '<button type="button" class="ghost-button" data-action="check-update">Check again</button>', '0.9 LAB');
    }
    return settingRow(1, 'Input device', 'The microphone used by dictation and live sessions.', settingSelect('microphone', ['Studio microphone', 'Built-in microphone', 'Display microphone'])) +
      settingRow(2, 'Automatic paste', 'Place finished text at the active cursor.', settingSwitch('autoPaste', 'Automatic paste'), 'READY') +
      settingRow(3, 'Hold to speak', 'Record only while the global trigger remains pressed.', settingSwitch('holdToTalk', 'Hold to speak')) +
      settingRow(4, 'Input chime', 'Play a restrained cue when listening starts and stops.', settingSwitch('inputChime', 'Input chime')) +
      settingRow(5, 'Global trigger', 'Available from anywhere without opening the window.', '<div class="shortcut"><kbd>CTRL</kbd><span>+</span><kbd>WIN</kbd><button type="button" data-action="change-shortcut">Change</button></div>');
  }

  function renderControls() {
    var current = controlSections.filter(function (section) { return section.id === state.controlSection; })[0] || controlSections[0];
    return '<section class="view-panel controls-view" aria-labelledby="controls-title">' +
      '<header class="view-heading"><div><p class="eyebrow">CALIBRATION CONSOLE · LOCAL PROFILE</p><h1 id="controls-title">Settings</h1><p>Six precise surfaces. Nothing hidden behind an account.</p></div><span class="saved-state">' + icon('check') + ' MOCK PROFILE READY</span></header>' +
      '<div class="control-workspace">' +
        '<aside class="control-index" aria-label="Settings sections"><header><span>SECTION</span><span>06 CHANNELS</span></header>' + controlSections.map(function (section, index) {
          var active = section.id === current.id;
          return '<button type="button" class="control-section ' + (active ? 'is-active' : '') + '" data-control-section="' + section.id + '" aria-pressed="' + active + '"><span class="control-section__number">0' + (index + 1) + '</span><span class="control-section__icon">' + icon(section.icon) + '</span><span><strong>' + escapeHtml(section.label) + '</strong><small>' + escapeHtml(section.note) + '</small></span><i>' + icon('arrow') + '</i></button>';
        }).join('') + '<footer>' + icon('lock') + '<span>These controls are visual mock state. No preferences are stored.</span></footer></aside>' +
        '<article class="control-panel"><header><div><p class="precision-label">CHANNEL ' + String(controlSections.indexOf(current) + 1).padStart(2, '0') + '</p><h2>' + escapeHtml(current.label) + '</h2><p>' + escapeHtml(current.note) + '</p></div><span class="panel-glyph">' + icon(current.icon) + '</span></header><div class="setting-stack">' + controlContent() + '</div></article>' +
      '</div>' +
    '</section>';
  }

  var renderers = {
    focus: renderFocus,
    deck: renderDeck,
    sessions: renderSessions,
    reader: renderReader,
    signals: renderSignals,
    controls: renderControls
  };

  function syncCompositeTabStops() {
    all('[role="radiogroup"], [role="tablist"]').forEach(function (group) {
      var items = all('button[role="radio"], button[role="tab"]', group);
      if (!items.length) return;
      var active = items.filter(function (item) {
        return item.getAttribute('aria-checked') === 'true' ||
          item.getAttribute('aria-selected') === 'true' ||
          item.classList.contains('is-active');
      })[0] || items[0];
      items.forEach(function (item) {
        item.tabIndex = item === active ? 0 : -1;
      });
    });
  }

  function render() {
    var view = one('#view');
    if (!view || !renderers[state.view]) return;
    body.dataset.view = state.view;
    view.innerHTML = renderers[state.view]();
    view.classList.remove('is-entering');
    void view.offsetWidth;
    view.classList.add('is-entering');
    syncNavigation();
    syncCompositeTabStops();
    if (state.view === 'reader') {
      window.requestAnimationFrame(updateReaderHighlight);
      restartReaderClock();
    } else {
      clearInterval(readerClock);
      readerClock = null;
    }
    if (state.sessionLive) updateSessionClock();
  }

  function syncNavigation() {
    all('[data-view-target]').forEach(function (button) {
      var active = button.dataset.viewTarget === state.view;
      button.classList.toggle('is-active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
      if (button.dataset.viewTarget === 'sessions') button.classList.toggle('has-live-state', state.sessionLive);
    });
    body.classList.toggle('has-live-session', state.sessionLive);
  }

  function navigate(viewName) {
    if (!renderers[viewName]) return;
    state.view = viewName;
    if (viewName !== 'reader') state.readerPlaying = false;
    render();
    var view = one('#view');
    if (view) view.scrollTop = 0;
  }

  function ensureCore() {
    var core = one('#voice-core');
    var wave = one('#core-wave');
    if (!core) return;
    if (core.tagName === 'BUTTON') core.type = 'button';
    else {
      core.setAttribute('role', 'button');
      core.tabIndex = 0;
    }
    core.setAttribute('aria-label', 'Start voice dictation');
    core.setAttribute('aria-pressed', 'false');
    if (wave) {
      wave.innerHTML = Array.from({ length: 19 }, function (_, index) {
        var height = 4 + ((index * 11 + index * index * 3) % 18);
        return '<i style="--bar:' + height + 'px;--delay:' + (index * 41) + 'ms"></i>';
      }).join('');
    }
    setVoiceMode('idle');
  }

  function setVoiceMode(mode) {
    var core = one('#voice-core');
    var status = one('#core-status');
    var caption = one('#core-caption');
    if (!core) return;
    state.voice = mode;
    body.dataset.voice = mode;
    core.classList.toggle('is-listening', mode === 'listening');
    core.classList.toggle('is-processing', mode === 'processing');
    core.setAttribute('aria-pressed', String(mode === 'listening'));
    if (mode === 'listening') {
      core.setAttribute('aria-label', 'Finish voice dictation');
      if (status) status.textContent = 'Listening — take your time';
      if (caption) caption.textContent = 'Local microphone · live words remain on this device';
    } else if (mode === 'processing') {
      core.setAttribute('aria-label', 'Processing dictation');
      if (status) status.textContent = 'Refining 38 words';
      if (caption) caption.textContent = 'Punctuation, structure and tone · on-device pass';
    } else {
      core.setAttribute('aria-label', 'Start voice dictation');
      if (status) status.textContent = 'Ready for a thought';
      if (caption) caption.textContent = 'Click the aperture · Ctrl + Win from anywhere';
    }
  }

  function toggleVoice() {
    clearTimeout(voiceClock);
    if (state.voice === 'idle' || state.voice === 'processing') {
      setVoiceMode('listening');
      return;
    }
    setVoiceMode('processing');
    voiceClock = window.setTimeout(function () {
      setVoiceMode('idle');
      toast('Dictation placed', '38 words · Clean text · local processing');
    }, 1750);
  }

  function startSession() {
    if (state.sessionLive) return;
    state.sessionLive = true;
    state.sessionElapsed = 0;
    state.sessionStarted = Date.now();
    clearInterval(sessionClock);
    sessionClock = window.setInterval(updateSessionClock, 1000);
    navigate('sessions');
    toast('Local session started', 'Audio is recording on this device.');
  }

  function updateSessionClock() {
    if (!state.sessionLive) return;
    state.sessionElapsed = Math.floor((Date.now() - state.sessionStarted) / 1000);
    all('[data-live-time]').forEach(function (node) { node.textContent = formatTime(state.sessionElapsed); });
  }

  function stopSession() {
    if (!state.sessionLive) return;
    updateSessionClock();
    state.sessionLive = false;
    clearInterval(sessionClock);
    sessionClock = null;
    render();
    toast('Session saved', formatTime(state.sessionElapsed) + ' · transcript and summary queued locally');
  }

  function updateReaderHighlight() {
    var words = all('[data-reader-word]');
    if (!words.length) return;
    state.readerWord = Math.max(0, Math.min(state.readerWord, words.length - 1));
    words.forEach(function (word, index) {
      word.classList.toggle('is-read', index < state.readerWord);
      word.classList.toggle('is-active', index === state.readerWord);
    });
  }

  function restartReaderClock() {
    clearInterval(readerClock);
    readerClock = null;
    if (!state.readerPlaying || state.view !== 'reader') return;
    readerClock = window.setInterval(function () {
      var wordCount = all('[data-reader-word]').length;
      if (!wordCount || state.readerWord >= wordCount - 1) {
        state.readerPlaying = false;
        clearInterval(readerClock);
        readerClock = null;
        render();
        toast('Passage complete', 'Your reading position has been held locally.');
        return;
      }
      state.readerWord += 1;
      updateReaderHighlight();
    }, Math.round(620 / state.readerSpeed));
  }

  function toast(title, detail) {
    var region = one('#toast-region');
    if (!region) return;
    var node = doc.createElement('div');
    node.className = 'toast';
    node.setAttribute('role', 'status');
    node.innerHTML = '<span class="toast-mark">' + icon('check') + '</span><span><strong>' + escapeHtml(title) + '</strong>' + (detail ? '<small>' + escapeHtml(detail) + '</small>' : '') + '</span>';
    region.appendChild(node);
    window.setTimeout(function () {
      node.classList.add('is-leaving');
      window.setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, 320);
    }, 3200);
  }

  var commands = [
    { id: 'voice', label: 'Start a new dictation', note: 'Ctrl + Win', icon: 'mic', keys: 'speak record voice' },
    { id: 'focus', label: 'Return to Focus', note: 'Workspace', icon: 'aperture', keys: 'home centre' },
    { id: 'deck', label: 'Open Transcript deck', note: '6 records', icon: 'deck', keys: 'library history transcripts' },
    { id: 'capture', label: 'Capture selected text', note: 'Local', icon: 'capture', keys: 'clipboard selection' },
    { id: 'session', label: 'Start a live session', note: 'Local audio', icon: 'sessions', keys: 'meeting record conversation' },
    { id: 'sessions', label: 'Browse Sessions', note: '12 this month', icon: 'sessions', keys: 'meetings archive' },
    { id: 'reader', label: 'Continue reading', note: 'Thinking in Systems', icon: 'reader', keys: 'play document book' },
    { id: 'signals', label: 'View personal Signals', note: 'Local metrics', icon: 'signals', keys: 'stats analytics pace' },
    { id: 'privacy', label: 'Open Privacy controls', note: 'Audio stays local', icon: 'shield', keys: 'settings security data' },
    { id: 'appearance', label: 'Calibrate appearance', note: 'Aperture', icon: 'controls', keys: 'settings theme motion' }
  ];

  function matchingCommands() {
    var query = state.commandQuery.trim().toLowerCase();
    if (!query) return commands;
    return commands.filter(function (command) {
      var haystack = [command.id, command.label, command.note, command.keys].join(' ').toLowerCase();
      return query.split(/\s+/).every(function (term) {
        return haystack.includes(term);
      });
    });
  }

  function renderCommands() {
    var result = one('#command-results');
    if (!result) return;
    var matches = matchingCommands();
    if (state.commandIndex >= matches.length) state.commandIndex = Math.max(0, matches.length - 1);
    result.innerHTML = matches.length ? '<div class="command-group-label"><span>AVAILABLE ACTIONS</span><span>' + matches.length + ' RESULTS</span></div>' + matches.map(function (command, index) {
      return '<button type="button" class="command-result ' + (index === state.commandIndex ? 'is-active' : '') + '" data-command-id="' + command.id + '" role="option" aria-selected="' + (index === state.commandIndex) + '"><span class="command-result__icon">' + icon(command.icon) + '</span><span><strong>' + escapeHtml(command.label) + '</strong><small>' + escapeHtml(command.note) + '</small></span><i>' + String(index + 1).padStart(2, '0') + '</i></button>';
    }).join('') : '<div class="command-empty"><span>' + icon('search') + '</span><strong>No matching instrument</strong><p>Try “session”, “reader” or “privacy”.</p></div>';
  }

  function openCommand() {
    var palette = one('#command-palette');
    var scrim = one('#scrim');
    var input = one('#command-input');
    var shell = one('#app-shell');
    if (!palette || !scrim || !input) return;
    lastCommandFocus = doc.activeElement;
    state.commandQuery = '';
    state.commandIndex = 0;
    input.value = '';
    palette.hidden = false;
    scrim.hidden = false;
    palette.setAttribute('aria-hidden', 'false');
    body.classList.add('command-open');
    renderCommands();
    input.focus();
    if (shell) {
      shell.inert = true;
      shell.setAttribute('aria-hidden', 'true');
    }
  }

  function closeCommand() {
    var palette = one('#command-palette');
    var scrim = one('#scrim');
    var shell = one('#app-shell');
    if (!palette || palette.hidden) return;
    palette.hidden = true;
    scrim.hidden = true;
    palette.setAttribute('aria-hidden', 'true');
    body.classList.remove('command-open');
    if (shell) {
      shell.inert = false;
      shell.removeAttribute('aria-hidden');
    }
    if (lastCommandFocus && typeof lastCommandFocus.focus === 'function') lastCommandFocus.focus();
  }

  function runCommand(id) {
    closeCommand();
    if (id === 'voice') return toggleVoice();
    if (id === 'focus' || id === 'deck' || id === 'sessions' || id === 'signals') return navigate(id);
    if (id === 'session') return startSession();
    if (id === 'reader') {
      state.readerPlaying = true;
      navigate('reader');
      return;
    }
    if (id === 'privacy' || id === 'appearance') {
      state.controlSection = id;
      navigate('controls');
      return;
    }
    if (id === 'capture') toast('Selection captured', 'Added to the mock deck · no clipboard was read.');
  }

  function handleAction(action, button) {
    if (action === 'core-toggle') return toggleVoice();
    if (action === 'capture') return toast('Capture staged', 'Prototype only · no clipboard content was accessed.');
    if (action === 'copy-transcript' || action === 'copy-line') return toast('Copied for the prototype', 'No system clipboard was changed.');
    if (action === 'export-transcript' || action === 'export-session') return toast('Export panel staged', 'Markdown · TXT · HTML · Clipboard');
    if (action === 'star-record' || action === 'star-session') return toast('Added to starred', 'Available from the transcript index.');
    if (action === 'apply-transform') return toast('Transformation applied', 'The preview now represents the finished text.');
    if (action === 'start-session') return startSession();
    if (action === 'stop-session') return stopSession();
    if (action === 'import-audio') return toast('Audio importer staged', 'Local WAV, MP3, M4A and FLAC.');
    if (action === 'toggle-action') {
      button.classList.toggle('is-done');
      button.textContent = button.classList.contains('is-done') ? 'Complete' : 'Open';
      return;
    }
    if (action === 'reader-toggle') {
      state.readerPlaying = !state.readerPlaying;
      render();
      return;
    }
    if (action === 'reader-back' || action === 'reader-forward') {
      state.readerWord += action === 'reader-back' ? -6 : 6;
      updateReaderHighlight();
      return;
    }
    if (action === 'bookmark') return toast('Bookmark placed', 'Page 216 · saved inside this mock session.');
    if (action === 'sleep-timer') return toast('Sleep timer ready', 'Playback will stop after 30 minutes.');
    if (action === 'add-document') return toast('Document picker staged', 'PDF, DOCX, EPUB, Markdown and TXT.');
    if (action === 'reader-search') return toast('Reader search ready', 'Four local documents are indexed.');
    if (action === 'open-reader-folder') return toast('Local library', 'Prototype only · no folder was opened.');
    if (action === 'manage-vocabulary') return toast('Vocabulary console staged', '24 personal words · stored locally.');
    if (action === 'open-local-data') return toast('Local data review staged', 'No files or history were changed.');
    if (action === 'check-update') return toast('Prototype is current', 'A sealed static experiment has no update channel.');
    if (action === 'change-shortcut') return toast('Shortcut recorder ready', 'Press a new key combination to preview it.');
    if (action === 'show-shortcuts') return toast('Global shortcuts', 'Ctrl + Win · speak  /  Ctrl + K · command');
  }

  function bindEvents() {
    doc.addEventListener('click', function (event) {
      var target = event.target;
      var commandTrigger = target.closest('#command-trigger');
      if (commandTrigger) {
        event.preventDefault();
        openCommand();
        return;
      }
      var scrim = target.closest('#scrim');
      if (scrim) {
        closeCommand();
        return;
      }
      var command = target.closest('[data-command-id]');
      if (command) return runCommand(command.dataset.commandId);
      var nav = target.closest('[data-view-target]');
      if (nav) return navigate(nav.dataset.viewTarget);
      var core = target.closest('#voice-core');
      if (core) return toggleVoice();
      var focusMode = target.closest('[data-focus-mode]');
      if (focusMode) {
        state.focusMode = focusMode.dataset.focusMode;
        render();
        return;
      }
      var openDeck = target.closest('[data-open-deck]');
      if (openDeck) {
        state.deckItem = openDeck.dataset.openDeck;
        navigate('deck');
        return;
      }
      var filter = target.closest('[data-deck-filter]');
      if (filter) {
        state.deckFilter = filter.dataset.deckFilter;
        var filteredIds = deckItems.filter(function (item) { return state.deckFilter === 'all' || (state.deckFilter === 'starred' ? item.starred : item.type === state.deckFilter); }).map(function (item) { return item.id; });
        if (filteredIds.indexOf(state.deckItem) === -1 && filteredIds.length) state.deckItem = filteredIds[0];
        render();
        return;
      }
      var deckItem = target.closest('[data-deck-id]');
      if (deckItem) {
        state.deckItem = deckItem.dataset.deckId;
        render();
        return;
      }
      var transform = target.closest('[data-transform]');
      if (transform) {
        state.transform = transform.dataset.transform;
        render();
        return;
      }
      var session = target.closest('[data-session-id]');
      if (session) {
        state.sessionId = session.dataset.sessionId;
        render();
        return;
      }
      var sessionTab = target.closest('[data-session-tab]');
      if (sessionTab) {
        state.sessionTab = sessionTab.dataset.sessionTab;
        render();
        return;
      }
      var readerDocument = target.closest('[data-reader-doc]');
      if (readerDocument) {
        state.readerDoc = readerDocument.dataset.readerDoc;
        state.readerWord = 0;
        render();
        return;
      }
      var readerSpeed = target.closest('[data-reader-speed]');
      if (readerSpeed) {
        state.readerSpeed = Number(readerSpeed.dataset.readerSpeed);
        render();
        return;
      }
      var signalRange = target.closest('[data-signal-range]');
      if (signalRange) {
        state.signalRange = signalRange.dataset.signalRange;
        render();
        return;
      }
      var controlSection = target.closest('[data-control-section]');
      if (controlSection) {
        state.controlSection = controlSection.dataset.controlSection;
        render();
        return;
      }
      var setting = target.closest('[data-setting-key]');
      if (setting) {
        var key = setting.dataset.settingKey;
        state.settings[key] = !state.settings[key];
        render();
        toast(state.settings[key] ? 'Control enabled' : 'Control disabled', 'Mock state · nothing was stored.');
        return;
      }
      var action = target.closest('[data-action]');
      if (action) handleAction(action.dataset.action, action);
    });

    doc.addEventListener('change', function (event) {
      var select = event.target.closest('[data-control-select]');
      if (!select) return;
      state.selects[select.dataset.controlSelect] = select.value;
      toast('Calibration changed', select.value + ' · mock state only');
    });

    var commandInput = one('#command-input');
    if (commandInput) commandInput.addEventListener('input', function (event) {
      state.commandQuery = event.target.value;
      state.commandIndex = 0;
      renderCommands();
    });

    doc.addEventListener('keydown', function (event) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        if (one('#command-palette') && !one('#command-palette').hidden) closeCommand();
        else openCommand();
        return;
      }
      var palette = one('#command-palette');
      var paletteOpen = palette && !palette.hidden;
      if (event.key === 'Escape' && paletteOpen) {
        event.preventDefault();
        closeCommand();
        return;
      }
      if (paletteOpen && event.key === 'Tab') {
        var focusable = all('input, button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])', palette)
          .filter(function (node) { return !node.hidden && node.offsetParent !== null; });
        if (!focusable.length) return;
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (event.shiftKey && doc.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && doc.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
        return;
      }
      if (paletteOpen && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
        event.preventDefault();
        var matches = matchingCommands();
        if (!matches.length) return;
        state.commandIndex = (state.commandIndex + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length;
        renderCommands();
        return;
      }
      if (paletteOpen && event.key === 'Enter') {
        event.preventDefault();
        var current = matchingCommands()[state.commandIndex];
        if (current) runCommand(current.id);
        return;
      }
      if (!paletteOpen && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].indexOf(event.key) !== -1) {
        var group = event.target.closest && event.target.closest('[role="radiogroup"], [role="tablist"]');
        if (group) {
          var compositeItems = all('button[role="radio"], button[role="tab"]', group);
          var itemIndex = compositeItems.indexOf(event.target);
          if (itemIndex !== -1 && compositeItems.length) {
            event.preventDefault();
            var nextIndex;
            if (event.key === 'Home') nextIndex = 0;
            else if (event.key === 'End') nextIndex = compositeItems.length - 1;
            else {
              var direction = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1;
              nextIndex = (itemIndex + direction + compositeItems.length) % compositeItems.length;
            }
            compositeItems[nextIndex].focus();
            compositeItems[nextIndex].click();
            return;
          }
        }
      }
      var core = one('#voice-core');
      if (core && event.target === core && core.tagName !== 'BUTTON' && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        toggleVoice();
      }
    });
  }

  function init() {
    var initialView = body.dataset.view;
    if (initialView && renderers[initialView]) state.view = initialView;
    ensureCore();
    bindEvents();
    render();
    renderCommands();
  }

  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', init);
  else init();
})();
