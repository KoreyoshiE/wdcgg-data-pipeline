"""Offline invariants of the frozen revised design and compact results."""

import json
import hashlib
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wdcgg_pipeline.coverage_sensitivity import qualify
from wdcgg_pipeline.trend import (
    candidate_blocks,
    contiguous_runs,
    residual_decomposition,
    same_month_geometry,
    seasonal_sen,
    stable_seed,
)


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_result_shapes_and_headlines():
    frozen = ROOT / "results" / "frozen"
    headline = json.loads((frozen / "headline_results.json").read_text(encoding="utf-8"))
    grid = pd.read_csv(frozen / "completeness_sensitivity.csv")
    anomaly = pd.read_csv(frozen / "anomaly_heldout_summary.csv")
    operational = pd.read_csv(frozen / "anomaly_operational_candidate.csv")
    realdata = pd.read_csv(frozen / "anomaly_realdata_review_flags.csv")
    gap = pd.read_csv(frozen / "gap_method_summary.csv")
    trend = pd.read_csv(frozen / "trend_core_summary.csv")
    assert len(grid) == 20
    assert len(gap) == 70
    assert len(operational) == 1 and len(realdata) == 20
    assert realdata.contributor_qc_unchanged.all()
    primary = grid.loc[(grid.within_month_threshold == .75)
                       & (grid.station_qualified_month_fraction == .75)].iloc[0]
    assert (primary.retained_datasets, primary.physical_stations) == (26, 14)
    f1 = anomaly.loc[(anomaly.scope == "PRIMARY_STATION_HELDOUT")
                     & (anomaly.metric == "f1"), "estimate"].item()
    assert f1 == pytest.approx(headline["anomaly_validation"]["heldout_macro_f1"], abs=1e-15)
    for gas, field in (("CO2", "co2_ppm_per_year"), ("CH4", "ch4_ppb_per_year")):
        row = trend.loc[(trend.gas == gas) & (trend.role == "PRIMARY")].iloc[0]
        assert row.seasonal_sen_median == pytest.approx(
            headline["primary_core_seasonal_sen"][field], abs=1e-15)


def test_missing_months_are_not_compressed_into_source_blocks():
    index = pd.DatetimeIndex(["2020-01-01", "2020-02-01", "2020-04-01", "2020-05-01"], tz="UTC")
    assert [len(run) for run in contiguous_runs(index)] == [2, 2]
    assert candidate_blocks(index, 2).tolist() == [[0, 1], [2, 3]]
    assert candidate_blocks(index, 3).shape == (0, 3)


def test_same_month_pairs_and_residual_reconstruction():
    index = pd.date_range("2018-01-01", periods=36, freq="MS", tz="UTC")
    values = pd.Series(2.0 * np.arange(36) / 12.0 + 1.0 * np.sin(np.arange(36) * np.pi / 6),
                       index=index)
    a, b, _ = same_month_geometry(index)
    assert np.all(index.month[a] == index.month[b])
    theta = seasonal_sen(values)
    model = residual_decomposition(values, theta)
    np.testing.assert_allclose(model["fitted"] + model["seasonal"] + model["residual"],
                               values.to_numpy(), rtol=0, atol=1e-12)


def test_qualification_uses_count_over_expected_utc_hours():
    frame = pd.DataFrame({"provider_valid_hour_count": [540, 539, 0],
                          "expected_hour_count": [720, 720, 744]})
    assert qualify(frame, .75).qualified.tolist() == [True, False, False]


def test_seed_is_deterministic_and_scope_specific():
    a = stable_seed(20260927, "rev04d_station", "BRW4003_CO2", 12)
    assert a == stable_seed(20260927, "rev04d_station", "BRW4003_CO2", 12)
    assert a != stable_seed(20260927, "rev04d_station", "BRW4003_CO2", 6)


def test_public_release_manifest_hashes():
    manifest = json.loads((ROOT / "provenance" / "RELEASE_MANIFEST.json")
                          .read_text(encoding="utf-8"))
    assert manifest["release_version"] == "1.1.0"
    assert manifest["file_count"] == len(manifest["files"])
    assert manifest["file_count"] >= 60
    for item in manifest["files"]:
        assert item["path"] != "provenance/RELEASE_MANIFEST.json"
        path = ROOT / item["path"]
        assert path.is_file()
        blob = subprocess.check_output(["git", "show", f"HEAD:{item['path']}"], cwd=ROOT)
        assert len(blob) == item["size_bytes"]
        assert hashlib.sha256(blob).hexdigest() == item["sha256"]
