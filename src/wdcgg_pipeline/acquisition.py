from __future__ import annotations

import csv
import os
import hashlib
import json
import re
import tarfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from wdcgg_pipeline.parser import iter_wdcgg_rows, parse_wdcgg_layout

PUBLIC_ROOT = Path(__file__).resolve().parents[2]
EXP = Path(os.environ.get("WDCGG_V1_DIR", PUBLIC_ROOT / "results_v1"))
RAW = Path(os.environ.get("WDCGG_DATA_DIR", PUBLIC_ROOT / "data"))
WDCGG = "https://gaw.kishou.go.jp"
THRESHOLDS = (0.70, 0.75, 0.80, 0.90)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def download(record_id: str, target: Path) -> dict[str, object]:
    if target.exists():
        return {"status": "EXISTING", "path": str(target), "size_bytes": target.stat().st_size, "sha256": digest(target)}
    payload = urlencode([("ch", record_id), ("agree", "agree"), ("format", "2"), ("dtype", "1"), ("bt", "Data Download")]).encode("ascii")
    request = Request(WDCGG + "/search", data=payload, method="POST", headers={"User-Agent": "wdcgg-data-pipeline/1.0", "Accept": "application/octet-stream"})
    with urlopen(request, timeout=300) as response:
        body = response.read()
        content_type = response.headers.get("Content-Type", "")
        disposition = response.headers.get("Content-Disposition", "")
    if content_type.split(";", 1)[0].lower() != "application/octet-stream":
        raise RuntimeError(f"unexpected content type {content_type!r}")
    match = re.search(r"filename\s*=\s*\"?([^\";]+)", disposition, re.I)
    filename = Path(match.group(1).strip()).name if match else ""
    if not re.fullmatch(r"WDCGG_[0-9]{14}\.tar\.gz", filename):
        raise RuntimeError(f"unexpected archive filename {filename!r}")
    if body[:2] != b"\x1f\x8b":
        raise RuntimeError("download response is not gzip data")
    target.write_bytes(body)
    return {"status": "DOWNLOADED", "path": str(target), "size_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "archive_filename": filename}


def extract_observation(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    observation = None
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            pure = Path(*Path(member.name).parts)
            if pure.is_absolute() or ".." in pure.parts or member.issym() or member.islnk():
                raise RuntimeError(f"unsafe archive member {member.name}")
            if not member.isfile():
                continue
            target = destination / pure
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                source = tar.extractfile(member)
                if source is None:
                    raise RuntimeError(f"unreadable archive member {member.name}")
                target.write_bytes(source.read())
            # Prefer the actual hourly observation file; the archive may also
            # contain a same-stem `_hourly_met.txt` meteorological companion.
            if member.name.lower().endswith("_hourly.txt"):
                observation = target
    if observation is None:
        raise RuntimeError("hourly observation text not found")
    return observation


def month_hours(year: int, month: int) -> int:
    if month == 12:
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return int((end - start).total_seconds() // 3600)


def process(item: dict[str, str], archive: Path, extracted: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    observation = extract_observation(archive, extracted)
    layout = parse_wdcgg_layout(observation)
    total = value_rows = provider_valid = 0
    first = last = None
    monthly: dict[tuple[int, int], dict[str, float]] = defaultdict(lambda: {"valid": 0, "sum": 0.0, "sumsq": 0.0})
    for row in iter_wdcgg_rows(observation, layout):
        total += 1
        first = row.timestamp_utc if first is None or row.timestamp_utc < first else first
        last = row.timestamp_utc if last is None or row.timestamp_utc > last else last
        if row.value is None:
            continue
        value_rows += 1
        if not row.qc_valid:
            continue
        provider_valid += 1
        bucket = monthly[(row.timestamp_utc.year, row.timestamp_utc.month)]
        bucket["valid"] += 1; bucket["sum"] += row.value; bucket["sumsq"] += row.value * row.value
    target_months = 120
    target = {(y, m) for y in range(2015, 2025) for m in range(1, 13)}
    coverage = {key: monthly[key]["valid"] / month_hours(*key) for key in target}
    threshold_fraction = {str(t): sum(coverage[key] >= t for key in target) / target_months for t in THRESHOLDS}
    monthly_rows = []
    for (year, month), vals in sorted(monthly.items()):
        n = vals["valid"]
        monthly_rows.append({"dataset_id": item["wdcgg_id"], "gas": item["gas"], "station_name": item["station_name"], "year": year, "month": month, "provider_valid_hour_count": int(n), "expected_hour_count": month_hours(year, month), "coverage_fraction": vals["valid"] / month_hours(year, month), "mean": vals["sum"] / n if n else "", "unit": "ppm" if item["gas"] == "CO2" else "ppb"})
    result = {
        **item,
        "archive_path": str(archive), "archive_sha256": digest(archive), "observation_path": str(observation), "observation_sha256": digest(observation),
        "dataset_doi": layout.metadata.get("data_set_doi", ""), "dataset_version": layout.metadata.get("data_set_version", ""),
        "dataset_start_date": layout.metadata.get("dataset_start_date", ""), "dataset_end_date": layout.metadata.get("dataset_end_date", ""),
        "record_count": total, "value_count": value_rows, "provider_valid_count": provider_valid, "provider_valid_fraction": provider_valid / value_rows if value_rows else 0.0,
        "qualified_start": first.isoformat() if first else "", "qualified_end": last.isoformat() if last else "", "target_period_coverage_2015_2024": threshold_fraction["0.75"],
        "qualified_month_fraction_70": threshold_fraction["0.7"], "qualified_month_fraction_75": threshold_fraction["0.75"], "qualified_month_fraction_80": threshold_fraction["0.8"], "qualified_month_fraction_90": threshold_fraction["0.9"],
        "processing_status": "HARMONISED_AND_SUMMARISED",
    }
    return result, monthly_rows


def main() -> None:
    candidates = list(csv.DictReader((EXP / "candidate_availability.csv").open(encoding="utf-8-sig")))
    # Geographic-gap rule: every operational, hourly, accessible GAW Global
    # product is retained; all operational hourly products from Africa and
    # South America are retained because those regions are absent/underfilled
    # in the GAW Global subset. This is not a station-count quota.
    selected = [row for row in candidates if row["hourly"] == "True" and row["accessible"] == "True" and row["station_status"] == "Operational" and (row["wmo_category"] == "GAW Global" or row["wmo_region"] in {"REGION I (Africa)", "REGION III (South America)"})]
    selected.sort(key=lambda r: (r["gas"], r["wmo_region"], r["wdcgg_id"]))
    screening = []
    selected_keys = {(r["wdcgg_id"], r["gas"]) for r in selected}
    for row in candidates:
        key = (row["wdcgg_id"], row["gas"])
        if key in selected_keys:
            status, reason = "SELECTED_FOR_ACQUISITION", "Operational hourly accessible product; global or geographic-gap rule"
        elif row["hourly"] != "True":
            status, reason = "EXCLUDED", "NOT_HOURLY"
        elif row["accessible"] != "True":
            status, reason = "EXCLUDED", "AUTHENTICATION_REQUIRED_OR_LOCKED"
        elif row["station_status"] != "Operational":
            status, reason = "EXCLUDED", "CLOSED_OR_NON_REPORTING"
        else:
            status, reason = "EXCLUDED", "OUTSIDE_PREDECLARED_GEOGRAPHIC_GAP_RULE"
        screening.append({**row, "screening_status": status, "screening_reason": reason, "screening_rule_version": "global_cohort_v1_gap_rule_20260814"})
    with (EXP / "DATASET_SCREENING.csv").open("w", encoding="utf-8", newline="") as h:
        writer = csv.DictWriter(h, fieldnames=list(screening[0])); writer.writeheader(); writer.writerows(screening)
    manifest = []; results = []; monthly_all = []; failures = []
    raw_dir = RAW / "raw_archives"; extract_dir = RAW / "extracted"; raw_dir.mkdir(parents=True, exist_ok=True); extract_dir.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(selected, start=1):
        candidate_id = f"{item['gas'].lower()}_{item['wdcgg_id'].lower()}"
        archive = raw_dir / f"{candidate_id}.tar.gz"
        try:
            info = download(item["record_id"], archive)
            info.update({"candidate_id": candidate_id, "gas": item["gas"], "wdcgg_id": item["wdcgg_id"], "record_id": item["record_id"], "download_url": WDCGG + "/search", "acquisition_timestamp_utc": datetime.now(timezone.utc).isoformat()})
            manifest.append(info)
            result, monthly = process(item, archive, extract_dir / candidate_id)
            results.append(result); monthly_all.extend(monthly)
            print(f"{index}/{len(selected)} OK {candidate_id} rows={result['record_count']} valid={result['provider_valid_count']}")
        except Exception as exc:
            failures.append({"candidate_id": candidate_id, "gas": item["gas"], "wdcgg_id": item["wdcgg_id"], "record_id": item["record_id"], "status": "FAILED", "error": repr(exc)})
            print(f"{index}/{len(selected)} FAIL {candidate_id}: {exc}")
    if manifest:
        with (EXP / "DOWNLOAD_MANIFEST.csv").open("w", encoding="utf-8", newline="") as h:
            writer = csv.DictWriter(h, fieldnames=sorted({k for row in manifest for k in row})); writer.writeheader(); writer.writerows(manifest)
    if results:
        with (EXP / "HARMONISATION_RESULTS.csv").open("w", encoding="utf-8", newline="") as h:
            writer = csv.DictWriter(h, fieldnames=list(results[0])); writer.writeheader(); writer.writerows(results)
    with (EXP / "GAP_COVERAGE_MONTHLY.csv").open("w", encoding="utf-8", newline="") as h:
        fields = list(monthly_all[0]) if monthly_all else ["dataset_id"]
        writer = csv.DictWriter(h, fieldnames=fields); writer.writeheader(); writer.writerows(monthly_all)
    summary = {"candidates_discovered": len(candidates), "candidates_selected": len(selected), "successfully_acquired": len(manifest), "successfully_harmonised": len(results), "failed": len(failures), "processed_records": sum(int(r["record_count"]) for r in results), "provider_valid_records": sum(int(r["provider_valid_count"]) for r in results), "manual_interventions": 0, "unresolved_failures": failures, "selection_rule": "all operational hourly accessible GAW Global plus all operational hourly accessible Africa/South America products", "thresholds_tested": list(THRESHOLDS)}
    (EXP / "PIPELINE_SCALABILITY_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
