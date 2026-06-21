"""Shared dataset helpers: audit heuristics, validation, duplicate checks.

Usage:
  python scripts/dataset_utils.py audit
  python scripts/dataset_utils.py validate --input data/labeled_posts_export.csv
  python scripts/dataset_utils.py duplicates
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "data" / "labeled_posts_export.csv"
LABELS = ("analysis", "hot_take", "reaction", "question")
MIN_ROWS = 200
MAX_LABEL_SHARE = 0.70


def title(text: str) -> str:
    return text.split("\n\n")[0][:150]


def flags(text: str) -> list[str]:
    lower = text.lower()
    tit = title(text).lower()
    found: list[str] = []
    if "?" in tit or tit.startswith(("how ", "what ", "who ", "why ", "can ", "is ", "does ", "should ")):
        found.append("title_question")
    if re.search(
        r"\b(need help|any suggestions|how to|how do i|what champ|which item|recommend|coaching|beginner)\b",
        lower[:400],
    ):
        found.append("help_seeking")
    if re.search(r"\b(patch|mechanic|winrate|compared|breakdown|dpm\.lol|op\.gg|u\.gg|https://)\b", lower):
        found.append("analysis_signals")
    if re.search(
        r"\b(unpopular opinion|should be brought back|i miss |sucks|overtuned|broken|homogenized|elo hell|matchmaking)\b",
        lower,
    ):
        found.append("hot_take_signals")
    if re.search(
        r"\b(finally hit|first time|screenshot|fanart|drawing by me|hit plat|hit gold|hit diamond|proud|vent)\b",
        lower,
    ):
        found.append("reaction_signals")
    return found


def suggest_label(text: str) -> str | None:
    fl = flags(text)
    if "help_seeking" in fl or ("title_question" in fl and "analysis_signals" not in fl):
        return "question"
    if "analysis_signals" in fl and "hot_take_signals" not in fl:
        return "analysis"
    if "hot_take_signals" in fl:
        return "hot_take"
    if "reaction_signals" in fl:
        return "reaction"
    return None


def read_labeled_csv(path: Path) -> list[dict]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if not rows:
        raise SystemExit(f"{path} is empty")

    fieldnames = {k.lower() for k in rows[0].keys()}
    if "text" not in fieldnames or "label" not in fieldnames:
        raise SystemExit(f"{path} must have text and label columns")

    normalized: list[dict] = []
    for r in rows:
        text = (r.get("text") or "").strip()
        label = (r.get("label") or "").strip()
        notes = (r.get("notes") or "").strip()
        if not text:
            continue
        row = {"text": text, "label": label, "notes": notes}
        if "source_url" in r or "source_url" in fieldnames:
            row["source_url"] = (r.get("source_url") or "").strip()
        normalized.append(row)
    return normalized


def cmd_audit(args: argparse.Namespace) -> None:
    rows = read_labeled_csv(args.input)
    labels = Counter(r["label"] for r in rows)
    notes = Counter((r.get("notes") or "").strip() or "(empty)" for r in rows)

    print("=== SUMMARY ===")
    print(f"Rows: {len(rows)}")
    print("Labels:", dict(labels))
    print("Notes:", dict(notes))
    print()

    disagree = []
    for i, r in enumerate(rows, 1):
        sug = suggest_label(r["text"])
        if sug and sug != r["label"]:
            disagree.append((i, r["label"], sug, r.get("notes", ""), title(r["text"])))

    by_pair = Counter((a, b) for _, a, b, _, _ in disagree)
    print(f"Heuristic disagreements (rough): {len(disagree)} / {len(rows)}")
    print("Top label pairs (current -> heuristic):")
    for pair, n in by_pair.most_common(8):
        print(f"  {pair[0]} -> {pair[1]}: {n}")
    print()

    for note in ("(empty)",):
        bucket = [r for r in rows if ((r.get("notes") or "").strip() or "(empty)") == note]
        bad = [r for r in bucket if suggest_label(r["text"]) and suggest_label(r["text"]) != r["label"]]
        print(f"=== {note} bucket: {len(bucket)} rows, {len(bad)} heuristic mismatches ===")
        for r in bad[:6]:
            sug = suggest_label(r["text"])
            print(f"  [{r['label']} vs {sug}] {title(r['text'])[:90]}")
        print()

    print("=== Same text prefix, different labels ===")
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["text"][:80].lower()].append(r)
    conflicts = {k: v for k, v in groups.items() if len(v) > 1 and len({x["label"] for x in v}) > 1}
    print(f"Conflicting prefix groups: {len(conflicts)}")
    for k, v in list(conflicts.items())[:5]:
        print(f"  labels={[x['label'] for x in v]} | {k[:70]}...")


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

    ok = True
    if len(labeled) < MIN_ROWS:
        print(f"FAIL: need at least {MIN_ROWS} labeled rows (have {len(labeled)})")
        ok = False
    for lb, count in dist.items():
        share = count / len(labeled) if labeled else 0
        if share > MAX_LABEL_SHARE:
            print(f"FAIL: {lb} is {share:.0%} of dataset (max {MAX_LABEL_SHARE:.0%})")
            ok = False

    if ok:
        print("PASS: row count and label balance look OK for training.")
    else:
        sys.exit(1)


def cmd_duplicates(args: argparse.Namespace) -> None:
    rows = read_labeled_csv(args.input)
    n = len(rows)

    exact: dict[str, list[int]] = {}
    for i, r in enumerate(rows, 1):
        t = (r["text"] or "").strip()
        exact.setdefault(t, []).append(i)
    exact_dups = {t: ids for t, ids in exact.items() if len(ids) > 1}

    pairs: list[tuple[float, int, int, str, str]] = []
    for a in range(n):
        for b in range(a + 1, n):
            ratio = SequenceMatcher(None, rows[a]["text"], rows[b]["text"]).ratio()
            if ratio >= args.threshold:
                pairs.append((ratio, a + 1, b + 1, rows[a]["label"], rows[b]["label"]))
    pairs.sort(reverse=True)

    print(f"Rows: {n}")
    print(f"Exact duplicate groups: {len(exact_dups)}")
    for text, ids in list(exact_dups.items())[:5]:
        print(f"  rows {ids}: {text[:60]}...")
    print(f"Near-duplicate pairs (>={args.threshold:.0%} similar): {len(pairs)}")
    for ratio, a, b, la, lb in pairs[:20]:
        tag = " CONFLICT" if la != lb else ""
        print(f"  {ratio:.4f}  {a}({la}) vs {b}({lb}){tag}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dataset audit and validation")
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="Heuristic label audit report")
    audit.add_argument("--input", type=Path, default=DEFAULT_CSV)
    audit.set_defaults(func=cmd_audit)

    validate = sub.add_parser("validate", help="Check row count and label balance")
    validate.add_argument("--input", type=Path, default=DEFAULT_CSV)
    validate.set_defaults(func=cmd_validate)

    dups = sub.add_parser("duplicates", help="Find exact and near-duplicate posts")
    dups.add_argument("--input", type=Path, default=DEFAULT_CSV)
    dups.add_argument("--threshold", type=float, default=0.85)
    dups.set_defaults(func=cmd_duplicates)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
