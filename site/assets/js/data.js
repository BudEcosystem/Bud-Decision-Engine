/* Content for the Bud Decision Studio product page.
 *
 * SCENARIOS are real responses from Intern-Decision 4B served by the studio on an NVIDIA GB10
 * (POST /v1/systemone with X-Basal-Extensions: 1), captured on 2026-09-30. Latency is the model's own figure.
 * MODELS mirror basal/catalog.py (sizes are download sizes including any base model).
 * RELEASE_FALLBACK mirrors the v0.2.1 GitHub release, used when the GitHub API can't be reached. */
window.BUD = {
  repo: 'BudEcosystem/Bud-Decision-Engine',

  SCENARIOS: [
    {
      name: 'Support ticket',
      state: 'Hi, we were billed twice for March on invoice #4411. Please refund the duplicate today or we will cancel our plan. This is the second time this has happened.',
      latency: 98, tokens: 344,
      questions: [
        { type: 'choice', label: 'Pick one', q: 'Which department should handle this request?',
          options: ['billing', 'technical', 'sales', 'other'],
          probs: { billing: 0.9829, technical: 0.0114, sales: 0.0022, other: 0.0035 }, top: 0.9829 },
        { type: 'score', label: 'Rate on a scale', q: 'How urgent is this request?',
          options: ['can wait', 'soon', 'today', 'blocking or at risk of churn'],
          probs: [0.0033, 0.0062, 0.7812, 0.2092], score: 2.1963, top: 0.7812 },
        { type: 'noul', label: 'Yes or no', q: 'Does the customer threaten to cancel or leave?',
          yes: 0.9926, top: 0.9926 },
      ],
    },
    {
      name: 'Server log',
      state: 'sshd[2291]: Failed password for root from 203.0.113.7 port 52144 ssh2. 312 failed attempts from this address in the last 60 seconds, followed by one successful login for user deploy.',
      latency: 105, tokens: 347,
      questions: [
        { type: 'noul', label: 'Yes or no', q: 'Is this a brute-force attack?', yes: 0.8941, top: 0.8941 },
        { type: 'score', label: 'Rate on a scale', q: 'How severe is this incident?',
          options: ['low', 'medium', 'high', 'critical'],
          probs: [0.0417, 0.257, 0.5457, 0.1556], score: 1.8152, top: 0.5457 },
        { type: 'choice', label: 'Pick one', q: 'What should happen next?',
          options: ['block the IP and page on-call', 'open a ticket', 'ignore'],
          probs: { 'block the IP and page on-call': 0.6052, 'open a ticket': 0.3231, ignore: 0.0717 }, top: 0.6052 },
      ],
    },
    {
      name: 'Supplier invoice',
      state: 'Invoice INV-2291 from Northwind Supplies: 48,200 USD for 400 office chairs, due in 3 days. No purchase order number is attached, and the bank details differ from the ones on file.',
      latency: 94, tokens: 346,
      questions: [
        { type: 'choice', label: 'Pick one', q: 'How should this invoice be handled?',
          options: ['approve for payment', 'send to finance review', 'reject'],
          probs: { 'approve for payment': 0.0255, 'send to finance review': 0.913, reject: 0.0615 }, top: 0.913 },
        { type: 'noul', label: 'Yes or no', q: 'Are there signs of payment fraud?', yes: 0.9502, top: 0.9502 },
        { type: 'noul', label: 'Yes or no', q: 'Is the invoice complete enough to pay?', yes: 0.0946, top: 0.9054 },
      ],
    },
  ],

  MAKERS: [
    { name: 'Supersonic Labs', logo: 'assets/logos/supersoniclabs-avatar.png' },
    { name: 'ConvAI Innovations', logo: 'assets/logos/convaiinnovations.svg' },
    { name: 'InternLM', logo: 'assets/logos/internlm.png' },
    { name: 'Jared Palmer', logo: 'assets/logos/jaredpalmer.png' },
    { name: 'Fastino', logo: 'assets/logos/fastino.png' },
    { name: 'Interfaze', logo: 'assets/logos/interfaze-ai.png' },
    { name: 'Contrastive LM, Stanford', logo: 'assets/logos/contrastive-lm.png' },
    { name: 'akhilaaa3', logo: 'assets/logos/akhilaaa3.svg' },
  ],

  MODELS: [
    { id: 'julia-1', name: 'Julia 1', maker: 'Supersonic Labs', logo: 'assets/logos/supersoniclabs-avatar.png',
      params: '144M', size: 0.61, mem: 0.8, speed: 'instant', badge: 'Smallest', reads: ['text'],
      langs: 'Multilingual, 52 locales tested', options: 20,
      tagline: 'The tiny, multilingual router.',
      metric: '73.2% on the typed-decisions benchmark (Jev: 72.7%)', summary: "A 144-million-parameter encoder (built on mmBERT-small) that reads your text and the answer options together and picks the best fit. Small enough to answer in milliseconds, and trained across many languages.",
      hf: 'SupersonicLabs/Julia-1' },
    { id: 'laya-multilingual', name: 'Laya Multilingual', maker: 'ConvAI Innovations', logo: 'assets/logos/convaiinnovations.svg',
      params: '322M', size: 0.68, mem: 1.0, speed: 'instant', badge: 'Most languages', reads: ['text'],
      langs: '100+ languages', options: 20,
      tagline: 'Decisions in 100+ languages.',
      metric: 'Usable in 45 of 51 languages tested (vs 23 for English Laya)', summary: "Laya's multilingual checkpoint on an mmBERT-base encoder. Each answer option gets its own marker token, so the model scores every option in one pass. Trained with RLCD, a reward that only pays off for honest probabilities.",
      hf: 'convaiinnovations/laya-multilingual' },
    { id: 'laya', name: 'Laya', maker: 'ConvAI Innovations', logo: 'assets/logos/convaiinnovations.svg',
      params: '421M', size: 0.85, mem: 1.2, speed: 'instant', badge: 'Most liked', reads: ['text'],
      langs: 'English', options: 20,
      tagline: 'The most popular open decision model.',
      metric: '0.86 on English XNLI; 39 ms per question on a T4', summary: "The English flagship of the Laya family: a ModernBERT-large encoder plus a small decision head with an act-or-escalate gate. The most-liked open alternative to TypeSafe's Jev.",
      hf: 'convaiinnovations/laya' },
    { id: 'laya-typed-decisions', name: 'Laya Typed-Decisions', maker: 'ConvAI Innovations', logo: 'assets/logos/convaiinnovations.svg',
      params: '421M', size: 0.85, mem: 1.2, speed: 'instant', badge: '', reads: ['text'],
      langs: 'English', options: 20,
      tagline: 'Laya, specialised for business workflows.',
      metric: '76.6% on typed-decisions (base Laya: 36.2%, Jev: 72.7%)', summary: "English Laya fine-tuned on four workflows: agent observability, invoice processing, security incidents and customer service. A good example of how much fine-tuning helps a small model.",
      hf: 'convaiinnovations/laya-typed-decisions' },
    { id: 'kev-0.5b', name: 'Kev 0.5B', maker: 'Jared Palmer', logo: 'assets/logos/jaredpalmer.png',
      params: '0.5B', size: 1.05, mem: 1.3, speed: 'instant', badge: '', reads: ['text'],
      langs: 'English', options: 255,
      tagline: 'The original Jev reconstruction, for learning.',
      metric: '79.9% on held-out data from its training sources', summary: "A small LoRA adapter and pointer head on Qwen2.5-0.5B that reproduces the architecture reverse-engineered from TypeSafe's Jev: the state is read once, every question branches off it in isolation, and a pointer picks among the options. Superseded by Kev 4B, but great for seeing the mechanism.",
      hf: 'jaredpalmer/kev-0.5b' },
    { id: 'gliner2.5-decide', name: 'GLiNER2.5 Decide', maker: 'Fastino', logo: 'assets/logos/fastino.png',
      params: '340M', size: 1.95, mem: 2.0, speed: 'instant', badge: 'CPU-friendly', reads: ['text'],
      langs: 'English', options: 64,
      tagline: 'Operational labels, fast enough for a CPU.',
      metric: '60.2% on fast-decisions across 17 domains (top of its board)', summary: "An encoder from the GLiNER family (famous for zero-shot entity extraction) trained to classify text against any label set you pass at call time. Several label sets are scored in one pass.",
      hf: 'fastino/GLiNER2.5-Decide' },
    { id: 'intern-decision-4b', name: 'Intern-Decision 4B', maker: 'InternLM (Shanghai AI Lab)', logo: 'assets/logos/internlm.png',
      params: '4B', size: 9.11, mem: 10.0, speed: 'fast', badge: 'Start here', reads: ['text', 'images'],
      langs: 'English and other major languages', options: 62,
      tagline: 'The best all-rounder. Also reads images.',
      metric: '90.0 average on its benchmark suite (Jev: 88.7)', summary: "Qwen3.5-4B fine-tuned end to end for structured decisions. It writes your questions into a JSON skeleton and reads every answer's probabilities out of a single forward pass. You can attach up to 8 images.",
      hf: 'internlm/Intern-Decision-4B' },
    { id: 'kev-4b', name: 'Kev 4B', maker: 'Jared Palmer', logo: 'assets/logos/jaredpalmer.png',
      params: '4B', size: 9.5, mem: 9.5, speed: 'fast', badge: 'Most careful', reads: ['text'],
      langs: 'English', options: 255,
      tagline: 'Carefully evaluated, honest about its limits.',
      metric: '83.8% out-of-domain on a locked test; 0.9% confident errors (Jev: 3.7%)', summary: "The current Kev: a LoRA adapter and pointer head on Qwen3.5-4B-Base, released only after pre-registered tests. Every question runs as its own branch off the shared state, so questions can never influence each other. Ships with a fitted calibration temperature.",
      hf: 'jaredpalmer/kev-4b' },
    { id: 'lev', name: 'Lev', maker: 'Interfaze', logo: 'assets/logos/interfaze-ai.png',
      params: '4B', size: 9.54, mem: 9.5, speed: 'fast', badge: 'Most options', reads: ['text'],
      langs: 'English', options: 500,
      tagline: 'Hundreds of options, no problem.',
      metric: '68.9% macro on all 13 S1Bench subsets; 98% on 77 banking intents', summary: "A LoRA adapter on Qwen3.5-4B that gives every option a one-token code and reads the answer straight from the model's next-token scores, averaging two option orders to cancel position bias. Past the token limit a learned head takes over, so option lists can run into the hundreds.",
      hf: 'interfaze-ai/lev' },
    { id: 'clm-v0.1-8b', name: 'CLM 8B', maker: 'Contrastive LM (Stanford)', logo: 'assets/logos/contrastive-lm.png',
      params: '8B', size: 16.48, mem: 17.0, speed: 'moderate', badge: 'Best for agents', reads: ['text'],
      langs: 'English', options: 1000,
      tagline: 'Matches situations to actions by meaning.',
      metric: 'On par with Jev on computer use, gaming and tool calling, with up to 9x lower latency', summary: "A different design: a frozen Qwen3-8B turns the state and each option into a vector, and two small heads trained with a contrastive objective measure how well they fit. Because options are encoded separately, it scales to very long candidate lists.",
      hf: 'Contrastive-LM/CLM-v0.1-8B' },
    { id: 'jev-omni', name: 'Jev-Omni', maker: 'akhilaaa3', logo: 'assets/logos/akhilaaa3.svg',
      params: '12B', size: 23.96, mem: 26.0, speed: 'heavy', badge: 'Multimodal', reads: ['text', 'images', 'audio', 'video'],
      langs: 'English', options: 256, gpu: true,
      tagline: 'Sees images, hears audio, watches video.',
      metric: '87.6% on DecisionBench Medium; calibration error 0.04', summary: "Gemma 4 12B with a decision head, trained on 30,000 questions. Give it a photo, a sound clip or a short video along with your question, and it returns a probability for each option instead of a description.",
      hf: 'akhilaaa3/Jev-Omni' },
  ],

  RELEASE_FALLBACK: {
    tag: 'v0.2.1', date: '2026-09-30',
    assets: [
      ['Bud.Decision.Studio_0.2.1_aarch64.dmg', 20456500],
      ['Bud.Decision.Studio_0.2.1_x64-setup.exe', 14544173],
      ['Bud.Decision.Studio_0.2.1_x64_en-US.msi', 21262336],
      ['Bud.Decision.Studio_0.2.1_amd64.deb', 23715346],
      ['Bud.Decision.Studio-0.2.1-1.x86_64.rpm', 23710404],
      ['Bud.Decision.Studio_0.2.1_amd64.AppImage', 99822072],
      ['Bud.Decision.Studio_0.2.1_arm64.deb', 23034778],
      ['Bud.Decision.Studio-0.2.1-1.aarch64.rpm', 23026233],
      ['Bud.Decision.Studio_0.2.1_aarch64.AppImage', 97167880],
    ],
  },

  TOUR: [
    { id: 'demo', title: 'Demo', video: true,
      body: 'The whole flow in thirty seconds: choose an example, press Decide, read every answer as a chart, then open the model library.' },
    { id: 'playground', title: 'Playground', img: 'assets/img/playground.webp',
      alt: 'Playground: a support email, three questions, and each answer drawn as a chart with its probability',
      body: 'Write a situation and questions, press Decide, and read each answer as a chart. Ctrl+L loads any model; the JSON and Code tabs show the exact request.' },
    { id: 'templates', title: 'Templates', img: 'assets/img/templates.webp',
      alt: 'Templates: the Support triage template with its variables, questions, default model and settings, and the call to run it',
      body: 'Save the questions you ask often as a template, then run it with new details from the app or one API call. Every change is a new version you can compare, promote and roll back.' },
    { id: 'models', title: 'Models', img: 'assets/img/models.webp',
      alt: 'Models: a table of eleven open models with size, memory and published results next to Jev',
      body: 'Eleven open models in one table: what each is good at, its size, what it reads, and its published results next to Jev. Download, load and eject with one click.' },
    { id: 'evaluate', title: 'Evaluate', img: 'assets/img/evaluate.webp',
      alt: 'Evaluate: two models scored on labelled examples, with calibration and act-threshold charts',
      body: 'Run one question over many labelled examples on several models at once, and get a leaderboard, calibration and threshold charts, and a recommendation.' },
    { id: 'history', title: 'History', img: 'assets/img/history.webp',
      alt: 'History: every decision on a timeline, with what acted automatically, what asked a human, and each answer as a chart',
      body: 'Every decision from the app, your code and any SDK: what the model saw, its answers, and which ones needed a person. Search, filter, label the right answers and rerun.' },
    { id: 'api', title: 'API', img: 'assets/img/api.webp',
      alt: 'API page: the server address and ready-to-run examples in curl, Python and JavaScript',
      body: 'The server address and ready-to-run examples in curl, Python, JavaScript and the official TypeSafe SDKs, filled in with the model you have loaded.' },
  ],

  CODE: {
    curl: `curl -s http://127.0.0.1:8420/v1/systemone \\
  -H 'content-type: application/json' -d '{
  "model": "intern-decision-4b",
  "state": "Hi, we were billed twice for March on invoice #4411. Please refund the duplicate today or we will cancel our plan. This is the second time this has happened.",
  "questions": {
    "department": {"type": "choice",
      "instructions": "Which department should handle this request?",
      "criteria": {"billing": "invoices, payments, refunds",
                   "technical": "bugs, outages, system errors",
                   "sales": "pricing, upgrades, new contracts",
                   "other": "anything else"}},
    "churn": {"type": "noul",
      "instructions": "Does the customer threaten to cancel or leave?"}
  }
}'`,
    python: `# pip install typesafe-sdk
from typesafe_sdk import TypeSafeClient, Noul

client = TypeSafeClient(api_key="local",
                        base_url="http://127.0.0.1:8420",
                        model="intern-decision-4b")

res = client.system_one(
    state="Hi, we were billed twice for March on invoice #4411...",
    questions={"churn": Noul(
        instructions="Does the customer threaten to cancel or leave?")})

print(res.answers["churn"].noul)   # the probability of yes`,
    js: `const res = await fetch("http://127.0.0.1:8420/v1/systemone", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    model: "intern-decision-4b",
    state: "Hi, we were billed twice for March on invoice #4411...",
    questions: {
      churn: { type: "noul",
        instructions: "Does the customer threaten to cancel or leave?" },
    },
  }),
});

const { answers } = await res.json();
console.log(answers.churn.noul);   // the probability of yes`,
  },

  RESPONSE: `{
  "model": "intern-decision-4b",
  "answers": {
    "department": {
      "type": "choice",
      "choice": "billing",
      "probabilities": {
        "billing": 0.9856,
        "technical": 0.0115,
        "sales": 0.0013,
        "other": 0.0016
      },
      "confidence": 0.9808
    },
    "churn": {
      "type": "noul",
      "noul": 0.9853
    }
  },
  "usage": { "input_tokens": 287, "output_tokens": 0 }
}`,
};
