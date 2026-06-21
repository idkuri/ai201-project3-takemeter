"""Export or prune a review queue CSV for review_gui.py.

Usage:
  python scripts/export_review_queue.py export
  python scripts/export_review_queue.py prune
  python scripts/export_review_queue.py prune --dry-run
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from dataset_utils import DEFAULT_CSV, suggest_label

REDDIT_IN_TEXT = re.compile(r"https?://(?:www\.)?reddit\.com/\S+", re.I)
DEFAULT_QUEUE = ROOT / "data" / "review_queue.csv"
QUEUE_FIELDS = [
    "row_id",
    "priority",
    "text",
    "label",
    "suggested_label",
    "notes",
    "review_reason",
    "flag",
    "source_url",
]


def extract_reddit_url(text: str) -> str:
    match = REDDIT_IN_TEXT.search(text or "")
    return match.group(0).rstrip(".,;") if match else ""


def duplicate_info(rows: list[dict]) -> dict[int, str]:
    groups: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for i, r in enumerate(rows):
        groups[r["text"][:80].lower()].append((i, r))

    notes: dict[int, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        labels = {r["label"] for _, r in members}
        lens = [(i, len(r["text"])) for i, r in members]
        if len(labels) > 1:
            for i, _ in members:
                other = [r["label"] for j, r in members if j != i]
                notes[i] = f"label conflict with duplicate opening (other labels: {other})"
        else:
            shortest = min(lens, key=lambda x: x[1])
            for i, ln in lens:
                if i == shortest[0] and ln < max(l for _, l in lens) - 50:
                    notes[i] = "possible excerpt duplicate; consider removing after keeping full post"
    return notes


def queue_entry(row_id: int, row: dict, priority: int, reason: str, suggested: str) -> dict:
    return {
        "row_id": row_id,
        "priority": priority,
        "text": row["text"],
        "label": row["label"],
        "suggested_label": suggested,
        "notes": row.get("notes") or "",
        "review_reason": reason,
        "flag": "",
        "source_url": row.get("source_url") or extract_reddit_url(row.get("text", "")),
    }


def build_queue(rows: list[dict]) -> list[dict]:
    dup_notes = duplicate_info(rows)
    seen: dict[int, dict] = {}

    def add(entry: dict) -> None:
        rid = entry["row_id"]
        if rid not in seen or entry["priority"] < seen[rid]["priority"]:
            seen[rid] = entry
        elif entry["priority"] == seen[rid]["priority"]:
            seen[rid]["review_reason"] += "; " + entry["review_reason"]

    for i, row in enumerate(rows, 1):
        note = (row.get("notes") or "").strip()
        sug = suggest_label(row["text"]) or ""
        idx = i - 1

        if "replacement" in note or "rebalance" in note:
            add(queue_entry(i, row, 1, "replacement post; confirm label", sug))

        if idx in dup_notes:
            pri = 2 if "conflict" in dup_notes[idx] else 3
            add(queue_entry(i, row, pri, dup_notes[idx], sug))

        if sug and sug != row["label"]:
            add(queue_entry(i, row, 4, f"heuristic hint: looks more like {sug}", sug))

    return sorted(seen.values(), key=lambda x: (x["priority"], x["row_id"]))


def is_reviewed(dataset_row: dict) -> bool:
    return "reviewed" in (dataset_row.get("notes") or "").lower()


def cmd_export(args: argparse.Namespace) -> None:
    rows = list(csv.DictReader(args.input.open(encoding="utf-8")))
    queue = build_queue(rows)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=QUEUE_FIELDS)
        w.writeheader()
        w.writerows(queue)

    by_pri = defaultdict(int)
    for q in queue:
        by_pri[q["priority"]] += 1

    print(f"Wrote {len(queue)} rows -> {args.output}")
    print("Priority 1 (replacements):", by_pri[1])
    print("Priority 2-3 (duplicates):", by_pri[2] + by_pri[3])
    print("Priority 4 (heuristic hints):", by_pri[4])
    print()
    print("Run: python scripts/review_gui.py")


def cmd_prune(args: argparse.Namespace) -> None:
    queue = list(csv.DictReader(args.queue.open(encoding="utf-8")))
    dataset = list(csv.DictReader(args.dataset.open(encoding="utf-8")))

    kept: list[dict] = []
    removed: list[int] = []
    for q in queue:
        rid = int(q["row_id"])
        if 1 <= rid <= len(dataset) and is_reviewed(dataset[rid - 1]):
            removed.append(rid)
            continue
        kept.append(q)

    print(f"Queue: {args.queue.name}")
    print(f"Dataset: {args.dataset.name}")
    print(f"Before: {len(queue)}  removed: {len(removed)}  remaining: {len(kept)}")
    if removed:
        print(f"Removed row_ids: {removed}")

    if args.dry_run:
        print("(dry-run — queue not written)")
        return

    with args.queue.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=QUEUE_FIELDS)
        w.writeheader()
        for row in kept:
            w.writerow({k: row.get(k, "") for k in QUEUE_FIELDS})
    print(f"Wrote {len(kept)} rows -> {args.queue}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export or prune label review queue")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="Build review queue from flagged dataset rows")
    export.add_argument("--input", type=Path, default=DEFAULT_CSV)
    export.add_argument("--output", type=Path, default=DEFAULT_QUEUE)
    export.set_defaults(func=cmd_export)

    prune = sub.add_parser("prune", help="Remove rows already reviewed in dataset notes")
    prune.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    prune.add_argument("--dataset", type=Path, default=DEFAULT_CSV)
    prune.add_argument("--dry-run", action="store_true")
    prune.set_defaults(func=cmd_prune)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
