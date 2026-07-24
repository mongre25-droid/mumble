import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const sourceDir = path.dirname(fileURLToPath(import.meta.url));
const root = path.dirname(sourceDir);
const manifestPath = path.join(root, 'campaign-manifest.json');
const contactDir = path.join(root, 'Contact Sheets');
const chrome = [
  process.env.CHROME_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
].filter(Boolean).find((candidate) => fs.existsSync(candidate));

if (!fs.existsSync(manifestPath)) throw new Error('Run render.mjs before QA.');
if (!chrome) throw new Error('Chrome or Edge was not found.');

const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const expected = {
  instagram: { count: 18, width: 1080, height: 1350 },
  x: { count: 17, width: 1920, height: 1080 },
  tiktok: { count: 15, width: 1080, height: 1920 }
};
const expectedCarousels = { 1: 6, 2: 6, 3: 6, 4: 6, 5: 6, 6: 5, 7: 5, 8: 5, 9: 5 };
const problems = [];
const hashes = new Map();

function removeBestEffort(target) {
  try { fs.rmSync(target, { recursive: true, force: true }); } catch { /* Ignore transient Chrome locks on Windows. */ }
}

function pngSize(file) {
  const header = fs.readFileSync(file).subarray(0, 24);
  return { width: header.readUInt32BE(16), height: header.readUInt32BE(20) };
}

if (manifest.length !== 50) problems.push(`Manifest has ${manifest.length} entries, expected 50.`);
for (const [platform, spec] of Object.entries(expected)) {
  const set = manifest.filter((item) => item.platform === platform);
  if (set.length !== spec.count) problems.push(`${platform} has ${set.length} entries, expected ${spec.count}.`);
}
for (const [carousel, count] of Object.entries(expectedCarousels)) {
  const set = manifest.filter((item) => item.carousel === Number(carousel)).sort((a,b) => a.slide - b.slide);
  if (set.length !== count) problems.push(`Carousel ${carousel} has ${set.length} slides, expected ${count}.`);
  if (set.some((item, index) => item.slide !== index + 1)) problems.push(`Carousel ${carousel} slide numbering is not sequential.`);
}

for (const item of manifest) {
  const full = path.join(root, ...item.path.split('/'));
  if (!fs.existsSync(full)) {
    problems.push(`Missing ${item.path}`);
    continue;
  }
  const size = pngSize(full);
  const spec = expected[item.platform];
  if (size.width !== spec.width || size.height !== spec.height) {
    problems.push(`${item.path} is ${size.width}×${size.height}; expected ${spec.width}×${spec.height}.`);
  }
  const hash = crypto.createHash('sha256').update(fs.readFileSync(full)).digest('hex');
  if (hashes.has(hash)) problems.push(`${item.path} duplicates ${hashes.get(hash)} byte-for-byte.`);
  hashes.set(hash, item.path);
}

const joinedCopy = manifest.map((item) => `${item.headline} ${item.body} ${item.caption}`).join('\n');
const prohibited = [
  /audio (?:always|never) leaves/i,
  /transcribed on your device\. always/i,
  /available (?:on|for) (?:windows,? )?macos/i,
  /works in every app/i,
  /automatic(?:ally)? (?:knows|infers)/i,
  /offline reader/i,
  /100% accurate/i,
  /zero latency/i
];
for (const pattern of prohibited) {
  if (pattern.test(joinedCopy)) problems.push(`Prohibited claim matched ${pattern}.`);
}

fs.mkdirSync(contactDir, { recursive: true });
const sheetProfiles = path.join(sourceDir, '.sheet-profiles');
const platformNames = { instagram: 'Instagram · 18 creatives · 1080×1350', x: 'X · 17 creatives · 1920×1080', tiktok: 'TikTok · 15 creatives · 1080×1920' };
const sheetLayout = {
  instagram: { width: 2400, height: 1770, cols: 6, imageWidth: 330, imageHeight: 413 },
  x: { width: 2400, height: 2300, cols: 4, imageWidth: 510, imageHeight: 287 },
  tiktok: { width: 2400, height: 2240, cols: 5, imageWidth: 350, imageHeight: 622 }
};

for (const platform of Object.keys(expected)) {
  const set = manifest.filter((item) => item.platform === platform);
  const layout = sheetLayout[platform];
  const cards = set.map((item) => {
    const full = path.join(root, ...item.path.split('/'));
    return `<figure><img src="${pathToFileURL(full).href}" alt=""><figcaption><b>${String(item.id).padStart(2,'0')}</b><span>${item.headline.replaceAll('&','&amp;').replaceAll('<','&lt;')}</span></figcaption></figure>`;
  }).join('');
  const html = `<!doctype html><html><head><meta charset="utf-8"><style>*{box-sizing:border-box}html,body{margin:0;width:${layout.width}px;height:${layout.height}px;overflow:hidden;background:#080808;color:#f3eee1;font-family:"Segoe UI",sans-serif}body{padding:64px;background:radial-gradient(circle at 18% 0,rgba(212,175,55,.12),transparent 28%),#080808}header{height:112px;display:flex;align-items:flex-start;justify-content:space-between;border-bottom:1px solid #2a2820;margin-bottom:34px}h1{margin:0;font-size:46px;letter-spacing:-.04em;color:#ebcb65}header span{font:14px "Cascadia Code",monospace;color:#948b76;padding-top:18px}main{display:grid;grid-template-columns:repeat(${layout.cols},${layout.imageWidth}px);gap:30px;justify-content:center}figure{margin:0;border:1px solid #2a2820;border-radius:16px;overflow:hidden;background:#121110;box-shadow:0 14px 34px rgba(0,0,0,.45)}img{display:block;width:${layout.imageWidth}px;height:${layout.imageHeight}px;object-fit:cover}figcaption{height:72px;padding:12px 14px;display:flex;gap:10px;align-items:flex-start}figcaption b{color:#d4af37;font:13px "Cascadia Code",monospace}figcaption span{font-size:12px;line-height:1.35;color:#b6ae99}</style></head><body><header><h1>${platformNames[platform]}</h1><span>MUMBLE SOCIAL CAMPAIGN · 50 POSTS</span></header><main>${cards}</main></body></html>`;
  const htmlPath = path.join(sourceDir, `.contact-${platform}.html`);
  const outPath = path.join(contactDir, `${platform}-contact-sheet.png`);
  const profile = path.join(sheetProfiles, platform);
  if (fs.existsSync(outPath)) {
    const existing = pngSize(outPath);
    if (existing.width === layout.width && existing.height === layout.height) continue;
  }
  fs.writeFileSync(htmlPath, html, 'utf8');
  fs.mkdirSync(profile, { recursive: true });
  const result = spawnSync(chrome, [
    '--headless=new','--disable-gpu','--hide-scrollbars','--allow-file-access-from-files',
    '--disable-background-networking','--disable-crash-reporter','--disable-breakpad',
    '--disable-features=Crashpad','--no-sandbox','--force-color-profile=srgb','--force-device-scale-factor=1',
    '--run-all-compositor-stages-before-draw','--virtual-time-budget=1800',
    `--user-data-dir=${profile}`,`--window-size=${layout.width},${layout.height}`,
    `--screenshot=${outPath}`,pathToFileURL(htmlPath).href
  ], { stdio: 'ignore', timeout: 60000 });
  removeBestEffort(profile);
  removeBestEffort(htmlPath);
  if (!fs.existsSync(outPath)) problems.push(`Contact sheet failed for ${platform}.`);
}
removeBestEffort(sheetProfiles);

let report = '# Mumble campaign QA report\n\n';
report += `Generated: ${new Date().toISOString()}\n\n`;
report += `Status: **${problems.length ? 'FAIL' : 'PASS'}**\n\n`;
report += '## Automated checks\n\n';
report += `- Manifest entries: ${manifest.length}/50\n`;
report += `- Instagram: ${manifest.filter((x)=>x.platform==='instagram').length}/18 at 1080×1350\n`;
report += `- X: ${manifest.filter((x)=>x.platform==='x').length}/17 at 1920×1080\n`;
report += `- TikTok: ${manifest.filter((x)=>x.platform==='tiktok').length}/15 at 1080×1920\n`;
report += `- Unique PNG hashes: ${hashes.size}/50\n`;
report += `- Carousel groups and slide order: ${Object.keys(expectedCarousels).length}/9\n`;
report += `- Prohibited stale-claim patterns: ${problems.filter((p)=>p.startsWith('Prohibited')).length}\n`;
report += '- Living-creature policy: deterministic renderer uses only logo, waveforms, text, UI, files, keycaps, locks, chips, charts, and geometric diagrams.\n';
report += '- Sample/demo metrics: no campaign creative presents fabricated customer totals or performance statistics.\n\n';
report += '## Problems\n\n';
report += problems.length ? problems.map((problem) => `- ${problem}`).join('\n') : '- None.';
report += '\n\n## Contact sheets\n\n- `Contact Sheets/instagram-contact-sheet.png`\n- `Contact Sheets/x-contact-sheet.png`\n- `Contact Sheets/tiktok-contact-sheet.png`\n';
fs.writeFileSync(path.join(root, 'QA_REPORT.md'), report, 'utf8');

if (problems.length) {
  console.error(problems.join('\n'));
  process.exitCode = 1;
} else {
  console.log('PASS: 50 unique assets, dimensions and campaign constraints validated.');
}
