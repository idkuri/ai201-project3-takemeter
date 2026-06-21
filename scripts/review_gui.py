#!/usr/bin/env python3
"""Manual label review GUI for TakeMeter dataset.

Usage:
  python scripts/review_gui.py

  # After rebalance or export:
  python scripts/rebalance_dataset.py export-queue
  python scripts/export_review_queue.py export

Keyboard: 1=analysis  2=hot_take  3=reaction  4=question
           u = toggle unrelated (replace later)
           Left/Right or j/k = prev/next   Ctrl+S = save
"""
from __future__ import annotations

import argparse
import csv
import re
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE = ROOT / "data" / "review_queue.csv"
DEFAULT_DATASET = ROOT / "data" / "labeled_posts_export.csv"
UNRELATED_REPORT = ROOT / "data" / "unrelated_to_replace.csv"
LABELS = ("analysis", "hot_take", "reaction", "question")
LABEL_KEYS = {"1": "analysis", "2": "hot_take", "3": "reaction", "4": "question"}
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
UNRELATED_FLAG = "unrelated"
UNRELATED_NOTE = "unrelated; needs replacement"
DATASET_FIELDS = ["text", "label", "notes", "source_url"]
REDDIT_IN_TEXT = re.compile(r"https?://(?:www\.)?reddit\.com/\S+", re.I)
SUBREDDIT = "leagueoflegends"


def extract_reddit_url(text: str) -> str:
    match = REDDIT_IN_TEXT.search(text or "")
    if not match:
        return ""
    return match.group(0).rstrip(".,;")


def is_reddit_permalink(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    if "/search?" in u or u.rstrip("/").endswith("/search"):
        return False
    return "/comments/" in u or "reddit.com" in u


def reddit_search_url(text: str) -> str:
    # Short title-only query; long pasted bodies rank the wrong threads.
    title = (text or "").split("\n")[0].strip()
    if len(title) > 80:
        title = title[:80].rsplit(" ", 1)[0]
    return (
        f"https://old.reddit.com/r/{SUBREDDIT}/search"
        f"?q={quote_plus(title)}&restrict_sr=on&sort=relevance"
    )


def resolve_reddit_url(row: dict) -> tuple[str, str]:
    """Return (url, kind) where kind is saved, embedded, search, or missing."""
    saved = (row.get("source_url") or "").strip()
    if saved and is_reddit_permalink(saved):
        return saved, "saved"
    embedded = extract_reddit_url(row.get("text", ""))
    if embedded:
        return embedded, "embedded"
    text = (row.get("text") or "").strip()
    if text:
        return reddit_search_url(text), "search"
    return "", "missing"


def load_csv(path: Path) -> list[dict]:
    return list(csv.DictReader(path.open(encoding="utf-8")))


def normalize_queue_rows(rows: list[dict], dataset: list[dict]) -> list[dict]:
    for q in rows:
        q.setdefault("flag", "")
        q.setdefault("source_url", "")
        rid = int(q["row_id"])
        if 1 <= rid <= len(dataset):
            ds = dataset[rid - 1]
            note = (ds.get("notes") or "").strip()
            if UNRELATED_NOTE in note:
                q["flag"] = UNRELATED_FLAG
            if not q["source_url"].strip():
                ds_url = (ds.get("source_url") or "").strip()
                if is_reddit_permalink(ds_url):
                    q["source_url"] = ds_url
            if not q["source_url"].strip():
                embedded = extract_reddit_url(q.get("text", ""))
                if is_reddit_permalink(embedded):
                    q["source_url"] = embedded
    return rows


def strip_unrelated_note(notes: str) -> str:
    text = (notes or "").replace(UNRELATED_NOTE, "").strip()
    return text.strip("; ").strip()


def with_unrelated_note(notes: str, unrelated: bool) -> str:
    base = strip_unrelated_note(notes)
    if unrelated:
        return UNRELATED_NOTE if not base else f"{UNRELATED_NOTE}; {base}"
    return base


def _write_queue_file(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=QUEUE_FIELDS)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in QUEUE_FIELDS})


def save_queue(path: Path, rows: list[dict]) -> Path:
    """Save queue CSV. Falls back to *_saved.csv if the target file is locked."""
    try:
        _write_queue_file(path, rows)
        return path
    except PermissionError:
        fallback = path.with_name(f"{path.stem}_saved{path.suffix}")
        _write_queue_file(fallback, rows)
        return fallback


def _write_dataset_file(path: Path, dataset: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=DATASET_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in dataset:
            w.writerow({k: row.get(k, "") for k in DATASET_FIELDS})


def apply_to_dataset(dataset_path: Path, queue_rows: list[dict], originals: dict[int, str]) -> tuple[int, int, Path]:
    dataset = load_csv(dataset_path)
    label_updates = 0
    flag_updates = 0
    for q in queue_rows:
        rid = int(q["row_id"])
        idx = rid - 1
        if idx < 0 or idx >= len(dataset):
            continue

        new_label = q["label"].strip()
        old = (originals.get(rid, dataset[idx]["label"]) or "").strip()
        if new_label and new_label != old:
            dataset[idx]["label"] = new_label
            note = (dataset[idx].get("notes") or "").strip()
            if not old:
                if note and "manual label" not in note:
                    dataset[idx]["notes"] = f"{note}; manual label"
                elif not note:
                    dataset[idx]["notes"] = "manual label"
            elif note and UNRELATED_NOTE not in note:
                dataset[idx]["notes"] = f"{note}; reviewed relabeled {old}->{new_label}"
            elif UNRELATED_NOTE not in note:
                dataset[idx]["notes"] = f"reviewed; relabeled {old}->{new_label}"
            label_updates += 1
        elif new_label and new_label == old:
            note = (dataset[idx].get("notes") or "").strip()
            if "reviewed" not in note.lower():
                dataset[idx]["notes"] = f"{note}; reviewed ok" if note else "reviewed ok"
                flag_updates += 1

        unrelated = q.get("flag", "").strip() == UNRELATED_FLAG
        new_note = with_unrelated_note(dataset[idx].get("notes") or "", unrelated)
        if new_note != (dataset[idx].get("notes") or "").strip():
            dataset[idx]["notes"] = new_note
            flag_updates += 1

        source_url = (q.get("source_url") or "").strip()
        if source_url != (dataset[idx].get("source_url") or "").strip():
            dataset[idx]["source_url"] = source_url
            flag_updates += 1

    try:
        _write_dataset_file(dataset_path, dataset)
        return label_updates, flag_updates, dataset_path
    except PermissionError:
        fallback = dataset_path.with_name(f"{dataset_path.stem}_saved{dataset_path.suffix}")
        _write_dataset_file(fallback, dataset)
        return label_updates, flag_updates, fallback


def write_unrelated_report(path: Path, queue_rows: list[dict]) -> int:
    flagged = [q for q in queue_rows if q.get("flag", "").strip() == UNRELATED_FLAG]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["row_id", "label", "text", "review_reason"])
        w.writeheader()
        for q in flagged:
            w.writerow(
                {
                    "row_id": q["row_id"],
                    "label": q["label"],
                    "text": q["text"],
                    "review_reason": q.get("review_reason", ""),
                }
            )
    return len(flagged)


class ReviewApp(tk.Tk):
    def __init__(self, queue_path: Path, dataset_path: Path) -> None:
        super().__init__()
        self.title("TakeMeter Label Review")
        self.geometry("920x720")
        self.minsize(720, 560)

        self.queue_path = queue_path
        self.dataset_path = dataset_path
        dataset = load_csv(dataset_path) if dataset_path.exists() else []
        self.rows = normalize_queue_rows(load_csv(queue_path), dataset)
        self.index = 0
        self.originals: dict[int, str] = {}
        for q in self.rows:
            rid = int(q["row_id"])
            if 1 <= rid <= len(dataset):
                self.originals[rid] = dataset[rid - 1]["label"]

        self.label_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.meta_var = tk.StringVar()
        self.flag_var = tk.StringVar()
        self.link_var = tk.StringVar()
        self.source_url_var = tk.StringVar()

        self._build_ui()
        self._bind_keys()
        self._show_row(0)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Label(top, textvariable=self.meta_var, wraplength=860).pack(anchor="w")
        ttk.Label(top, textvariable=self.status_var).pack(anchor="w")
        self.flag_label = ttk.Label(top, textvariable=self.flag_var, foreground="#b00020")
        self.flag_label.pack(anchor="w")

        link_frame = ttk.LabelFrame(self, text="Reddit source")
        link_frame.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Label(link_frame, textvariable=self.link_var, wraplength=840).pack(
            anchor="w", padx=8, pady=(6, 2)
        )
        url_row = ttk.Frame(link_frame)
        url_row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(url_row, text="URL:").pack(side="left")
        self.url_entry = ttk.Entry(url_row, textvariable=self.source_url_var)
        self.url_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.url_entry.bind("<FocusOut>", lambda e: self._sync_source_url())
        self.url_entry.bind("<Return>", lambda e: self._open_reddit())
        ttk.Button(url_row, text="Open on Reddit (o)", command=self._open_reddit).pack(side="left")

        text_frame = ttk.LabelFrame(self, text="Post text")
        text_frame.pack(fill="both", expand=True, **pad)
        self.text_frame = text_frame
        self.text_box = tk.Text(text_frame, wrap="word", font=("Segoe UI", 11))
        scroll = ttk.Scrollbar(text_frame, command=self.text_box.yview)
        self.text_box.configure(yscrollcommand=scroll.set, state="disabled")
        self.text_box.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        hint = ttk.Label(
            self,
            text="Pick one label (1-4) per post using planning.md. suggested_label is a hint only.",
            wraplength=860,
        )
        hint.pack(anchor="w", padx=10)

        btn_frame = ttk.LabelFrame(self, text="Label (keys 1-4)")
        btn_frame.pack(fill="x", **pad)
        self.label_buttons: dict[str, tk.Button] = {}
        for i, label in enumerate(LABELS):
            b = tk.Button(
                btn_frame,
                text=f"{i + 1}. {label}",
                command=lambda lb=label: self._set_label(lb),
                width=14,
            )
            b.pack(side="left", padx=4, pady=8)
            self.label_buttons[label] = b

        flag_frame = ttk.Frame(self)
        flag_frame.pack(fill="x", padx=10, pady=(0, 6))
        self.unrelated_btn = tk.Button(
            flag_frame,
            text="Mark unrelated - replace later (u)",
            command=self._toggle_unrelated,
            width=34,
        )
        self.unrelated_btn.pack(side="left", padx=4)

        nav = ttk.Frame(self)
        nav.pack(fill="x", **pad)
        ttk.Button(nav, text="← Prev (j)", command=self._prev).pack(side="left", padx=4)
        ttk.Button(nav, text="Next → (k)", command=self._next).pack(side="left", padx=4)
        ttk.Button(nav, text="Save queue (Ctrl+S)", command=self._save_queue).pack(side="left", padx=16)
        ttk.Button(nav, text="Save + apply to dataset", command=self._save_and_apply).pack(side="left", padx=4)
        ttk.Button(nav, text="Jump to row…", command=self._jump).pack(side="right", padx=4)

        self.progress = ttk.Progressbar(self, maximum=max(len(self.rows), 1))
        self.progress.pack(fill="x", padx=10, pady=(0, 10))

    def _bind_keys(self) -> None:
        self.bind("<Left>", lambda e: self._prev())
        self.bind("<Right>", lambda e: self._next())
        self.bind("j", lambda e: self._prev())
        self.bind("k", lambda e: self._next())
        self.bind("<Control-s>", lambda e: self._save_queue())
        self.bind("u", lambda e: self._toggle_unrelated())
        self.bind("o", lambda e: self._open_reddit())
        for key, label in LABEL_KEYS.items():
            self.bind(key, lambda e, lb=label: self._set_label(lb))

    def _changed_count(self) -> int:
        count = 0
        for q in self.rows:
            rid = int(q["row_id"])
            original = (self.originals.get(rid, q["label"]) or "").strip()
            current = q["label"].strip()
            if current and current != original:
                count += 1
        return count

    def _labeled_count(self) -> int:
        return sum(1 for q in self.rows if q["label"].strip())

    def _unrelated_count(self) -> int:
        return sum(1 for q in self.rows if q.get("flag", "").strip() == UNRELATED_FLAG)

    def _is_unrelated(self, row: dict) -> bool:
        return row.get("flag", "").strip() == UNRELATED_FLAG

    def _show_row(self, index: int) -> None:
        self.index = max(0, min(index, len(self.rows) - 1))
        row = self.rows[self.index]
        rid = int(row["row_id"])
        original = (self.originals.get(rid, row["label"]) or "").strip()
        changed = bool(row["label"].strip()) and row["label"].strip() != original
        unrelated = self._is_unrelated(row)
        original_display = original or "(none)"

        self.meta_var.set(
            f"Row {self.index + 1}/{len(self.rows)}  |  CSV row_id {rid}  |  "
            f"priority {row.get('priority', '?')}  |  original: {original_display}  |  "
            f"suggested: {row.get('suggested_label') or '(none)'}  |  "
            f"reason: {row.get('review_reason', '')}"
        )
        self.status_var.set(
            f"Current label: {row['label'] or '(pick one)'}  {'(modified)' if changed else ''}  |  "
            f"{self._labeled_count()}/{len(self.rows)} labeled  |  "
            f"{self._changed_count()} updated  |  "
            f"{self._unrelated_count()} unrelated flagged"
        )
        if unrelated:
            self.flag_var.set("UNRELATED - find a replacement post later (saved to unrelated_to_replace.csv on apply)")
            self.unrelated_btn.configure(text="Clear unrelated flag (u)", bg="#ffd6d6")
        else:
            self.flag_var.set("")
            self.unrelated_btn.configure(
                text="Mark unrelated - replace later (u)",
                bg="SystemButtonFace",
            )

        if row.get("source_url") and not is_reddit_permalink(row["source_url"]):
            row["source_url"] = ""
        url, kind = resolve_reddit_url(row)
        if not row.get("source_url", "").strip() and kind == "embedded":
            row["source_url"] = url
        self.source_url_var.set(row.get("source_url", "").strip())
        if kind == "saved":
            self.link_var.set("Opens your saved Reddit permalink.")
        elif kind == "embedded":
            self.link_var.set("Found a Reddit link inside the post text.")
        elif kind == "search":
            self.link_var.set(
                "No permalink saved. Open on Reddit runs an r/leagueoflegends title search. Paste the real URL if you find it."
            )
        else:
            self.link_var.set("No Reddit link available for this row.")

        self.text_box.configure(state="normal")
        self.text_box.delete("1.0", tk.END)
        self.text_box.insert("1.0", row["text"])
        self.text_box.configure(state="disabled")

        self.label_var.set(row["label"])
        self._refresh_label_buttons(row["label"])
        self.progress["value"] = self.index + 1

    def _refresh_label_buttons(self, active: str) -> None:
        for label, btn in self.label_buttons.items():
            if label == active:
                btn.configure(relief="sunken", bg="#cce5ff")
            else:
                btn.configure(relief="raised", bg="SystemButtonFace")

    def _set_label(self, label: str) -> None:
        self.rows[self.index]["label"] = label
        self._show_row(self.index)

    def _toggle_unrelated(self) -> None:
        row = self.rows[self.index]
        if self._is_unrelated(row):
            row["flag"] = ""
        else:
            row["flag"] = UNRELATED_FLAG
        self._show_row(self.index)

    def _sync_source_url(self) -> None:
        self.rows[self.index]["source_url"] = self.source_url_var.get().strip()

    def _open_reddit(self) -> None:
        self._sync_source_url()
        url, kind = resolve_reddit_url(self.rows[self.index])
        if not url:
            messagebox.showinfo(
                "No Reddit link",
                "Paste a permalink into the URL box, or search r/leagueoflegends manually.",
            )
            return
        webbrowser.open(url)
        if kind == "search":
            messagebox.showinfo(
                "Reddit search",
                "Opened an r/leagueoflegends search using the post title.\n"
                "If you find the thread, paste its URL into the box and save.",
            )

    def _prev(self) -> None:
        self._sync_source_url()
        if self.index > 0:
            self._show_row(self.index - 1)

    def _next(self) -> None:
        self._sync_source_url()
        if self.index < len(self.rows) - 1:
            self._show_row(self.index + 1)

    def _jump(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Jump to queue row")
        dialog.geometry("320x120")
        ttk.Label(dialog, text=f"Enter 1–{len(self.rows)}:").pack(pady=8)
        entry = ttk.Entry(dialog)
        entry.pack(padx=12, fill="x")
        entry.focus()

        def go() -> None:
            try:
                n = int(entry.get()) - 1
                if 0 <= n < len(self.rows):
                    self._show_row(n)
                    dialog.destroy()
            except ValueError:
                pass

        ttk.Button(dialog, text="Go", command=go).pack(pady=8)

    def _save_queue(self) -> None:
        try:
            saved = save_queue(self.queue_path, self.rows)
            write_unrelated_report(UNRELATED_REPORT, self.rows)
        except PermissionError:
            messagebox.showerror(
                "Could not save",
                f"Close {self.queue_path.name} in Excel/Cursor, then save again.\n\n"
                "Your label changes are still in memory until you save.",
            )
            return
        if saved != self.queue_path:
            messagebox.showwarning(
                "Saved to fallback file",
                f"{self.queue_path.name} is locked (close it in Excel/Cursor).\n\n"
                f"Saved to:\n{saved}",
            )
        else:
            messagebox.showinfo(
                "Saved",
                f"Wrote {saved.name}\nUnrelated list: {UNRELATED_REPORT.name} ({self._unrelated_count()} rows)",
            )

    def _save_and_apply(self) -> None:
        try:
            saved_queue = save_queue(self.queue_path, self.rows)
            n_labels, n_flags, saved_dataset = apply_to_dataset(
                self.dataset_path, self.rows, self.originals
            )
            unrelated_n = write_unrelated_report(UNRELATED_REPORT, self.rows)
        except PermissionError:
            messagebox.showerror(
                "Could not save",
                f"Close CSV files in Excel/Cursor, then try again.\n\n"
                "Your label changes are still in memory.",
            )
            return
        msg = f"Applied {n_labels} label change(s) and {n_flags} note update(s)."
        msg += f"\nUnrelated flagged: {unrelated_n} -> {UNRELATED_REPORT.name}"
        if saved_queue != self.queue_path:
            msg += f"\n\nQueue saved to: {saved_queue.name}"
        else:
            msg += f"\n\nQueue saved to: {saved_queue.name}"
        if saved_dataset != self.dataset_path:
            msg += (
                f"\n\n{self.dataset_path.name} is locked."
                f"\nDataset saved to: {saved_dataset.name}"
                f"\n\nClose the original file, then rename or copy over it."
            )
        else:
            msg += f"\n\nDataset updated: {saved_dataset.name}"
        messagebox.showinfo("Saved", msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="GUI to review and fix labels")
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()

    if not args.queue.exists():
        raise SystemExit(
            f"Missing {args.queue}.\n"
            "Run: python scripts/rebalance_dataset.py export-queue\n"
            "  or: python scripts/export_review_queue.py export"
        )

    app = ReviewApp(args.queue, args.dataset)
    app.mainloop()


if __name__ == "__main__":
    main()
