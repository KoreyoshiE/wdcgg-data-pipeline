"""Run a small, archive-free end-to-end scientific behaviour smoke check."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wdcgg_pipeline.coverage_sensitivity import qualify
from wdcgg_pipeline.parser import iter_wdcgg_rows
from wdcgg_pipeline.qc import map_wdcgg_qc
from wdcgg_pipeline.trend import candidate_blocks, seasonal_sen


def main() -> None:
    fixture = ROOT / "data" / "examples" / "wdcgg_surface_official_format_synthetic.txt"
    row = next(iter_wdcgg_rows(fixture))
    assert row.timestamp_utc.utcoffset().total_seconds() == 0
    assert map_wdcgg_qc("1").retain and not map_wdcgg_qc("3").retain
    months = pd.DataFrame({"provider_valid_hour_count": [540, 539],
                           "expected_hour_count": [720, 720]})
    assert qualify(months, 0.75).qualified.tolist() == [True, False]
    dates = pd.date_range("2020-01-01", periods=36, freq="MS", tz="UTC")
    values = pd.Series(1.25 * np.arange(36) / 12.0, index=dates)
    assert abs(seasonal_sen(values) - 1.25) < 1e-12
    assert len(candidate_blocks(dates, 12)) == 25
    print(json.dumps({"status": "PASS", "fixture": fixture.name,
                      "qualified_months": 1, "seasonal_sen": 1.25}))


if __name__ == "__main__":
    main()
