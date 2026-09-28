"""Archive-dependent regression, excluded from the default offline test run."""

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wdcgg_pipeline.coverage_sensitivity import reproduce_grid
from wdcgg_pipeline.anomaly_validation import reproduce_anomaly
from wdcgg_pipeline.gap_validation import reproduce_gap
from wdcgg_pipeline.trend import reproduce_core
from scripts.reproduce_study import compare_table


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.requires_wdcgg


@pytest.mark.skipif(not os.environ.get("WDCGG_DATA_ROOT"), reason="set WDCGG_DATA_ROOT to the frozen archives")
def test_full_frozen_archive_regression(tmp_path):
    data_root = Path(os.environ["WDCGG_DATA_ROOT"])
    baseline = tmp_path / "baseline"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "reproduce_global_cohort_v2.py"),
                    "--mode", "frozen", "--data-root", str(data_root),
                    "--output-dir", str(baseline), "--verify-against-freeze"], check=True)
    coverage = reproduce_grid(baseline, tmp_path / "coverage",
                              ROOT / "configs" / "completeness_sensitivity.json")
    expected_grid = pd.read_csv(ROOT / "results" / "frozen" / "completeness_sensitivity.csv")
    numeric = coverage.select_dtypes(include=[np.number]).columns
    np.testing.assert_allclose(coverage[numeric], expected_grid[numeric], rtol=1e-10, atol=1e-10)
    assert coverage.region_names.equals(expected_grid.region_names)
    trend = reproduce_core(baseline, tmp_path / "trend", ROOT / "configs" / "trend_analysis.json")
    frozen = pd.read_csv(ROOT / "results" / "frozen" / "trend_core_summary.csv")
    for column in ("seasonal_sen_median", "cohort_ci_lower", "cohort_ci_upper",
                   "cohort_ci_width", "bootstrap_median", "bootstrap_mean"):
        np.testing.assert_allclose(trend[column], frozen[column], rtol=0, atol=1e-10)
    anomaly = reproduce_anomaly(baseline, tmp_path / "anomaly",
                                ROOT / "configs" / "anomaly_validation.json")
    frozen_anomaly = pd.read_csv(ROOT / "results" / "frozen" / "anomaly_heldout_summary.csv")
    for column in ("estimate", "ci_lower", "ci_upper", "fold_min", "fold_max"):
        np.testing.assert_allclose(anomaly[column], frozen_anomaly[column], rtol=0, atol=1e-10)
    compare_table(pd.read_csv(tmp_path / "anomaly" / "operational_candidate.csv",
                              float_precision="round_trip"),
                  ROOT / "results" / "frozen" / "anomaly_operational_candidate.csv",
                  ["configuration_id"])
    compare_table(pd.read_csv(tmp_path / "anomaly" / "realdata_review_flags.csv",
                              float_precision="round_trip"),
                  ROOT / "results" / "frozen" / "anomaly_realdata_review_flags.csv",
                  ["dataset_key", "role"])
    gap = reproduce_gap(baseline, tmp_path / "gap", ROOT / "configs" / "gap_validation.json", workers=4)
    frozen_gap = pd.read_csv(ROOT / "results" / "frozen" / "gap_method_summary.csv")
    for column in ("median_rmse", "median_mae", "q25_station_rmse", "q75_station_rmse"):
        np.testing.assert_allclose(gap[column], frozen_gap[column], rtol=0, atol=1e-10)
    inference = tmp_path / "gap" / "paired_inference"
    compare_table(pd.read_csv(inference / "block_bootstrap_summary.csv", float_precision="round_trip"),
                  ROOT / "results" / "frozen" / "gap_block_summary.csv",
                  ["gas", "gap_hours", "analysis_type", "identity", "block_unit"])
    compare_table(pd.read_csv(inference / "station_paired_contrasts.csv", float_precision="round_trip"),
                  ROOT / "results" / "frozen" / "gap_paired_station_contrasts.csv",
                  ["physical_station_id", "gas", "gap_hours", "pair"])
