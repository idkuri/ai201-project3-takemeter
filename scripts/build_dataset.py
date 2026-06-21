"""Build and validate the labeled dataset CSV for Colab upload.

Usage:
  # Create a blank CSV from collected post texts (manual labeling in Sheets/Excel)
  python scripts/build_dataset.py from-json --input data/unlabeled_posts.json

  # Check row count and label distribution before training
  python scripts/build_dataset.py validate --input data/labeled_posts.csv

  # Normalize column names and write the standard output file
  python scripts/build_dataset.py export --input data/labeled_posts_export.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "unlabeled_posts.json"
DEFAULT_OUT = ROOT / "data" / "labeled_posts.csv"
LABELS = ("analysis", "hot_take", "reaction", "question")
MIN_ROWS = 200
MAX_LABEL_SHARE = 0.70


def normalize(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<"))
    return text.strip()[:1500]


def load_texts(path: Path, limit: int | None) -> list[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a JSON array of strings")

    texts: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        t = normalize(item)
        if len(t) < 40 or "[removed]" in t.lower() or "automoderator" in t.lower():
            continue
        key = t[:100].lower()
        if key in seen:
            continue
        seen.add(key)
        texts.append(t)
        if limit and len(texts) >= limit:
            break
    return texts


def read_labeled_csv(path: Path) -> list[dict]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if not rows:
        raise SystemExit(f"{path} is empty")

    fieldnames = {k.lower() for k in rows[0].keys()}
    if "text" not in fieldnames or "label" not in fieldnames:
        raise SystemExit(f"{path} must have text and label columns")

    normalized = []
    for r in rows:
        text = (r.get("text") or "").strip()
        label = (r.get("label") or "").strip()
        notes = (r.get("notes") or "").strip()
        if not text:
            continue
        normalized.append({"text": text, "label": label, "notes": notes})
    return normalized


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["text", "label", "notes"])
        w.writeheader()
        w.writerows(rows)


def cmd_from_json(args: argparse.Namespace) -> None:
    if not args.input.exists():
        raise SystemExit(f"Input not found: {args.input}")

    texts = load_texts(args.input, args.limit)
    if not texts:
        raise SystemExit(f"No usable texts in {args.input}")

    rows = [{"text": t, "label": "", "notes": ""} for t in texts]
    write_csv(args.output, rows)
    print(f"Wrote {len(rows)} rows -> {args.output}")
    print("Fill label and notes columns manually, then run validate.")


def cmd_validate(args: argparse.Namespace) -> None:
    if not args.input.exists():
        raise SystemExit(f"Input not found: {args.input}")

    rows = read_labeled_csv(args.input)
    labeled = [r for r in rows if r["label"]]
    missing = len(rows) - len(labeled)
    dist = Counter(r["label"] for r in labeled)
    unknown = [lb for lb in dist if lb not in LABELS]

    print(f"File: {args.input}")
    print(f"Total rows: {len(rows)}")
    print(f"Labeled rows: {len(labeled)}")
    if missing:
        print(f"WARNING: {missing} rows missing label")

    print(f"Distribution: {dict(dist)}")
    if unknown:
        print(f"WARNING: unknown labels: {unknown}")

    if len(labeled) < MIN_ROWS:
        print(f"FAIL: need at least {MIN_ROWS} labeled rows (have {len(labeled)})")
        sys.exit(1)

    for lb, count in dist.items():
        share = count / len(labeled)
        if share > MAX_LABEL_SHARE:
            print(f"WARNING: {lb} is {share:.0%} of dataset (>70% imbalance)")

    print("OK: dataset meets minimum row count")


def cmd_export(args: argparse.Namespace) -> None:
    if not args.input.exists():
        raise SystemExit(f"Input not found: {args.input}")

    rows = read_labeled_csv(args.input)
    write_csv(args.output, rows)
    dist = dict(Counter(r["label"] for r in rows if r["label"]))
    print(f"Wrote {len(rows)} rows -> {args.output}")
    print(f"Distribution: {dist}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and validate labeled_posts.csv")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("from-json", help="Create blank CSV from unlabeled JSON posts")
    create.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    create.add_argument("--output", type=Path, default=DEFAULT_OUT)
    create.add_argument("--limit", type=int, default=200)
    create.set_defaults(func=cmd_from_json)

    validate = sub.add_parser("validate", help="Check row count and label balance")
    validate.add_argument("--input", type=Path, default=DEFAULT_OUT)
    validate.set_defaults(func=cmd_validate)

    export = sub.add_parser("export", help="Write normalized labeled CSV")
    export.add_argument("--input", type=Path, required=True)
    export.add_argument("--output", type=Path, default=DEFAULT_OUT)
    export.set_defaults(func=cmd_export)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
