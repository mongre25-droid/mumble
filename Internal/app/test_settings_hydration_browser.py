"""Real Edge/Chromium checks for Settings first-frame and hydration races."""

import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

import pytest


APP_DIR = Path(__file__).resolve().parent
INDEX_URI = (APP_DIR / "webui" / "index.html").as_uri()
PLATFORM_APP_DIRS = (
    APP_DIR,
    APP_DIR / "Ports" / "macOS" / "app",
    APP_DIR / "Ports" / "Linux" / "app",
)
EDGE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


def test_settings_hydration_and_microphone_error_contract_exists_on_every_platform():
    for platform_app in PLATFORM_APP_DIRS:
        html = (platform_app / "webui" / "index.html").read_text(encoding="utf-8")
        js = (platform_app / "webui" / "app.js").read_text(encoding="utf-8")
        css_path = platform_app / "webui" / "remaster.css"
        if not css_path.exists():
            css_path = platform_app / "webui" / "app.css"
        css = css_path.read_text(encoding="utf-8")

        assert 'data-settings-state="loading" aria-busy="true"' in html
        assert 'id="settings-hydration"' in html
        assert '<option value="lite">Light effects</option>' in html
        assert '<option value="standard">Standard effects</option>' in html
        assert '<option value="enhanced">Full effects</option>' in html
        assert '<option value="lite">Basic</option>' not in html
        assert "SETTINGS_HYDRATION_VERSION" in js
        assert 'mics = await call("list_microphones")' in js
        assert 'if (requestId !== SETTINGS_HYDRATION_VERSION) return false;' in js
        assert '"Microphones could not be checked.' in js
        assert 'setSettingsHydrationState("ready")' in js
        assert '[data-settings-state="ready"] .settings-hydration' in css


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_debugger(port):
    url = f"http://127.0.0.1:{port}/json/list"
    for _ in range(80):
        try:
            with urllib.request.urlopen(url, timeout=0.2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.05)
    raise AssertionError("Edge debugging endpoint did not start")


def test_settings_delayed_rejected_and_stale_hydration_are_truthful():
    edge = next((str(path) for path in EDGE_CANDIDATES if path.exists()), None)
    node = shutil.which("node")
    if not edge or not node:
        pytest.skip("Edge and Node.js are required for the real-browser check")

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="mumble-settings-browser-")
    browser = subprocess.Popen(
        [
            edge,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--disable-gpu",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_debugger(port)
        script = r"""
const port = Number(process.argv[1]);
const targetUrl = process.argv[2];
const pages = await fetch(`http://127.0.0.1:${port}/json/list`).then(r => r.json());
const page = pages.find(item => item.type === 'page' && item.url === 'about:blank') || pages.find(item => item.type === 'page');
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let nextId = 0;
const pending = new Map();
ws.onmessage = event => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) {
    const {resolve, reject} = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error)));
    else resolve(message.result);
  }
};
function send(method, params={}) {
  const id = ++nextId;
  ws.send(JSON.stringify({id, method, params}));
  return new Promise((resolve, reject) => pending.set(id, {resolve, reject}));
}
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', {expression, awaitPromise:true, returnByValue:true});
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
await send('Page.enable');
await send('Runtime.enable');
await send('Page.addScriptToEvaluateOnNewDocument', {source: `
  window.__settingsRequests = [];
  window.__micRequests = [];
  const fallback = async (name) => {
    if (name === 'pretty_binding') return {pretty:'Ctrl + Win'};
    if (name === 'local_llm_status') return {enabled:false, ready:false};
    if (name === 'account_status') return {authenticated:false};
    return {};
  };
  window.pywebview = {api: new Proxy({}, {get(_target, name) {
    if (name === 'get_settings') return () => new Promise((resolve, reject) => {
      window.__settingsRequests.push({resolve, reject});
    });
    if (name === 'list_microphones') return () => new Promise((resolve, reject) => {
      window.__micRequests.push({resolve, reject});
    });
    return (..._args) => fallback(String(name));
  }})};
`});
await send('Page.navigate', {url: targetUrl});
let appReady = false;
for (let i=0; i<100; i++) {
  if (await evaluate(`typeof navTo === 'function'`)) { appReady = true; break; }
  await new Promise(r => setTimeout(r, 25));
}
if (!appReady) {
  const diagnostic = await evaluate(`({url:location.href, title:document.title, scripts:Array.from(document.scripts).map(s=>s.src), body:document.body?.innerText?.slice(0,80)})`);
  throw new Error('Mumble app.js did not load: '+JSON.stringify(diagnostic));
}
const base = {
  ui_effects:'enhanced', resource_saver:false, pro_mode:true,
  local_only_mode:false, instant_text:true, llm_provider:'cerebras',
  cerebras_api_key:'', transcription_mode:'local',
  cloud_transcription_provider:'groq', vocabulary_terms:[], vocabulary:{},
  mic_device:3,
  foreign_languages:[], prompt_prefs:{}, _route_state:{
    transcription:{effective:'local',reason:'selected',provider:'groq'},
    plain_processing:{effective:'local',reason:'instant_text'},
    action_processing:{effective:'local',reason:'no_key',provider:'cerebras',provider_supported:true,has_key:false}
  }
};
await evaluate(`navTo('settings'); true`);
const delayed = await evaluate(`(() => { const v=document.querySelector('[data-view="settings"]'); const s=document.querySelector('[data-setting="ui_effects"]'); return {state:v.dataset.settingsState,busy:v.getAttribute('aria-busy'),disabled:s.disabled,visibility:getComputedStyle(s).visibility,value:s.value}; })()`);
if (delayed.state !== 'loading' || delayed.busy !== 'true' || !delayed.disabled || delayed.visibility !== 'hidden') throw new Error('false first frame: '+JSON.stringify(delayed));
await evaluate(`window.__settingsRequests[0].resolve(${JSON.stringify(base)}); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`window.__micRequests.length === 1`)) break;
  await new Promise(r => setTimeout(r, 25));
}
await evaluate(`window.__micRequests[0].reject(new Error('microphone bridge rejected')); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`document.querySelector('[data-view="settings"]').dataset.settingsState === 'error'`)) break;
  await new Promise(r => setTimeout(r, 25));
}
const micFailed = await evaluate(`(() => { const v=document.querySelector('[data-view="settings"]'); const s=document.querySelector('[data-setting="ui_effects"]'); return {state:v.dataset.settingsState,busy:v.getAttribute('aria-busy'),disabled:s.disabled,visibility:getComputedStyle(s).visibility}; })()`);
if (micFailed.state !== 'error' || micFailed.busy !== 'false' || !micFailed.disabled || micFailed.visibility !== 'hidden') throw new Error('microphone rejection stayed loading: '+JSON.stringify(micFailed));

await evaluate(`hydrateSettings(); true`);
await evaluate(`window.__settingsRequests[1].resolve(${JSON.stringify(base)}); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`window.__micRequests.length === 2`)) break;
  await new Promise(r => setTimeout(r, 25));
}
await evaluate(`window.__micRequests[1].resolve([{index:1,name:'Recovered microphone'}]); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`document.querySelector('[data-view="settings"]').dataset.settingsState === 'ready'`)) break;
  await new Promise(r => setTimeout(r, 25));
}
const ready = await evaluate(`(() => { const v=document.querySelector('[data-view="settings"]'); const s=document.querySelector('[data-setting="ui_effects"]'); return {state:v.dataset.settingsState,disabled:s.disabled,visibility:getComputedStyle(s).visibility,value:s.value}; })()`);
if (ready.state !== 'ready' || ready.disabled || ready.visibility === 'hidden' || ready.value !== 'enhanced') throw new Error('bad ready state: '+JSON.stringify(ready));

await evaluate(`hydrateSettings(); true`);
await evaluate(`window.__settingsRequests[2].resolve(${JSON.stringify(base)}); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`window.__micRequests.length === 3`)) break;
  await new Promise(r => setTimeout(r, 25));
}
await evaluate(`hydrateSettings(); true`);
await evaluate(`window.__settingsRequests[3].resolve(${JSON.stringify(base)}); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`window.__micRequests.length === 4`)) break;
  await new Promise(r => setTimeout(r, 25));
}
await evaluate(`window.__micRequests[3].resolve([{index:3,name:'Newest microphone'}]); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`document.querySelector('[data-view="settings"]').dataset.settingsState === 'ready'`)) break;
  await new Promise(r => setTimeout(r, 25));
}
await evaluate(`window.__micRequests[2].resolve([{index:2,name:'Stale microphone'}]); true`);
await new Promise(r => setTimeout(r, 100));
const race = await evaluate(`(() => { const s=document.querySelector('#set-mic'); return {value:s.value,labels:Array.from(s.options).map(o=>o.textContent)}; })()`);
if (race.value !== '3' || race.labels.join('|') !== 'Newest microphone') throw new Error('stale microphone response won: '+JSON.stringify(race));

await evaluate(`hydrateSettings(); true`);
await evaluate(`window.__settingsRequests[4].reject(new Error('bridge rejected')); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`document.querySelector('[data-view="settings"]').dataset.settingsState === 'error'`)) break;
  await new Promise(r => setTimeout(r, 25));
}
const failed = await evaluate(`(() => { const v=document.querySelector('[data-view="settings"]'); const s=document.querySelector('[data-setting="ui_effects"]'); return {state:v.dataset.settingsState,disabled:s.disabled,visibility:getComputedStyle(s).visibility}; })()`);
if (failed.state !== 'error' || !failed.disabled || failed.visibility !== 'hidden') throw new Error('bad error state: '+JSON.stringify(failed));
console.log(JSON.stringify({delayed, micFailed, ready, race, failed}));
ws.close();
"""
        result = subprocess.run(
            [node, "--input-type=module", "-e", script, str(port), INDEX_URI],
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        evidence = json.loads(result.stdout.strip().splitlines()[-1])
        assert evidence["delayed"]["state"] == "loading"
        assert evidence["micFailed"]["state"] == "error"
        assert evidence["ready"]["value"] == "enhanced"
        assert evidence["race"]["labels"] == ["Newest microphone"]
        assert evidence["failed"]["state"] == "error"
    finally:
        browser.terminate()
        try:
            browser.wait(timeout=5)
        except subprocess.TimeoutExpired:
            browser.kill()
        shutil.rmtree(profile, ignore_errors=True)
