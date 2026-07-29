"""Real Edge/Chromium checks for Settings first-frame and hydration races."""

import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
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
        assert '<option value="lite">Light</option>' in html
        assert '<option value="standard">Standard</option>' in html
        assert '<option value="enhanced">Full effects</option>' in html
        assert '<option value="lite">Basic</option>' not in html
        assert "Normal dictation records for up to 10 minutes" not in html
        if platform_app != APP_DIR:
            assert "10 minutes as a temporary safety guard" in html
            assert "bounded, recovery-safe segments" not in html
        assert "home-capabilities-grid" not in html
        assert 'class="home-latest-result"' in html
        tab_labels = [
            "Overview", "Speech to text", "Text shaping", "Deck &amp; data", "System"
        ]
        assert all(f'>{label}</button>' in html for label in tab_labels)
        required_route_facts = (
            "Saved choice", "Effective route", "Why this route",
            "Input and engine", "Location", "What leaves this device",
            "Speed", "Privacy", "Quality boundary", "Cost",
        )
        for route_id in ("transcription-route-facts", "processing-route-facts"):
            match = re.search(
                rf'<dl class="processing-route-facts" id="{route_id}">(.*?)</dl>',
                html,
                re.DOTALL,
            )
            assert match
            assert re.findall(r"<dt>(.*?)</dt>", match.group(1)) == list(
                required_route_facts
            )
        assert 'id="feature-route-rows"' in html
        assert "FEATURE_ROUTE_ROWS" in js
        assert "renderFeatureRouteLedger" in js
        if platform_app != APP_DIR:
            assert 'setText("#route-fact-' not in js
            assert js.count('writeRouteFacts("route"') == 1
            assert "route-fact-tradeoff" not in js
        assert "async function reflectProvider" in js
        assert 'call("activate_model_provider", provider)' in js
        finish_source = js[js.index("async function finishOnboarding"):]
        finish_source = finish_source.split(
            "/* ============================================================================", 1
        )[0]
        assert 'set_setting", "llm_provider"' not in finish_source
        assert 'llm_provider: provider' not in finish_source
        shell = (platform_app / "webui_shell.py").read_text(encoding="utf-8")
        assert "self._confirmed_models" not in shell
        assert "model_authority.ModelDiscoveryAuthority" in shell
        authority = (platform_app / "model_authority.py").read_text(encoding="utf-8")
        assert 'AUTHORITY_KEY = "_confirmed_text_models"' in authority
        cloud_sync = (platform_app / "cloud_sync.py").read_text(encoding="utf-8")
        assert '"_confirmed_text_models"' in cloud_sync
        if platform_app.name == "app" and platform_app.parent.name == "macOS":
            controller = (platform_app / "mumble_mac.py").read_text(encoding="utf-8")
            process = controller[controller.index("    def _process("):]
            process = process[:process.index("    def ", 5)]
            assert "capture_text_provider_settings(self.settings)" in process
        else:
            controller_name = "mumble_linux.py" if platform_app.parent.name == "Linux" else "mumble.py"
            controller = (platform_app / controller_name).read_text(encoding="utf-8")
        assert 'const cloudStt = transcriptionRoute.effective === "cloud"' in js
        assert "Cloud choice remains saved" in js
        assert "ROUTE_REFRESH_VERSION" in js
        for model_key in (
            "cerebras_model", "openrouter_model", "groq_transcription_model",
            "openai_transcription_model", "openrouter_transcription_model",
        ):
            assert model_key in js
        assert "SETTINGS_HYDRATION_VERSION" in js
        assert 'mics = await call("list_microphones")' in js
        assert 'if (requestId !== SETTINGS_HYDRATION_VERSION) return false;' in js
        assert '"Microphones could not be checked.' in js
        assert 'setSettingsHydrationState("ready")' in js
        assert '[data-settings-state="ready"] .settings-hydration' in css


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
def test_model_authority_reload_acknowledgement_is_synchronous(platform_app):
    script = r'''
import json
import sys
sys.path.insert(0, sys.argv[1])
import model_authority

events = []
def apply_change(key):
    events.append(("apply", key))
    return key == "_confirmed_text_models"

def start_background(action):
    events.append(("background", None))
    action()

authority = model_authority.controller_reload_response(
    apply_change, "_confirmed_text_models", start_background
)
provider = model_authority.controller_reload_response(
    apply_change, "llm_provider", start_background
)
ordinary = model_authority.controller_reload_response(
    apply_change, "language", start_background
)
print(json.dumps({"authority": authority, "provider": provider,
                  "ordinary": ordinary, "events": events}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app)],
        capture_output=True,
        text=True,
        timeout=20,
        cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["authority"] == {"ok": True}
    assert evidence["provider"] == {
        "ok": False, "message": "Settings reload failed."
    }
    assert evidence["ordinary"] == {"ok": True}
    assert evidence["events"][:2] == [
        ["apply", "_confirmed_text_models"], ["apply", "llm_provider"]
    ]
    assert evidence["events"][2] == ["background", None]


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


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
def test_settings_delayed_rejected_and_stale_hydration_are_truthful(platform_app):
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
  window.__bridgeCalls = [];
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
    return (..._args) => {
      window.__bridgeCalls.push(String(name));
      return fallback(String(name));
    };
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
let expectedMicRequests = await evaluate(`window.__micRequests.length`);
await evaluate(`navTo('settings'); true`);
const delayed = await evaluate(`(() => { const v=document.querySelector('[data-view="settings"]'); const s=document.querySelector('[data-setting="ui_effects"]'); return {state:v.dataset.settingsState,busy:v.getAttribute('aria-busy'),disabled:s.disabled,visibility:getComputedStyle(s).visibility,value:s.value}; })()`);
if (delayed.state !== 'loading' || delayed.busy !== 'true' || !delayed.disabled || delayed.visibility !== 'hidden') throw new Error('false first frame: '+JSON.stringify(delayed));
const loadingActions = await evaluate(`(() => {
  const view=document.querySelector('[data-view="settings"]');
  const nodes=Array.from(view.querySelectorAll('button,input,select,textarea,a[href],[role="button"],[tabindex]'));
  const retry=document.querySelector('#settings-hydration-retry');
  const actionable=nodes.filter(el => el !== retry);
  const bad=actionable.filter(el => !el.disabled && el.getAttribute('aria-disabled') !== 'true' && el.tabIndex !== -1);
  const labels=bad.map(el => el.id || el.getAttribute('data-setting') || el.getAttribute('data-settings-tab') || el.textContent.trim().slice(0,40));
  const before=window.__bridgeCalls.length;
  actionable.forEach(el => el.click());
  return {count:actionable.length,bad:labels,bridgeDelta:window.__bridgeCalls.length-before};
})()`);
if (loadingActions.count < 40 || loadingActions.bad.length || loadingActions.bridgeDelta) throw new Error('loading actions were usable: '+JSON.stringify(loadingActions));
await evaluate(`window.__settingsRequests.at(-1).resolve(${JSON.stringify(base)}); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`window.__micRequests.length > ${expectedMicRequests}`)) break;
  await new Promise(r => setTimeout(r, 25));
}
expectedMicRequests = await evaluate(`window.__micRequests.length`);
await evaluate(`window.__micRequests.at(-1).reject(new Error('microphone bridge rejected')); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`document.querySelector('[data-view="settings"]').dataset.settingsState === 'error'`)) break;
  await new Promise(r => setTimeout(r, 25));
}
const micFailed = await evaluate(`(() => { const v=document.querySelector('[data-view="settings"]'); const s=document.querySelector('[data-setting="ui_effects"]'); return {state:v.dataset.settingsState,busy:v.getAttribute('aria-busy'),disabled:s.disabled,visibility:getComputedStyle(s).visibility}; })()`);
if (micFailed.state !== 'error' || micFailed.busy !== 'false' || !micFailed.disabled || micFailed.visibility !== 'hidden') throw new Error('microphone rejection stayed loading: '+JSON.stringify(micFailed));
const errorActions = await evaluate(`(() => {
  const view=document.querySelector('[data-view="settings"]');
  const retry=document.querySelector('#settings-hydration-retry');
  const nodes=Array.from(view.querySelectorAll('button,input,select,textarea,a[href],[role="button"],[tabindex]'));
  const actionable=nodes.filter(el => el !== retry);
  const bad=actionable.filter(el => !el.disabled && el.getAttribute('aria-disabled') !== 'true' && el.tabIndex !== -1);
  return {count:actionable.length,bad:bad.map(el => el.id || el.getAttribute('data-setting') || el.textContent.trim().slice(0,40)),retryUsable:!retry.disabled && retry.getAttribute('aria-disabled') !== 'true'};
})()`);
if (errorActions.count < 40 || errorActions.bad.length || !errorActions.retryUsable) throw new Error('error actions were usable or retry was blocked: '+JSON.stringify(errorActions));

await evaluate(`hydrateSettings(); true`);
await evaluate(`window.__settingsRequests.at(-1).resolve(${JSON.stringify(base)}); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`window.__micRequests.length > ${expectedMicRequests}`)) break;
  await new Promise(r => setTimeout(r, 25));
}
expectedMicRequests = await evaluate(`window.__micRequests.length`);
await evaluate(`window.__micRequests.at(-1).resolve([{index:1,name:'Recovered microphone'}]); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`document.querySelector('[data-view="settings"]').dataset.settingsState === 'ready'`)) break;
  await new Promise(r => setTimeout(r, 25));
}
const ready = await evaluate(`(() => { const v=document.querySelector('[data-view="settings"]'); const s=document.querySelector('[data-setting="ui_effects"]'); return {state:v.dataset.settingsState,disabled:s.disabled,visibility:getComputedStyle(s).visibility,value:s.value}; })()`);
if (ready.state !== 'ready' || ready.disabled || ready.visibility === 'hidden' || ready.value !== 'enhanced') throw new Error('bad ready state: '+JSON.stringify(ready));
const routeDisclosure = await evaluate(`(() => ({
  transcription:Array.from(document.querySelectorAll('#transcription-route-facts dt')).map(node=>node.textContent.trim()),
  processing:Array.from(document.querySelectorAll('#processing-route-facts dt')).map(node=>node.textContent.trim()),
  features:Array.from(document.querySelectorAll('[data-route-feature]')).map(row=>({
    id:row.dataset.routeFeature,
    facts:Array.from(row.querySelectorAll('dt')).map(node=>node.textContent.trim()),
  })),
}))()`);
const requiredFacts = ['Saved choice','Effective route','Why this route','Input and engine','Location','What leaves this device','Speed','Privacy','Quality boundary','Cost'];
const requiredFeatureFacts = ['Saved','Effective','Reason','Input and engine','Location','What leaves this device','Speed','Privacy','Quality boundary','Cost'];
if (routeDisclosure.transcription.join('|') !== requiredFacts.join('|') || routeDisclosure.processing.join('|') !== requiredFacts.join('|')) throw new Error('incomplete route facts: '+JSON.stringify(routeDisclosure));
if (routeDisclosure.features.length !== 7 || routeDisclosure.features.some(row=>row.facts.join('|') !== requiredFeatureFacts.join('|'))) throw new Error('incomplete feature decisions: '+JSON.stringify(routeDisclosure));

const discoveryOrder = await evaluate(`(async () => {
  const originalBridge = window.pywebview;
  const order = [];
  let releaseDiscovery;
  const discovery = new Promise(resolve => { releaseDiscovery = resolve; });
  window.pywebview = {api:{
    set_setting: async () => { order.push('saved'); return {ok:true}; },
    list_models: async () => { order.push('discovery-start'); const result = await discovery; order.push('discovery-done'); return result; },
    get_settings: async () => { order.push('route-refresh'); return {_route_state:SET._route_state}; },
  }};
  const key = document.querySelector('[data-setting="cerebras_api_key"]');
  key.dataset.masked = '';
  key.value = 'ordered-test-key';
  key.dispatchEvent(new Event('change',{bubbles:true}));
  await new Promise(resolve => setTimeout(resolve,30));
  const beforeRelease = [...order];
  releaseDiscovery({ok:true,models:['gpt-oss-120b']});
  for (let attempt=0; attempt<80 && !order.includes('route-refresh'); attempt++) await new Promise(resolve => setTimeout(resolve,10));
  const afterRelease = [...order];
  window.pywebview = originalBridge;
  return {beforeRelease,afterRelease};
})()`);
if (discoveryOrder.beforeRelease.includes('route-refresh') || !discoveryOrder.afterRelease.includes('route-refresh') || discoveryOrder.afterRelease.indexOf('route-refresh') < discoveryOrder.afterRelease.indexOf('discovery-done')) throw new Error('model discovery and route refresh were out of order: '+JSON.stringify(discoveryOrder));

const providerDiscoveryOrder = await evaluate(`(async () => {
  const originalBridge = window.pywebview;
  const order = [];
  let releaseDiscovery;
  const discovery = new Promise(resolve => { releaseDiscovery = resolve; });
  SET.openrouter_api_key = 'ordered-openrouter-key';
  window.pywebview = {api:{
    activate_model_provider: async () => { order.push('activation-start'); const result = await discovery; order.push('activation-done'); return result; },
    get_settings: async () => { order.push('route-refresh'); return {_route_state:SET._route_state}; },
  }};
  const provider = document.querySelector('[data-setting="llm_provider"]');
  provider.value = 'openrouter';
  provider.dispatchEvent(new Event('change',{bubbles:true}));
  await new Promise(resolve => setTimeout(resolve,30));
  const beforeRelease = [...order];
  releaseDiscovery({ok:true,models:['openrouter/ordered-model']});
  for (let attempt=0; attempt<80 && !order.includes('route-refresh'); attempt++) await new Promise(resolve => setTimeout(resolve,10));
  const afterRelease = [...order];
  window.pywebview = originalBridge;
  return {skipped:false,beforeRelease,afterRelease};
})()`);
if (!providerDiscoveryOrder.skipped && (providerDiscoveryOrder.beforeRelease.join('|') !== 'activation-start' || !providerDiscoveryOrder.afterRelease.includes('route-refresh') || providerDiscoveryOrder.afterRelease.indexOf('route-refresh') < providerDiscoveryOrder.afterRelease.indexOf('activation-done'))) throw new Error('provider discovery and route refresh were out of order: '+JSON.stringify(providerDiscoveryOrder));

const onboardingProviderOrder = await evaluate(`(async () => {
  const originalBridge = window.pywebview;
  const order = [];
  SET.cerebras_api_key = 'ordered-onboarding-key';
  window.pywebview = {api:{
    activate_model_provider: async () => { order.push('activation'); return {ok:true,models:['gpt-oss-120b']}; },
    get_settings: async () => { order.push('route-refresh'); return {_route_state:SET._route_state}; },
  }};
  wireOnboardingProvider();
  const provider = document.querySelector('#ob-provider');
  provider.value = 'cerebras';
  provider.dispatchEvent(new Event('change',{bubbles:true}));
  for (let attempt=0; attempt<80 && !order.includes('route-refresh'); attempt++) await new Promise(resolve => setTimeout(resolve,10));
  window.pywebview = originalBridge;
  return {skipped:false,order};
})()`);
if (!onboardingProviderOrder.skipped && onboardingProviderOrder.order.join('|') !== 'activation|route-refresh') throw new Error('onboarding provider activation was out of order: '+JSON.stringify(onboardingProviderOrder));

const supersededProvider = await evaluate(`(async () => {
  const originalBridge = window.pywebview;
  let releaseFirst;
  const first = new Promise(resolve => { releaseFirst = resolve; });
  let calls = 0;
  window.pywebview = {api:{
    activate_model_provider: async provider => {
      calls += 1;
      if (calls === 1) return first;
      return {ok:true,models:[provider + '/current']};
    },
    get_settings: async () => ({_route_state:SET._route_state}),
  }};
  const provider = document.querySelector('[data-setting="llm_provider"]');
  provider.value = 'openrouter';
  provider.dispatchEvent(new Event('change',{bubbles:true}));
  await new Promise(resolve => setTimeout(resolve,20));
  provider.value = 'cerebras';
  provider.dispatchEvent(new Event('change',{bubbles:true}));
  for (let attempt=0; attempt<80 && SET.llm_provider !== 'cerebras'; attempt++) await new Promise(resolve => setTimeout(resolve,10));
  releaseFirst({ok:true,models:['openrouter/stale']});
  await new Promise(resolve => setTimeout(resolve,50));
  const result = {calls, saved:SET.llm_provider, control:provider.value};
  window.pywebview = originalBridge;
  return result;
})()`);
if (supersededProvider.calls !== 2 || supersededProvider.saved !== 'cerebras' || supersededProvider.control !== 'cerebras') throw new Error('superseded provider activation won: '+JSON.stringify(supersededProvider));

const onboardingFinish = await evaluate(`(async () => {
  const originalBridge = window.pywebview;
  const order = [];
  let finishPayload = null;
  window.pywebview = {api:{
    activate_model_provider: async provider => { order.push('activation:' + provider); return {ok:true,models:[provider + '/confirmed']}; },
    get_settings: async () => ({_route_state:SET._route_state}),
    finish_onboarding: async payload => { order.push('finish'); finishPayload = payload; return {ok:true}; },
    apply_shortcuts: async () => ({ok:true}),
    set_onboarding_mode: async () => true,
  }};
  document.querySelector('#ob-provider').value = 'cerebras';
  await finishOnboarding();
  window.pywebview = originalBridge;
  return {order, finishPayload};
})()`);
if (onboardingFinish.order[0] !== 'activation:cerebras' || onboardingFinish.order[onboardingFinish.order.length - 1] !== 'finish' || Object.prototype.hasOwnProperty.call(onboardingFinish.finishPayload || {}, 'llm_provider')) throw new Error('Finish bypassed guarded provider activation: '+JSON.stringify(onboardingFinish));

const failClosedMatrix = await evaluate(`(() => {
  const cases = [
    {name:'missing_model', reason:'missing_model', supported:true, key:true, model:'', fetched:new Set()},
    {name:'missing_key', reason:'missing_key', supported:true, key:false, model:'gpt-oss-120b', fetched:new Set(['gpt-oss-120b'])},
    {name:'unsupported_provider', reason:'unsupported_provider', supported:false, key:false, model:'', fetched:new Set()},
    {name:'hosted_processing_off', reason:'hosted_processing_off', supported:true, key:true, model:'gpt-oss-120b', fetched:new Set(['gpt-oss-120b'])},
    {name:'device_only', reason:'device_only', supported:true, key:true, model:'gpt-oss-120b', fetched:new Set(['gpt-oss-120b'])},
    {name:'unconfirmed_model', reason:'ready', supported:true, key:true, model:'saved-but-absent', fetched:new Set(['listed-model'])},
  ];
  return cases.map(item => {
    MODELS_FETCHED.cerebras = item.fetched;
    const decision = {requested_route:'hosted', effective_route:'local', reason:item.name === 'unconfirmed_model' ? 'unconfirmed_model' : item.reason, provider:'cerebras', provider_supported:item.supported, key_present:item.key, model:item.model, ready:false};
    SET._route_state = {...SET._route_state,
      action_processing:{requested:'hosted', effective:decision.effective_route === 'hosted' ? 'cloud' : 'local', reason:item.reason, provider:'cerebras', provider_supported:item.supported, has_key:item.key, decision},
      feature_routes:Object.fromEntries(FEATURE_ROUTE_ROWS.map(([,key]) => [key,{...decision}])),
    };
    updateSetupSummary();
    const banner = document.querySelector('#hosted-capability-status') || document.querySelector('#processing-route-status');
    return {name:item.name, banner:banner?.dataset.available, rows:Array.from(document.querySelectorAll('[data-route-value="effective"]')).map(node=>node.textContent.trim())};
  });
})()`);
if (failClosedMatrix.some(item => item.banner !== 'false' || item.rows.length !== 7 || item.rows.some(value => /Hosted.*ready/i.test(value)))) throw new Error('fail-closed route matrix contradicted itself: '+JSON.stringify(failClosedMatrix));

const hosted = {...base, instant_text:false, _route_state:{
  transcription:{effective:'local',reason:'selected',provider:'groq'},
  plain_processing:{effective:'cloud',reason:'selected'},
  action_processing:{effective:'cloud',reason:'selected',provider:'cerebras',provider_supported:true,has_key:true,decision:{requested_route:'hosted',effective_route:'hosted',reason:'ready',provider:'cerebras',model:'gpt-oss-120b',ready:true}}
}};
await evaluate(`MODELS_FETCHED.cerebras = new Set(['gpt-oss-120b']); true`);
const routeRequestsBefore = await evaluate(`window.__settingsRequests.length`);
await evaluate(`(() => { const el=document.querySelector('[data-setting="instant_text"]'); el.checked=false; el.dispatchEvent(new Event('change',{bubbles:true})); return true; })()`);
for (let i=0; i<100; i++) {
  if (await evaluate(`window.__settingsRequests.length > ${routeRequestsBefore}`)) break;
  await new Promise(r => setTimeout(r, 25));
}
await evaluate(`window.__settingsRequests.at(-1).resolve(${JSON.stringify(hosted)}); true`);
for (let i=0; i<100; i++) {
  if ((await evaluate(`document.querySelector('#route-fact-effective')?.textContent || ''`)).includes('Hosted')) break;
  await new Promise(r => setTimeout(r, 25));
}
const routeRefresh = await evaluate(`({
  effective:document.querySelector('#route-fact-effective')?.textContent || '',
  location:document.querySelector('#route-fact-location')?.textContent || '',
  egress:document.querySelector('#route-fact-egress')?.textContent || '',
  getSettingsCalls:window.__settingsRequests.length
})`);
if (!routeRefresh.effective.includes('Hosted') || !routeRefresh.location.toLowerCase().includes('hosted') || !routeRefresh.egress.includes('Transcript text') || routeRefresh.getSettingsCalls !== routeRequestsBefore + 1) throw new Error('route disclosure did not refresh after save: '+JSON.stringify(routeRefresh));

await evaluate(`hydrateSettings(); true`);
await evaluate(`window.__settingsRequests.at(-1).resolve(${JSON.stringify(base)}); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`window.__micRequests.length > ${expectedMicRequests}`)) break;
  await new Promise(r => setTimeout(r, 25));
}
expectedMicRequests = await evaluate(`window.__micRequests.length`);
const staleMicIndex = (await evaluate(`window.__micRequests.length`)) - 1;
await evaluate(`hydrateSettings(); true`);
await evaluate(`window.__settingsRequests.at(-1).resolve(${JSON.stringify(base)}); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`window.__micRequests.length > ${expectedMicRequests}`)) break;
  await new Promise(r => setTimeout(r, 25));
}
expectedMicRequests = await evaluate(`window.__micRequests.length`);
await evaluate(`window.__micRequests.at(-1).resolve([{index:3,name:'Newest microphone'}]); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`document.querySelector('[data-view="settings"]').dataset.settingsState === 'ready'`)) break;
  await new Promise(r => setTimeout(r, 25));
}
await evaluate(`window.__micRequests[${staleMicIndex}].resolve([{index:2,name:'Stale microphone'}]); true`);
await new Promise(r => setTimeout(r, 100));
const race = await evaluate(`(() => { const s=document.querySelector('#set-mic'); return {value:s.value,labels:Array.from(s.options).map(o=>o.textContent)}; })()`);
if (race.value !== '3' || race.labels.join('|') !== 'Newest microphone') throw new Error('stale microphone response won: '+JSON.stringify(race));

await evaluate(`hydrateSettings(); true`);
await evaluate(`window.__settingsRequests.at(-1).reject(new Error('bridge rejected')); true`);
for (let i=0; i<100; i++) {
  if (await evaluate(`document.querySelector('[data-view="settings"]').dataset.settingsState === 'error'`)) break;
  await new Promise(r => setTimeout(r, 25));
}
const failed = await evaluate(`(() => { const v=document.querySelector('[data-view="settings"]'); const s=document.querySelector('[data-setting="ui_effects"]'); return {state:v.dataset.settingsState,disabled:s.disabled,visibility:getComputedStyle(s).visibility}; })()`);
if (failed.state !== 'error' || !failed.disabled || failed.visibility !== 'hidden') throw new Error('bad error state: '+JSON.stringify(failed));
console.log(JSON.stringify({delayed, loadingActions, micFailed, errorActions, ready, routeDisclosure, discoveryOrder, providerDiscoveryOrder, onboardingProviderOrder, supersededProvider, onboardingFinish, failClosedMatrix, routeRefresh, race, failed}));
ws.close();
"""
        result = subprocess.run(
            [node, "--input-type=module", "-e", script, str(port), (platform_app / "webui" / "index.html").as_uri()],
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        evidence = json.loads(result.stdout.strip().splitlines()[-1])
        assert evidence["delayed"]["state"] == "loading"
        assert evidence["loadingActions"]["count"] >= 40
        assert evidence["loadingActions"]["bad"] == []
        assert evidence["micFailed"]["state"] == "error"
        assert evidence["errorActions"]["bad"] == []
        assert evidence["errorActions"]["retryUsable"] is True
        assert evidence["ready"]["value"] == "enhanced"
        assert len(evidence["routeDisclosure"]["features"]) == 7
        assert "route-refresh" not in evidence["discoveryOrder"]["beforeRelease"]
        assert "route-refresh" not in evidence["providerDiscoveryOrder"]["beforeRelease"]
        assert evidence["onboardingProviderOrder"]["order"] == [] or evidence["onboardingProviderOrder"]["order"] == ["activation", "route-refresh"]
        assert len(evidence["failClosedMatrix"]) == 6
        assert all(item["banner"] == "false" for item in evidence["failClosedMatrix"])
        assert "Hosted" in evidence["routeRefresh"]["effective"]
        assert evidence["race"]["labels"] == ["Newest microphone"]
        assert evidence["failed"]["state"] == "error"
    finally:
        browser.terminate()
        try:
            browser.wait(timeout=5)
        except subprocess.TimeoutExpired:
            browser.kill()
        shutil.rmtree(profile, ignore_errors=True)
