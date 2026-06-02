# Setup Instructions

## Environment

Recommended baseline:

- Python 3.11
- Ollama installed locally
- Enough disk space for generated outputs and figures

Create and activate a virtual environment:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Model Requirements

Series 1:

- `mistral`
- `llama3`
- `phi3:mini-4k`

Series 2:

- `qwen3:8b`
- `gemma3:4b`

Install or pull the models locally with Ollama before generation.

## Detoxify and PyTorch

`detoxify` depends on PyTorch. If `pip install -r requirements.txt` does not provide a usable PyTorch build for your system, install a compatible PyTorch build first and then reinstall `detoxify` if needed.

## Validation

Run prompt validation before generation:

```bash
python scripts/00_validate_dataset.py --batch-id batch_10
python scripts/00_validate_dataset.py --series series_2 --batch-id s2_batch_10
```

## Reproducing Existing Results

The repository already includes the final released outputs. Reproduction commands are useful if you want to rerun parts of the pipeline from scratch.

Generate:

```bash
python scripts/01_generate.py --batch-id batch_10
python scripts/01_generate.py --series series_2 --batch-id s2_batch_10
```

Score:

```bash
python scripts/02_score.py --batch-id batch_10
python scripts/02_score.py --series series_2 --batch-id s2_batch_10
```

Analyze:

```bash
python scripts/03_analyze.py --batch-id batch_10
python scripts/03_analyze.py --series series_2 --batch-id s2_batch_10
```

Combined inferential statistics:

```bash
python scripts/04_inferential_stats.py
```

Figures:

```bash
python scripts/04_make_figures.py
```

Publication tables:

```bash
python scripts/05_export_paper_tables.py
```

## Result Locations

- Prompt assets: `data/`, `data/series_2/`
- Batch outputs: `data/batches/`, `data/series_2/batches/`
- Figures: `outputs/figures/`, `outputs/series_2/figures/`
- Tables: `outputs/tables/`, `outputs/series_2/tables/`
- Combined inferential reports: `reports_final_v3/`
