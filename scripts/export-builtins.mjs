// Writes basal/builtin_templates.json: one starter template per Playground scenario (ui/js/examples.js), with the
// models whose guides recommend it (ui/js/model-guides.js). The studio seeds these at startup as read-only
// builtin/<scenario> templates. Run after changing either file:
//
//     node scripts/export-builtins.mjs          (tests/test_templates.py checks the file is in sync)
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const { EXAMPLES } = await import(pathToFileURL(join(root, 'ui/js/examples.js')).href);
const { GUIDES } = await import(pathToFileURL(join(root, 'ui/js/model-guides.js')).href);

const out = EXAMPLES.filter((e) => e.questions && Object.keys(e.questions).length).map((e) => ({
  id: e.id,
  title: e.title,
  blurb: e.blurb || '',
  group: e.group || '',
  state: e.state,
  questions: e.questions,
  modalities: e.sample ? ['text', 'image'] : ['text'],
  recommended_models: Object.entries(GUIDES).filter(([, g]) => (g.examples || []).includes(e.id)).map(([id]) => id),
  ...(e.sample ? { sample: e.sample } : {}),
}));
const file = join(root, 'basal/builtin_templates.json');
const text = `${JSON.stringify(out, null, 1)}\n`;
if (process.argv.includes('--check')) {
  const { readFileSync } = await import('node:fs');
  let cur = '';
  try { cur = readFileSync(file, 'utf8'); } catch { /* missing */ }
  if (cur !== text) { console.error('basal/builtin_templates.json is out of date: run node scripts/export-builtins.mjs'); process.exit(1); }
  console.log('builtin_templates.json is in sync');
} else {
  writeFileSync(file, text);
  console.log(`wrote ${out.length} starter templates to basal/builtin_templates.json`);
}
