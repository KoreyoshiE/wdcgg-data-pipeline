# Reproducibility guide

## System and installation

Python 3.11 or 3.12 is supported. A machine with at least 16 GB RAM and sufficient free disk space for the extracted observations and generated registries is recommended; exact peak memory is platform-dependent. The complete gap benchmark and archive parsing may take substantially longer than the offline tests. Paths below are examples, not embedded paths in the software.

```bash
python -m venv .venv
python -m pip install -e '.[test]'
python -m pytest -q
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` or invoke `.venv\Scripts\python.exe` directly. The offline suite skips the raw-data regression unless the archive root is explicitly supplied.

## Obtaining the official WDCGG observations

Obtain the study's WDCGG archives from the [WDCGG portal](https://gaw.kishou.go.jp/) under its provider terms. The exact 38-source frozen-study manifest is [`provenance/DOWNLOAD_MANIFEST_V2.csv`](../provenance/DOWNLOAD_MANIFEST_V2.csv); it records candidate ID, gas, record ID, expected SHA-256 and size. The program recursively indexes `.tar.gz` files below a user-supplied `--data-root`. Keep archives outside this repository. The source container hash and extracted observation-member hash are checked separately; an archive recompressed without changing the observation member is reported as container drift.

Fresh downloads can differ from the archived study versions. Such a download is a new source version, not an exact frozen-study reproduction. Do not silently substitute files or reinterpret a failed hash comparison.

## Full revised-study reproduction and frozen verification

Use an empty output directory:

```bash
python scripts/reproduce_study.py --data-root /path/to/wdcgg-archives --output-dir /path/to/new-output --verify-freeze --gap-workers 4
```

The driver verifies archive identity; parses, harmonises and contributor-QC-filters hourly data; constructs monthly coverage and cohorts; runs original analyses and figures; then executes final completeness sensitivity, trend uncertainty, independent anomaly validation and repeated gap-reconstruction validation. The original 12 baseline frozen result tables and the four compact revised frozen tables are numerically compared. A nonzero exit code means a failed stage or comparison. `STUDY_REPRODUCTION_SUMMARY.json` in the output root records the checks.

Expected output subdirectories are `baseline/`, `completeness/`, `trend/`, `anomaly/` and `gap/` (including `gap/paired_inference/`). The anomaly directory contains the post-evaluation operational candidate and a real-data review-flag comparison; these flags do not alter contributor QC or observed-only environmental inputs. Registries can be large; output files are local generated evidence, not source-controlled input. The package never writes into `--data-root`.

## Individual experiments and faster diagnostics

The original baseline alone can be checked with:

```bash
python scripts/reproduce_global_cohort_v2.py --mode frozen --data-root /path/to/wdcgg-archives --output-dir /path/to/new-baseline --verify-against-freeze
```

For a shorter revised run that omits the two expensive validation experiments, pass `--skip-anomaly-validation --skip-gap-validation` to `scripts/reproduce_study.py`. This **does not** constitute full-study validation. The anomaly and gap functions are also callable independently from `wdcgg_pipeline.anomaly_validation` and `wdcgg_pipeline.gap_validation` using the baseline monthly/representative-segment output, their public JSON configs, and a fresh output directory. The complete workflow is the authoritative integration command.

## What is and is not frozen

`results/frozen/` contains compact byte-preserved scientific summaries plus a headline index. `configs/` contains frozen scientific parameters. `scripts/reproduce_study.py --verify-freeze` checks recomputation against these outcomes rather than regenerating the reference values. The manifest fixes the provider inputs actually used. Fresh-provider acquisition is a separate activity and may change source versions, available periods or hashes; it is not interchangeable with the frozen-study verification claim.
