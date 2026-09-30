// Code and JSON presentation: a small syntax highlighter, line-numbered code blocks, and a collapsible JSON tree.

import { copy, esc, icon } from './util.js';

// ------------------------------------------------------------------ tokenizer
const RULES = {
  json: [
    [/"(?:[^"\\]|\\.)*"(?=\s*:)/y, 'tk-key'],
    [/"(?:[^"\\]|\\.)*"/y, 'tk-s'],
    [/-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/y, 'tk-n'],
    [/\b(?:true|false|null)\b/y, 'tk-lit'],
    [/[{}[\],:]/y, 'tk-p'],
  ],
  python: [
    [/#.*/y, 'tk-c'],
    [/[rbfu]{0,2}("""[\s\S]*?"""|'''[\s\S]*?''')/y, 'tk-s'],
    [/[rbfu]{0,2}("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')/y, 'tk-s'],
    [/\b(?:import|from|as|def|return|for|in|if|elif|else|while|with|try|except|finally|raise|class|lambda|and|or|not|is|async|await|pass|print)\b/y, 'tk-k'],
    [/\b(?:True|False|None)\b/y, 'tk-lit'],
    [/\b\d+(?:\.\d+)?\b/y, 'tk-n'],
    [/[A-Za-z_]\w*(?=\()/y, 'tk-fn'],
    [/[{}[\](),:=.]/y, 'tk-p'],
  ],
  javascript: [
    [/\/\/.*/y, 'tk-c'],
    [/`(?:[^`\\]|\\.)*`/y, 'tk-s'],
    [/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/y, 'tk-s'],
    [/\b(?:import|from|const|let|var|await|async|function|return|new|for|of|in|if|else|export|default|try|catch|throw)\b/y, 'tk-k'],
    [/\b(?:true|false|null|undefined)\b/y, 'tk-lit'],
    [/\b\d+(?:\.\d+)?\b/y, 'tk-n'],
    [/[A-Za-z_$][\w$]*(?=\()/y, 'tk-fn'],
    [/[{}[\](),:;=.]/y, 'tk-p'],
  ],
  bash: [
    [/#.*/y, 'tk-c'],
    [/'[^']*'/y, 'tk-s'],
    [/"(?:[^"\\]|\\.)*"/y, 'tk-s'],
    [/(?<=^|\s)--?[A-Za-z][\w-]*/y, 'tk-k'],
    [/\$\{?\w+\}?/y, 'tk-lit'],
    [/(?<=^|\n|\|\s?)(?:curl|pip|npm|uv|export|python|node|hf)\b/y, 'tk-fn'],
    [/\\$/my, 'tk-p'],
  ],
};
RULES.shell = RULES.bash;
RULES.js = RULES.javascript;
RULES.ts = RULES.javascript;
RULES.py = RULES.python;

export function tokenize(code, lang) {
  const rules = RULES[lang] || [];
  const out = [];
  let i = 0, plain = '';
  const flush = () => { if (plain) { out.push(['', plain]); plain = ''; } };
  outer: while (i < code.length) {
    for (const [re, cls] of rules) {
      re.lastIndex = i;
      const m = re.exec(code);
      if (m && m.index === i && m[0].length) { flush(); out.push([cls, m[0]]); i += m[0].length; continue outer; }
    }
    plain += code[i++];
  }
  flush();
  return out;
}

// Highlighted HTML split into one block span per line (tokens that span lines are closed and reopened).
export function highlightLines(code, lang) {
  const lines = [''];
  for (const [cls, text] of tokenize(code, lang)) {
    text.split('\n').forEach((part, k) => {
      if (k > 0) lines.push('');
      if (part) lines[lines.length - 1] += cls ? `<span class="${cls}">${esc(part)}</span>` : esc(part);
    });
  }
  return lines.map((l) => `<span class="ln">${l || ' '}</span>`).join('');
}

// ------------------------------------------------------------------ code block
const sources = new Map();
let seq = 0;
const LANG_NAME = { json: 'JSON', python: 'Python', javascript: 'JavaScript', bash: 'Shell', shell: 'Shell', ts: 'TypeScript' };

export function codeBlock(code, { lang = 'json', file = '', wrap = false, maxHeight } = {}) {
  const id = `c${++seq}`;
  sources.set(id, code);
  if (sources.size > 300) sources.delete(sources.keys().next().value);
  return `<div class="code ${wrap ? 'wrap' : ''}" data-code="${id}">
    <div class="code-head">
      ${file ? `<span class="file">${esc(file)}</span>` : ''}<span class="lang">${esc(LANG_NAME[lang] || lang)}</span>
      <span class="right">
        <button class="icon-btn" data-code-wrap aria-label="Wrap long lines" data-tip="Wrap long lines">${icon('text-t')}</button>
        <button class="btn btn-quiet sm" data-code-copy>${icon('copy')}Copy</button>
      </span>
    </div>
    <pre tabindex="0"${maxHeight ? ` style="max-height:${maxHeight}px"` : ''}><code>${highlightLines(code, lang)}</code></pre>
  </div>`;
}

document.addEventListener('click', (e) => {
  const c = e.target.closest('[data-code-copy]');
  if (c) { const box = c.closest('[data-code]'); copy(sources.get(box.dataset.code) ?? '', 'Copied to the clipboard'); return; }
  const w = e.target.closest('[data-code-wrap]');
  if (w) w.closest('.code').classList.toggle('wrap');
  const j = e.target.closest('[data-json-copy]');
  if (j) copy(sources.get(j.dataset.jsonCopy) ?? '', 'JSON copied');
});

// ------------------------------------------------------------------ JSON tree
const scalar = (v) => {
  if (v === null) return '<span class="tk-lit">null</span>';
  if (typeof v === 'boolean') return `<span class="tk-lit">${v}</span>`;
  if (typeof v === 'number') return `<span class="tk-n">${v}</span>`;
  return `<span class="tk-s">${esc(JSON.stringify(v))}</span>`;
};

function node(key, v, depth, last, opts) {
  const k = key == null ? '' : `<span class="tk-key">${esc(JSON.stringify(String(key)))}</span><span class="tk-p">: </span>`;
  const comma = last ? '' : '<span class="tk-p">,</span>';
  if (v === null || typeof v !== 'object') return `<span class="row">${k}${scalar(v)}${comma}</span>`;
  const isArr = Array.isArray(v);
  const entries = isArr ? v.map((x, i) => [null, x]) : Object.entries(v);
  const [o, c] = isArr ? ['[', ']'] : ['{', '}'];
  if (!entries.length) return `<span class="row">${k}<span class="tk-p">${o}${c}</span>${comma}</span>`;
  // Short arrays of scalars stay on one line, like a formatter would print them.
  if (isArr && entries.length <= 8 && v.every((x) => x === null || typeof x !== 'object') && JSON.stringify(v).length < 72) {
    return `<span class="row">${k}<span class="tk-p">[</span>${v.map(scalar).join('<span class="tk-p">, </span>')}<span class="tk-p">]</span>${comma}</span>`;
  }
  const open = depth < (opts.openDepth ?? 3) && !(opts.collapse || []).includes(key);
  const fold = `<span class="fold"> ${entries.length} ${isArr ? (entries.length === 1 ? 'item' : 'items') : (entries.length === 1 ? 'key' : 'keys')} </span><span class="fold tk-p">${c}${last ? '' : ','}</span>`;
  const kids = entries.map(([kk, vv], i) => node(kk, vv, depth + 1, i === entries.length - 1, opts)).join('');
  return `<details ${open ? 'open' : ''}><summary>${k}<span class="tk-p">${o}</span>${fold}</summary><div class="kids">${kids}</div><span class="row"><span class="tk-p">${c}</span>${comma}</span></details>`;
}

export function jsonTree(value, { file = '', openDepth = 3, collapse = [] } = {}) {
  const id = `j${++seq}`;
  const text = JSON.stringify(value, null, 2);
  sources.set(id, text);
  return `<div class="code">
    <div class="code-head">${file ? `<span class="file">${esc(file)}</span>` : ''}<span class="lang">JSON</span>
      <span class="right"><button class="btn btn-quiet sm" data-json-copy="${id}">${icon('copy')}Copy</button></span></div>
    <div class="jtree" tabindex="0">${node(null, value, 0, true, { openDepth, collapse })}</div>
  </div>`;
}

// Python literal for generated snippets (True/False/None, 4-space indent).
export function py(v, ind = 0) {
  const pad = ' '.repeat(ind + 4), end = ' '.repeat(ind);
  if (v === null || v === undefined) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  if (typeof v === 'number') return String(v);
  if (typeof v === 'string') return JSON.stringify(v);
  if (Array.isArray(v)) {
    if (v.every((x) => x === null || typeof x !== 'object') && JSON.stringify(v).length < 64) return `[${v.map((x) => py(x)).join(', ')}]`;
    return `[\n${v.map((x) => pad + py(x, ind + 4)).join(',\n')},\n${end}]`;
  }
  const e = Object.entries(v);
  if (!e.length) return '{}';
  return `{\n${e.map(([k, x]) => `${pad}${JSON.stringify(k)}: ${py(x, ind + 4)}`).join(',\n')},\n${end}}`;
}
