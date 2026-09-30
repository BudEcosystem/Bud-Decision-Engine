"""Download one Hugging Face repo into the standard cache. Run as a subprocess by basal.hub."""
import argparse

from huggingface_hub import snapshot_download


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--include", nargs="*", default=None)
    ap.add_argument("--exclude", nargs="*", default=None)
    a = ap.parse_args()
    path = snapshot_download(a.repo, allow_patterns=a.include or None, ignore_patterns=a.exclude or None)
    print(f"done: {a.repo} -> {path}", flush=True)


if __name__ == "__main__":
    main()
