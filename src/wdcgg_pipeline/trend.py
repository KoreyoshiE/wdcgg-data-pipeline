"""Frozen Seasonal Sen and calendar-run-aware uncertainty calculations.

This module is a portable transcription of the validated point estimator and
resampling formulas. The primary environmental series contains observed,
contributor-valid, qualified monthly means only; missing months are not filled.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


MONTHS = pd.date_range("2015-01-01", "2024-12-01", freq="MS", tz="UTC")


def decimal_year(index: pd.DatetimeIndex) -> np.ndarray:
    return index.year.to_numpy(dtype=float) + (index.month.to_numpy(dtype=float) - 0.5) / 12.0


def stable_seed(master_seed: int, scope: str, identity: str, block_length: int) -> int:
    token = f"{master_seed}|{scope}|{identity}|{block_length}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(token).digest()[:8], "little", signed=False)


def same_month_geometry(index: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pair only equal calendar months using actual decimal-year separation."""
    left, right = [], []
    months = index.month.to_numpy()
    x = decimal_year(index)
    for month in range(1, 13):
        positions = np.flatnonzero(months == month)
        i, j = np.triu_indices(len(positions), 1)
        left.extend(positions[i])
        right.extend(positions[j])
    a, b = np.asarray(left, dtype=int), np.asarray(right, dtype=int)
    denominator = x[b] - x[a]
    if np.any(months[a] != months[b]) or np.any(denominator <= 0):
        raise ValueError("Invalid same-month pair construction")
    return a, b, denominator


def seasonal_sen(values: pd.Series) -> float:
    a, b, denominator = same_month_geometry(values.index)
    if not len(a):
        raise ValueError("No same-month slopes; no imputation permitted")
    y = values.to_numpy()
    return float(np.median((y[b] - y[a]) / denominator))


def seasonal_sen_matrix(values: np.ndarray, index: pd.DatetimeIndex) -> np.ndarray:
    a, b, denominator = same_month_geometry(index)
    if not len(a):
        raise ValueError("No same-month slopes")
    return np.median((values[:, b] - values[:, a]) / denominator, axis=1)


def contiguous_runs(index: pd.DatetimeIndex) -> list[np.ndarray]:
    if len(index) == 0:
        return []
    ordinal = index.tz_localize(None).to_period("M").astype(int).to_numpy()
    split_points = np.where(np.diff(ordinal) != 1)[0] + 1
    return [chunk for chunk in np.split(np.arange(len(index)), split_points) if len(chunk)]


def candidate_blocks(index: pd.DatetimeIndex, block_length: int) -> np.ndarray:
    blocks = [run[start : start + block_length]
              for run in contiguous_runs(index)
              for start in range(0, len(run) - block_length + 1)]
    if not blocks:
        return np.empty((0, block_length), dtype=int)
    return np.vstack(blocks).astype(int, copy=False)


def ordinal(index: pd.DatetimeIndex) -> np.ndarray:
    return index.year.to_numpy() * 12 + index.month.to_numpy()


def residual_decomposition(values: pd.Series, beta: float) -> dict[str, object]:
    x = decimal_year(values.index)
    t0 = float(x[0])
    y = values.to_numpy()
    detrended = y - beta * (x - t0)
    months = values.index.month.to_numpy()
    lookup = {m: float(np.median(detrended[months == m])) for m in sorted(set(months))}
    assigned = np.array([lookup[m] for m in months])
    center = float(assigned.mean())
    seasonal = assigned - center
    fitted = center + beta * (x - t0)
    residual = y - fitted - seasonal
    if not np.allclose(fitted + seasonal + residual, y, atol=1e-12, rtol=0):
        raise ValueError("Residual decomposition failed reconstruction")
    return dict(x=x, t0=t0, beta=float(beta), intercept_at_t0=center,
                fitted=fitted, seasonal=seasonal, residual=residual)


def run_sample(residual: np.ndarray, index: pd.DatetimeIndex, length: int,
               replicates: int, seed: int):
    """Draw contiguous source blocks within observed runs; preserve destination gaps."""
    candidates = candidate_blocks(index, length)
    if not len(candidates):
        raise ValueError("No eligible contiguous source blocks")
    rng = np.random.default_rng(seed)
    sampled = np.empty((replicates, len(index)))
    segments, chosen = [], []
    for run_id, run in enumerate(contiguous_runs(index)):
        for offset in range(0, len(run), length):
            destination = run[offset:offset + length]
            if len(destination) > 1 and not np.all(np.diff(ordinal(index[destination])) == 1):
                raise ValueError("Destination gap within block assignment")
            draw = rng.integers(0, len(candidates), size=replicates)
            sampled[:, destination] = residual[candidates[draw, :len(destination)]]
            segments.append((run_id, destination))
            chosen.append(draw)
    return sampled, np.column_stack(chosen), candidates, segments


def intervals(slopes: np.ndarray, theta: float) -> dict[str, float | bool]:
    lo, hi = np.quantile(slopes, [.025, .975], method="linear")
    sd = float(np.std(slopes, ddof=1))
    bounds = {"percentile": (lo, hi), "basic": (2 * theta - hi, 2 * theta - lo),
              "se_centered": (theta - 1.959964 * sd, theta + 1.959964 * sd)}
    result = {}
    for method, (a, b) in bounds.items():
        result.update({method + "_lower": float(a), method + "_upper": float(b),
                       method + "_width": float(b - a),
                       method + "_contains_theta": bool(a <= theta <= b)})
    return result


def diagnose(slopes: np.ndarray, theta: float) -> dict[str, float | int | bool]:
    sd = float(np.std(slopes, ddof=1))
    bias = float(np.mean(slopes) - theta)
    return dict(theta_hat=float(theta), bootstrap_mean=float(np.mean(slopes)),
                bootstrap_median=float(np.median(slopes)), bootstrap_sd=sd,
                mean_bias=bias, median_bias=float(np.median(slopes) - theta),
                standardized_bias=bias / sd if sd > 1e-12 else np.nan,
                relative_bias=bias / theta if theta else np.nan,
                bootstrap_skewness=float(stats.skew(slopes, bias=False)) if sd > 1e-12 else np.nan,
                bootstrap_excess_kurtosis=float(stats.kurtosis(slopes, bias=False)) if sd > 1e-12 else np.nan,
                successful_replicates=len(slopes), **intervals(slopes, theta))


def bootstrap(values: pd.Series, key: str, length: int, config: dict):
    theta = seasonal_sen(values)
    model = residual_decomposition(values, theta)
    seed = stable_seed(config["master_seed"], config["station_seed_scope"], key, length)
    sampled, chosen, candidates, segments = run_sample(
        model["residual"], values.index, length, config["station_replicates"], seed)
    pseudo = model["fitted"][None, :] + model["seasonal"][None, :] + sampled
    slopes = seasonal_sen_matrix(pseudo, values.index)
    if not np.isfinite(slopes).all():
        raise ValueError("Nonfinite Seasonal Sen bootstrap replicate")
    for i in sorted({0, len(slopes) // 2, len(slopes) - 1}):
        if abs(seasonal_sen(pd.Series(pseudo[i], index=values.index)) - slopes[i]) > 1e-10:
            raise ValueError("Scalar and vectorized estimators disagree")
    return slopes, dict(theta=theta, model=model, pseudo=pseudo, chosen=chosen,
                        candidates=candidates, segments=segments, seed=seed)


def cohort(frame: pd.DataFrame, distributions: dict[str, np.ndarray], config: dict):
    if frame.gas.nunique() != 1 or frame.station_id.duplicated().any():
        raise ValueError("Cohort must contain unique physical stations for one gas")
    f = frame.sort_values("station_id").reset_index(drop=True)
    n = len(f)
    seed = stable_seed(config["master_seed"], config["cohort_seed_scope"], f.gas.iloc[0], 12)
    rng = np.random.default_rng(seed)
    B = config["cohort_replicates"]
    outer = rng.integers(0, n, (B, n))
    inner = rng.integers(0, config["station_replicates"], (B, n))
    theta = f.theta_hat.to_numpy()
    draws = np.stack([distributions[k] for k in f.dataset_key])
    errors = draws - draws.mean(axis=1)[:, None]
    results = {"PRIMARY_PHYSICAL_STATION_PLUS_CENTERED_TEMPORAL":
               np.median(theta[outer] + errors[outer, inner], axis=1),
               "STATION_ONLY": np.median(theta[outer], axis=1)}
    return results, dict(seed=seed, outer=outer, inner=inner,
                         station_ids=f.station_id.tolist(), centered_errors=errors)


def load_core(monthly_dir: Path):
    monthly = pd.read_csv(monthly_dir / "MONTHLY_PROVIDER_VALID_V2.csv")
    monthly["date"] = pd.to_datetime(dict(year=monthly.year, month=monthly.month, day=1), utc=True)
    monthly["dataset_key"] = monthly.dataset_id.astype(str) + "_" + monthly.gas
    final = pd.read_csv(monthly_dir / "FINAL_GLOBAL_COHORT_V2.csv")
    core = final.loc[final.primary_analysis].copy()
    if len(core) != 26 or core.wdcgg_id.nunique() != 14:
        raise ValueError("Frozen Core membership is not 26 products / 14 stations")
    series = {}
    for row in core.itertuples(index=False):
        frame = monthly.loc[monthly.dataset_key == row.dataset_key].set_index("date").sort_index()
        if frame.index.duplicated().any():
            raise ValueError(f"Duplicate monthly timestamp: {row.dataset_key}")
        frame = frame.reindex(MONTHS)
        valid_count = frame.provider_valid_hour_count.fillna(0.0)
        expected = pd.Series(MONTHS.days_in_month * 24.0, index=MONTHS)
        known = frame.expected_hour_count.notna()
        if not np.allclose(frame.loc[known, "expected_hour_count"], expected.loc[known], atol=0, rtol=0):
            raise ValueError(f"Expected-hour mismatch: {row.dataset_key}")
        coverage = valid_count / expected
        qualified = (coverage >= 0.75) & frame["mean"].notna()
        values = frame.loc[qualified, "mean"].astype(float)
        if len(values) < 90:
            raise ValueError(f"Core series below frozen threshold: {row.dataset_key}")
        series[row.dataset_key] = values
    return core, series


def reproduce_core(monthly_dir: Path, output_dir: Path, config_path: Path) -> pd.DataFrame:
    """Recompute frozen Core point/interval tables from the supplied monthly data."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    core, series = load_core(monthly_dir)
    distributions = {}
    rows = []
    for meta in core.sort_values(["gas", "dataset_key"]).itertuples(index=False):
        values = series[meta.dataset_key]
        theta = seasonal_sen(values)
        for length in config["block_lengths"]:
            slopes, info = bootstrap(values, meta.dataset_key, length, config)
            if length == config["primary_block_length"]:
                distributions[meta.dataset_key] = slopes
                rows.append(dict(dataset_key=meta.dataset_key, station_id=meta.wdcgg_id,
                                 station=meta.station_name, gas=meta.gas,
                                 qualified_months=len(values), eligible_source_blocks=len(info["candidates"]),
                                 **diagnose(slopes, theta)))
    station = pd.DataFrame(rows)
    if (station.bootstrap_sd <= 1e-12).mean() > .5 or (station.relative_bias.abs() >= 1).mean() > .5:
        raise ValueError("Distribution degeneracy or trend-sized systematic bias")
    cohort_rows = []
    for gas, group in station.groupby("gas"):
        point = float(np.median(group.theta_hat))
        draws, _ = cohort(group, distributions, config)
        for method, sample in draws.items():
            lo, hi = np.quantile(sample, [.025, .975], method="linear")
            cohort_rows.append(dict(gas=gas, method=method,
                                    role="PRIMARY" if method.startswith("PRIMARY_") else "SENSITIVITY_ONLY",
                                    station_gas_series=len(group), physical_stations=group.station_id.nunique(),
                                    seasonal_sen_median=point, cohort_ci_lower=lo, cohort_ci_upper=hi,
                                    cohort_ci_width=hi - lo, bootstrap_median=float(np.median(sample)),
                                    bootstrap_mean=float(np.mean(sample)), primary_block_length=12,
                                    temporal_replicates=config["station_replicates"],
                                    cohort_replicates=config["cohort_replicates"],
                                    unit_per_year="ppm yr^-1" if gas == "CO2" else "ppb yr^-1"))
    output_dir.mkdir(parents=True, exist_ok=True)
    station.to_csv(output_dir / "seasonal_sen_station_summary.csv", index=False, float_format="%.17g")
    result = pd.DataFrame(cohort_rows)
    result.to_csv(output_dir / "seasonal_sen_core_summary.csv", index=False, float_format="%.17g")
    return result
