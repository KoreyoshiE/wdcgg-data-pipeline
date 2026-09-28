"""Reproduce the frozen revised WDCGG study from official source archives."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wdcgg_pipeline.anomaly_validation import reproduce_anomaly
from wdcgg_pipeline.coverage_sensitivity import reproduce_grid
from wdcgg_pipeline.gap_validation import reproduce_gap
from wdcgg_pipeline.trend import reproduce_core


def compare_table(actual: pd.DataFrame, frozen_path: Path, keys: list[str],
                  columns: list[str] | None = None) -> dict:
    frozen = pd.read_csv(frozen_path)
    if len(actual) != len(frozen):
        raise ValueError(f"Row count differs from frozen table: {frozen_path.name}")
    if columns is None:
        columns = [column for column in frozen.columns if column in actual.columns and column not in keys]
    a = actual.sort_values(keys).reset_index(drop=True)
    b = frozen.sort_values(keys).reset_index(drop=True)
    if not a[keys].equals(b[keys]):
        raise ValueError(f"Row identities differ from frozen table: {frozen_path.name}")
    maximum = 0.0
    for column in columns:
        left, right = a[column], b[column]
        if pd.api.types.is_numeric_dtype(left) and pd.api.types.is_numeric_dtype(right):
            x, y = left.to_numpy(dtype=float), right.to_numpy(dtype=float)
            if not np.allclose(x, y, rtol=1e-10, atol=1e-10, equal_nan=True):
                raise ValueError(f"Frozen numerical regression changed: {frozen_path.name}:{column}")
            finite = np.abs(x - y)
            if np.isfinite(finite).any():
                maximum = max(maximum, float(np.nanmax(finite)))
        elif not left.fillna("").astype(str).equals(right.fillna("").astype(str)):
            raise ValueError(f"Frozen text regression changed: {frozen_path.name}:{column}")
    return dict(table=frozen_path.name, rows=len(actual), compared_columns=len(columns),
                maximum_absolute_numeric_difference=maximum, status="PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path,
                        help="Directory containing the 38 official WDCGG .tar.gz archives")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="New directory for reproduced outputs; inputs are never modified")
    parser.add_argument("--config-dir", type=Path, default=ROOT / "configs")
    parser.add_argument("--verify-freeze", action="store_true",
                        help="Fail if any reproduced frozen baseline or revised table differs")
    parser.add_argument("--skip-anomaly-validation", action="store_true")
    parser.add_argument("--skip-gap-validation", action="store_true")
    parser.add_argument("--gap-workers", type=int, default=4)
    args = parser.parse_args()
    if not args.data_root.is_dir():
        parser.error("--data-root must be an existing directory")
    if args.gap_workers < 1:
        parser.error("--gap-workers must be positive")
    output = args.output_dir.resolve()
    baseline = output / "baseline"
    if baseline.exists() and any(baseline.iterdir()):
        parser.error("output baseline directory already contains files; choose a new --output-dir")
    output.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(ROOT / "scripts" / "reproduce_global_cohort_v2.py"),
               "--mode", "frozen", "--data-root", str(args.data_root.resolve()),
               "--output-dir", str(baseline)]
    if args.verify_freeze:
        command.append("--verify-against-freeze")
    subprocess.run(command, check=True)
    frozen_root = ROOT / "results" / "frozen"
    checks = []
    coverage = reproduce_grid(baseline, output / "completeness",
                              args.config_dir / "completeness_sensitivity.json")
    if args.verify_freeze:
        checks.append(compare_table(coverage, frozen_root / "completeness_sensitivity.csv",
                                    ["within_month_threshold", "station_qualified_month_fraction"]))
    trend = reproduce_core(baseline, output / "trend", args.config_dir / "trend_analysis.json")
    if args.verify_freeze:
        checks.append(compare_table(trend, frozen_root / "trend_core_summary.csv",
                                    ["gas", "method"],
                                    ["seasonal_sen_median", "cohort_ci_lower", "cohort_ci_upper",
                                     "cohort_ci_width", "bootstrap_median", "bootstrap_mean"]))
    if not args.skip_anomaly_validation:
        anomaly = reproduce_anomaly(baseline, output / "anomaly",
                                    args.config_dir / "anomaly_validation.json")
        if args.verify_freeze:
            checks.append(compare_table(anomaly, frozen_root / "anomaly_heldout_summary.csv",
                                        ["scope", "metric"]))
            checks.append(compare_table(
                pd.read_csv(output / "anomaly" / "operational_candidate.csv",
                            float_precision="round_trip"),
                frozen_root / "anomaly_operational_candidate.csv", ["configuration_id"]))
            checks.append(compare_table(
                pd.read_csv(output / "anomaly" / "realdata_review_flags.csv",
                            float_precision="round_trip"),
                frozen_root / "anomaly_realdata_review_flags.csv", ["dataset_key", "role"]))
    if not args.skip_gap_validation:
        gap = reproduce_gap(baseline, output / "gap", args.config_dir / "gap_validation.json",
                            workers=args.gap_workers)
        if args.verify_freeze:
            checks.append(compare_table(gap, frozen_root / "gap_method_summary.csv",
                                        ["gas", "gap_hours", "method"]))
            checks.append(compare_table(
                pd.read_csv(output / "gap" / "paired_inference" / "block_bootstrap_summary.csv",
                            float_precision="round_trip"),
                frozen_root / "gap_block_summary.csv",
                ["gas", "gap_hours", "analysis_type", "identity", "block_unit"]))
            checks.append(compare_table(
                pd.read_csv(output / "gap" / "paired_inference" / "station_paired_contrasts.csv",
                            float_precision="round_trip"),
                frozen_root / "gap_paired_station_contrasts.csv",
                ["physical_station_id", "gas", "gap_hours", "pair"]))
    summary = dict(status="PASS" if args.verify_freeze else "COMPUTED_NOT_COMPARED",
                   scientific_evidence_state="FROZEN", source_archives=38,
                   baseline_summary=json.loads((baseline / "REPRODUCTION_SUMMARY.json").read_text()),
                   revised_checks=checks, skipped_anomaly=args.skip_anomaly_validation,
                   skipped_gap=args.skip_gap_validation,
                   output_directory=str(output))
    (output / "STUDY_REPRODUCTION_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
