#!/usr/bin/env python3
"""Rebalance labeled_posts_export.csv by swapping overrepresented rows for new Reddit posts.

Browser collection (Reddit blocks server-side scraping):
  1. Run scripts/collect_in_browser.js in Cursor browser on old.reddit.com/r/leagueoflegends
  2. Save output to data/reddit_pool/collection.json

Usage:
  python scripts/rebalance_dataset.py status
  python scripts/rebalance_dataset.py import-pool path/to/browser.json
  python scripts/rebalance_dataset.py pick --label analysis --count 5
  python scripts/rebalance_dataset.py run --target 50 --from-label question
  python scripts/rebalance_dataset.py export-queue
  python scripts/rebalance_dataset.py scan-pool
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "labeled_posts_export.csv"
POOL_DIR = ROOT / "data" / "reddit_pool"
QUEUE_PATH = ROOT / "data" / "review_queue.csv"
UNRELATED_PATH = ROOT / "data" / "unrelated_to_replace.csv"
LABELS = ("analysis", "hot_take", "reaction", "question")
MAX_TEXT = 1500
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


def post_text(p: dict) -> str:
    title = p.get("title") or ""
    body = p.get("selftext") or ""
    return f"{title} {body}".strip()[:MAX_TEXT]


def load_rows(path: Path = CSV_PATH) -> list[dict]:
    return list(csv.DictReader(path.open(encoding="utf-8")))


def write_rows(rows: list[dict], path: Path = CSV_PATH) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["text", "label", "notes", "source_url"])
        w.writeheader()
        w.writerows(rows)


def distribution(rows: list[dict]) -> Counter:
    return Counter((r.get("label") or "").strip() for r in rows if (r.get("label") or "").strip())


def is_promo(t: str) -> bool:
    return bool(
        re.search(
            r"coaching|free coaching|buy me a coffee|huggingface|sign ups|"
            r"matchupdrills|shoot me a dm|my website|check out my|tournament.*register|"
            r"represent your country|fan nations cup|\bfree dataset\b",
            t.lower()[:400],
        )
    )


def is_news_list(t: str) -> bool:
    return bool(
        re.search(
            r"broadcast talent|starting line-up|appointed its league|"
            r"the list of those who would be on broadcast",
            t.lower()[:300],
        )
    )


def is_question_post(t: str) -> bool:
    lower = t.lower()
    head = lower[:250]
    if re.search(
        r"^(how do i|how to|how can i|how should i|how are you supposed to|"
        r"what champ|which champ|any suggestions|need help|advice on|help with|"
        r"can anyone|does anyone know|totally new to lol|beginner.*help|"
        r"who should i (main|pick)|coaching needed|how to climb|how to play lol|"
        r"need help finding|what rune|which rune|is there a way to)\b",
        head,
    ):
        return True
    if "?" in head[:100] and re.search(
        r"how (do|to)|any suggestions|need help|what should|recommend|pick for me",
        head,
    ):
        return True
    return False


def classify_candidate(t: str) -> str | None:
    lower = t.lower()
    if is_promo(t) or is_news_list(t) or is_question_post(t):
        return None

    if re.search(
        r"\b(finally hit|first time ever|hit (plat|gold|diamond|master|emerald|challenger)|"
        r"reached.*(plat|gold|diamond|master|emerald)|drawing by me|fanart by me|"
        r"share this accomplishment|calling it a day|most rare thing i|"
        r"guess who won the game from the draft|old frames\? i logged|"
        r"i got all the scopes|drinking game|from stacking nashors on nunu to master|"
        r"hit plat now feel flat|years after.*finally hit|finally reached diamond)\b",
        lower[:600],
    ):
        if not re.search(r"how do i climb|any suggestions on champs|need help to improve", lower[:200]):
            return "reaction"

    if re.search(
        r"\b(unpopular opinion|hot take|i miss (twisted treeline|old|the old|when)|"
        r"should be brought back|should be removed|overtuned|undertuned|broken meta|"
        r"this season feel very off|matchmaking.*(broken|confused|frustrating|off)|"
        r"clash tiering|returning player.*horrible|removal of the .* ping|"
        r"why everyone hate|why did .* get buffed|mid streamers|not beeing able to ff|"
        r"just absolutely confused|i can't believe some people still play|"
        r"more fun to think about playing than|tt was fun whenever|are we going to ignore|"
        r"remake needs 3 votes|typing more of negative|he has the weirdest limits|"
        r"game has been homogenized|elo hell|ranked is (broken|dead)|"
        r"riot (needs to|should)|worst season|this game sucks|"
        r"bring back|was better when|nostalgia|overrated|underrated)\b",
        lower[:900],
    ):
        if not re.search(
            r"patch \d\.\d|winrate.*%|dpm\.lol|op\.gg.*emerald|u\.gg.*build|here's how",
            lower[:700],
        ):
            return "hot_take"

    if re.search(
        r"\b(dpm\.lol|op\.gg|u\.gg|patch \d|winrate|pickrate|mechanic|cooldown|"
        r"deathfire touch|aegis is too unbalanced|why is ksante considered|"
        r"map length.*calculate|biggest riot leaker|enc qualifiers|qualifies for enc|"
        r"top lane meta in pro play|i0ki played vayne|why was the ryze rework|"
        r"phantasm.*lp|first day of results|how does league determine match termination|"
        r"rework concept|doran's (blade|bow|helm)|van 57|root cause|after patch \d|"
        r"data presented without|winrate in bronze and diamond|jinx has had a winrate|"
        r"asol winrate|pro play feels the most disconnected|statistical analysis|"
        r"patch preview|full patch|lck analyst|worlds pick|by the numbers|"
        r"selection bias|press conference|ability interaction|passive|item build|"
        r"matchup|calculated|breakdown|compared to|step-by-step)\b",
        lower[:1200],
    ):
        return "analysis"

    if len(t) > 350 and re.search(
        r"\b(because|therefore|for example|means that|compared to|breakdown)\b", lower[:900]
    ):
        if re.search(r"https://|patch|ability|item|winrate|stats|mechanic", lower[:900]):
            if not is_question_post(t):
                return "analysis"

    return None


def pool_label(p: dict, text: str, label: str) -> bool:
    """Match pool post to target label (uses browser analysis_score when present)."""
    if is_promo(text) or is_news_list(text):
        return False
    if label == "question":
        return is_question_post(text)
    if label == "analysis":
        if p.get("analysis_score", 0) >= 2:
            return not is_question_post(text)
        return classify_candidate(text) == "analysis"
    return classify_candidate(text) == label


def parse_pool_file(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "result" in raw and "value" in raw["result"]:
        val = raw["result"]["value"]
        data = json.loads(val) if isinstance(val, str) else val
    else:
        data = raw
    if isinstance(data, dict) and "posts" in data:
        return data["posts"]
    if isinstance(data, list):
        return data
    raise ValueError(f"{path} must be {{posts: [...]}}, a JSON array, or CDP evaluate output")


def load_pool(pool_dir: Path = POOL_DIR) -> list[dict]:
    posts: list[dict] = []
    seen: set[str] = set()
    if not pool_dir.exists():
        return posts
    for path in sorted(pool_dir.glob("*.json")):
        for p in parse_pool_file(path):
            pid = p.get("id") or p.get("reddit_id") or ""
            if pid and pid in seen:
                continue
            if pid:
                seen.add(pid)
            posts.append(p)
    return posts


def pick_from_pool(
    pool: list[dict],
    existing: set[str],
    need: dict[str, int],
) -> dict[str, list[dict]]:
    picked: dict[str, list[dict]] = {k: [] for k in need}
    used: set[str] = set()

    for label in ("reaction", "hot_take", "analysis", "question"):
        if label not in need or need[label] <= 0:
            continue
        for p in pool:
            if len(picked[label]) >= need[label]:
                break
            url = (p.get("url") or p.get("source_url") or "").strip()
            pid = p.get("id") or p.get("reddit_id") or ""
            if not url or url in existing or pid in used:
                continue
            text = post_text(p)
            if len(text) < 80:
                continue
            if not pool_label(p, text, label):
                continue
            picked[label].append(p)
            used.add(pid)
            existing.add(url)

    return picked


def compute_need(rows: list[dict], target: int) -> tuple[dict[str, int], str | None]:
    dist = distribution(rows)
    need = {lb: max(0, target - dist.get(lb, 0)) for lb in LABELS if lb != "question"}
    over = max(dist.items(), key=lambda x: x[1])
    from_label = over[0] if over[1] > target else None
    total_need = sum(need.values())
    return need, from_label if from_label and total_need > 0 else None


def apply_swaps(
    rows: list[dict],
    swaps: list[tuple[int, dict, str]],
    note_prefix: str = "rebalance replacement",
) -> None:
    for row_id, post, label in swaps:
        rows[row_id - 1] = {
            "text": post_text(post),
            "label": label,
            "notes": f"{note_prefix}; flair={post.get('flair') or 'none'}",
            "source_url": (post.get("url") or post.get("source_url") or "").strip(),
        }


def cmd_scan_pool(_: argparse.Namespace) -> None:
    """List analysis-like pool posts not already in the dataset."""
    analysis_re = re.compile(
        r"patch \d+\.\d|winrate|pickrate|pick rate|mechanic|cooldown|"
        r"ability|passive|interaction|\bbug\b|rework|\bitem\b|"
        r"dpm\.lol|op\.gg|u\.gg|lolalytics|pro play|meta|build|rune|"
        r"according to|breakdown|compared to|patch notes|step-by-step|"
        r"calculated|data shows|win rate",
        re.I,
    )
    help_re = re.compile(
        r"^(how do i|how to|any suggestions|need help|what champ|which champ)\b",
        re.I,
    )

    rows = load_rows()
    existing = {r["source_url"] for r in rows}
    cands: list[tuple[int, str, str]] = []
    for path in sorted(POOL_DIR.glob("*.json")):
        for p in parse_pool_file(path):
            url = (p.get("url") or p.get("source_url") or "").strip()
            if not url or url in existing:
                continue
            t = post_text(p)
            if len(t) < 120 or help_re.search(t[:150]):
                continue
            if analysis_re.search(t) and len(t) > 180:
                cands.append((len(t), p.get("title", "")[:72], url))

    cands.sort(reverse=True)
    print(f"Analysis-like pool posts not in CSV: {len(cands)}")
    for length, title, url in cands[:30]:
        print(f"  [{length}] {title}")
        print(f"       {url}")


def cmd_status(_: argparse.Namespace) -> None:
    rows = load_rows()
    dist = distribution(rows)
    blank = sum(1 for r in rows if not (r.get("label") or "").strip())
    print(f"File: {CSV_PATH}")
    print(f"Rows: {len(rows)}  (blank: {blank})")
    for lb in LABELS:
        n = dist.get(lb, 0)
        print(f"  {lb}: {n} ({100 * n / len(rows):.1f}%)")
    need, from_label = compute_need(rows, 50)
    if from_label:
        print(f"\nTo reach 50 each: replace {sum(need.values())} '{from_label}' rows")
        print(f"  need: { {k: v for k, v in need.items() if v} }")


def cmd_import_pool(args: argparse.Namespace) -> None:
    POOL_DIR.mkdir(parents=True, exist_ok=True)
    src = args.input.resolve()
    dest = POOL_DIR / (args.name or src.name)
    posts = parse_pool_file(src)
    dest.write_text(
        json.dumps({"count": len(posts), "posts": posts}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Imported {len(posts)} posts -> {dest}")


def cmd_pick(args: argparse.Namespace) -> None:
    rows = load_rows()
    existing = {r["source_url"] for r in rows}
    pool = load_pool()
    if not pool:
        raise SystemExit(f"No pool in {POOL_DIR}. Run: import-pool <browser.json>")

    need = {args.label: args.count}
    picked = pick_from_pool(pool, existing, need)[args.label]
    print(f"Found {len(picked)} / {args.count} for {args.label}:")
    for p in picked:
        print(f"  {p.get('title', '')[:72]}")
        print(f"    {p.get('url') or p.get('source_url')}")


def cmd_run(args: argparse.Namespace) -> None:
    rows = load_rows()
    existing = {r["source_url"] for r in rows}
    pool = load_pool()
    if not pool:
        raise SystemExit(f"No pool in {POOL_DIR}. Run: import-pool <browser.json>")

    need, from_label = compute_need(rows, args.target)
    total = sum(need.values())
    if not from_label or total == 0:
        print("Already balanced at target (or nothing to swap).")
        cmd_status(args)
        return

    if args.from_label:
        from_label = args.from_label

    picked = pick_from_pool(pool, existing, need)
    for label, posts in picked.items():
        if len(posts) < need[label]:
            raise SystemExit(f"Not enough {label} candidates ({len(posts)}/{need[label]})")

    source_rows = [i for i, r in enumerate(rows, 1) if r["label"] == from_label]
    if len(source_rows) < total:
        raise SystemExit(f"Not enough '{from_label}' rows ({len(source_rows)} < {total})")

    to_replace = source_rows[-total:]
    swaps: list[tuple[int, dict, str]] = []
    idx = 0
    for label in ("analysis", "hot_take", "reaction"):
        for p in picked.get(label, []):
            swaps.append((to_replace[idx], p, label))
            idx += 1

    apply_swaps(rows, swaps, note_prefix=f"rebalance replacement; was {from_label}")
    write_rows(rows)
    print(f"Replaced {len(swaps)} '{from_label}' rows")
    print("New distribution:", dict(distribution(rows)))


def cmd_replace(args: argparse.Namespace) -> None:
    row_ids = [int(x) for x in args.rows.split(",")]
    labels = [x.strip() for x in args.labels.split(",")]
    if len(row_ids) != len(labels):
        raise SystemExit("--rows and --labels must have the same count")

    rows = load_rows()
    existing = {r["source_url"] for r in rows}
    pool = load_pool()
    if not pool:
        raise SystemExit(f"No pool in {POOL_DIR}. Run: import-pool <browser.json>")

    need = Counter(labels)
    picked = pick_from_pool(pool, existing, dict(need))
    swaps: list[tuple[int, dict, str]] = []
    for row_id, label in zip(row_ids, labels):
        if not picked[label]:
            raise SystemExit(f"No pool candidate left for {label}")
        swaps.append((row_id, picked[label].pop(0), label))

    apply_swaps(rows, swaps, note_prefix="replacement post")
    write_rows(rows)

    if args.clear_unrelated:
        kept = [
            r
            for r in csv.DictReader(UNRELATED_PATH.open(encoding="utf-8"))
            if int(r["row_id"]) not in row_ids
        ] if UNRELATED_PATH.exists() else []
        if UNRELATED_PATH.exists():
            with UNRELATED_PATH.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["row_id", "label", "text", "review_reason"])
                w.writeheader()
                w.writerows(kept)

    print(f"Replaced rows {row_ids}")
    print("Distribution:", dict(distribution(rows)))


def cmd_export_queue(args: argparse.Namespace) -> None:
    rows = load_rows()
    tag = args.tag
    queue = []
    for i, row in enumerate(rows, 1):
        notes = row.get("notes") or ""
        if tag not in notes:
            continue
        queue.append(
            {
                "row_id": i,
                "priority": 1,
                "text": row["text"],
                "label": row.get("label") or "",
                "suggested_label": row.get("label") or "",
                "notes": notes,
                "review_reason": f"{tag}; confirm label",
                "flag": "",
                "source_url": row.get("source_url") or "",
            }
        )

    out = args.output or QUEUE_PATH
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=QUEUE_FIELDS)
        w.writeheader()
        w.writerows(queue)
    print(f"Wrote {len(queue)} rows -> {out}")
    print("Run: python scripts/review_gui.py")


def cmd_swap(args: argparse.Namespace) -> None:
    """Replace N rows of one label with pool posts of another label."""
    rows = load_rows()
    existing = {r["source_url"] for r in rows}
    pool = load_pool()
    if not pool:
        raise SystemExit(f"No pool in {POOL_DIR}.")

    need = {args.to: args.count}
    picked = pick_from_pool(pool, existing, need)
    if len(picked[args.to]) < args.count:
        raise SystemExit(f"Not enough {args.to} candidates ({len(picked[args.to])}/{args.count})")

    source_rows = [i for i, r in enumerate(rows, 1) if r["label"] == args.from_label]
    if len(source_rows) < args.count:
        raise SystemExit(f"Not enough '{args.from_label}' rows ({len(source_rows)} < {args.count})")

    to_replace = source_rows[-args.count:]
    swaps = [(rid, picked[args.to][i], args.to) for i, rid in enumerate(to_replace)]
    apply_swaps(rows, swaps, note_prefix=f"rebalance; was {args.from_label}")
    write_rows(rows)
    print(f"Swapped {args.count} {args.from_label} -> {args.to}")
    print("Distribution:", dict(distribution(rows)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebalance labeled_posts_export.csv")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show label distribution").set_defaults(func=cmd_status)

    imp = sub.add_parser("import-pool", help="Import browser JSON into data/reddit_pool/")
    imp.add_argument("input", type=Path)
    imp.add_argument("--name", help="Output filename under reddit_pool/")
    imp.set_defaults(func=cmd_import_pool)

    pick = sub.add_parser("pick", help="Preview pool candidates for a label")
    pick.add_argument("--label", required=True, choices=LABELS)
    pick.add_argument("--count", type=int, default=5)
    pick.set_defaults(func=cmd_pick)

    run = sub.add_parser("run", help="Auto-swap overrepresented rows to hit target counts")
    run.add_argument("--target", type=int, default=50)
    run.add_argument("--from-label", choices=LABELS, help="Label to replace (default: largest class)")
    run.set_defaults(func=cmd_run)

    rep = sub.add_parser("replace", help="Replace specific row IDs from the pool")
    rep.add_argument("--rows", required=True, help="Comma-separated row IDs, e.g. 20,41,61")
    rep.add_argument("--labels", required=True, help="Comma-separated labels, e.g. analysis,hot_take,reaction")
    rep.add_argument("--clear-unrelated", action="store_true")
    rep.set_defaults(func=cmd_replace)

    eq = sub.add_parser("export-queue", help="Export review queue for swapped rows")
    eq.add_argument("--tag", default="rebalance replacement")
    eq.add_argument("--output", type=Path)
    eq.set_defaults(func=cmd_export_queue)

    sw = sub.add_parser("swap", help="Replace N rows of one label with pool posts of another")
    sw.add_argument("--from-label", dest="from_label", required=True, choices=LABELS)
    sw.add_argument("--to", required=True, choices=LABELS)
    sw.add_argument("--count", type=int, required=True)
    sw.set_defaults(func=cmd_swap)

    sub.add_parser("scan-pool", help="List analysis-like pool posts not in dataset").set_defaults(
        func=cmd_scan_pool
    )

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
