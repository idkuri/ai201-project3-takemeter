# Scripts

TakeMeter dataset and evaluation tooling. Final dataset: `data/labeled_posts_export.csv`.

**Primary training/eval:** Google Colab notebook (`ai201_project3_takemeter_starter_clean.ipynb`, Sections 1–6). Download `evaluation_results.json` and `confusion_matrix.png` from Colab.

## Core workflow

| Script | Purpose |
|--------|---------|
| [`review_gui.py`](review_gui.py) | Review and fix labels; **Save + apply** writes to the dataset CSV |
| [`rebalance_dataset.py`](rebalance_dataset.py) | Class balance: import browser pool, swap rows, export review queue |
| [`export_review_queue.py`](export_review_queue.py) | Build or prune `data/review_queue.csv` for the GUI |
| [`run_evaluation.py`](run_evaluation.py) | Optional local train + eval fallback (Colab is primary) |
| [`dataset_utils.py`](dataset_utils.py) | Audit, validate, duplicate checks |

## Browser collection

Reddit blocks server-side scraping. Run in the browser on old.reddit.com:

| File | Output |
|------|--------|
| [`collect_in_browser.js`](collect_in_browser.js) | General posts → `data/reddit_pool/collection.json` |
| [`collect_analysis_in_browser.js`](collect_analysis_in_browser.js) | Analysis posts → `data/reddit_pool/fresh_analysis.json` |

Then: `python scripts/rebalance_dataset.py import-pool <json file>`

## Evaluation

**Colab (primary):** Run notebook Sections 1–6 on T4 GPU. Section 6 writes `evaluation_results.json` with validation history, per-class reports, and confusion matrix.

**Latest Colab results** (repo-root `evaluation_results.json`):

| Model | Test accuracy | Macro F1 | Val accuracy (best) |
|-------|---------------|----------|---------------------|
| Groq baseline | 0.567 | 0.560 | — |
| Fine-tuned (`1e-4`, 5 epochs) | 0.467 | 0.440 | **0.733** (epoch 5) |

**Local fallback:** `python scripts/run_evaluation.py` (requires `GROQ_API_KEY` in `.env`; CPU-only, may differ slightly from Colab).

## Common commands

```bash
python scripts/review_gui.py
python scripts/rebalance_dataset.py status
python scripts/dataset_utils.py validate
python scripts/export_review_queue.py export
python scripts/export_review_queue.py prune
python scripts/run_evaluation.py --skip-baseline   # local train only
```
