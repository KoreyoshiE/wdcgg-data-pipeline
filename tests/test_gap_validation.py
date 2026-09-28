"""Offline design checks for repeated, non-overlapping artificial gaps."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from wdcgg_pipeline.gap_validation import feasible_positions, select_placements
from wdcgg_pipeline import gap_inference


CONFIG = json.loads((Path(__file__).resolve().parents[1] / "configs" / "gap_validation.json")
                    .read_text(encoding="utf-8"))


def test_strict_frozen_position_margins():
    index = pd.date_range("2020-01-01", periods=1200, freq="h", tz="UTC")
    series = pd.Series(np.arange(len(index), dtype=float), index=index, name="TEST_CO2")
    feasible = feasible_positions(series, "DJF")
    assert feasible.min() == 201
    assert feasible.max() == 899


def test_repeated_placements_do_not_overlap_anchor_or_each_other():
    index = pd.date_range("2020-01-01", periods=1800, freq="h", tz="UTC")
    series = pd.Series(np.arange(len(index), dtype=float), index=index, name="TEST_CO2")
    length = 24
    starts, anchor = select_placements(series, "DJF", length, CONFIG)
    assert len(starts) == CONFIG["target_repeated_placements"]
    assert starts == select_placements(series, "DJF", length, CONFIG)[0]
    intervals = [(start, start + length) for start in [anchor, *starts]]
    for i, (left, right) in enumerate(intervals):
        for other_left, other_right in intervals[i + 1:]:
            assert right <= other_left or other_right <= left


def test_paired_gap_difference_uses_the_same_base_gap(monkeypatch):
    monkeypatch.setattr(gap_inference, "PAIRS", [("a", "b")])
    rows = []
    for gap_id, a_rmse, b_rmse in (("g1", 1.0, 2.0), ("g2", 3.0, 2.0)):
        for method, rmse in (("a", a_rmse), ("b", b_rmse)):
            rows.append(dict(base_gap_id=gap_id, dataset_key="TST0001_CO2",
                             physical_station_id="TST0001", gas="CO2", season="DJF",
                             gap_hours=1, placement_id=1, placement_role="REPEATED_DESIGN",
                             method=method, rmse=rmse, mae=rmse,
                             all_method_complete_base_gap=True))
    paired = gap_inference.paired_base(pd.DataFrame(rows))
    assert paired.sort_values("base_gap_id").delta_rmse.tolist() == [-1.0, 1.0]
    station = gap_inference.paired_stations(gap_inference.paired_strata(paired))
    assert len(station) == 1
    assert station.iloc[0].paired_n == 2
    assert station.iloc[0].median_delta_rmse == 0.0
