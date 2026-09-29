"""Frozen synthetic-event evaluation with physical-station-held-out selection.

Structural empty seasonal domains remain unchanged negative controls. They never
receive an artificial zero positive-event F1 or influence candidate selection.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from wdcgg_pipeline import experiments


METRICS = ["precision", "recall", "f1", "fpr"]
POSITIVE = "POSITIVE_EVENT_EVALUABLE"
EMPTY = "STRUCTURAL_EMPTY_DOMAIN_CONTROL"
UNCERTAINTY_LABEL = "DESCRIPTIVE_PHYSICAL_STATION_CLUSTER_BOOTSTRAP"


def configuration_id(method: str, k: float) -> str:
    return f"{method}__k{int(k)}"


def candidates(config: dict):
    return [(configuration_id(method, k), method, k)
            for method in config["methods"] for k in config["k_values"]]


def seed(config: dict, *parts) -> int:
    payload = json.dumps([config["master_seed"], *parts], separators=(",", ":"), ensure_ascii=True)
    value = int.from_bytes(hashlib.sha256(payload.encode()).digest()[:8], "big")
    if value == config["historical_seed"]:
        raise ValueError("Historical seed entered independent evaluation")
    return value


def representative_series(baseline_dir: Path):
    harm = pd.read_csv(baseline_dir / "HARMONISATION_RESULTS_V2.csv", float_precision="round_trip")
    harm["dataset_key"] = harm.wdcgg_id.astype(str) + "_" + harm.gas
    sample = harm[harm.wdcgg_id.isin(experiments.REPRESENTATIVE)
                  & harm.gas.isin(["CO2", "CH4"])]
    if len(sample) != 10 or sample.wdcgg_id.nunique() != 5:
        raise ValueError("Frozen representative design must have 10 series and 5 stations")
    series = {}
    for row in sample.itertuples():
        path = Path(row.observation_path)
        s = experiments.longest_segment(experiments.read_obs(path))
        s.name = row.dataset_key
        series[row.dataset_key] = s
    return series


def evaluate(baseline_dir: Path, config: dict):
    series = representative_series(baseline_dir)
    rows, realizations = [], []
    for key, s in series.items():
        station, gas = key.rsplit("_", 1)
        clean = s.to_numpy().copy()
        for event in config["events"]:
            for repetition in range(1, config["repetitions"] + 1):
                resolved_seed = seed(config, "injection", station, key, gas, event, repetition)
                x, truth = experiments.inject(s, event, np.random.default_rng(resolved_seed))
                domain = POSITIVE if bool(truth.any()) else EMPTY
                if domain == EMPTY:
                    if event != "season_dependent_positive" or key not in config["structural_empty_series"]:
                        raise ValueError("Unexpected empty synthetic-event domain")
                    if not np.array_equal(x.to_numpy(), clean):
                        raise ValueError("Structural control changed its clean input")
                elif not truth.any():
                    raise ValueError("Positive-event realization has no truth")
                if not np.array_equal(s.to_numpy(), clean):
                    raise ValueError("Injection mutated the source series")
                realization_id = f"{key}__{event}__r{repetition:02d}"
                common = dict(realization_id=realization_id, physical_station_id=station,
                              dataset_key=key, gas=gas, event_type=event, repetition=repetition,
                              rng_seed=resolved_seed, realization_domain_status=domain,
                              eligible_for_positive_detection_metrics=domain == POSITIVE,
                              eligible_for_negative_control_metrics=domain == EMPTY,
                              truth_count=int(truth.sum()))
                realizations.append(common)
                for cid, method, k in candidates(config):
                    flag = experiments.anomaly_flags(x, method, k)
                    if len(flag) != len(s) or not np.isfinite(flag).all():
                        raise ValueError(f"Detector failed: {cid} {realization_id}")
                    raw = experiments.metrics(flag, truth)
                    metrics = dict(precision=raw["precision"], recall=raw["recall"],
                                   f1=raw["f1"], fpr=raw["false_positive_rate"])
                    if domain == EMPTY:
                        for metric in ("precision", "recall", "f1"):
                            metrics[metric] = np.nan
                    rows.append(dict(**common, configuration_id=cid, detector=method, k=k,
                                     n_observations=len(s), tp=raw["tp"], fp=raw["fp"],
                                     fn=raw["fn"], tn=raw["tn"], **metrics,
                                     predicted_flag_count=int(flag.sum()),
                                     flag_proportion=float(flag.mean()),
                                     prediction_mask_sha256=hashlib.sha256(flag.tobytes()).hexdigest(),
                                     execution_status="SUCCESS"))
    registry, evaluations = pd.DataFrame(realizations), pd.DataFrame(rows)
    if len(registry) != 1000 or (registry.realization_domain_status == EMPTY).sum() != 60:
        raise ValueError("Realization design fingerprint mismatch")
    if len(evaluations) != 12000 or (evaluations.realization_domain_status == EMPTY).sum() != 720:
        raise ValueError("Detector evaluation fingerprint mismatch")
    for (_, _), group in evaluations[evaluations.realization_domain_status == EMPTY].groupby(
            ["dataset_key", "configuration_id"]):
        if len(group) != 20 or group.prediction_mask_sha256.nunique() != 1:
            raise ValueError("Repeated structural-control predictions differ")
    return registry, evaluations


def hierarchy(frame: pd.DataFrame):
    positive = frame[frame.realization_domain_status == POSITIVE]
    if not np.isfinite(positive[METRICS].to_numpy()).all():
        raise ValueError("Positive-event metric is nonfinite")
    by_gas = positive.groupby(["physical_station_id", "gas", "event_type"], sort=True)[METRICS].mean().reset_index()
    by_station_event = by_gas.groupby(["physical_station_id", "event_type"], sort=True)[METRICS].mean().reset_index()
    by_event = by_station_event.groupby("event_type", sort=True)[METRICS].mean().reset_index()
    return by_gas, by_station_event, by_event


def training_scores(training: pd.DataFrame, config: dict):
    stations = sorted(training.physical_station_id.unique())
    expected = len(stations) * 2 * 5 * config["repetitions"]
    rows = []
    for cid, method, k in candidates(config):
        group = training[training.configuration_id == cid]
        if len(group) != expected or group.realization_id.nunique() != expected:
            raise ValueError("Training fold count mismatch")
        _, _, by_event = hierarchy(group)
        if set(by_event.event_type) != set(config["events"]):
            raise ValueError("Training fold lacks an evaluable event type")
        mean = by_event[METRICS].mean()
        rows.append(dict(configuration_id=cid, detector=method, k=k,
                         **{f"macro_{metric}": float(mean[metric]) for metric in METRICS}))
    return pd.DataFrame(rows)


def select_candidate(scores: pd.DataFrame, tolerance: float):
    tied = scores
    for column, maximize in (("macro_f1", True), ("macro_fpr", False),
                             ("macro_recall", True), ("macro_precision", True)):
        extreme = tied[column].max() if maximize else tied[column].min()
        tied = tied[np.abs(tied[column] - extreme) <= tolerance]
    return tied.sort_values("configuration_id").iloc[0]


def heldout_summary(evaluations: pd.DataFrame, config: dict):
    selected, trace = [], []
    for station in sorted(evaluations.physical_station_id.unique()):
        training = evaluations[evaluations.physical_station_id != station]
        scores = training_scores(training, config)
        winner = select_candidate(scores, config["tie_tolerance"])
        trace.append(dict(held_out_station=station, configuration_id=winner.configuration_id,
                          training_macro_f1=winner.macro_f1))
        selected.append(evaluations[(evaluations.physical_station_id == station)
                                    & (evaluations.configuration_id == winner.configuration_id)])
    heldout = pd.concat(selected, ignore_index=True)
    _, by_station_event, by_event = hierarchy(heldout)
    folds = by_station_event.groupby("physical_station_id", sort=True)[METRICS].mean().reset_index()
    counts = by_station_event.groupby("physical_station_id").event_type.nunique()
    folds["number_of_evaluable_event_types"] = folds.physical_station_id.map(counts)
    station_metrics = folds[METRICS].to_numpy()
    rng = np.random.default_rng(seed(config, "bootstrap", "PRIMARY_STATION_HELDOUT"))
    indexes = rng.integers(0, len(folds), size=(config["bootstrap_replicates"], len(folds)))
    boot = station_metrics[indexes].mean(axis=1)
    summary = []
    for j, metric in enumerate(METRICS):
        lo, hi = np.percentile(boot[:, j], [2.5, 97.5], method="linear")
        values = station_metrics[:, j]
        summary.append(dict(scope="PRIMARY_STATION_HELDOUT", metric=metric,
                            estimate=float(values.mean()), ci_lower=float(lo), ci_upper=float(hi),
                            fold_min=float(values.min()), fold_max=float(values.max()),
                            fold_median=float(np.median(values)),
                            fold_iqr=float(np.percentile(values, 75) - np.percentile(values, 25)),
                            physical_station_clusters=len(folds),
                            replicates=config["bootstrap_replicates"],
                            uncertainty_label=UNCERTAINTY_LABEL))
    return heldout, folds, pd.DataFrame(trace), pd.DataFrame(summary), by_event


def operational_candidate(evaluations: pd.DataFrame, config: dict):
    """Select on all stations only after held-out performance is finalised."""
    scores = training_scores(evaluations, config)
    remaining = scores.copy()
    ranked = []
    while len(remaining):
        winner = select_candidate(remaining, config["tie_tolerance"])
        ranked.append(winner)
        remaining = remaining[remaining.configuration_id != winner.configuration_id]
    first, second = ranked[:2]
    controls = evaluations[(evaluations.realization_domain_status == EMPTY)
                           & (evaluations.configuration_id == first.configuration_id)]
    unique_controls = controls.groupby("dataset_key", sort=True).fpr.first()
    if len(unique_controls) != 3 or not controls.groupby("dataset_key").fpr.nunique().eq(1).all():
        raise ValueError("Structural controls must have three unique deterministic domains")
    return pd.DataFrame([dict(
        configuration_id=first.configuration_id,
        selected_detector=first.detector, selected_k=first.k,
        full_data_selection_score=first.macro_f1,
        full_data_macro_precision=first.macro_precision,
        full_data_macro_recall=first.macro_recall,
        full_data_macro_fpr=first.macro_fpr,
        second_ranked_configuration=second.configuration_id,
        score_margin=float(first.macro_f1 - second.macro_f1),
        label="FULL_DATA_SELECTION_ONLY_NOT_INDEPENDENT_PERFORMANCE",
        structural_unique_domain_fpr=float(unique_controls.mean()),
    )])


def realdata_review_flags(baseline_dir: Path, candidate: pd.Series) -> pd.DataFrame:
    """Compare review flags without changing contributor QC or primary inputs."""
    harm = pd.read_csv(baseline_dir / "HARMONISATION_RESULTS_V2.csv", float_precision="round_trip")
    historical = pd.read_csv(baseline_dir / "ANOMALY_REAL_FLAG_AGREEMENT_V2.csv",
                             float_precision="round_trip")
    sample = harm[harm.wdcgg_id.isin(experiments.REPRESENTATIVE)
                  & harm.gas.isin(["CO2", "CH4"])]
    rows = []
    for source in sample.itertuples():
        key = f"{source.wdcgg_id}_{source.gas}"
        data = experiments.read_obs(Path(source.observation_path)).iloc[:30000].copy()
        qc_before, valid_before = data.qc.copy(), data.valid.copy()
        usable = data.value.dropna()
        invalid = ~data.valid.to_numpy()
        masks = []
        for role, method, k in (
                ("HISTORICAL_HAMPEL_K3", "hampel_7day", 3.),
                ("POST_EVALUATION_OPERATIONAL_CANDIDATE",
                 candidate.selected_detector, candidate.selected_k)):
            mask = np.zeros(len(data), dtype=bool)
            mask[data.index.get_indexer(usable.index)] = experiments.anomaly_flags(
                usable, method, k)
            masks.append(mask)
            categories = dict(
                provider_invalid_statistical_flag=int((invalid & mask).sum()),
                provider_invalid_no_statistical_flag=int((invalid & ~mask).sum()),
                provider_valid_statistical_review_flag=int((~invalid & mask).sum()),
                provider_valid_no_flag=int((~invalid & ~mask).sum()))
            if role == "HISTORICAL_HAMPEL_K3":
                old = historical[(historical.dataset_key == key)
                                 & (historical.method == "hampel_7day")].iloc[0]
                if old.parameter_k != 3 or old.n != len(data) or any(
                        old[column] != value for column, value in categories.items()):
                    raise ValueError(f"Historical review-flag regression changed: {key}")
            rows.append(dict(
                dataset_key=key, physical_station_id=source.wdcgg_id, gas=source.gas,
                role=role, detector=method, k=k,
                total_historical_rows_examined=len(data),
                numerical_hours_examined=len(usable),
                contributor_valid_hours_examined=int((~invalid).sum()),
                total_flags=int(mask.sum()),
                contributor_valid_flags=categories["provider_valid_statistical_review_flag"],
                contributor_valid_flag_proportion=(
                    categories["provider_valid_statistical_review_flag"] / int((~invalid).sum())),
                flag_proportion=float(mask.mean()),
                prediction_mask_sha256=hashlib.sha256(mask.tobytes()).hexdigest(),
                **categories, policy="REVIEW_FLAGS_ONLY_NO_AUTOMATIC_DELETION"))
        for row in rows[-2:]:
            row.update(
                historical_revised_overlap_all=int((masks[0] & masks[1]).sum()),
                historical_revised_overlap_contributor_valid=int((masks[0] & masks[1] & ~invalid).sum()),
                symmetric_difference_all=int((masks[0] ^ masks[1]).sum()),
                contributor_qc_unchanged=bool(data.qc.equals(qc_before)
                                               and data.valid.equals(valid_before)))
    return pd.DataFrame(rows)


def reproduce_anomaly(baseline_dir: Path, output_dir: Path, config_path: Path):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    registry, evaluations = evaluate(baseline_dir, config)
    heldout, folds, trace, summary, events = heldout_summary(evaluations, config)
    output_dir.mkdir(parents=True, exist_ok=True)
    registry.to_csv(output_dir / "realizations.csv", index=False)
    evaluations.to_csv(output_dir / "all_configuration_evaluations.csv", index=False, na_rep="NA_NOT_APPLICABLE")
    heldout.to_csv(output_dir / "heldout_selected_procedure.csv", index=False, na_rep="NA_NOT_APPLICABLE")
    folds.to_csv(output_dir / "heldout_station_folds.csv", index=False)
    trace.to_csv(output_dir / "fold_selection.csv", index=False)
    summary.to_csv(output_dir / "anomaly_heldout_summary.csv", index=False, float_format="%.17g")
    events.to_csv(output_dir / "event_standardized_sensitivity.csv", index=False)
    candidate = operational_candidate(evaluations, config)
    candidate.to_csv(output_dir / "operational_candidate.csv", index=False,
                     float_format="%.17g")
    realdata_review_flags(baseline_dir, candidate.iloc[0]).to_csv(
        output_dir / "realdata_review_flags.csv", index=False, float_format="%.17g")
    return summary
