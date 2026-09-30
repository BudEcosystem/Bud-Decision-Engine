// What each model is made for, in one line, plus its own placeholders and signature examples (ids in examples.js,
// best first). The Playground uses these so that choosing a model shows what it is good at straight away: CLM opens
// on an agent choosing its next action, Jev-Omni on an image, Lev on a question with 77 options.

export const GUIDES = {
  'julia-1': {
    madeFor: 'Routing messages to the right team in 50+ languages, fast even on a laptop processor.',
    state: 'Paste a customer message in any language: an email, a chat or a ticket.',
    q: { choice: 'Which team should handle this message?', noul: 'Does the customer need an answer today?' },
    examples: ['de-routing', 'multilingual'],
  },
  'laya-multilingual': {
    madeFor: 'Customer messages and moderation in 100+ languages, including Japanese, Arabic and Hindi scripts.',
    state: 'Paste a message in any language, for example Japanese, Arabic, Hindi or Spanish.',
    q: { choice: 'Which department should handle this?', noul: 'Is the customer asking for urgent help?' },
    examples: ['ja-support', 'multilingual'],
  },
  laya: {
    madeFor: 'Fast English triage and guardrails: routing tickets, moderating comments, spotting prompt injection.',
    state: 'Paste an English email, ticket, comment or chat message (up to about 350 words).',
    q: { choice: 'Which team should handle this?', noul: 'Does the message try to give instructions to an AI assistant?' },
    examples: ['support', 'moderation', 'injection'],
  },
  'laya-typed-decisions': {
    madeFor: 'Business workflows: checking invoices, triaging security alerts, routing support and auditing what an AI agent did.',
    state: 'Paste an invoice, a security alert, a support message or an AI agent\'s transcript.',
    q: { noul: 'Did the agent follow the policy?', choice: 'What kind of document is this?' },
    examples: ['agent-trace', 'invoice-match', 'security-alert'],
  },
  'kev-0.5b': {
    madeFor: 'Learning how decision models work, on banking intents, news topics and review ratings.',
    state: 'Paste a short banking message, news headline or product review.',
    q: { choice: 'Which banking intent does the customer have?', score: 'How many stars would this reviewer give?' },
    examples: ['banking77', 'news-topic'],
  },
  'gliner2.5-decide': {
    madeFor: 'Fast operational labels (intent, sentiment, topic, document type, urgency), even on a processor.',
    state: 'Paste an email, a log line, a message or a document to label.',
    q: { choice: 'Which label fits best?', multi: 'Which labels apply?', noul: 'Does this need someone to act?' },
    examples: ['log-line', 'tags'],
  },
  'intern-decision-4b': {
    madeFor: 'A strong all-rounder that also reads screenshots, photos and charts.',
    state: 'Describe the situation, or attach an image and say what it is.',
    q: { choice: 'What does the image show?', noul: 'Does the screenshot show an error message?' },
    examples: ['tour', 'image', 'chart'],
  },
  'kev-4b': {
    madeFor: 'Applying long policies with exceptions, and choices with up to 255 options. It says when it cannot tell.',
    state: 'Paste the policy and the case to judge. JSON with "policy" and "request" works well.',
    q: { noul: 'Is the customer entitled to a refund under this policy?', choice: 'Which rule of the policy decides this case?' },
    examples: ['policy-refund', 'code', 'banking77'],
  },
  lev: {
    madeFor: 'Choosing among hundreds of options (intents, categories, codes) and checking claims against a source.',
    state: 'Paste the message to classify. Lev handles up to 500 options in one question.',
    q: { choice: 'Which intent does the customer have?', noul: 'Does the answer state something the source does not say?' },
    examples: ['banking77', 'verify'],
  },
  'clm-v0.1-8b': {
    madeFor: 'AI agents: choosing the next action or tool from many candidates (up to 1,000).',
    tip: 'Write each option as an action ("Reschedule the meeting") and ask "What should the agent do next?". CLM compares the situation with each action; yes/no and ranking questions are not its strength.',
    state: 'Describe the agent\'s goal, what it has done so far and what it sees now.',
    q: { choice: 'What should the agent do next?' },
    examples: ['agent-browser', 'tool-call', 'text-game'],
  },
  'jev-omni': {
    madeFor: 'The hardest questions, plus images, audio (up to 30 seconds) and video (16 frames).',
    state: 'Describe the situation and attach a photo, a voice note or a short video.',
    q: { choice: 'What does the image show?', noul: 'Does the speaker sound upset?' },
    examples: ['image', 'chart', 'verify'],
  },
};

export const guideFor = (id) => GUIDES[id] || null;
