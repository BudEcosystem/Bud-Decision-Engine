// Prepares everything the desktop app bundles, before `tauri dev` or `tauri build`:
//   src-tauri/engine/            the studio's code (basal/, ui/, installer/engine.py, requirements*.txt)
//   installer/assets/            the brand mark and font the setup screens use
//   src-tauri/binaries/bud-uv-*  uv for the target platform (copied from PATH when it matches, else downloaded)
import { execFileSync } from 'node:child_process';
import { chmodSync, cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const desktop = join(dirname(fileURLToPath(import.meta.url)), '..');
const repo = join(desktop, '..');
const tauri = join(desktop, 'src-tauri');

// 1. engine code
const engine = join(tauri, 'engine');
rmSync(engine, { recursive: true, force: true });
const skip = (src) => !/__pycache__|\.pyc$|\.DS_Store$/.test(src);
cpSync(join(repo, 'basal'), join(engine, 'basal'), { recursive: true, filter: skip });
cpSync(join(repo, 'ui'), join(engine, 'ui'), { recursive: true, filter: skip });
mkdirSync(join(engine, 'installer'), { recursive: true });
cpSync(join(repo, 'installer', 'engine.py'), join(engine, 'installer', 'engine.py'));
for (const f of readdirSync(repo).filter((f) => /^requirements.*\.txt$/.test(f))) cpSync(join(repo, f), join(engine, f));

// 2. setup screen assets
const assets = join(desktop, 'installer', 'assets');
mkdirSync(assets, { recursive: true });
cpSync(join(repo, 'ui', 'brand', 'bud-mark.png'), join(assets, 'bud-mark.png'));
cpSync(join(repo, 'ui', 'fonts', 'InterVariable.woff2'), join(assets, 'InterVariable.woff2'));
cpSync(join(repo, 'ui', 'js', 'phosphor.js'), join(assets, 'phosphor.js'));
// The studio's colour, type and shadow tokens (light, dark, and the explicit theme override), so setup matches exactly.
const css = readFileSync(join(repo, 'ui', 'styles.css'), 'utf8');
const from = css.indexOf(':root {');
const lastBlock = css.indexOf(':root[data-theme="dark"]');
if (from < 0 || lastBlock < 0) throw new Error('token blocks not found in ui/styles.css');
writeFileSync(join(assets, 'tokens.css'), `/* generated from ui/styles.css by scripts/stage.mjs */\n${css.slice(from, css.indexOf('}', lastBlock) + 1)}\n`);

// 3. uv sidecar
const host = execFileSync('rustc', ['-vV']).toString().match(/host: (\S+)/)[1];
const triple = process.env.TAURI_ENV_TARGET_TRIPLE || process.env.BUD_TARGET || host;
const exe = triple.includes('windows') ? '.exe' : '';
const bin = join(tauri, 'binaries', `bud-uv-${triple}${exe}`);
mkdirSync(dirname(bin), { recursive: true });
if (!existsSync(bin)) {
  let local = null;
  if (triple === host) {
    try { local = execFileSync(process.platform === 'win32' ? 'where' : 'which', ['uv']).toString().split(/\r?\n/)[0].trim(); } catch { local = null; }
  }
  if (local) {
    cpSync(local, bin);
  } else {
    const zip = triple.includes('windows');
    const url = `https://github.com/astral-sh/uv/releases/latest/download/uv-${triple}.${zip ? 'zip' : 'tar.gz'}`;
    console.log(`Downloading uv for ${triple}`);
    const res = await fetch(url);
    if (!res.ok) throw new Error(`uv download failed: ${res.status} ${url}`);
    const tmp = mkdtempSync(join(tmpdir(), 'bud-uv-'));
    const archive = join(tmp, zip ? 'uv.zip' : 'uv.tar.gz');
    writeFileSync(archive, Buffer.from(await res.arrayBuffer()));
    execFileSync('tar', ['-xf', archive, '-C', tmp]);
    const found = [join(tmp, `uv${exe}`), join(tmp, `uv-${triple}`, `uv${exe}`)].find(existsSync);
    if (!found) throw new Error('uv archive did not contain the uv binary');
    cpSync(found, bin);
    rmSync(tmp, { recursive: true, force: true });
  }
  if (!exe) chmodSync(bin, 0o755);
}
console.log(`Staged the engine and bud-uv for ${triple}`);
