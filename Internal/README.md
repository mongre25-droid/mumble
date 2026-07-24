# Mumble

**Speak. It types. Anywhere.**

> **This is the user-facing product guide.** For development documentation —
> architecture, coding standards, session workflow, known bugs, and the build
> pipeline — see the Core docs at **[`Development Files/Core/`](Development%20Files/Core/README.html)**
> (start with the [README](Development%20Files/Core/README.html) and the [INDEX](Development%20Files/Core/INDEX.html)).

Mumble is a private, on-device voice-to-text app for Windows. Tap a hotkey,
talk, tap again — your words are cleaned up and pasted wherever your cursor is.
It runs quietly in the background, starts with Windows, and lives behind a
system-tray icon with a full app window.

**Local transcription is the private default.** With Local selected, speech is
transcribed on-device with faster-whisper and audio is not uploaded. The optional
Cloud transcription setting sends each audio clip to the provider you choose.
Optional AI polish (Pro Mode) sends transcribed text — not audio — to your chosen
AI provider. The Home privacy badge always reflects the active transcription mode.

---

## Install

Double-click **`Install Mumble.bat`** (or, from the release zip, just
**`Mumble`** — first run installs automatically). It sets up everything
(Python, a private environment, a real branded `Mumble.exe`, Start-menu and
run-at-login shortcuts), pre-downloads the speech model, and launches Mumble.
The first launch opens a welcome tour.

That's it — from then on Mumble starts automatically with Windows and is always
a hotkey away.

## Everyday use

1. Click into any text field — a browser, editor, chat box, anywhere.
2. Tap **Ctrl + Win**. A small gold island appears at the bottom of your screen.
3. Speak.
4. Tap **Ctrl + Win** again. Mumble transcribes locally and pastes at your cursor.

Normal dictation is capped at **10 minutes per recording**. Mumble stops and
transcribes automatically at that point, so an accidental long press cannot
exhaust memory or exceed the supported cloud providers' direct-upload limit.

Open the app any time from the **tray icon**, the Desktop shortcut, or the
Start menu.

## Smart Modes — one explicit Prompt toggle

Ordinary dictation always produces clean text. While listening, turn **Prompt** on
from the island when you want a rough idea shaped into a structured AI prompt. Email, Reply,
Foreign and other transformations are actions in the **Deck**, where you select
the source material and action deliberately. There is no held Right Shift mode
key and no spoken mode-name workflow in the current product.

## The Deck — Ctrl + Alt + D

Your mini dashboard of everything you've made and copied: transcripts,
clipboard history and saved prompts in one searchable list. Click an item to
paste it at your cursor, ★ favourite anything to keep it forever, or pick an
AI preset (17 built-in + 3 custom = 20) and/or a Smart Mode, tick some items, and
**Go** — the result pastes where you were typing.

**Pinned = a floating palette.** When pinned (the default), the Deck stays on top
of your other apps and, while you're on the Deck page, becomes *non-activating*:
clicking it never steals focus, so it never "takes over" when you click a text
field elsewhere — and whatever you have **highlighted** in another app stays live.
Press **Capture** (in the Deck header) to pull that highlighted text straight into
the Deck as a "Captured selection" you can convert, tick, or run a preset on. (To
type in the Deck's own search box, un-pin it first.)

## Formatting — the AI's job, not voice commands

Spoken formatting commands were removed (they triggered accidentally — saying
"new line" now just types the words). Structure — paragraphs, bullets and
numbered lists — is shaped deliberately with a Deck action or preset.

## The app window

A black-and-gold liquid-glass window (resizable; opens as a tall column and
returns to that default on every launch):

- **Home** — dictation controls, one above-the-fold hub for every global
  shortcut, and a guided path from first word to finished work
- **History** — transcripts / clipboard / prompts / ★ favourites (which keep
  their original mode + colour), grouped by Today / Yesterday / Older, with
  search, filters, sort, **word-count · duration · words-per-minute**, locally
  derived audio-quality chips, and a Pre-AI ⇄ Post-AI toggle; updates live
- **Stats** — real numbers only: words, streaks, trends (green when growing),
  a 13-week activity heatmap (with a plain-language legend), Time-saved (marked
  *estimated*), and your Smart-Mode mix
- **Reader** — open and read documents in-app: PDF, DOCX, HTML, EPUB, RTF,
  Markdown, TXT, and now CSV, ODT, PPTX and XLSX; tables render as proper HTML
  tables, library entries show a format badge, and in-document find supports
  Ctrl+F, Enter / Shift+Enter navigation, ARIA labels and managed focus
- **Meetings** — record, transcribe and diarise meetings, then Summarise,
  extract and export, with instant library and transcript search (see
  *Meeting Mode* below)
- **Search (Experimental; Windows + Linux only)** — a keyboard-first local
  launcher for installed applications, files and folders, styled in Mumble's
  gold-on-black glass language and using the operating system's native app icons;
  macOS does not show this view because the platform already provides Spotlight/Finder search
- **Settings** — name, shortcuts, Smart Modes, **Foreign-mode languages**, AI
  providers (Cerebras / OpenAI / Anthropic / DeepSeek / Groq / local), microphone,
  transcription model (**Small by default**) & language, history limits, visual
  effects (Basic / Standard / Enhanced), and updates

## Reader Mode

Reader accepts source files up to **32 MiB** and stores at most **600,000
characters of readable text** per document. That is roughly 100,000 words, or
about **11 hours of narration at 150 words per minute** (before applying the
0.75x-3x playback-speed control). Files over either limit are rejected with a
clear message rather than silently truncated.

To prevent malformed or compressed files from exhausting memory, Reader also
caps PDFs at **5,000 pages** and 4,194,304 extracted characters during parsing.
ZIP-based formats such as EPUB, DOCX, ODT, PPTX and XLSX may contain at most
**20,000 archive members** and **256 MiB expanded data**. Scanned PDFs require
OCR before import; Reader does not currently perform OCR itself.

## Meeting Mode

Turn Mumble into a meeting recorder (up to **4 hours** per recording or import):

1. Open the app and go to the **Meetings** view.
2. Click **Record** to capture room audio from the selected microphone, or import
   WAV, MP3, FLAC, or OGG audio from an online call. **Pause** / **Resume** any time.
3. Click **Stop**. Mumble saves the WAV first, then transcribes it in bounded
   overlapping 10-minute chunks using the selected Local or Cloud transcription
   mode and applies best-effort speaker labels. At four hours it stops and saves
   automatically.
4. With the transcript ready, pick what you need:
   - **Summarise** — an AI summary of the meeting
   - **Extract** — action items, key decisions and open questions
   - **Export** — TXT, Markdown, JSON, HTML, or copy to clipboard
   - **Search** — filter saved meetings by title/preview and find matching
     transcript segments with highlighted terms
5. Meetings are saved to your `%APPDATA%\Mumble` store and listed in the
   Meetings view for replay and re-export.

Processing has two modes: **lightweight** (fast, summary-only) and **deep**
(summary + action items + key decisions + open questions in one pass). As
everywhere else in Mumble, Local transcription keeps audio on your machine;
optional Cloud transcription sends bounded audio chunks to the provider chosen
in Settings. Only transcribed *text* is sent to the configured LLM provider when
you Summarise or Extract.

## Your data

Everything stays on your machine in `%APPDATA%\Mumble` on Windows or the
Mumble XDG data directory on Linux:

- `settings.json` — your preferences
- `history.json` / `transcripts.txt` — your dictations (app view + readable log)
- `clipboard.json`, `prompts.json`, `favorites.json`, `stats.json` — the Deck,
  prompt history, favourites and statistics
- `system_search_index.json` / `system_search_state.json` — the bounded local
  app/file index plus favourites, recent opens and ranking counts

## Mumble Search (Experimental; Windows + Linux only)

On Windows, press the configurable **Ctrl + Alt + F** default or open **Search**
in Mumble. Start typing to find
installed applications, documents, downloads and folders. Use Up/Down to move,
Enter to open, Ctrl+Enter to reveal an indexed item in its folder, and Esc to
clear or close the launcher. Results can be filtered to Apps, Files or Folders;
`app:`, `file:` and `folder:` prefixes work from the keyboard too.

The index is deliberately bounded (75,000 items by default) and scans only
standard user folders or roots configured by the user. Hidden folders, caches,
virtual environments and dependency trees are skipped. Searches, paths,
favourites and usage ranking stay on this computer. A selected web provider is
offered only as a low-ranked fallback after local matches.

Search has a narrow authority boundary: the browser sends an opaque result ID,
and Python opens, reveals, copies or favourites only an item already present in
its own index. Search text is never evaluated as a shell command or arbitrary
path. Linux application launches use desktop entries without a shell; Windows
uses Start Menu entries, registered App Paths and Start apps.

Application marks come from the operating system rather than generated letters:
Windows asks Explorer for the exact shell icon (including virtual Start apps such
as Settings), while Linux resolves the local desktop-entry icon through installed
icon themes. The bounded icon cache is local and never downloads artwork.

The implementation is in `app/experimental/system_search/` and is mirrored in
the Linux port. macOS intentionally ships neither the loader nor the feature
bridge, so Search never appears there.


## Privacy

In the default **Local transcription** mode, audio is processed on this PC and
discarded; only resulting text is saved to your own history. If you explicitly
select **Cloud transcription**, each activated dictation is sent to that
configured speech provider. Separately, Pro Mode can send transcribed *text* to your configured
AI provider for cleanup. Mumble adds no telemetry or account requirement.

## Uninstall

Run **`Uninstall Mumble.bat`** (removes the shortcuts and stops Mumble), then
delete this folder. Your transcripts in `%APPDATA%\Mumble` are left untouched —
delete that folder too if you want them gone.

## Troubleshooting

- **Can't paste into an app run as Administrator** — run Mumble as admin too;
  Windows blocks keystroke injection into higher-privilege windows.
- **Start menu pops when toggling** — press `Ctrl` a hair before `Win`, or pick a
  different hotkey in Settings.
- **First dictation used to feel slow** — fixed: the model now warms up at
  start, so the first press runs at full speed.
- **No sound bars while speaking** — pick the right device in Settings →
  Microphone and use **Test microphone**.
- **Logs** — `%APPDATA%\Mumble\mumble.log`.

---

## For developers

Pure-Python, no build step. Run from source with **`Start Mumble (console).bat`**
to see live logs.

| File | Responsibility |
| --- | --- |
| `mumble.py` | App controller — audio, transcription, modes, tray, hotkeys, command server |
| `webui_shell.py` | The main window: pywebview shell + the JS↔Python Api bridge |
| `webui/` | The window itself — `index.html`, `app.css`, `app.js` (zero-CDN SPA) |
| `overlay.py` | The island controller + tkinter fallback (+ the no-WebView2 fallback Deck) |
| `island_shell.py` / `webui/island.html` | The floating island — the UX-Pilot HTML pill |
| `formatting.py` | Offline text engine: cleanup, vocabulary, Smart-Mode detection |
| `ai.py` | Cloud lanes: polish, prompt, email, list, reply, foreign, convert, intents |
| `settings.py` | Persisted settings (`%APPDATA%\Mumble\settings.json`) |
| `history.py` / `clipboard.py` / `favorites.py` / `stats.py` / `prompt_memory.py` | The stores |
| `meeting.py` / `meeting_diarise.py` / `meeting_store.py` | Meeting Mode — record / pause / resume / stop, speaker diarisation, AI summary & action-item / key-decision / open-question extraction, export (TXT / Markdown / JSON / HTML / clipboard), and the meeting store |
| `presets.py` | The Deck's 17 built-in + 3 custom AI presets (20 total) |
| `bindings.py` | Keyboard + mouse bindings (capture, validate, register) |
| `autostart.py` | Start-menu & run-at-login shortcuts (+ legacy Run-key cleanup) |
| `brand_exe.py` | Builds the branded `Mumble.exe` (base interpreter + icon/version resources) |
| `branding.py` | Names, paths, versions, and the design tokens |
| `install.ps1` / `Install Mumble.bat` | The installer |

Run the tests (11 suites):

```
.\.venv\Scripts\python.exe test_formatting.py
.\.venv\Scripts\python.exe test_mode_select.py
.\.venv\Scripts\python.exe test_bindings.py
.\.venv\Scripts\python.exe test_presets.py
.\.venv\Scripts\python.exe test_favorites.py
.\.venv\Scripts\python.exe test_stream_seam.py
.\.venv\Scripts\python.exe test_webui_api.py
.\.venv\Scripts\python.exe test_ui.py
.\.venv\Scripts\python.exe test_meeting.py
.\.venv\Scripts\python.exe test_meeting_controller.py
.\.venv\Scripts\python.exe test_reader_parser.py
```

Built with faster-whisper, sounddevice, pystray, Pillow, keyboard, mouse,
pywebview, and tkinter. Everything runs on your machine.

## Licensing

Mumble is released under the **MIT License** — see `LICENSE` at the repo root.
It builds on open-source libraries; full attributions and per-dependency
license notes (including the PyMuPDF AGPL-3.0 compliance analysis) are in
**§13 Licensing & Open-Source Attributions** of the master system
documentation (`Development Files\Core\MUMBLE_MASTER_SYSTEM_DOCUMENTATION.md`).
Runtime notices and exact licence texts ship in `app/THIRD_PARTY_NOTICES.md`
and `app/licenses/`. Mumble Search adds no third-party runtime dependency and
does not copy source from the permissively licensed launchers researched for it.
