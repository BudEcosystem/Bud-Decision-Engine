"""Generate ui/samples/support-tickets.csv: the example file the Train page offers to people without their own data.

Synthetic help-desk tickets for a fictional company. Each is routed to one of six teams and marked urgent or not. Many
tickets name the company's own systems ("Keystone" sign-in, "Ledger" invoicing, "Harbor" laptops...), which no
released model can know: exactly the kind of company knowledge fine-tuning adds. Deterministic (seeded), written for
this project, no third-party text.

    .venv/bin/python scripts/make_training_sample.py
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "ui" / "samples" / "support-tickets.csv"

TEAMS = {
    "identity": {"systems": ["Keystone", "the Keystone portal", "Keystone MFA"],
                 "problems": ["my password reset link expired", "I'm locked out after the security prompt",
                              "the two-step code never arrives", "my account shows the wrong manager",
                              "I can't get into anything since my name changed", "sign-in loops back to the start"]},
    "payments": {"systems": ["Ledger", "the Ledger invoice screen", "Tollgate"],
                 "problems": ["a customer was charged twice", "the refund hasn't gone out after ten days",
                              "invoice totals don't match the order", "the card terminal declines every payment",
                              "a vendor says we paid the wrong account", "the monthly payout report is empty"]},
    "devices": {"systems": ["Harbor", "my Harbor laptop", "the Harbor dock"],
                "problems": ["the screen flickers and goes black", "the battery dies in under an hour",
                             "the laptop won't turn on after the update", "the keyboard stopped typing some letters",
                             "the external monitor isn't detected", "the fan is loud and it's very hot"]},
    "network": {"systems": ["Beacon VPN", "the office Wi-Fi", "Beacon"],
                "problems": ["the connection drops every few minutes", "nothing loads when I'm at home",
                             "video calls freeze constantly", "I can't reach the shared drive from the branch office",
                             "the guest network asks for a code nobody has", "downloads crawl since this morning"]},
    "data": {"systems": ["Atlas", "the Atlas dashboard", "the nightly Atlas export"],
             "problems": ["the numbers stopped updating yesterday", "a chart shows last quarter twice",
                          "the scheduled report failed overnight", "I need access to the sales dataset",
                          "the export is missing half the rows", "a query that used to take seconds now times out"]},
    "people": {"systems": ["Compass", "the Compass HR portal", "Compass payroll"],
               "problems": ["my payslip shows the wrong hours", "I can't book my holiday dates",
                            "a new starter has no contract in the system", "my bank details didn't save",
                            "the benefits enrolment form won't submit", "my address change isn't showing"]},
}
URGENT = ["The whole team is blocked.", "Customers are affected right now.", "This stops us closing the month today.",
          "We have a client demo in an hour.", "Nobody on the floor can work.", "It's hitting every order since 9am."]
CALM = ["No rush, whenever you get a chance.", "It can wait until next week.", "Just flagging it.",
        "Only happens occasionally.", "I found a workaround for now.", ""]
OPENERS = ["Hi team,", "Hello,", "Hey,", "Good morning,", "", "Quick one:", "Hi there,"]
CLOSERS = ["Thanks!", "Thank you.", "Cheers,", "", "Appreciate it.", "Best,"]


def ticket(rng: random.Random, team: str, urgent: bool, name_system: bool) -> str:
    t = TEAMS[team]
    problem = rng.choice(t["problems"])
    if name_system:
        where = rng.choice([f"In {rng.choice(t['systems'])}, ", f"{rng.choice(t['systems'])}: ", f"Since this morning in {rng.choice(t['systems'])} "])
        body = where + problem + "."
    else:
        body = problem[:1].upper() + problem[1:] + "."
    tail = rng.choice(URGENT) if urgent else rng.choice(CALM)
    parts = [rng.choice(OPENERS), body, tail, rng.choice(CLOSERS)]
    return " ".join(p for p in parts if p).strip()


def main():
    rng = random.Random(7)
    rows = []
    for i in range(420):
        team = rng.choice(list(TEAMS))
        urgent = rng.random() < 0.35
        rows.append((ticket(rng, team, urgent, name_system=rng.random() < 0.75), team, "yes" if urgent else "no"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticket", "team", "urgent"])
        w.writerows(rows)
    print(OUT, len(rows), "rows")


if __name__ == "__main__":
    main()
