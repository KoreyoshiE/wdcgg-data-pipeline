"""Offline checks for deterministic synthetic-event design and selection."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from wdcgg_pipeline import experiments
from wdcgg_pipeline.anomaly_validation import candidates, hierarchy, seed, select_candidate


CONFIG = json.loads((Path(__file__).resolve().parents[1] / "configs" / "anomaly_validation.json")
                    .read_text(encoding="utf-8"))


def test_twelve_frozen_detector_configurations():
    assert len(candidates(CONFIG)) == 12
    assert candidates(CONFIG)[0][0] == "seasonal_mad__k3"


def test_seed_scope_is_stable_and_not_historical_seed():
    parts = ("injection", "JFJ6036", "JFJ6036_CH4", "CH4", "season_dependent_positive", 1)
    assert seed(CONFIG, *parts) == seed(CONFIG, *parts)
    assert seed(CONFIG, *parts) != CONFIG["historical_seed"]
    assert seed(CONFIG, *parts) != seed(CONFIG, *parts[:-1], 2)


def test_structural_empty_seasonal_domain_is_unmodified():
    index = pd.date_range("2020-03-01", periods=1000, freq="h", tz="UTC")
    series = pd.Series(np.linspace(1800.0, 1801.0, len(index)), index=index,
                       name="JFJ6036_CH4")
    injected, truth = experiments.inject(series, "season_dependent_positive",
                                         np.random.default_rng(seed(CONFIG, "synthetic-test")))
    assert not truth.any()
    np.testing.assert_array_equal(injected.to_numpy(), series.to_numpy())


def test_tie_rule_fpr_then_recall_then_precision_then_id():
    rows = pd.DataFrame([
        dict(configuration_id="a", macro_f1=.5, macro_fpr=.03, macro_recall=.4, macro_precision=.4),
        dict(configuration_id="b", macro_f1=.5, macro_fpr=.02, macro_recall=.2, macro_precision=.2),
        dict(configuration_id="c", macro_f1=.4, macro_fpr=.00, macro_recall=.9, macro_precision=.9),
    ])
    assert select_candidate(rows, 1e-12).configuration_id == "b"


def test_structural_controls_never_become_zero_positive_scores():
    frame = pd.DataFrame([
        dict(physical_station_id="ONE", gas="CO2", event_type="positive_spikes",
             realization_domain_status="POSITIVE_EVENT_EVALUABLE",
             precision=.4, recall=.8, f1=.5, fpr=.1),
        dict(physical_station_id="ONE", gas="CO2", event_type="season_dependent_positive",
             realization_domain_status="STRUCTURAL_EMPTY_DOMAIN_CONTROL",
             precision=np.nan, recall=np.nan, f1=np.nan, fpr=.2),
    ])
    _, _, by_event = hierarchy(frame)
    assert by_event.event_type.tolist() == ["positive_spikes"]
    assert by_event.f1.item() == .5
