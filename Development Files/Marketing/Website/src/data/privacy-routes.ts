export const privacyFactDefinitions = [
  { key: 'input', label: 'Input' },
  { key: 'localStage', label: 'Local stage' },
  { key: 'egress', label: 'Egress' },
  { key: 'provider', label: 'Provider' },
  { key: 'network', label: 'Network' },
  { key: 'keyOrAccount', label: 'Key or account' },
  { key: 'externalCost', label: 'External cost' },
  { key: 'output', label: 'Output' },
  { key: 'userControl', label: 'User control' },
  { key: 'failureBoundary', label: 'Failure boundary' },
] as const;

type PrivacyFactKey = (typeof privacyFactDefinitions)[number]['key'];

type PrivacyRouteFacts = Record<PrivacyFactKey, string>;

type RouteStep = {
  title: string;
  detail: string;
};

type ProductProof = {
  kind: 'product-proof';
  image: {
    src: string;
    width: number;
    height: number;
    alt: string;
  };
  captionTitle: string;
  caption: string;
};

type ControlProof = {
  kind: 'controls';
  titleId: string;
  title: string;
  description: string;
  items: readonly {
    term: string;
    detail: string;
  }[];
};

type PrivacyRoute = {
  panelId: string;
  tabId: string;
  titleId: string;
  pathLabelId: string;
  label: string;
  tabSummary: string;
  summary: string;
  status: string;
  pathLabel: string;
  pathSteps: readonly RouteStep[];
  explanation: {
    title: string;
    paragraphs: readonly string[];
  };
  evidenceLayout?: 'find';
  evidence: ProductProof | ControlProof;
  tableCaption: string;
  facts: PrivacyRouteFacts;
};

export const privacyRoutes = [
  {
    panelId: 'local-transcription',
    tabId: 'privacy-tab-transcription',
    titleId: 'local-transcription-title',
    pathLabelId: 'local-transcription-path-label',
    label: 'Local Transcription',
    tabSummary: 'Default · selected microphone audio stays local',
    summary: 'Microphone audio is processed by faster-whisper on this computer. Local speech-to-text is the default route; text shaping is a separate later decision.',
    status: 'Default · on this device',
    pathLabel: 'Local Transcription data-path explanation · not an app screenshot',
    pathSteps: [
      { title: 'Selected microphone', detail: 'One-channel speech capture' },
      { title: 'Recovery segments', detail: 'Bounded PCM16 files in Mumble app data' },
      { title: 'faster-whisper', detail: 'Local CPU or available GPU' },
      { title: 'Finished text', detail: 'Target cursor and local Deck history' },
    ],
    explanation: {
      title: 'Audio is not treated as a slogan.',
      paragraphs: [
        'When dictation starts, Mumble saves accepted audio in local PCM16 files of no more than 30 seconds. A small record tracks which files belong to this dictation and whether it finished. That lets interrupted work stop safely or recover without inserting the same text twice.',
        'Recovery files and the finished transcript are durable local data. The current source does not promise automatic deletion after a set time. Nothing leaves this computer for speech-to-text on this route.',
      ],
    },
    evidence: {
      kind: 'product-proof',
      image: {
        src: '/product/settings.webp',
        width: 1180,
        height: 820,
        alt: 'Accepted Mumble Settings capture showing the processing setup with Transcription set to Local (private)',
      },
      captionTitle: 'Real product capture',
      caption: 'Accepted Windows source view showing the saved Local (private) transcription state. Current source also exposes microphone selection, location, egress, privacy, and cost facts; this is not an installed-release claim.',
    },
    tableCaption: 'Local Transcription data path',
    facts: {
      input: 'One-channel audio from the microphone selected in Settings. This route does not claim computer-audio capture or direct video import.',
      localStage: 'Accepted audio is written to durable recovery segments, then faster-whisper runs on local CPU or an available supported GPU.',
      egress: 'Nothing leaves this computer for speech-to-text. Later optional Text Shaping receives finished text only through its own effective route.',
      provider: 'No external provider. The selected local faster-whisper model is the speech-to-text engine.',
      network: 'Not required for transcription once the selected local speech model is present.',
      keyOrAccount: 'No Mumble account and no provider key are required.',
      externalCost: "No external provider charge. This route uses the computer's own processing and storage.",
      output: 'Finished text is saved in local History/Deck before one target-bound insertion is attempted. Durable session records support crash recovery.',
      userControl: 'Choose and test the microphone, keep Local selected, start or stop dictation, adjust language or the local model, and use the device-only override.',
      failureBoundary: 'A missing model, device problem, storage pressure, or processing failure stays local and reports a failure or recovery state. It never silently sends audio online.',
    },
  },
  {
    panelId: 'cloud-transcription',
    tabId: 'privacy-tab-cloud-transcription',
    titleId: 'cloud-transcription-title',
    pathLabelId: 'cloud-transcription-path-label',
    label: 'Cloud Transcription',
    tabSummary: 'Optional · recorded audio may go online',
    summary: 'Recorded audio may be sent only after the user deliberately selects Cloud Transcription and its configured provider route is ready.',
    status: 'Optional · audio may leave',
    pathLabel: 'Cloud Transcription data-path explanation · not an app screenshot',
    pathSteps: [
      { title: 'Selected microphone', detail: 'Recorded one-channel audio' },
      { title: 'Local recovery', detail: 'Bounded segments stay in Mumble app data' },
      { title: 'Configured provider', detail: 'Groq, OpenAI, or OpenRouter' },
      { title: 'Returned transcript', detail: 'Saved locally and delivered once' },
    ],
    explanation: {
      title: 'The saved key does not choose this route.',
      paragraphs: [
        'The user must deliberately select Cloud Transcription. For each invocation, Mumble freezes the effective provider, model, key-presence state, language, and bounded vocabulary hints before the final provider adapter can run. A stored provider key on its own sends nothing.',
        'Local recovery segments remain available even when the online request fails. A Cloud failure produces a visible notice and can attempt on-device transcription if a local model can be loaded; it never switches to another online provider.',
      ],
    },
    evidence: {
      kind: 'controls',
      titleId: 'cloud-controls-title',
      title: 'Current route controls',
      description: 'These are processing choices in accepted source, not a promise that a provider account or credit is bundled.',
      items: [
        { term: 'Speech-to-text route', detail: 'Local or Cloud' },
        { term: 'Cloud provider', detail: 'Groq · OpenAI · OpenRouter' },
        { term: 'Provider model', detail: 'Selected per configured provider' },
        { term: 'Device-only override', detail: 'Keep every route on this device' },
      ],
    },
    tableCaption: 'Cloud Transcription data path',
    facts: {
      input: 'The recorded microphone-audio clip for the dictation whose effective route is Cloud.',
      localStage: 'Mumble captures durable local recovery segments and freezes one route snapshot before any provider adapter is allowed to run.',
      egress: 'A WAV representation of recorded audio plus the selected language and bounded vocabulary hints, when configured, is sent to the effective provider. Finished text shaping is not part of this request.',
      provider: 'The configured Groq, OpenAI, or OpenRouter transcription provider and selected model. Mumble does not switch providers during an invocation.',
      network: 'An internet connection is required for the provider request and response.',
      keyOrAccount: 'The user needs their own provider account and matching API key saved in Settings. Mumble supplies no built-in key.',
      externalCost: 'The selected provider may charge for transcription under its own plan. Mumble does not absorb that provider cost.',
      output: "The provider's transcript returns to Mumble, where it enters the local History/Deck and target-bound delivery path.",
      userControl: 'Deliberately select Cloud, choose the provider and model, save the matching key, or return to Local. The device-only override keeps the saved Cloud choice inactive.',
      failureBoundary: 'Device-only mode, a missing key/model, or an unsupported provider blocks egress. Network/provider failure is reported and may trigger a notified on-device attempt; there is no silent online fallback.',
    },
  },
  {
    panelId: 'text-shaping',
    tabId: 'privacy-tab-text-shaping',
    titleId: 'text-shaping-title',
    pathLabelId: 'text-shaping-path-label',
    label: 'Text Shaping',
    tabSummary: 'Optional · finished text, never captured audio',
    summary: 'Hosted shaping sends finished text through the effective configured route. It never sends the captured microphone audio used to make that text.',
    status: 'Optional · finished text may leave',
    pathLabel: 'Text Shaping data-path explanation · not an app screenshot',
    pathSteps: [
      { title: 'Finished text', detail: 'Transcript or selected Deck text' },
      { title: 'Local preparation', detail: 'Offline cleanup and mode inference' },
      { title: 'Effective provider', detail: 'Cerebras or OpenRouter' },
      { title: 'Shaped result', detail: 'Returned to local work and history' },
    ],
    explanation: {
      title: 'Speech-to-text and shaping are separate decisions.',
      paragraphs: [
        'Dictation first becomes finished text. Offline cleanup and mode inference run locally. Prompt, Email, Reply, Deck, Meetings, and Reader summary actions can then request hosted shaping through one frozen effective route.',
        'The accepted Windows provider surface supports Cerebras and OpenRouter for hosted text shaping. Provider/model readiness is confirmed against the saved credential before that provider can receive text.',
      ],
    },
    evidence: {
      kind: 'controls',
      titleId: 'shaping-controls-title',
      title: 'Current shaping controls',
      description: 'The local-first override and provider readiness are part of the route, not post-request recovery copy.',
      items: [
        { term: 'Plain dictation', detail: 'Instant local text by default' },
        { term: 'Hosted providers', detail: 'Cerebras · OpenRouter' },
        { term: 'Explicit actions', detail: 'Prompt · Email · Reply · Deck · Meetings · Reader summary' },
        { term: 'Local guard', detail: 'Device-only mode or hosted processing off' },
      ],
    },
    tableCaption: 'Text Shaping data path',
    facts: {
      input: 'Finished transcript or selected text plus the explicit task context for Prompt, Email, Reply, Deck, Meetings, or Reader summary.',
      localStage: 'Offline cleanup and mode inference run first. Mumble freezes the effective provider, model, credential state, preferences, and gathered context for the invocation.',
      egress: 'Finished text and the action context may be sent. This route never sends captured audio or a microphone recording.',
      provider: 'The effective configured Cerebras or OpenRouter provider on the accepted Windows surface, using the confirmed selected model.',
      network: 'An internet connection is required only when the effective shaping route is hosted.',
      keyOrAccount: 'The user needs their own provider account and matching API key. Mumble supplies no hosted-processing key.',
      externalCost: 'The selected provider may charge for text processing. Any provider tier or usage charge remains between the user and that provider.',
      output: 'Shaped text returns to the originating Mumble action and can be saved locally or delivered through the existing target-bound path.',
      userControl: 'Choose the action, provider and confirmed model; switch hosted processing off; or turn on the device-only override while preserving saved choices.',
      failureBoundary: 'Device-only mode, hosted processing off, a missing key/model, an unconfirmed model, or an unsupported provider blocks egress. Text stays local and Mumble does not try another online provider.',
    },
  },
  {
    panelId: 'reader-speech',
    tabId: 'privacy-tab-reader-speech',
    titleId: 'reader-speech-title',
    pathLabelId: 'reader-speech-path-label',
    label: 'Reader speech',
    tabSummary: 'Online required · passage text becomes speech',
    summary: "Reader voice playback uses online text-to-speech. It requires the user's selected provider, matching API key, network connection, and available provider credits; it is not local playback.",
    status: 'Online required · text becomes audio',
    pathLabel: 'Reader speech data-path explanation · not an app screenshot',
    pathSteps: [
      { title: 'Current passage', detail: 'Text from the opened document' },
      { title: 'Local reading state', detail: 'Chunks, position, bookmarks, and controls' },
      { title: 'TTS provider', detail: 'OpenRouter or OpenAI' },
      { title: 'Returned audio', detail: 'Played while progress stays local' },
    ],
    explanation: {
      title: 'The library is local; the voice route is not.',
      paragraphs: [
        'Opening, finding within, bookmarking, and tracking progress in a document are local Reader work. When the user starts speech or tests a voice, the current passage text or test phrase is sent to the frozen OpenRouter or OpenAI route with the selected model and voice.',
        "OpenRouter exposes a balance check inside Mumble. Both providers require the user's own key and may consume provider credit. The current product does not offer a system-voice or offline speech fallback.",
      ],
    },
    evidence: {
      kind: 'controls',
      titleId: 'reader-controls-title',
      title: 'Current Reader controls',
      description: 'Human choices lead; provider and model detail remains inspectable beneath them.',
      items: [
        { term: 'Provider', detail: 'OpenRouter · OpenAI' },
        { term: 'Voice setup', detail: 'Provider model · voice · speed' },
        { term: 'Playback', detail: 'Play · pause · seek · voice test' },
        { term: 'Provider balance', detail: 'OpenRouter credit check' },
      ],
    },
    tableCaption: 'Reader speech data path',
    facts: {
      input: 'The current text passage from an opened Reader document, or the short built-in phrase used to test a voice.',
      localStage: 'Mumble extracts and chunks document text locally; the Reader library, position, bookmarks, find state, provider choice, voice, and speed remain local settings.',
      egress: 'The passage text plus the selected speech model and voice request is sent for synthesis. The original document file and microphone audio are not sent by this route.',
      provider: 'The configured OpenRouter or OpenAI speech provider and selected model are frozen for this invocation. Mumble makes one synthesis attempt with that exact pair.',
      network: 'An internet connection is required for each text-to-speech request.',
      keyOrAccount: 'The user needs their own OpenRouter or OpenAI account and matching API key. Mumble supplies no speech-provider key.',
      externalCost: 'Speech consumes provider credits and the provider may charge under its own plan. Mumble does not include or absorb those credits.',
      output: 'Provider-generated audio returns to Mumble for playback while Reader progress and bookmarks continue to be stored locally.',
      userControl: 'Choose provider, model, voice, speed, play/pause, position, and voice test. Turning on device-only mode leaves Reader speech unavailable rather than presenting it as local.',
      failureBoundary: 'A missing/invalid key, device-only policy, unavailable provider/model, exhausted credits, network failure, or synthesis error stops speech after that one attempt. There is no local speech fallback, no sibling-model fallback, and no switch to another provider.',
    },
  },
  {
    panelId: 'mumble-find',
    tabId: 'privacy-tab-find',
    titleId: 'mumble-find-title',
    pathLabelId: 'mumble-find-path-label',
    label: 'Mumble Find',
    tabSummary: 'Local · typed queries search apps and files',
    summary: 'Find apps & files is a local launcher route. It does not add hosted matches, open a browser on no result, or share a query with Web Search.',
    status: 'Separate · local apps and files',
    pathLabel: 'Mumble Find data-path explanation · not an app screenshot',
    pathSteps: [
      { title: 'Typed query', detail: 'Entered in the Mumble Find overlay' },
      { title: 'Local indexes', detail: 'App catalogue and Windows Search SystemIndex' },
      { title: 'Ranked rows', detail: 'Up to 12 apps, files, and folders' },
      { title: 'Local action', detail: 'Open, Show in folder, or trusted drag' },
    ],
    explanation: {
      title: 'The empty state stays local too.',
      paragraphs: [
        "The accepted Windows app searches a versioned application catalogue and the operating system's Windows Search SystemIndex. Each search has a result limit and time window, and Mumble can cancel it when a newer search starts. Text appears before optional icons.",
        'Loading, no-match, partial, and failed-search states never substitute Web Search. Open, Show in folder, and native drag send only an opaque result identity back to Mumble, which checks the trusted local action.',
      ],
    },
    evidenceLayout: 'find',
    evidence: {
      kind: 'controls',
      titleId: 'find-controls-title',
      title: 'Current controls in accepted source',
      description: 'These labels and keys come from the Mumble Find surface, not from a decorative privacy illustration.',
      items: [
        { term: 'Open Mumble Find', detail: 'Ctrl + Alt + F' },
        { term: 'Search categories', detail: 'Everything · Apps · Files · Folders' },
        { term: 'Move selection', detail: 'Up / Down arrow keys' },
        { term: 'Open result', detail: 'Enter' },
        { term: 'Show in folder', detail: 'Ctrl + Enter' },
        { term: 'Clear or close', detail: 'Escape' },
      ],
    },
    tableCaption: 'Mumble Find data path',
    facts: {
      input: 'The words typed into the resident Mumble Find overlay.',
      localStage: "The accepted Windows source queries a versioned app catalogue and Windows Search SystemIndex through bounded local workers.",
      egress: 'Nothing leaves this computer. The query and local result list have no hosted results and no automatic Web Search fallback.',
      provider: "No external provider. Mumble's local application catalogue and the operating system's local search index supply results.",
      network: 'Not required for the local query or local result actions.',
      keyOrAccount: 'No Mumble account and no provider key are required.',
      externalCost: 'No external provider charge.',
      output: 'Up to 12 text-first rows for local apps, files, and folders, followed by checked local Open, Show in folder, or native drag actions.',
      userControl: 'Use the separate global shortcut, category filters, keyboard selection, Open, Show in folder, local-index refresh, and checked native drag.',
      failureBoundary: 'An unavailable or stale operating-system index can reduce file results while local apps remain usable. No-match, degraded, error, or timeout states remain local and never become Web Search.',
    },
  },
  {
    panelId: 'web-search',
    tabId: 'privacy-tab-web-search',
    titleId: 'web-search-title',
    pathLabelId: 'web-search-path-label',
    label: 'Web Search',
    tabSummary: 'Consent-gated · selected words go online',
    summary: 'Web Search is separate from Mumble Find. Selected or dictated words remain local until a provider-named confirmation and the user chooses Search online.',
    status: 'Consent-gated · selected words may leave',
    pathLabel: 'Web Search data-path explanation · not an app screenshot',
    pathSteps: [
      { title: 'Selected words', detail: 'Text chosen for Web Search' },
      { title: 'Bounded local request', detail: 'Query and provider freeze for one consent' },
      { title: 'Provider-named choice', detail: 'Keep private or Search online' },
      { title: 'External browser', detail: 'Open only after confirmed consent' },
    ],
    explanation: {
      title: 'Mumble Find cannot arrive here by accident.',
      paragraphs: [
        'The Web Search command freezes the selected Google, Perplexity, or Brave provider and query inside a bounded local request. The centred confirmation names that provider, shows the selected words, and offers Keep private or Search online.',
        'Only the matching one-shot request can open the external browser. Mumble reports “opened” only after the platform browser helper confirms success; a failed open consumes the consent and reports recovery guidance.',
      ],
    },
    evidence: {
      kind: 'controls',
      titleId: 'web-search-controls-title',
      title: 'Current consent controls',
      description: 'No result list is hosted inside Mumble and no Mumble Find failure enters this route.',
      items: [
        { term: 'Selected provider', detail: 'Google · Perplexity · Brave' },
        { term: 'Do not send', detail: 'Keep private' },
        { term: 'Allow this request', detail: 'Search online' },
        { term: 'Request lifetime', detail: 'Bounded · expiring · one use' },
      ],
    },
    tableCaption: 'Web Search data path',
    facts: {
      input: 'The words explicitly selected or dictated for the separate Web Search command.',
      localStage: 'Mumble keeps the query and selected provider in one bounded, expiring local request until the matching consent decision returns.',
      egress: "Only after Search online is chosen, the selected words are encoded into the external provider's search URL and handed to the configured browser.",
      provider: 'The selected Google, Perplexity, or Brave Web Search destination, named in the confirmation before egress.',
      network: 'An internet connection and a working configured or default browser are required after consent.',
      keyOrAccount: 'Mumble configures no API key for this route. The destination may apply its own account, access, or service terms.',
      externalCost: 'Mumble charges nothing for the action and supplies no provider access. Connectivity or destination-provider charges, if any, remain external.',
      output: 'A confirmed external browser destination, not hosted results inside Mumble. Success is reported only when the browser opener confirms it opened.',
      userControl: 'Choose the provider in Settings, invoke the separate Web Search command, review the provider-named confirmation, then choose Keep private or Search online.',
      failureBoundary: 'Blank input, Keep private, expiry, replay, missing consent surface, or browser-open failure makes no successful external-open claim. Mumble Find never falls back to this route.',
    },
  },
] satisfies readonly PrivacyRoute[];
