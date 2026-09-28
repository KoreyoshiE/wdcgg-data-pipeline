# Methods-to-implementation map

This map links the revised study's methods to executable public code and frozen outputs. The frozen summary tables are evidence, not substitutes for the official hourly archives needed for full reproduction.

| Method | Implementation | Frozen output |
|---|---|---|
| Archive identity, parsing, UTC and unit harmonisation | `scripts/reproduce_global_cohort_v2.py`; `src/wdcgg_pipeline/acquisition.py`, `parser.py`, `units.py`, `archive.py` | `provenance/DOWNLOAD_MANIFEST_V2.csv` |
| Contributor QC and monthly observed-only means | `src/wdcgg_pipeline/acquisition.py` (`process`), `qc.py`; baseline reproduction script | Baseline cohort and monthly tables generated under the reproduction output |
| Core 75% month / 75% station-period and Extended 75% / 60% qualification | `src/wdcgg_pipeline/acquisition.py` (`process`), `analysis.py` (`main`); `configs/global_cohort_v2.json` | Baseline cohort tables; `results/frozen/headline_results.json` |
| Twenty-cell completeness and trend sensitivity | `src/wdcgg_pipeline/coverage_sensitivity.py`; `configs/completeness_sensitivity.json` | `results/frozen/completeness_sensitivity.csv` |
| Four anomaly-detector families, five synthetic event types, and physical-station held-out evaluation | `src/wdcgg_pipeline/anomaly_validation.py`; `configs/anomaly_validation.json` | `results/frozen/anomaly_heldout_summary.csv` |
| Structurally inapplicable seasonal-event domains | `src/wdcgg_pipeline/anomaly_validation.py` (`reproduce_anomaly`) | Reproduced realization and detector registries; `results/frozen/headline_results.json` |
| Post-evaluation operational candidate and unchanged-QC real-data review flags | `src/wdcgg_pipeline/anomaly_validation.py` (`operational_candidate`, `realdata_review_flags`) | `results/frozen/anomaly_operational_candidate.csv`, `results/frozen/anomaly_realdata_review_flags.csv` |
| Five gap-reconstruction methods and repeated, non-overlapping placements | `src/wdcgg_pipeline/gap_validation.py`; `configs/gap_validation.json` | `results/frozen/gap_method_summary.csv` |
| Paired base-gap contrasts, equal seasonal-stratum summaries, and station/stratum-block inference | `src/wdcgg_pipeline/gap_inference.py` (`paired_base`, `paired_strata`, `paired_stations`, `block_inference`) | `results/frozen/gap_paired_station_contrasts.csv`, `results/frozen/gap_block_summary.csv` |
| Paired-gas, physical-station and other original analyses | `src/wdcgg_pipeline/analysis.py`; baseline reproduction script | Baseline analysis tables generated under the reproduction output |
| Same-month Seasonal Sen trend | `src/wdcgg_pipeline/trend.py` (`seasonal_sen`) | `results/frozen/trend_core_summary.csv` |
| Detrended seasonal residuals, run-aware moving-block bootstrap (primary 12 months; 6 and 24 months sensitivity) | `src/wdcgg_pipeline/trend.py` (`residual_decomposition`, `candidate_blocks`, `bootstrap`) | Reproduced trend outputs; frozen compact trend summary |
| Cohort uncertainty by physical-station resampling | `src/wdcgg_pipeline/trend.py` (`cohort`, `reproduce_core`) | `results/frozen/trend_core_summary.csv` |

The original contributor-valid observations remain the primary analysis input. Statistical anomaly flags are review annotations only; gap reconstructions do not replace the observed-only monthly series. The compact frozen summaries retain the final revised numerical results, while `scripts/reproduce_study.py --verify-freeze` checks regenerated values against them.
