"""The models Bud Decision Studio knows how to download, load and run.

Every entry pairs the technical facts the runtime needs (Hugging Face repo, base
model, which adapter drives it) with the plain-language copy the UI shows to
people who have never used a decision model before. Facts come from each
model's own card on Hugging Face; numbers are the publisher's, not ours.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Repo:
    """One Hugging Face repository to fetch, with optional include/exclude file patterns."""
    id: str
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    size_gb: float = 0.0          # fallback when the Hub is unreachable
    likes: int = 0                # fallback when the Hub is unreachable


@dataclass(frozen=True)
class ModelSpec:
    id: str                       # stable local id, also the `model` field in API requests
    name: str
    maker: str
    adapter: str                  # which basal.adapters module runs it
    repo: Repo
    base: Repo | None = None      # frozen backbone downloaded alongside an adapter
    params: str = ""
    memory_gb: float = 1.0        # rough GPU memory once loaded (bf16 unless noted)
    tagline: str = ""
    summary: str = ""
    good_for: tuple[str, ...] = ()
    watch_out: tuple[str, ...] = ()
    languages: str = "English"
    modalities: tuple[str, ...] = ("text",)
    types: tuple[str, ...] = ("choice", "score", "noul")
    max_options: int = 20
    max_questions: int = 64
    context_tokens: int = 8192
    one_pass: bool = True         # all questions answered in one forward pass
    speed: str = "fast"           # instant | fast | moderate | heavy
    badge: str = ""
    headline_metric: str = ""
    license: str = "Apache-2.0"
    needs_gpu: bool = False       # too large or GPU-only code; never offered on the CPU
    # Devices the model's own library can run on, as PyTorch names them ("cuda", "mps", "xpu", "cpu"); empty: any.
    # Jev-Omni's loader refuses everything but an NVIDIA GPU, and Lev's puts the model on one or else on the processor.
    devices: tuple[str, ...] = ()
    load_options: tuple[dict, ...] = field(default_factory=tuple)
    # Fine-tuned models (basal/finetunes.py): the released model this one was trained from, the folder holding the
    # trained delta, and the calibration temperature per question type fitted by the trainer.
    base_id: str = ""
    finetune_dir: str = ""
    temperature: tuple[tuple[str, float], ...] = ()

    def repos(self) -> list[Repo]:
        return [self.repo] + ([self.base] if self.base else [])

    def options(self) -> list[dict]:
        """Load options with "Run on" filled in from this computer's devices (see config.py)."""
        from .config import device_option
        out = []
        for o in self.load_options:
            if o["key"] == "device":
                o = device_option()
                if self.needs_gpu:
                    gpus = [c for c in o["choices"] if c[0] != "cpu"]
                    o = {**o, "choices": gpus or o["choices"], "default": gpus[0][0] if gpus else o["default"]}
                if self.devices:      # only where its library runs; the default moves there too (Lev on an Intel GPU: CPU)
                    can = [c for c in o["choices"] if c[0] in self.devices]
                    if can:
                        o = {**o, "choices": can, "default": o["default"] if o["default"] in self.devices else can[0][0]}
            out.append(o)
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["load_options"] = self.options()
        from .config import fit
        d["fit"] = fit(self.memory_gb, self.needs_gpu, self.devices)
        d["hf_url"] = f"https://huggingface.co/{self.repo.id}"
        return d


# Load-time options the UI exposes under "Advanced". Each is passed to the worker
# as a plain value; adapters read only the keys they understand.
# The device choices differ per computer, so this is a placeholder that ModelSpec.options() replaces.
DEVICE = {"key": "device", "label": "Run on", "type": "select", "default": "", "choices": []}
DTYPE = {"key": "dtype", "label": "Number precision", "type": "select", "default": "bf16",
         "choices": [["bf16", "bfloat16 (recommended)"], ["fp32", "float32 (exact, 2x memory)"]],
         "help": "How many bits each weight uses. bfloat16 halves memory and runs faster with nearly identical answers."}


def _ctx(default: int, maximum: int) -> dict:
    return {"key": "max_length", "label": "Max input length (tokens)", "type": "number", "default": default,
            "min": 128, "max": maximum, "step": 128,
            "help": "The longest state + question the model will accept. Longer costs memory and time; a token is roughly 3/4 of a word."}


CATALOG: list[ModelSpec] = [
    ModelSpec(
        id="julia-1", name="Julia 1", maker="Supersonic Labs", adapter="julia",
        repo=Repo("SupersonicLabs/Julia-1", exclude=("assets/*",), size_gb=0.61, likes=290),
        params="144M", memory_gb=0.8, speed="instant",
        tagline="The tiny, multilingual router.",
        summary="A 144-million-parameter encoder (built on mmBERT-small) that reads your text and the answer options together "
                "and picks the best fit. Small enough to answer in milliseconds, and trained across many languages.",
        good_for=("Routing tickets, emails or chats to the right team", "Topic and emotion tagging",
                  "Quick yes/no checks grounded in the text you give it", "Non-English text"),
        watch_out=("Accepts 2 to 20 options per question", "Can't supply facts that aren't in your text",
                   "Weak at arithmetic and multi-step reasoning", "Long label lists hurt accuracy (64% on 72 banking intents)"),
        languages="Multilingual (52 locales tested)", max_options=20, context_tokens=8192,
        headline_metric="73.2% on the typed-decisions benchmark (Jev: 72.7%)",
        badge="Smallest",
        load_options=(DEVICE, _ctx(8192, 8192)),
    ),
    ModelSpec(
        id="laya-multilingual", name="Laya Multilingual", maker="ConvAI Innovations", adapter="laya",
        repo=Repo("convaiinnovations/laya-multilingual", size_gb=0.68, likes=326),
        params="322M", memory_gb=1.0, speed="instant",
        tagline="Decisions in 100+ languages.",
        summary="Laya's multilingual checkpoint on an mmBERT-base encoder. Each answer option gets its own marker token, "
                "so the model scores every option in one pass. Trained with RLCD, a reward that only pays off for honest probabilities.",
        good_for=("Customer messages in any language", "Guardrails and moderation", "Intent routing across locales"),
        watch_out=("Zero-shot accuracy is modest; it shines after fine-tuning on your data",
                   "Ships over-confident: fit a calibration temperature before trusting thresholds",
                   "Practical limit is about 20 options"),
        languages="100+ languages", max_options=20, context_tokens=1024,
        headline_metric="Usable in 45 of 51 languages tested (vs 23 for English Laya)",
        badge="Most languages",
        load_options=(DEVICE, _ctx(1024, 8192)),
    ),
    ModelSpec(
        id="laya", name="Laya", maker="ConvAI Innovations", adapter="laya",
        repo=Repo("convaiinnovations/laya", exclude=("multilingual/*", "typed-decisions/*", "assets/*", "eval/*"),
                  size_gb=0.85, likes=4491),
        params="421M", memory_gb=1.2, speed="instant",
        tagline="The most popular open decision model.",
        summary="The English flagship of the Laya family: a ModernBERT-large encoder plus a small decision head with an "
                "act-or-escalate gate. The most-liked open alternative to TypeSafe's Jev.",
        good_for=("English email and ticket triage", "Guardrails and content moderation", "Fast yes/no checks"),
        watch_out=("Fails confidently on non-Latin scripts (use Laya Multilingual for those)",
                   "Zero-shot accuracy is close to chance on unfamiliar tasks; best after fine-tuning",
                   "512-token context: long documents get cut off"),
        languages="English", max_options=20, context_tokens=512,
        headline_metric="0.86 on English XNLI; 39 ms per question on a T4",
        badge="Most liked",
        load_options=(DEVICE, _ctx(512, 512)),
    ),
    ModelSpec(
        id="laya-typed-decisions", name="Laya Typed-Decisions", maker="ConvAI Innovations", adapter="laya",
        repo=Repo("convaiinnovations/laya-typed-decisions", size_gb=0.85, likes=131),
        params="421M", memory_gb=1.2, speed="instant",
        tagline="Laya, specialised for business workflows.",
        summary="English Laya fine-tuned on four workflows: agent observability, invoice processing, security incidents "
                "and customer service. A good example of how much fine-tuning helps a small model.",
        good_for=("Invoice and document processing decisions", "Security incident triage",
                  "Customer-service routing", "Checking what an AI agent did"),
        watch_out=("Strongest inside its four trained workflows", "English only"),
        languages="English", max_options=20, context_tokens=1024,
        headline_metric="76.6% on typed-decisions (base Laya: 36.2%, Jev: 72.7%)",
        load_options=(DEVICE, _ctx(1024, 1024)),
    ),
    ModelSpec(
        id="kev-0.5b", name="Kev 0.5B", maker="Jared Palmer", adapter="kev",
        repo=Repo("jaredpalmer/kev-0.5b", size_gb=0.05, likes=59),
        base=Repo("Qwen/Qwen2.5-0.5B", size_gb=1.0, likes=460),
        params="0.5B", memory_gb=1.3, speed="instant",
        tagline="The original Jev reconstruction, for learning.",
        summary="A small LoRA adapter and pointer head on Qwen2.5-0.5B that reproduces the architecture reverse-engineered "
                "from TypeSafe's Jev: the state is read once, every question branches off it in isolation, and a pointer "
                "picks among the options. Superseded by Kev 4B, but great for seeing the mechanism.",
        good_for=("Learning how decision models work", "Banking intents, news topics, review ratings",
                  "Up to 255 options per question"),
        watch_out=("A prototype trained on six datasets: weak outside them", "English only",
                   "Mildly over-confident before temperature scaling"),
        languages="English", max_options=255, context_tokens=8192,
        headline_metric="79.9% on held-out data from its training sources",
        load_options=(DEVICE, DTYPE),
    ),
    ModelSpec(
        id="gliner2.5-decide", name="GLiNER2.5 Decide", maker="Fastino", adapter="gliner",
        repo=Repo("fastino/GLiNER2.5-Decide", size_gb=1.95, likes=240),
        params="340M", memory_gb=2.0, speed="instant",
        tagline="Operational labels, fast enough for a CPU.",
        summary="An encoder from the GLiNER family (famous for zero-shot entity extraction) trained to classify text against "
                "any label set you pass at call time. Several label sets are scored in one pass.",
        good_for=("Intent, sentiment and topic labels", "Document-type detection", "Urgency, spam and moderation flags",
                  "Air-gapped or CPU-only setups"),
        watch_out=("Classifies; does not reason or explain", "Can over-trigger on keywords outside its domains",
                   "Question wording matters less than label names"),
        languages="English", max_options=64, context_tokens=2048,
        headline_metric="60.2% on fast-decisions across 17 domains (top of its board)",
        badge="CPU-friendly",
        load_options=(DEVICE,),
    ),
    ModelSpec(
        id="intern-decision-4b", name="Intern-Decision 4B", maker="InternLM (Shanghai AI Lab)", adapter="intern",
        repo=Repo("internlm/Intern-Decision-4B", size_gb=9.11, likes=67),
        params="4B", memory_gb=10.0, speed="fast",
        tagline="The best all-rounder. Also reads images.",
        summary="Qwen3.5-4B fine-tuned end to end for structured decisions. It writes your questions into a JSON skeleton "
                "and reads every answer's probabilities out of a single forward pass. You can attach up to 8 images.",
        good_for=("General-purpose decisions when you don't know where to start", "Questions about screenshots, photos or charts",
                  "Tool selection and agent routing", "Jailbreak and safety screening"),
        watch_out=("1 to 16 questions per request, up to 62 options each", "Needs about 10 GB of GPU memory"),
        languages="English (and other major languages)", modalities=("text", "image"),
        max_options=62, max_questions=16, context_tokens=8192,
        headline_metric="90.0 average on its benchmark suite (Jev: 88.7)",
        badge="Start here",
        load_options=(DEVICE, _ctx(8192, 8192)),
    ),
    ModelSpec(
        id="kev-4b", name="Kev 4B", maker="Jared Palmer", adapter="kev",
        repo=Repo("jaredpalmer/kev-4b", size_gb=0.16, likes=81),
        base=Repo("Qwen/Qwen3.5-4B-Base", size_gb=9.34, likes=105),
        params="4B", memory_gb=9.5, speed="fast",
        tagline="Carefully evaluated, honest about its limits.",
        summary="The current Kev: a LoRA adapter and pointer head on Qwen3.5-4B-Base, released only after pre-registered "
                "tests. Every question runs as its own branch off the shared state, so questions can never influence each other. "
                "Ships with a fitted calibration temperature.",
        good_for=("Long policy documents with exceptions and limits", "Developer-tooling decisions (code review, commits)",
                  "Choices with many options (up to 255)", "Cases where 'I can't tell' matters: it rarely claims certainty on unknowable items"),
        watch_out=("Date arithmetic is weak unless you turn on 'date facts'", "Knowledge questions trail Jev (MMLU 0.70 vs 0.90)",
                   "English only"),
        languages="English", max_options=255, context_tokens=8192,
        headline_metric="83.8% out-of-domain on a locked test; 0.9% confident errors (Jev: 3.7%)",
        badge="Most careful",
        load_options=(DEVICE, DTYPE,
                      {"key": "date_facts", "label": "Date facts", "type": "toggle", "default": False,
                       "help": "Before the model reads your text, spell out the number of days between every pair of dates in it. "
                               "Kev can't subtract dates reliably but uses a stated day count well (deadline accuracy 0.60 → 0.85)."}),
    ),
    ModelSpec(
        id="lev", name="Lev", maker="Interfaze", adapter="lev",
        repo=Repo("interfaze-ai/lev", exclude=("assets/*",), size_gb=0.20, likes=97),
        base=Repo("Qwen/Qwen3.5-4B", size_gb=9.34, likes=980),
        params="4B", memory_gb=9.5, speed="fast",
        tagline="Hundreds of options, no problem.",
        summary="A LoRA adapter on Qwen3.5-4B that gives every option a one-token code and reads the answer straight from the "
                "model's next-token scores, averaging two option orders to cancel position bias. Past the token limit a learned "
                "head takes over, so option lists can run into the hundreds.",
        good_for=("Intent detection with very large label sets (151 intents at 96.8%)", "Natural-language inference and fact checks",
                  "Moderation and safety screening"),
        watch_out=("Weak on one-word-difference comparisons and fine 5-level ratings", "English only"),
        languages="English", max_options=500, context_tokens=8192,
        headline_metric="68.9% macro on all 13 S1Bench subsets; 98% on 77 banking intents",
        badge="Most options", devices=("cuda", "cpu"),
        load_options=(DEVICE,),
    ),
    ModelSpec(
        id="clm-v0.1-8b", name="CLM 8B", maker="Contrastive LM (Stanford)", adapter="clm",
        repo=Repo("Contrastive-LM/CLM-v0.1-8B", exclude=("assets/*",), size_gb=0.08, likes=519),
        base=Repo("Qwen/Qwen3-8B", size_gb=16.40, likes=2061),
        params="8B", memory_gb=17.0, speed="moderate",
        tagline="Matches situations to actions by meaning.",
        summary="A different design: a frozen Qwen3-8B turns the state and each option into a vector, and two small heads "
                "trained with a contrastive objective measure how well they fit. Because options are encoded separately, "
                "it scales to very long candidate lists.",
        good_for=("Picking the next action for an agent (tools, moves, clicks)", "Ranking many candidates, like best-of-N answers",
                  "Tool-call selection"),
        watch_out=("Probabilities are relative to the options you give", "Heaviest text-only model here (about 17 GB)",
                   "Best verifier results need its fine-tuned heads, not this zero-shot checkpoint"),
        languages="English", max_options=1000, context_tokens=2048,
        headline_metric="On par with Jev on computer-use, gaming and tool-calling, up to 9x lower latency",
        badge="Best for agents",
        load_options=(DEVICE,),
    ),
    ModelSpec(
        id="jev-omni", name="Jev-Omni", maker="akhilaaa3", adapter="jev_omni",
        repo=Repo("akhilaaa3/Jev-Omni", exclude=("assets/*",), size_gb=23.96, likes=310),
        params="12B", memory_gb=26.0, speed="heavy", one_pass=False,
        tagline="Sees images, hears audio, watches video.",
        summary="Gemma 4 12B with a decision head, trained on 30,000 questions. Give it a photo, a sound clip or a short video "
                "along with your question, and it returns a probability for each option instead of a description.",
        good_for=("Questions about images, audio clips (up to 30 s) and videos (16 frames)", "Hard text decisions where accuracy matters most"),
        watch_out=("Needs an NVIDIA GPU: its own loader does not run on Apple or Intel GPUs",
                   "Answers one question per pass, so many questions take longer", "Biggest model here: about 24 GB download and 26 GB of memory",
                   "Best with 20 options or fewer"),
        languages="English", modalities=("text", "image", "audio", "video"), max_options=256, context_tokens=8192,
        headline_metric="87.6% on DecisionBench Medium; calibration error 0.04",
        badge="Multimodal", needs_gpu=True, devices=("cuda",),
        load_options=(DEVICE,),
    ),
]

# A deterministic stand-in for tests and CI (basal/adapters/fake_adapter.py): never shown otherwise.
if __import__("os").environ.get("BASAL_FAKE_MODEL") == "1":
    CATALOG.append(ModelSpec(
        id="fake-decider", name="Fake Decider", maker="Bud Decision Studio tests", adapter="fake",
        repo=Repo("basal-tests/fake-decider"), params="0", memory_gb=0.0, speed="instant",
        tagline="Deterministic answers for tests.", summary="Probabilities come from a hash of the input; loads instantly.",
        languages="Any", modalities=("text", "image", "audio", "video"), max_options=1000, max_questions=256,
        context_tokens=100000, license="Apache-2.0"))

# Models fine-tuned on this computer (basal/training), listed after the model each was trained from.
try:
    from .finetunes import derived_specs as _derived
    CATALOG.extend(_derived(CATALOG))
except Exception as _e:  # noqa: BLE001 - a damaged fine-tune folder must never stop the studio from starting
    print(f"fine-tunes not listed: {_e}")

BY_ID = {m.id: m for m in CATALOG}
