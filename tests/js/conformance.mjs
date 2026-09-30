// Runs the official TypeSafe JavaScript SDK (@typesafe-ai/sdk 0.6.0) against Bud Decision Studio.
// Prints one JSON line with what the SDK returned; tests/test_conformance.py asserts on it.
import { TypeSafeClient, choice, noul, score, NotFoundError } from "@typesafe-ai/sdk";

const baseURL = process.env.BASAL_TEST_URL || "http://127.0.0.1:8420";
const model = process.env.BASAL_TEST_MODEL || "laya";
const client = new TypeSafeClient({ apiKey: "local", baseURL, defaultModel: model, timeout: 120000, logLevel: "error" });

const out = {};
try {
  const { data, requestId } = await client.systemOne({
    state: "Hi, I was charged twice for my order and I want a refund today.",
    questions: {
      team: choice("Which team should handle this?", { billing: "payments, refunds", technical: "bugs, crashes", other: null }),
      urgency: score("How urgent is it?", ["low", "medium", "high"]),
      refund: noul("Does the customer ask for a refund?"),
    },
  }).withResponse();
  out.model = data.model;
  out.requestId = requestId;
  out.teamChoice = data.answers.team.choice;
  out.teamProbKeys = Object.keys(data.answers.team.probabilities).sort();
  out.urgencyScore = data.answers.urgency.score;
  out.refundNoul = data.answers.refund.noul;
  out.usage = data.usage;
  const models = await client.models.list();
  out.modelNames = models.map((m) => m.name);
  try {
    await client.systemOne({ model: "no-such-model", state: "x", questions: { q: noul("ok?") } });
    out.unknownModelError = "none";
  } catch (e) {
    out.unknownModelError = e instanceof NotFoundError ? "NotFoundError" : e.constructor.name;
  }
  out.ok = true;
} catch (e) {
  out.ok = false;
  out.error = `${e.constructor.name}: ${e.message}`;
}
console.log(JSON.stringify(out));
