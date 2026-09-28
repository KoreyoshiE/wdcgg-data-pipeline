# WDCGG Data Pipeline

Reproducible screening, quality control and analysis of long-term WDCGG atmospheric CO2 and CH4 observations for the DSIT 2026 peer-review study. The repository contains executable research software, tests, source manifests, frozen compact results and documentation. It does **not** redistribute the third-party hourly observations.

## Overview

The study screened 362 station–gas catalogue products, identified 137 public-hourly products, and acquired 38 official datasets spanning 23 physical stations. A non-random Core cohort contains 26 station–gas products at 14 stations; an Extended cohort contains 29 products at 16 stations. Source identity, UTC/unit harmonisation, contributor quality-control (QC), and completeness are established before environmental analyses.

## Scientific workflow

1. Match the 38 study archives to the [frozen source manifest](provenance/DOWNLOAD_MANIFEST_V2.csv) by SHA-256 and verify extracted observation members.
2. Parse hourly records, harmonise UTC timestamps and units, and retain contributor-valid observations under WDCGG QC rules.
3. Build observed-only monthly means. Qualify Core products at 75% within-month and 75% station-period coverage; use 75% / 60% for Extended.
4. Recompute the 20-cell completeness sensitivity grid independently for each threshold combination.
5. Validate four anomaly-detector families on five synthetic event types with physical-station-held-out selection. Structurally absent seasonal events remain labelled negative controls, not pseudo-zero F1 observations.
6. Compare five gap-reconstruction methods across historical anchors and repeated non-overlapping placements, with paired physical-station summaries.
7. Estimate Core same-month Seasonal Sen trends and dependence-aware uncertainty using a run-aware residual moving-block bootstrap (12-month primary; 6- and 24-month sensitivity) and physical-station cohort resampling.

The [architecture](docs/ARCHITECTURE.md) and [methods map](docs/METHODS_IMPLEMENTATION.md) connect these stages to public code.

## Repository structure

| Path | Contents |
|---|---|
| `src/wdcgg_pipeline/` | Parsing, QC, cohort, analysis and revised experiment modules |
| `scripts/` | Full-study and baseline reproduction CLIs |
| `configs/` | Frozen baseline and revision parameters |
| `provenance/` | Source identities and release-file hashes |
| `results/frozen/` | Compact final scientific summaries |
| `tests/` and `data/examples/` | Offline tests and synthetic input fixtures |
| `docs/` | Reproduction, methods, data availability and figures |

## Installation and quick start

Python 3.11 or 3.12 is supported:

```bash
python -m venv .venv
python -m pip install -e '.[test]'
python -m pytest -q
python scripts/reproduce_study.py --help
```

For PowerShell, invoke `.venv\Scripts\python.exe` in place of `python` if the environment is not activated. The default tests use synthetic fixtures and compact frozen fingerprints; official WDCGG archives are not fetched by CI.

## Reproduce the revised study

After obtaining the 38 source archives under their provider terms, run:

```bash
python scripts/reproduce_study.py --data-root /path/to/wdcgg-archives --output-dir /path/to/empty-output --verify-freeze --gap-workers 4
```

This runs original baseline reproduction, completeness sensitivity, trend uncertainty, held-out anomaly validation and repeated gap validation, and checks regenerated tables against the frozen results. The full archive-dependent workflow requires substantial disk space and compute time. See the [reproducibility guide](docs/REPRODUCIBILITY.md) for source layout, stage outputs, faster diagnostics and the distinction between frozen reproduction and fresh provider acquisition.

## Final study design and key frozen results

| Analysis | Frozen revised result |
|---|---:|
| Core / Extended | 26 products, 14 stations / 29 products, 16 stations |
| Independent anomaly validation | 5 held-out physical-station folds; 1,000 synthetic realizations; 12,000 detector evaluations; primary F1 = 0.2173 |
| Structural seasonal-event controls | 60 of 1,000 realizations; excluded from positive-event F1/recall aggregation |
| Gap benchmark | 91 historical anchors + 1,345 repeated placements = 1,436 base-gap cases; 7,180 method evaluations |
| Core CO2 Seasonal Sen | 2.5113 ppm yr⁻¹; descriptive cohort interval [2.4633, 2.5355] |
| Core CH4 Seasonal Sen | 11.3146 ppb yr⁻¹; descriptive cohort interval [10.9303, 11.7709] |

Exact values and comparison tables are in [`results/frozen/`](results/frozen/) and [`headline_results.json`](results/frozen/headline_results.json). These are summaries of a selected, non-random Core cohort, **not global atmospheric averages**. Statistical anomaly flags are review annotations, not automatic observation deletion. Gap reconstruction benchmarks do not authorise global imputation. Primary environmental findings use observed-only, contributor-valid data.

## Data availability and reproducibility

Raw WDCGG observations are subject to provider terms and are not included here. See [data availability](docs/DATA_AVAILABILITY.md) for acquisition, hashes, directory layout and third-party licensing; see [reproducibility](docs/REPRODUCIBILITY.md) for the executable verification procedure.

## Citation, license and publication status

Use [`CITATION.cff`](CITATION.cff) for software attribution. The software is distributed under the [MIT License](LICENSE); WDCGG data and third-party materials retain their own terms. The associated DSIT 2026 manuscript remains under peer-review revision; no acceptance, DOI or proceedings citation is claimed here.
