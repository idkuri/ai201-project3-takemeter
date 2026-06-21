#!/usr/bin/env python3
"""Run TakeMeter train + evaluate pipeline locally (mirrors Colab notebook).

Outputs evaluation_results.json and confusion_matrix.png in repo root.

Usage:
  python scripts/run_evaluation.py
  python scripts/run_evaluation.py --baseline-only
  python scripts/run_evaluation.py --epochs 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from dotenv import load_dotenv
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    ConfusionMatrixDisplay,
)
from sklearn.model_selection import train_test_split
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    DataCollatorWithPadding,
    Trainer,
    TrainingArguments,
)

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "labeled_posts_export.csv"
OUT_JSON = ROOT / "evaluation_results.json"
OUT_CM = ROOT / "confusion_matrix.png"

LABEL_MAP = {
    "analysis": 0,
    "hot_take": 1,
    "reaction": 2,
    "question": 3,
}
ID_TO_LABEL = {v: k for k, v in LABEL_MAP.items()}
MODEL_NAME = "distilbert-base-uncased"

SYSTEM_PROMPT = """
You are classifying posts and comments from r/leagueoflegends.
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
Do not explain.
"""


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(CSV_PATH)
    df = df.dropna(subset=["text", "label"])
    unknown = set(df["label"].unique()) - set(LABEL_MAP)
    if unknown:
        raise SystemExit(f"Unknown labels in CSV: {unknown}")
    df["label_id"] = df["label"].map(LABEL_MAP).astype(int)

    train_df, temp_df = train_test_split(
        df, test_size=0.30, random_state=42, stratify=df["label_id"]
    )
    val_df, test_df = train_test_split(
        temp_df, test_size=0.50, random_state=42, stratify=temp_df["label_id"]
    )
    return train_df, val_df, test_df


class TextDataset(torch.utils.data.Dataset):
    def __init__(self, texts, labels, tokenizer, max_length=256):
        self.encodings = tokenizer(
            list(texts), truncation=True, padding=False, max_length=max_length
        )
        self.labels = list(labels)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        item = {k: torch.tensor(v[idx]) for k, v in self.encodings.items()}
        item["labels"] = torch.tensor(self.labels[idx])
        return item


def compute_metrics(eval_pred):
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=-1)
    return {"accuracy": accuracy_score(labels, preds)}


def classify_with_groq(client, text: str) -> str | None:
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Classify this post:\n\n{text}"},
            ],
            temperature=0,
            max_tokens=20,
        )
        raw = response.choices[0].message.content.strip().lower()
        for label in sorted(LABEL_MAP, key=len, reverse=True):
            if raw == label or label in raw:
                return label
        return None
    except Exception as e:
        print(f"API error: {e}")
        return None


def run_baseline(test_df: pd.DataFrame) -> tuple[float, dict, int, int]:
    load_dotenv(ROOT / ".env")
    from groq import Groq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise SystemExit("GROQ_API_KEY not set in .env")

    client = Groq(api_key=api_key)
    preds: list[str | None] = []
    print(f"Running Groq baseline on {len(test_df)} test examples...")
    for i, (_, row) in enumerate(test_df.iterrows()):
        preds.append(classify_with_groq(client, row["text"]))
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(test_df)} complete...")
        time.sleep(0.1)

    valid = [(p, t) for p, t in zip(preds, test_df["label_id"]) if p is not None]
    none_count = preds.count(None)
    if not valid:
        return float("nan"), {}, 0, none_count

    bl_pred_ids = [LABEL_MAP[p] for p, _ in valid]
    bl_true_ids = [t for _, t in valid]
    acc = accuracy_score(bl_true_ids, bl_pred_ids)
    report = classification_report(
        bl_true_ids,
        bl_pred_ids,
        target_names=[ID_TO_LABEL[i] for i in range(len(LABEL_MAP))],
        zero_division=0,
        output_dict=True,
    )
    return acc, report, len(valid), none_count


def extract_val_history(trainer: Trainer) -> list[dict]:
    """Per-epoch validation metrics from Trainer log history."""
    rows: list[dict] = []
    for entry in trainer.state.log_history:
        if "eval_loss" not in entry:
            continue
        epoch = entry.get("epoch")
        if epoch is None:
            continue
        epoch_num = int(epoch) if float(epoch).is_integer() else round(epoch)
        train_loss = None
        for prior in reversed(trainer.state.log_history):
            if prior is entry:
                break
            if "loss" in prior and "eval_loss" not in prior:
                pe = prior.get("epoch")
                if pe is not None and int(pe) == epoch_num:
                    train_loss = prior.get("loss")
                    break
        rows.append(
            {
                "epoch": epoch_num,
                "train_loss": round(float(train_loss), 6) if train_loss is not None else None,
                "eval_loss": round(float(entry["eval_loss"]), 6),
                "eval_accuracy": round(float(entry["eval_accuracy"]), 6),
            }
        )
    deduped: dict[int, dict] = {}
    for row in rows:
        deduped[row["epoch"]] = row
    return [deduped[k] for k in sorted(deduped)]


def run_finetune(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
    epochs: int,
    batch_size: int,
    learning_rate: float = 1e-4,
) -> tuple[Trainer, np.ndarray, np.ndarray, np.ndarray, list[dict]]:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME, num_labels=len(LABEL_MAP), id2label=ID_TO_LABEL, label2id=LABEL_MAP
    )

    train_ds = TextDataset(train_df["text"], train_df["label_id"], tokenizer)
    val_ds = TextDataset(val_df["text"], val_df["label_id"], tokenizer)
    test_ds = TextDataset(test_df["text"], test_df["label_id"], tokenizer)
    collator = DataCollatorWithPadding(tokenizer)

    args = TrainingArguments(
        output_dir=str(ROOT / "takemeter-model"),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=32,
        learning_rate=learning_rate,
        weight_decay=0.01,
        warmup_steps=50,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=1,
        load_best_model_at_end=True,
        metric_for_best_model="accuracy",
        logging_steps=10,
        report_to="none",
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=collator,
        compute_metrics=compute_metrics,
    )

    print(f"Fine-tuning {MODEL_NAME} for {epochs} epochs (lr={learning_rate})...")
    trainer.train()
    val_history = extract_val_history(trainer)

    output = trainer.predict(test_ds)
    pred_ids = np.argmax(output.predictions, axis=-1)
    true_ids = output.label_ids
    probs = torch.nn.functional.softmax(
        torch.tensor(output.predictions), dim=-1
    ).numpy()
    return trainer, pred_ids, true_ids, probs, val_history


def save_confusion_matrix(true_ids, pred_ids, path: Path) -> list[list[int]]:
    label_names = [ID_TO_LABEL[i] for i in range(len(LABEL_MAP))]
    cm = confusion_matrix(true_ids, pred_ids)
    disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=label_names)
    fig, ax = plt.subplots(figsize=(7, 5))
    disp.plot(ax=ax, cmap="Blues", colorbar=False)
    ax.set_title("Fine-Tuned Model — Confusion Matrix (Test Set)")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return cm.tolist()


def print_wrong(test_df, pred_ids, true_ids, probs, limit=15):
    wrong_idx = np.where(pred_ids != true_ids)[0]
    print(f"\nWrong predictions: {len(wrong_idx)} / {len(true_ids)}")
    texts = test_df["text"].tolist()
    for i, idx in enumerate(wrong_idx[:limit]):
        conf = float(probs[idx][pred_ids[idx]])
        print(f"\n--- #{i + 1} ---")
        print(f"Text:      {texts[idx][:200]}{'...' if len(texts[idx]) > 200 else ''}")
        print(f"True:      {ID_TO_LABEL[true_ids[idx]]}")
        print(f"Predicted: {ID_TO_LABEL[pred_ids[idx]]}  (confidence: {conf:.2f})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--baseline-accuracy", type=float, default=None,
                        help="Use when Groq rate-limited; skips baseline API calls")
    args = parser.parse_args()

    train_df, val_df, test_df = load_data()
    print(f"Split: train={len(train_df)} val={len(val_df)} test={len(test_df)}")

    bl_acc, bl_report, bl_valid, bl_none = 0.0, {}, 0, 0
    if not args.skip_baseline:
        bl_acc, bl_report, bl_valid, bl_none = run_baseline(test_df)
        print(f"Baseline accuracy: {bl_acc:.3f} ({bl_valid}/{len(test_df)} parseable)")
    elif args.baseline_accuracy is not None:
        bl_acc = args.baseline_accuracy
        bl_valid = len(test_df)
        print(f"Using provided baseline accuracy: {bl_acc:.3f}")
    else:
        print("Skipping baseline (--skip-baseline)")

    if args.baseline_only:
        return

    _, ft_pred_ids, ft_true_ids, ft_probs, val_history = run_finetune(
        train_df, val_df, test_df, args.epochs, args.batch_size, args.learning_rate
    )
    ft_acc = accuracy_score(ft_true_ids, ft_pred_ids)
    ft_report = classification_report(
        ft_true_ids,
        ft_pred_ids,
        target_names=[ID_TO_LABEL[i] for i in range(len(LABEL_MAP))],
        zero_division=0,
        output_dict=True,
    )
    print(f"Fine-tuned accuracy: {ft_acc:.3f}")

    cm = save_confusion_matrix(ft_true_ids, ft_pred_ids, OUT_CM)
    print_wrong(test_df.reset_index(drop=True), ft_pred_ids, ft_true_ids, ft_probs)

    macro_f1 = np.mean([ft_report[l]["f1-score"] for l in LABEL_MAP])
    bl_macro_f1 = None
    if bl_valid:
        bl_macro_f1 = round(
            float(np.mean([bl_report[l]["f1-score"] for l in LABEL_MAP])), 4
        )
    best_val = max(val_history, key=lambda r: r["eval_accuracy"]) if val_history else None
    results = {
        "baseline_accuracy": round(float(bl_acc), 4) if bl_valid else None,
        "baseline_macro_f1": bl_macro_f1,
        "finetuned_accuracy": round(float(ft_acc), 4),
        "improvement": round(float(ft_acc - bl_acc), 4) if bl_valid else None,
        "macro_f1_finetuned": round(float(macro_f1), 4),
        "baseline_parseable": bl_valid,
        "baseline_unparseable": bl_none,
        "test_set_size": len(test_df),
        "val_set_size": len(val_df),
        "train_set_size": len(train_df),
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "batch_size": args.batch_size,
        "label_map": LABEL_MAP,
        "model": MODEL_NAME,
        "validation_history": val_history,
        "best_val_accuracy": best_val["eval_accuracy"] if best_val else None,
        "best_val_epoch": best_val["epoch"] if best_val else None,
        "classification_report_finetuned": ft_report,
        "classification_report_baseline": bl_report if bl_valid else None,
        "confusion_matrix": cm,
    }
    OUT_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved {OUT_JSON.name} and {OUT_CM.name}")


if __name__ == "__main__":
    main()
