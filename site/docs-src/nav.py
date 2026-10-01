"""The documentation's sections and pages, in reading order. Each slug is a Markdown file in pages/."""

SITE_NAME = "Bud Decision Studio"
VERSION = "0.2.1"

NAV = [
    ("Get started", [
        ("index", "Introduction"),
        ("install", "Installation"),
        ("quickstart", "Quickstart"),
    ]),
    ("User manual", [
        ("manual/index", "The app at a glance"),
        ("manual/playground", "Playground"),
        ("manual/templates", "Templates"),
        ("manual/history", "History"),
        ("manual/models", "Models"),
        ("manual/evaluate", "Evaluate"),
        ("manual/system", "API, Learn and System"),
    ]),
    ("Concepts", [
        ("concepts/decision-models", "Decision models"),
        ("concepts/question-types", "The six question types"),
        ("concepts/acting", "Probabilities, certainty and acting"),
        ("concepts/templates", "Templates and versions"),
        ("concepts/history", "History and privacy"),
    ]),
    ("Guides", [
        ("guides/support-triage", "Build a support-triage template"),
        ("guides/from-code", "Call the studio from your code"),
        ("guides/improve", "Improve a template safely"),
        ("guides/agents", "Let an agent choose its next action"),
        ("guides/media", "Decide about images, audio and video"),
        ("guides/migrate", "Move from Jev or a gateway"),
    ]),
    ("API reference", [
        ("api/index", "Overview"),
        ("api/decisions", "Decisions"),
        ("api/history", "History, feedback and statistics"),
        ("api/templates", "Templates"),
        ("api/examples", "Test examples"),
        ("api/resources", "Files, models and settings"),
        ("api/systemone", "TypeSafe and gateway formats"),
        ("api/errors", "Errors"),
    ]),
    ("Developers", [
        ("dev/architecture", "Architecture"),
        ("dev/source", "Run from source"),
        ("dev/adding-a-model", "Add a model"),
        ("dev/storage", "Data and storage"),
        ("dev/testing", "Testing"),
        ("dev/desktop", "Desktop app and releases"),
    ]),
    ("Help", [
        ("troubleshooting", "Troubleshooting"),
        ("faq", "FAQ"),
        ("changelog", "Changelog"),
    ]),
]
