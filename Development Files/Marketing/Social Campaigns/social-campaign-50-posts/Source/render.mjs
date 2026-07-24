import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const sourceDir = path.dirname(fileURLToPath(import.meta.url));
const campaignRoot = path.dirname(sourceDir);
const rendererPath = path.join(sourceDir, 'renderer.html');
const dataPath = path.join(sourceDir, 'campaign-data.js');
const chromeCandidates = [
  process.env.CHROME_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'
].filter(Boolean);
const chrome = chromeCandidates.find((candidate) => fs.existsSync(candidate));

if (!chrome) throw new Error('Chrome or Edge was not found. Set CHROME_PATH and run again.');

const sandbox = { window: {} };
vm.runInNewContext(fs.readFileSync(dataPath, 'utf8'), sandbox, { filename: dataPath });
const posts = sandbox.window.MUMBLE_POSTS;
if (!Array.isArray(posts) || posts.length !== 50) {
  throw new Error(`Expected exactly 50 campaign entries; found ${posts?.length ?? 0}.`);
}

const platformSpec = {
  instagram: { width: 1080, height: 1350, label: 'Instagram 4:5' },
  x: { width: 1920, height: 1080, label: 'X 16:9' },
  tiktok: { width: 1080, height: 1920, label: 'TikTok 9:16' }
};

function outputFolder(post) {
  if (post.platform === 'instagram') {
    if (post.carousel === 1) return path.join(campaignRoot, 'Instagram', '01_Core_Voice');
    if (post.carousel === 2) return path.join(campaignRoot, 'Instagram', '02_Deck_and_Control');
    return path.join(campaignRoot, 'Instagram', '03_Meetings_and_More');
  }
  if (post.platform === 'x') {
    if (post.carousel === 4) return path.join(campaignRoot, 'X', '04_Architecture_and_Choice');
    if (post.carousel === 5) return path.join(campaignRoot, 'X', '05_Workflows_and_Reuse');
    return path.join(campaignRoot, 'X', '06_Beyond_Dictation');
  }
  if (post.carousel === 7) return path.join(campaignRoot, 'TikTok', '07_Voice_First');
  if (post.carousel === 8) return path.join(campaignRoot, 'TikTok', '08_Deck_and_Meetings');
  return path.join(campaignRoot, 'TikTok', '09_Search_and_Download');
}

function readPngSize(file) {
  const header = fs.readFileSync(file).subarray(0, 24);
  if (header.toString('ascii', 1, 4) !== 'PNG') throw new Error(`${file} is not a PNG.`);
  return { width: header.readUInt32BE(16), height: header.readUInt32BE(20) };
}

function removeBestEffort(target) {
  try { fs.rmSync(target, { recursive: true, force: true }); } catch { /* Chrome may release Windows locks a moment later. */ }
}

function renderPost(post) {
  const spec = platformSpec[post.platform];
  const outDir = outputFolder(post);
  const outPath = path.join(outDir, post.filename);
  const profile = path.join(sourceDir, '.chrome-profile', String(post.id).padStart(2, '0'));
  fs.mkdirSync(outDir, { recursive: true });
  fs.mkdirSync(profile, { recursive: true });

  if (fs.existsSync(outPath)) {
    const existing = readPngSize(outPath);
    if (existing.width === spec.width && existing.height === spec.height) return outPath;
  }

  const url = `${pathToFileURL(rendererPath).href}?id=${post.id}`;
  const args = [
    '--headless=new',
    '--disable-gpu',
    '--hide-scrollbars',
    '--allow-file-access-from-files',
    '--disable-background-networking',
    '--disable-default-apps',
    '--disable-extensions',
    '--disable-sync',
    '--disable-crash-reporter',
    '--disable-breakpad',
    '--disable-features=Crashpad',
    '--no-sandbox',
    '--no-first-run',
    '--no-default-browser-check',
    '--force-color-profile=srgb',
    '--force-device-scale-factor=1',
    '--run-all-compositor-stages-before-draw',
    '--virtual-time-budget=1200',
    `--user-data-dir=${profile}`,
    `--window-size=${spec.width},${spec.height}`,
    `--screenshot=${outPath}`,
    url
  ];

  const result = spawnSync(chrome, args, { stdio: 'ignore', timeout: 30000 });
  removeBestEffort(profile);
  if (!fs.existsSync(outPath)) {
    throw new Error(`Render ${post.id} failed.\n${result.error || `Chrome exited with status ${result.status}`}`);
  }
  const actual = readPngSize(outPath);
  if (actual.width !== spec.width || actual.height !== spec.height) {
    throw new Error(`Render ${post.id} is ${actual.width}×${actual.height}; expected ${spec.width}×${spec.height}.`);
  }
  return outPath;
}

function csvCell(value) {
  return `"${String(value ?? '').replaceAll('"', '""')}"`;
}

function relativeFromRoot(file) {
  return path.relative(campaignRoot, file).replaceAll('\\', '/');
}

function writeCampaignFiles(rendered) {
  const manifest = posts.map((post) => {
    const spec = platformSpec[post.platform];
    const file = rendered.get(post.id);
    return {
      ...post,
      width: spec.width,
      height: spec.height,
      path: relativeFromRoot(file),
      bytes: fs.statSync(file).size
    };
  });
  fs.writeFileSync(path.join(campaignRoot, 'campaign-manifest.json'), JSON.stringify(manifest, null, 2), 'utf8');

  const headers = ['id','platform','carousel','slide','stage','width','height','filename','path','headline','body','cta','qualifier','caption','alt','bytes'];
  const rows = [headers.map(csvCell).join(',')].concat(manifest.map((item) => headers.map((header) => csvCell(item[header])).join(',')));
  fs.writeFileSync(path.join(campaignRoot, 'campaign-manifest.csv'), rows.join('\r\n'), 'utf8');

  let captions = '# Mumble social campaign — captions and alt text\n\n';
  captions += 'Fifty carousel-ready static creatives. Publish slides in numeric order within each carousel folder. Replace or append the final download link in the platform caption when the public URL is ready.\n\n';
  captions += '> Factual guardrail: Local transcription is the default; Cloud transcription is an explicit option. Optional AI/Reader services may send text or audio to the provider selected by the user.\n\n';
  for (let carousel = 1; carousel <= 9; carousel += 1) {
    const set = manifest.filter((item) => item.carousel === carousel);
    const platform = set[0].platform === 'x' ? 'X' : set[0].platform[0].toUpperCase() + set[0].platform.slice(1);
    captions += `## Carousel ${String(carousel).padStart(2, '0')} — ${platform}\n\n`;
    for (const item of set) {
      captions += `### ${String(item.id).padStart(2, '0')}. ${item.headline}\n\n`;
      captions += `- File: \`${item.path}\`\n`;
      captions += `- Stage: ${item.stage}\n`;
      captions += `- Caption: ${item.caption}\n`;
      captions += `- Alt text: ${item.alt}\n`;
      if (item.qualifier) captions += `- Qualifier: ${item.qualifier}\n`;
      captions += '\n';
    }
  }
  fs.writeFileSync(path.join(campaignRoot, 'CAPTIONS_AND_ALT_TEXT.md'), captions, 'utf8');

  const cards = manifest.map((item) => `<figure data-platform="${item.platform}"><img loading="lazy" src="${item.path}" alt="${item.alt.replaceAll('&','&amp;').replaceAll('"','&quot;')}"><figcaption><b>${String(item.id).padStart(2,'0')} · ${item.headline}</b><span>${item.platform.toUpperCase()} · Carousel ${String(item.carousel).padStart(2,'0')} · ${item.width}×${item.height}</span></figcaption></figure>`).join('\n');
  const gallery = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mumble — 50 social creatives</title><style>
  :root{color-scheme:dark;font-family:"Segoe UI",sans-serif;background:#0A0A0B;color:#F3EEE1}*{box-sizing:border-box}body{margin:0;padding:48px;background:radial-gradient(circle at 20% 0,rgba(212,175,55,.08),transparent 28%),#0A0A0B}header{max-width:1500px;margin:0 auto 36px}h1{font-size:clamp(34px,5vw,72px);letter-spacing:-.04em;margin:0;color:#EBCB65}p{color:#B6AE99;font-size:18px}.filters{display:flex;gap:10px;flex-wrap:wrap;margin-top:24px}.filters button{border:1px solid #2A2820;background:#16140F;color:#F3EEE1;border-radius:999px;padding:10px 16px;cursor:pointer}.filters button:hover{border-color:#D4AF37;color:#D4AF37}main{max-width:1800px;margin:auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:26px}figure{margin:0;background:linear-gradient(145deg,#1e1c17,#121110);border:1px solid #2A2820;border-radius:20px;overflow:hidden;box-shadow:0 16px 50px rgba(0,0,0,.55)}figure img{width:100%;display:block;background:#090909}figcaption{display:grid;gap:6px;padding:15px 17px}figcaption b{font-size:14px}figcaption span{font:11px "Cascadia Code",monospace;color:#948B76}figure[hidden]{display:none}</style></head><body><header><h1>50 Mumble creatives.</h1><p>Nine carousel-ready groups across Instagram, X, and TikTok. No humans, mascots, animals, or living creatures.</p><div class="filters"><button data-filter="all">All 50</button><button data-filter="instagram">Instagram 18</button><button data-filter="x">X 17</button><button data-filter="tiktok">TikTok 15</button></div></header><main>${cards}</main><script>document.querySelectorAll('button[data-filter]').forEach(b=>b.onclick=()=>document.querySelectorAll('figure').forEach(f=>f.hidden=b.dataset.filter!=='all'&&f.dataset.platform!==b.dataset.filter));</script></body></html>`;
  fs.writeFileSync(path.join(campaignRoot, 'index.html'), gallery, 'utf8');
}

const rendered = new Map();
for (const [index, post] of posts.entries()) {
  const outPath = renderPost(post);
  rendered.set(post.id, outPath);
  process.stdout.write(`[${String(index + 1).padStart(2, '0')}/50] ${relativeFromRoot(outPath)}\n`);
}

removeBestEffort(path.join(sourceDir, '.chrome-profile'));
writeCampaignFiles(rendered);
process.stdout.write(`Rendered and validated 50 PNG files in ${campaignRoot}\n`);
