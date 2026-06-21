"""Groq-assisted labeling workflow for r/leagueoflegends posts.

Usage:
  pip install -r requirements.txt
  Copy .env.example to .env and set GROQ_API_KEY

  # 1. Pre-label unlabeled posts (JSON array of strings)
  python scripts/label.py prelabel --input data/unlabeled_posts.json --limit 200

  # 2. Review data/prelabeled_for_review.csv — fix label column per post

  # 3. Export final CSV for Colab
  python scripts/label.py finalize
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "unlabeled_posts.json"
PRELABEL_OUT = ROOT / "data" / "prelabeled_for_review.csv"
FINAL_OUT = ROOT / "data" / "labeled_posts.csv"

LABELS = ("analysis", "hot_take", "reaction", "question")

SYSTEM_PROMPT = """You are classifying posts and comments from r/leagueoflegends.
Assign each post to exactly one category.

analysis: Explains, argues, or reports using game knowledge, mechanics, patch context, stats, links, or step-by-step reasoning — even if the conclusion is debatable.
Example: A post walking through Deathfire Touch duration overwrite mechanics on Smolder with spell-type durations.

hot_take: States a bold opinion, complaint, or nostalgia claim with little supporting evidence — asserts rather than argues, or blames teammates/meta without mechanical detail.
Example: "I miss twisted treeline, should be brought back" with no data.

reaction: Shares a personal moment, emotion, or low-stakes share without primarily asking for advice — rank milestones, vents, screenshot flexes, polls.
Example: "Finally hit Platinum, calling it a day for the season."

question: Main purpose is asking for advice, recommendations, or factual information from the community.
Example: "Any suggestions on champs to play to climb from bronze?"

Edge rules:
- News/stats with patch context and links -> analysis
- One cherry-picked stat + hyperbolic claim -> hot_take
- Teammate blame without self-review -> hot_take
- "How do I climb?" / champ pick help -> question
- Kit walkthrough arguing a design point -> analysis, not question

Respond with ONLY one label: analysis, hot_take, reaction, or question
Do not explain."""


def load_texts(path: Path, limit: int | None) -> list[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a JSON array of strings")
    texts = [t.strip() for t in raw if isinstance(t, str) and len(t.strip()) >= 40]
    return texts[:limit] if limit else texts


def parse_label(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip().lower()
    for label in sorted(LABELS, key=len, reverse=True):
        if s == label or label in s:
            return label
    return None


def classify(client, text: str) -> tuple[str | None, str]:
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Classify this post:\n\n{text[:2000]}"},
        ],
        temperature=0,
        max_tokens=20,
    )
    raw = response.choices[0].message.content
    label = parse_label(raw)
    return label, (raw or "").strip()


def cmd_prelabel(args: argparse.Namespace) -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("Set GROQ_API_KEY in .env or your environment.", file=sys.stderr)
        sys.exit(1)

    try:
        from groq import Groq
    except ImportError:
        print("Install groq: pip install groq", file=sys.stderr)
        sys.exit(1)

    if not args.input.exists():
        print(f"Input not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    texts = load_texts(args.input, args.limit)
    print(f"Pre-labeling {len(texts)} posts from {args.input.name}...")

    client = Groq(api_key=api_key)
    rows: list[dict] = []
    unparseable = 0

    for i, text in enumerate(texts, 1):
        try:
            prelabel, raw = classify(client, text)
        except Exception as e:
            prelabel, raw = None, str(e)

        if prelabel is None:
            unparseable += 1
            notes = f"prelabeled_groq; unparseable: {raw[:80]}"
            label = ""
        else:
            notes = "prelabeled_groq; pending review"
            label = prelabel

        rows.append(
            {
                "text": text,
                "prelabel": prelabel or "",
                "label": label,
                "notes": notes,
            }
        )

        if i % 10 == 0:
            print(f"  {i}/{len(texts)} done...")
        time.sleep(args.delay)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["text", "prelabel", "label", "notes"])
        w.writeheader()
        w.writerows(rows)

    print(f"\nWrote {len(rows)} rows -> {args.output}")
    print(f"Unparseable: {unparseable}")
    print("\nNext: review label column, then run: python scripts/label.py finalize")


def cmd_finalize(args: argparse.Namespace) -> None:
    src = args.input
    if not src.exists():
        raise SystemExit(f"Missing {src} — run: python scripts/label.py prelabel")

    rows = list(csv.DictReader(src.open(encoding="utf-8")))
    bad = [i for i, r in enumerate(rows, 1) if not (r.get("label") or "").strip()]
    if bad:
        raise SystemExit(f"Rows missing final label: {bad[:10]}{'...' if len(bad) > 10 else ''}")

    out_rows = []
    for r in rows:
        notes = (r.get("notes") or "").strip()
        pre = (r.get("prelabel") or "").strip()
        final = r["label"].strip()
        if notes == "prelabeled_groq; pending review":
            if pre == final:
                notes = "prelabeled_groq; unchanged"
            elif pre:
                notes = f"prelabeled_groq; corrected: {pre}->{final}"
            else:
                notes = "prelabeled_groq; hand-labeled after unparseable"
        out_rows.append({"text": r["text"], "label": final, "notes": notes})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["text", "label", "notes"])
        w.writeheader()
        w.writerows(out_rows)

    dist = dict(Counter(r["label"] for r in out_rows))
    print(f"Wrote {len(out_rows)} rows -> {args.output}")
    print(f"Distribution: {dist}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Groq pre-label and finalize labeled CSV")
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("prelabel", help="Pre-label posts with Groq")
    pre.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    pre.add_argument("--output", type=Path, default=PRELABEL_OUT)
    pre.add_argument("--limit", type=int, default=200)
    pre.add_argument("--delay", type=float, default=0.15, help="Seconds between API calls")
    pre.set_defaults(func=cmd_prelabel)

    fin = sub.add_parser("finalize", help="Export reviewed prelabels to labeled_posts.csv")
    fin.add_argument("--input", type=Path, default=PRELABEL_OUT)
    fin.add_argument("--output", type=Path, default=FINAL_OUT)
    fin.set_defaults(func=cmd_finalize)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
