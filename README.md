# TAME-Research

TAME-Research is a reproducible evaluation pipeline for measuring toxicity amplification in local large language models. The repository preserves the prompt sets, raw generations, scored outputs, analysis tables, and combined statistical summaries used for the TAME study.

## Paper Title

TAME: Toxicity Amplification Measurement and Evaluation for Local LLM Auditing

## Project Scope

- Total analyzed generations: 20,000
- Models: 5 local Ollama models
- Series 1 models: `mistral`, `llama3`, `phi3:mini-4k`
- Series 2 models: `qwen3:8b`, `gemma3:4b`
- Experimental design: neutral, single-identity, and intersectional prompt variants scored with Detoxify and analyzed with paired and aggregate comparison metrics

## Repository Layout

```text
TAME-Research/
|- data/
|  |- batches/
|  |- series_2/
|  `- prompt and group-definition files
|- outputs/
|  |- figures/
|  |- series_2/
|  `- tables/
|- reports_final_v3/
|- scripts/
|- figures/
|- paper/
|- prompts/
|- README.md
|- requirements.txt
|- setup_instructions.md
|- LICENSE
`- CITATION.cff
```

Notes:

- Prompt CSVs remain in `data/` and `data/series_2/` because the released pipeline reads them there.
- Generated figures and tables remain in `outputs/` because the figure and export scripts are configured to use those paths.
- The combined inferential-statistics release files remain in `reports_final_v3/` because `scripts/04_inferential_stats.py` writes there.

## Core Research Assets Included

This release preserves the files required for scientific reproducibility:

- Prompt CSVs for Series 1 and Series 2
- Group-definition files used to build prompt batches
- `generations_raw.csv`, `generations_scored.csv`, and `tame_analysis.csv` outputs
- Figure-generation and statistical-analysis scripts
- Combined inferential summaries in `reports_final_v3/`
- Generated tables and figures in `outputs/`

## Experiment Pipeline

The project follows a four-stage workflow:

1. Validate prompt files and batch configuration
2. Generate model outputs with Ollama
3. Score outputs with Detoxify
4. Analyze prompt-level and group-level toxicity patterns

Key scripts:

- `scripts/00_validate_dataset.py`
- `scripts/01_generate.py`
- `scripts/02_score.py`
- `scripts/03_analyze.py`
- `scripts/04_inferential_stats.py`
- `scripts/04_make_figures.py`
- `scripts/05_export_paper_tables.py`

## Installation

See [setup_instructions.md](setup_instructions.md) for a fuller environment guide.

Minimum setup:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

This project expects:

- Python 3.11
- Local Ollama installation
- Local availability of the configured models
- Detoxify dependencies, including a working PyTorch installation

## Reproduction Steps

The released repository already contains the final experimental outputs. You do not need to regenerate them to inspect the published results. If you want to rerun the pipeline, use the commands below from the repository root.

### 1. Validate prompt assets

```bash
python scripts/00_validate_dataset.py --batch-id batch_10
python scripts/00_validate_dataset.py --series series_2 --batch-id s2_batch_10
```

### 2. Generate outputs

Series 1:

```bash
python scripts/01_generate.py --batch-id batch_10
```

Series 2:

```bash
python scripts/01_generate.py --series series_2 --batch-id s2_batch_10
```

### 3. Score generations

Series 1:

```bash
python scripts/02_score.py --batch-id batch_10
```

Series 2:

```bash
python scripts/02_score.py --series series_2 --batch-id s2_batch_10
```

### 4. Analyze scored outputs

Series 1:

```bash
python scripts/03_analyze.py --batch-id batch_10
```

Series 2:

```bash
python scripts/03_analyze.py --series series_2 --batch-id s2_batch_10
```

## Statistical Analysis

To regenerate the combined row-level inferential summaries:

```bash
python scripts/04_inferential_stats.py
```

This writes the released combined reports to `reports_final_v3/`.

## Figure Generation

To regenerate the batch-aware figures:

```bash
python scripts/04_make_figures.py
```

Figures are written to:

- `outputs/figures/`
- `outputs/series_2/figures/`

## Table Export

To regenerate publication-ready CSV and Markdown tables:

```bash
python scripts/05_export_paper_tables.py
```

Tables are written to:

- `outputs/tables/`
- `outputs/series_2/tables/`

## Released Results

The main released aggregate outputs are:

- `reports_final_v3/tame_all_rows_20000.csv`
- `reports_final_v3/model_summary_with_ci95.csv`
- `reports_final_v3/prompt_type_summary_with_ci95.csv`
- `reports_final_v3/model_pair_tests.csv`
- `reports_final_v3/paired_prompt_group_tests_by_model.csv`

## Citation

If you use this repository, please cite the project using the metadata in [CITATION.cff](CITATION.cff).

## License

This repository is released under the terms of the [LICENSE](LICENSE) file.

## Research Use Note

The prompts in this repository were designed for bias and safety auditing. They avoid slurs and explicit hate phrasing and should be used for evaluation, auditing, and reproducibility purposes.
