"""Writes site/release.json: the latest published release, for the website.

The product page and the docs read the latest release from GitHub in the visitor's browser (site/assets/js/release.js).
GitHub's API allows only 60 anonymous requests an hour per network address, so they also read this file, straight from
the repository. The release workflow runs this script the moment a release is published, and commits the result; nobody
has to edit a version number by hand.

    python scripts/release_info.py            # needs no login; GH_TOKEN or GITHUB_TOKEN is used when set
    python scripts/release_info.py --check    # exit 1 if site/release.json is not the latest release
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY") or "BudEcosystem/Bud-Decision-Engine"
OUT = Path(__file__).resolve().parent.parent / "site" / "release.json"


def latest() -> dict:
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "bud-decision-studio"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.load(r)
    assets = sorted(({"name": a["name"], "size": a["size"], "url": a["browser_download_url"]} for a in j.get("assets", [])),
                    key=lambda a: a["name"])
    if not j.get("tag_name") or not assets:
        raise SystemExit("The latest release has no tag or no files; site/release.json was left as it is.")
    return {"tag": j["tag_name"], "date": (j.get("published_at") or "")[:10], "html": j["html_url"], "assets": assets}


def main() -> int:
    text = json.dumps(latest(), indent=2) + "\n"
    if "--check" in sys.argv:
        same = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("site/release.json is the latest release" if same else "site/release.json is out of date")
        return 0 if same else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(OUT.parent.parent)}: {json.loads(text)['tag']}, {len(json.loads(text)['assets'])} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
