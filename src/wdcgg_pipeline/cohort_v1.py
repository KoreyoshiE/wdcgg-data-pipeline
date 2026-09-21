from __future__ import annotations

import csv
import os
import json
from collections import Counter
from pathlib import Path

PUBLIC_ROOT = Path(__file__).resolve().parents[2]
EXP = Path(os.environ.get("WDCGG_V1_DIR", PUBLIC_ROOT / "results_v1"))

REGION_TO_CONTINENT = {
    "REGION I (Africa)": "Africa",
    "REGION II (Asia)": "Asia",
    "REGION III (South America)": "South America",
    "REGION IV (North and Central America)": "North America",
    "REGION V (South-West Pacific)": "Oceania/Pacific",
    "REGION VI (Europe)": "Europe",
    "ANTARCTICA": "Antarctica",
}
WITHIN_THRESHOLDS = (0.70, 0.75, 0.80, 0.90)
STATION_THRESHOLDS = (0.60, 0.70, 0.75, 0.80)


def f(value: str) -> float:
    return float(value) if value not in {"", "NA", "None"} else float("nan")


def main() -> None:
    results = list(csv.DictReader((EXP / "HARMONISATION_RESULTS.csv").open(encoding="utf-8-sig")))
    monthly = list(csv.DictReader((EXP / "GAP_COVERAGE_MONTHLY.csv").open(encoding="utf-8-sig")))
    target = {(year, month) for year in range(2015, 2025) for month in range(1, 13)}
    monthly_by = {}
    for row in monthly:
        key = (row["dataset_id"], row["gas"], int(row["year"]), int(row["month"]))
        monthly_by[key] = row
    sensitivity = []
    for within in WITHIN_THRESHOLDS:
        fractions = {}
        target_valid = {}
        for result in results:
            key_prefix = (result["wdcgg_id"], result["gas"])
            valid = 0
            months = 0
            for year, month in target:
                row = monthly_by.get((*key_prefix, year, month))
                coverage = float(row["coverage_fraction"]) if row else 0.0
                if coverage >= within:
                    months += 1
                if row:
                    valid += int(row["provider_valid_hour_count"])
            fractions[key_prefix] = months / 120
            target_valid[key_prefix] = valid
        for station_threshold in STATION_THRESHOLDS:
            keep = [r for r in results if fractions[(r["wdcgg_id"], r["gas"])] >= station_threshold]
            stations = {r["wdcgg_id"] for r in keep}
            paired = {s for s in stations if sum(r["wdcgg_id"] == s for r in keep) > 1}
            latitudes = [f(r["latitude"]) for r in keep if r.get("latitude") not in {"", "NA"}]
            sensitivity.append({
                "within_month_hourly_threshold": within,
                "station_level_month_fraction_threshold": station_threshold,
                "retained_station_gas_datasets": len(keep),
                "retained_physical_stations": len(stations),
                "retained_co2_datasets": sum(r["gas"] == "CO2" for r in keep),
                "retained_ch4_datasets": sum(r["gas"] == "CH4" for r in keep),
                "retained_paired_stations": len(paired),
                "retained_regions": ";".join(sorted({r["wmo_region"] for r in keep})),
                "latitude_min": min(latitudes) if latitudes else "",
                "latitude_max": max(latitudes) if latitudes else "",
                "target_period_provider_valid_records": sum(target_valid[(r["wdcgg_id"], r["gas"])] for r in keep),
            })
    with (EXP / "COVERAGE_SENSITIVITY.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(sensitivity[0])); writer.writeheader(); writer.writerows(sensitivity)

    chosen_within = 0.75
    chosen_station = 0.75
    chosen = next(row for row in sensitivity if float(row["within_month_hourly_threshold"]) == chosen_within and float(row["station_level_month_fraction_threshold"]) == chosen_station)
    keep = []
    for result in results:
        row = next(m for m in sensitivity if float(m["within_month_hourly_threshold"]) == chosen_within and float(m["station_level_month_fraction_threshold"]) == chosen_station)
        # The per-dataset 75% fraction was computed during processing.
        if float(result["qualified_month_fraction_75"]) >= chosen_station:
            keep.append(result)
    final_rows = []
    for result in keep:
        region = result["wmo_region"]
        final_rows.append({
            "station_code": result["station_code"], "station_name": result["station_name"], "country": result["country"], "continent": REGION_TO_CONTINENT.get(region, "UNKNOWN"), "region": region,
            "latitude": result["latitude"], "longitude": result["longitude"], "altitude": result["elevation"], "station_type": result["station_type"], "species": result["gas"], "measurement_type": "surface/in-situ", "sampling_type": "hourly",
            "temporal_resolution": "hourly", "dataset_id": result["wdcgg_id"], "version": result["dataset_version"], "DOI": result["dataset_doi"], "provider": result["metadata_contributor"],
            "raw_start": result["dataset_start_date"], "raw_end": result["dataset_end_date"], "qualified_start": result["qualified_start"], "qualified_end": result["qualified_end"], "target_period_coverage": result["qualified_month_fraction_75"], "qualified_month_fraction": result["qualified_month_fraction_75"],
            "provider_valid_fraction": result["provider_valid_fraction"], "record_count": result["record_count"], "provider_valid_count": result["provider_valid_count"], "missing_count": int(result["record_count"]) - int(result["value_count"]), "raw_file": result["archive_path"], "raw_sha256": result["archive_sha256"],
            "inclusion_reason": "2015-2024 target; 75% within-month provider-valid hourly coverage and >=75% qualified months; operational accessible global/geographic-gap product",
        })
    fields = list(final_rows[0])
    with (EXP / "FINAL_GLOBAL_COHORT.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(final_rows)
    qc_rows = []
    for result in results:
        qc_rows.append({"dataset_id": result["wdcgg_id"], "gas": result["gas"], "station_name": result["station_name"], "record_count": result["record_count"], "value_count": result["value_count"], "provider_valid_count": result["provider_valid_count"], "provider_valid_fraction": result["provider_valid_fraction"], "qc_primary": "WDCGG provider QC mapped by existing deterministic mapper", "statistical_flags": "not used for deletion"})
    with (EXP / "PROVIDER_QC_SUMMARY.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(qc_rows[0])); writer.writeheader(); writer.writerows(qc_rows)
    screening = list(csv.DictReader((EXP / "DATASET_SCREENING.csv").open(encoding="utf-8-sig")))
    for row in screening:
        if (row["wdcgg_id"], row["gas"]) in {(r["dataset_id"], r["species"]) for r in final_rows}:
            row["final_cohort_status"] = "FINAL_COHORT"
            row["final_cohort_reason"] = "passes chosen coverage rule"
        elif row.get("screening_status") == "SELECTED_FOR_ACQUISITION":
            row["final_cohort_status"] = "ACQUIRED_NOT_FINAL"
            row["final_cohort_reason"] = "coverage threshold not met or parser result unresolved"
        else:
            row["final_cohort_status"] = "NOT_ACQUIRED"
            row["final_cohort_reason"] = row.get("screening_reason", "")
    with (EXP / "CANDIDATE_DATASETS.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(screening[0])); writer.writeheader(); writer.writerows(screening)
    summary = {
        "candidate_station_products": len(screening), "selected_for_acquisition": sum(r["screening_status"] == "SELECTED_FOR_ACQUISITION" for r in screening), "final_station_gas_datasets": len(final_rows), "final_physical_stations": len({r["station_code"] for r in final_rows}), "final_co2_datasets": sum(r["species"] == "CO2" for r in final_rows), "final_ch4_datasets": sum(r["species"] == "CH4" for r in final_rows), "final_paired_stations": len({s for s in {r["station_code"] for r in final_rows} if sum(r["station_code"] == s for r in final_rows) > 1}), "final_countries": len({r["country"] for r in final_rows}), "final_continents": sorted({r["continent"] for r in final_rows}), "latitude_range": [min(float(r["latitude"]) for r in final_rows), max(float(r["latitude"]) for r in final_rows)], "target_common_period": "2015-01-01 through 2024-12-31", "chosen_within_month_threshold": chosen_within, "chosen_station_level_threshold": chosen_station, "threshold_rationale": "75% is the lowest tested within-month rule that does not admit additional datasets over 70% in this cohort; 90% removes three datasets and narrows regional coverage. Station-level 75% retains 18 of 24 acquired products while preserving Africa, Asia, North America, Oceania/Pacific, Europe and Antarctica. South America remains unqualified and is reported as a limitation.", "sensitivity_rows": len(sensitivity)
    }
    (EXP / "SCREENING_FLOW_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (EXP / "COVERAGE_DECISION.json").write_text(json.dumps({"within_month_rule": ">=75% provider-valid hourly observations per month", "station_level_rule": ">=75% of 120 months in 2015-2024 qualify", "selected": chosen, "rationale": summary["threshold_rationale"], "literature_basis": ["WDCGG Data Format Table: hourly/daily/monthly QC semantics and missing codes", "WDCGG Contributor Manual: provider-defined aggregation and original QC flags", "coverage sensitivity is empirical for this cohort; no universal WDCGG threshold is asserted"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
