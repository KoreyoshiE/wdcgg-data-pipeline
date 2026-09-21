# Reproducibility guide

## Environment

- Python 3.11 or later
- Dependencies listed in `requirements.txt`
- No local absolute paths are required by the public workflow

Install the package in editable mode and run the offline tests:

```bash
python -m pip install -e .
python -m pytest -q
```

## Frozen reproduction

The frozen workflow expects a directory containing the 38 source `.tar.gz` archives. It indexes candidate files by SHA-256 and matches them to `provenance/DOWNLOAD_MANIFEST_V2.csv`. Outputs are written to a new directory; input archives are not modified.

Major stages are:

1. source matching and safe archive extraction;
2. hourly parsing, UTC/unit harmonisation and contributor-QC filtering;
3. monthly completeness calculations and cohort construction;
4. anomaly and gap-reconstruction validation experiments;
5. time-series, seasonal, trend, latitude, regional and paired-gas analyses;
6. figure generation and comparison with frozen result tables.

The selected public result tables under `results/` allow numerical inspection without redistributing raw observations. Exact upstream reproduction still requires the frozen provider archives.
