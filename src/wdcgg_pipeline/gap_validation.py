"""Frozen non-overlapping artificial-gap design and equal-station summaries."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from wdcgg_pipeline import experiments
from wdcgg_pipeline.anomaly_validation import representative_series


SEASONS = {"DJF": [12, 1, 2], "MAM": [3, 4, 5],
           "JJA": [6, 7, 8], "SON": [9, 10, 11]}


def scoped_seed(config: dict, *parts) -> int:
    token = "|".join(map(str, (config["master_placement_seed"], *parts))).encode()
    return int.from_bytes(hashlib.sha256(token).digest()[:8], "big")


def feasible_positions(series: pd.Series, season: str) -> np.ndarray:
    positions = np.where(series.index.month.isin(SEASONS[season]))[0]
    return positions[(positions > 200) & (positions < len(series) - 300)]


def anchor_position(series: pd.Series, feasible: np.ndarray, length: int) -> int:
    return min(int(feasible[len(feasible) // 2]), len(series) - length - 200)


def select_placements(series: pd.Series, season: str, length: int, config: dict):
    pool = feasible_positions(series, season)
    if not len(pool):
        return [], None
    anchor = anchor_position(series, pool, length)
    rng = np.random.default_rng(scoped_seed(config, "placement", series.name, season, length))
    accepted = []
    for start in rng.permutation(pool[pool != anchor]):
        start = int(start)
        if start < anchor + length and anchor < start + length:
            continue
        if any(start < q + length and q < start + length for q in accepted):
            continue
        accepted.append(start)
        if len(accepted) == config["target_repeated_placements"]:
            break
    return accepted, anchor


def placement_registry(series: dict[str, pd.Series], config: dict) -> pd.DataFrame:
    rows = []
    lengths = config["gap_lengths_hours"]
    for key, values in series.items():
        values.name = key
        station, gas = key.rsplit("_", 1)
        for season in SEASONS:
            for length in lengths:
                starts, anchor = select_placements(values, season, length, config)
                assignments = [("HISTORICAL_ANCHOR", 0, anchor)] if anchor is not None else []
                assignments += [("REPEATED_DESIGN", i + 1, q) for i, q in enumerate(starts)]
                for role, placement_id, start in assignments:
                    identity = f"{key}|{season}|{length}|{values.index[start].isoformat()}"
                    rows.append(dict(dataset_key=key, physical_station_id=station, gas=gas,
                                     season=season, gap_hours=length,
                                     base_gap_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
                                     placement_id=placement_id, placement_role=role,
                                     start_index=start, start_utc=str(values.index[start]),
                                     seed=scoped_seed(config, "placement", key, season, length)))
    registry = pd.DataFrame(rows)
    if registry.base_gap_id.duplicated().any():
        raise ValueError("Duplicate artificial-gap identity")
    if (registry.placement_role == "HISTORICAL_ANCHOR").sum() != 91:
        raise ValueError("Historical anchor count changed")
    if (registry.placement_role == "REPEATED_DESIGN").sum() != 1345:
        raise ValueError("Repeated non-overlapping placement count changed")
    return registry


def gap_metrics(series: pd.Series, start: int, length: int, method: str,
                original_trend: float):
    truth = series.iloc[start:start + length].copy()
    if len(truth) != length or not np.isfinite(truth).all():
        raise ValueError("Invalid withheld truth")
    masked = series.copy()
    masked.iloc[start:start + length] = np.nan
    reconstructed = experiments.fill_gap(masked, start, length, method)
    predicted = reconstructed.iloc[start:start + length]
    if len(predicted) != length or not np.isfinite(predicted).all():
        raise ValueError("Incomplete or nonfinite reconstruction")
    error = predicted.to_numpy() - truth.to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        correlation = float(np.corrcoef(truth, predicted)[0, 1]) if length >= 3 else np.nan
    return dict(n_masked=length, mae=float(np.mean(np.abs(error))),
                rmse=float(np.sqrt(np.mean(error ** 2))), bias=float(np.mean(error)),
                max_absolute_error=float(np.max(np.abs(error))),
                correlation=correlation,
                monthly_mean_distortion=float(predicted.mean() - truth.mean()),
                annual_mean_distortion=float(reconstructed.mean() - series.mean()),
                seasonal_amplitude_distortion=float(experiments.seasonal_amp(reconstructed)
                                                    - experiments.seasonal_amp(series)),
                trend_distortion_per_year=float(experiments.trend_per_year(reconstructed)
                                                - original_trend))


def evaluate_one(task):
    row, series, original_trend, methods = task
    start, length = int(row["start_index"]), int(row["gap_hours"])
    return [dict(**row, method=method, execution_status="SUCCESS",
                 unit="ppm" if row["gas"] == "CO2" else "ppb",
                 trend_diagnostic_role="HISTORICAL_DIAGNOSTIC_ONLY",
                 **gap_metrics(series, start, length, method, original_trend))
            for method in methods]


def evaluate(registry: pd.DataFrame, series: dict[str, pd.Series],
             config: dict, workers: int = 1) -> pd.DataFrame:
    original = {key: float(experiments.trend_per_year(values)) for key, values in series.items()}
    tasks = [(row, series[row["dataset_key"]], original[row["dataset_key"]], config["methods"])
             for row in registry.to_dict("records")]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            parts = list(pool.map(evaluate_one, tasks, chunksize=1))
    else:
        parts = [evaluate_one(task) for task in tasks]
    result = pd.DataFrame(record for part in parts for record in part)
    complete = result.groupby("base_gap_id").method.nunique().eq(5)
    result["all_method_complete_base_gap"] = result.base_gap_id.map(complete).fillna(False).astype(bool)
    if len(result) != 7180 or not result.all_method_complete_base_gap.all():
        raise ValueError("Five-method evaluation fingerprint changed")
    return result


def gas_gap_method_summary(long: pd.DataFrame) -> pd.DataFrame:
    comparison = long[long.placement_role.eq("REPEATED_DESIGN")
                      & long.all_method_complete_base_gap].copy()
    strata = comparison.groupby(
        ["dataset_key", "physical_station_id", "gas", "season", "gap_hours", "method"], sort=True
    ).agg(successful_placements=("base_gap_id", "nunique"),
          median_rmse=("rmse", "median"), median_mae=("mae", "median")).reset_index()
    stations = strata.groupby(["physical_station_id", "gas", "gap_hours", "method"], sort=True).agg(
        seasonal_strata=("dataset_key", "size"),
        repeated_base_gaps=("successful_placements", "sum"),
        median_rmse=("median_rmse", "median"),
        median_mae=("median_mae", "median")).reset_index()
    summary = stations.groupby(["gas", "gap_hours", "method"], sort=True).agg(
        physical_stations=("physical_station_id", "nunique"),
        contributing_strata=("seasonal_strata", "sum"),
        repeated_base_gaps=("repeated_base_gaps", "sum"),
        median_rmse=("median_rmse", "median"),
        median_mae=("median_mae", "median"),
        minimum_station_rmse=("median_rmse", "min"),
        maximum_station_rmse=("median_rmse", "max"),
        q25_station_rmse=("median_rmse", lambda x: x.quantile(.25)),
        q75_station_rmse=("median_rmse", lambda x: x.quantile(.75))).reset_index()
    summary["unit"] = summary.gas.map({"CO2": "ppm", "CH4": "ppb"})
    return summary


def reproduce_gap(baseline_dir: Path, output_dir: Path, config_path: Path,
                  workers: int = 1) -> pd.DataFrame:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    series = representative_series(baseline_dir)
    registry = placement_registry(series, config)
    long = evaluate(registry, series, config, workers)
    summary = gas_gap_method_summary(long)
    output_dir.mkdir(parents=True, exist_ok=True)
    registry.to_csv(output_dir / "gap_placements.csv", index=False)
    long.to_csv(output_dir / "gap_evaluations.csv", index=False, float_format="%.17g")
    summary.to_csv(output_dir / "gap_method_summary.csv", index=False, float_format="%.17g")
    from wdcgg_pipeline.gap_inference import reproduce_inference
    inference = reproduce_inference(long, output_dir / "paired_inference", config_path)
    if not inference["gas_gap_method_summary"].sort_values(
            ["gas", "gap_hours", "method"]).reset_index(drop=True).equals(
                summary.sort_values(["gas", "gap_hours", "method"]).reset_index(drop=True)):
        raise ValueError("Paired-inference and gap-method summaries disagree")
    return summary
