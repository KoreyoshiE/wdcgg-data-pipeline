"""Threshold-specific monthly qualification and final cohort sensitivity."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from wdcgg_pipeline.analysis import amplitude, slope
from wdcgg_pipeline.trend import MONTHS, same_month_geometry, seasonal_sen


def qualify(frame: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Retain all 120 target months; absence is explicit zero coverage."""
    out = frame.copy()
    out["coverage_recomputed"] = out.provider_valid_hour_count / out.expected_hour_count
    out["qualified"] = out.coverage_recomputed >= threshold
    return out


def load_groups(monthly_dir: Path):
    month = pd.read_csv(monthly_dir / "MONTHLY_PROVIDER_VALID_V2.csv")
    month["date"] = pd.to_datetime(dict(year=month.year, month=month.month, day=1), utc=True)
    month["dataset_key"] = month.dataset_id.astype(str) + "_" + month.gas
    month = month[month.date.isin(MONTHS)].copy()
    harm = pd.read_csv(monthly_dir / "HARMONISATION_RESULTS_V2.csv")
    harm["dataset_key"] = harm.wdcgg_id.astype(str) + "_" + harm.gas
    groups = {}
    for key in harm.dataset_key:
        frame = month[month.dataset_key == key].set_index("date").sort_index()
        if frame.index.duplicated().any():
            raise ValueError(f"Duplicate monthly timestamp: {key}")
        frame = frame.reindex(MONTHS)
        frame["provider_valid_hour_count"] = frame.provider_valid_hour_count.fillna(0)
        expected = pd.Series(MONTHS.days_in_month * 24, index=MONTHS)
        known = frame.expected_hour_count.notna()
        if not np.allclose(frame.loc[known, "expected_hour_count"], expected.loc[known]):
            raise ValueError(f"Expected-hour mismatch: {key}")
        frame["expected_hour_count"] = expected
        coverage = frame.provider_valid_hour_count / frame.expected_hour_count
        available = frame.coverage_fraction.notna()
        if not np.allclose(coverage[available], frame.loc[available, "coverage_fraction"], rtol=0, atol=1e-14):
            raise ValueError(f"Coverage mismatch: {key}")
        frame["coverage_fraction"] = coverage
        groups[key] = frame
    return harm, groups


def reproduce_grid(monthly_dir: Path, output_dir: Path, config_path: Path) -> pd.DataFrame:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    harm, groups = load_groups(monthly_dir)
    rows = []
    for month_threshold in config["grid"]["within_month"]:
        qualified = {key: qualify(frame, month_threshold) for key, frame in groups.items()}
        series = {key: frame.loc[frame.qualified, "mean"].astype(float)
                  for key, frame in qualified.items()}
        fractions = {key: len(values) / 120 for key, values in series.items()}
        primary_slopes = {key: seasonal_sen(values) if len(same_month_geometry(values.index)[0]) else np.nan
                          for key, values in series.items()}
        raw_slopes = {key: slope(values) for key, values in series.items()}
        amplitudes = {key: amplitude(values) for key, values in series.items()}
        for station_fraction in config["grid"]["station_fraction"]:
            retained = {key for key, fraction in fractions.items() if fraction >= station_fraction}
            cohort = harm[harm.dataset_key.isin(retained)]
            row = dict(within_month_threshold=month_threshold,
                       station_qualified_month_fraction=station_fraction,
                       retained_datasets=len(cohort),
                       physical_stations=cohort.wdcgg_id.nunique(),
                       co2_datasets=int((cohort.gas == "CO2").sum()),
                       ch4_datasets=int((cohort.gas == "CH4").sum()),
                       paired_stations=int(cohort.groupby("wdcgg_id").gas.nunique().ge(2).sum()),
                       regions=cohort.wmo_region.nunique(),
                       latitude_min=cohort.latitude.min(),
                       latitude_max=cohort.latitude.max(),
                       provider_valid_observations=int(cohort.provider_valid_count.sum()),
                       median_provider_valid_fraction=cohort.provider_valid_fraction.median(),
                       south_america_retained=bool(cohort.wmo_region.eq("REGION III (South America)").any()),
                       region_names="|".join(sorted(cohort.wmo_region.unique())),
                       qualified_target_provider_valid_observations=int(sum(
                           qualified[key].loc[lambda x: x.qualified, "provider_valid_hour_count"].sum()
                           for key in retained)))
            for gas in ("CO2", "CH4"):
                keys = sorted(cohort.loc[cohort.gas == gas, "dataset_key"])
                all_keys = sorted(harm.loc[harm.gas == gas, "dataset_key"])
                prefix = gas.lower()
                primary = float(np.nanmedian([primary_slopes[key] for key in keys]))
                raw = float(np.nanmedian([raw_slopes[key] for key in keys]))
                amp = float(np.nanmedian([amplitudes[key] for key in keys]))
                row[f"{prefix}_median_trend"] = primary
                row[f"{prefix}_trend_shift_vs_all_candidates"] = primary - float(np.nanmedian(
                    [primary_slopes[key] for key in all_keys]))
                row[f"{prefix}_median_amplitude"] = amp
                row[f"{prefix}_amplitude_shift_vs_all_candidates"] = amp - float(np.nanmedian(
                    [amplitudes[key] for key in all_keys]))
                row[f"{prefix}_median_trend_RAW_HISTORICAL"] = raw
                row[f"{prefix}_trend_shift_vs_all_candidates_RAW_HISTORICAL"] = raw - float(np.nanmedian(
                    [raw_slopes[key] for key in all_keys]))
            row["primary_trend_estimator"] = "SEASONAL_SEN_POOLED"
            rows.append(row)
    result = pd.DataFrame(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_dir / "completeness_sensitivity.csv", index=False, float_format="%.17g")
    return result
