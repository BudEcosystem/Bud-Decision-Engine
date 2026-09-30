// Ready-to-run code for a request, in the published API formats the studio speaks.
// Generated snippets use the exact wire shapes documented in docs/external/SPEC_SUMMARY.md.

import { py } from './format.js';

export const FORMATS = {
  typesafe: { name: 'TypeSafe Jev API', path: '/v1/systemone', note: 'The format Jev introduced. Official TypeSafe SDKs work unchanged.' },
  openrouter: { name: 'OpenRouter Decisions API', path: '/api/alpha/decisions', note: 'OpenRouter\'s decision endpoint: the same body, plus id, provider and cost in the response.' },
  vercel: { name: 'Vercel AI Gateway', path: '/typesafe/v1/systemone', note: 'Vercel\'s TypeSafe-compatible route, with provider_metadata in the response.' },
};

export const LANGS = {
  curl: { name: 'curl', lang: 'bash', file: 'decide.sh' },
  python: { name: 'Python', lang: 'python', file: 'decide.py' },
  'python-sdk': { name: 'Python SDK', lang: 'python', file: 'decide_sdk.py', sdk: true },
  javascript: { name: 'JavaScript', lang: 'javascript', file: 'decide.mjs' },
  'js-sdk': { name: 'JS SDK', lang: 'javascript', file: 'decide_sdk.mjs', sdk: true },
};

const EXT = new Set(['multi', 'rank', 'number']);
export const base = () => `${location.protocol}//${location.host}`;

function clean(request) {
  const r = { model: request.model, state: request.state, questions: request.questions };
  if (request.settings?.temperature) r.settings = request.settings;
  return r;
}

export function snippet(langKey, request, { format = 'typesafe' } = {}) {
  const r = clean(request);
  const url = `${base()}${FORMATS[format].path}`;
  const json = JSON.stringify(r, null, 2);
  const hasExt = Object.values(r.questions).some((q) => EXT.has(q.type));
  if (langKey === 'curl') {
    return `curl -s ${url} \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer $BUD_API_KEY" \\
  -d '${json.replace(/'/g, "'\\''")}'`;
  }
  if (langKey === 'python') {
    return `import os
import requests

request = ${py(r)}

res = requests.post(
    "${url}",
    json=request,
    headers={"Authorization": f"Bearer {os.environ.get('BUD_API_KEY', 'local')}"},
    timeout=120,
)
res.raise_for_status()
print("request id:", res.headers.get("x-typesafe-request-id"))
for name, answer in res.json()["answers"].items():
    print(name, answer)`;
  }
  if (langKey === 'javascript') {
    return `const request = ${json};

const res = await fetch("${url}", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: \`Bearer \${process.env.BUD_API_KEY ?? "local"}\`,
  },
  body: JSON.stringify(request),
});
if (!res.ok) throw new Error(\`\${res.status}: \${await res.text()}\`);

const { answers, usage } = await res.json();
console.log(answers, usage);`;
  }
  // Official SDKs only know choice, score and noul; the three studio extensions are sent over plain HTTP.
  const prim = Object.entries(r.questions).filter(([, q]) => !EXT.has(q.type));
  const skipped = Object.keys(r.questions).length - prim.length;
  const extNote = hasExt ? `${skipped} studio-extension question${skipped === 1 ? '' : 's'} (pick any, put in order, estimate a number) left out: the official SDK only knows choice, score and noul. Use the plain HTTP tab for those.` : '';
  const state = typeof r.state === 'string' ? JSON.stringify(r.state) : null;
  if (langKey === 'python-sdk') {
    const qs = prim.map(([k, q]) => {
      const cls = { choice: 'Choice', score: 'Score', noul: 'Noul' }[q.type];
      const crit = q.criteria != null ? `, criteria=${py(q.criteria, 8)}` : '';
      return `        ${JSON.stringify(k)}: ${cls}(instructions=${JSON.stringify(q.instructions)}${crit}),`;
    }).join('\n');
    return `# pip install typesafe-sdk
import os
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

client = TypeSafeClient(
    api_key=os.environ.get("BUD_API_KEY", "local"),
    base_url="${base()}",   # the only change from TypeSafe's hosted API
    model=${JSON.stringify(r.model)},
)
${extNote ? `# ${extNote}\n` : ''}
result = client.system_one(
    state=${state ?? py(r.state, 4)},
    questions={
${qs}
    },
)
print("request id:", result.request_id)
for name, answer in result.answers.items():
    print(name, answer)`;
  }
  const qs = prim.map(([k, q]) => {
    const args = [JSON.stringify(q.instructions)];
    if (q.criteria != null) args.push(JSON.stringify(q.criteria, null, 2).replace(/\n/g, '\n    '));
    return `    ${/^[A-Za-z_$][\w$]*$/.test(k) ? k : JSON.stringify(k)}: ${q.type}(${args.join(', ')}),`;
  }).join('\n');
  return `// npm install @typesafe-ai/sdk
import { TypeSafeClient, choice, noul, score } from "@typesafe-ai/sdk";

const client = new TypeSafeClient({
  apiKey: process.env.BUD_API_KEY ?? "local",
  baseURL: "${base()}", // the only change from TypeSafe's hosted API
  defaultModel: ${JSON.stringify(r.model)},
});
${extNote ? `// ${extNote}\n` : ''}
const { data, requestId } = await client.systemOne({
  state: ${state ?? JSON.stringify(r.state, null, 2).replace(/\n/g, '\n  ')},
  questions: {
${qs}
  },
}).withResponse();

console.log(requestId, data.answers);`;
}
