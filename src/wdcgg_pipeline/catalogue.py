from __future__ import annotations

import csv
import os
import hashlib
import io
import json
import re
import tarfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PUBLIC_ROOT = Path(__file__).resolve().parents[2]
EXP = Path(os.environ.get("WDCGG_V1_DIR", PUBLIC_ROOT / "results_v1"))
RAW = ROOT / "data" / "WDCGG_Global_Cohort_v1"
WDCGG = "https://gaw.kishou.go.jp"


def get(url: str, data: list[tuple[str, str]] | None = None, timeout: int = 180) -> bytes:
    request = Request(url, data=urlencode(data or []).encode() if data is not None else None, method="POST" if data is not None else "GET")
    request.add_header("User-Agent", "wdcgg-data-pipeline/1.0")
    return urlopen(request, timeout=timeout).read()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def current_metadata() -> tuple[str, list[dict[str, str]]]:
    html = get(WDCGG + "/search").decode("utf-8", "replace")
    match = re.search(r'href="(https://gaw\.kishou\.go\.jp/tmp/metadata/[^" ]+\.csv)"', html)
    if not match:
        raise RuntimeError("public WDCGG metadata CSV link not found")
    url = match.group(1)
    rows = list(csv.DictReader(io.StringIO(get(url).decode("utf-8-sig", "replace"))))
    return url, rows


def parse_gas_page(html: str, gas: str) -> list[dict[str, object]]:
    records = []
    for match in re.finditer(r"<tr>\s*(.*?)</tr>", html, re.I | re.S):
        row = match.group(1)
        id_match = re.search(rf'id="{gas}_([A-Z0-9]+)"', row, re.I)
        if not id_match:
            continue
        cells = re.findall(r"<td\b[^>]*>.*?</td>", row, re.I | re.S)
        flags = [bool(re.search(r"check_red\.gif", cell, re.I)) for cell in cells]
        record_match = re.search(r'name="ch"\s+value="([^"]+)"', row, re.I)
        station_match = re.search(r"<td>\s*([^<]+)<br>\s*\(<a[^>]*>([A-Z0-9]+)</a>,([^,]+),([^\)]+)\)", row, re.I | re.S)
        contributor_match = re.search(r"search/contributor[^>]*>([^<]+)</a>", row, re.I)
        # The table order is access, download, favourite, station, contributor,
        # event, hourly, daily, monthly, metadata, platform, scale.
        records.append({
            "gas": gas.upper(),
            "wdcgg_id": id_match.group(1).upper(),
            "record_id": record_match.group(1) if record_match else "",
            "accessible": bool(re.search(r"unlock\.svg", row, re.I)),
            "event": bool(len(flags) > 5 and flags[5]),
            "hourly": bool(len(flags) > 6 and flags[6]),
            "daily": bool(len(flags) > 7 and flags[7]),
            "monthly": bool(len(flags) > 8 and flags[8]),
            "metadata_link": bool(len(flags) > 9 and flags[9]),
            "station_name": station_match.group(1).strip() if station_match else "",
            "station_code": station_match.group(2).upper() if station_match else "",
            "country": station_match.group(3).strip() if station_match else "",
            "wigos_id": station_match.group(4).strip() if station_match else "",
            "contributor": contributor_match.group(1).strip() if contributor_match else "",
        })
    return records


def discover(meta_rows: list[dict[str, str]]) -> list[dict[str, object]]:
    station_codes = [row["Station Code"] for row in meta_rows if row.get("Station Type") == "fixed station"]
    availability: list[dict[str, object]] = []
    for gas, code in (("CO2", "1001"), ("CH4", "1002")):
        payload = [("st_name", code_) for code_ in station_codes]
        payload += [("pm_wdcgg_code", code), ("bt", "Search")]
        html = get(WDCGG + "/search", payload).decode("utf-8", "replace")
        availability.extend(parse_gas_page(html, gas))
    by_id = {(row["WDCGG ID"], row["Gas Species"], row["Station Type"]): row for row in meta_rows}
    merged = []
    seen = set()
    for item in availability:
        key = (item["wdcgg_id"], item["gas"])
        if key in seen:
            continue
        seen.add(key)
        meta = next((row for row in meta_rows if row.get("WDCGG ID", "").upper() == item["wdcgg_id"]), {})
        merged.append({
            **item,
            "station_code": meta.get("Station Code", item["station_code"]),
            "station_name": meta.get("Station Name", item["station_name"]).strip(),
            "latitude": meta.get("Latitude", ""),
            "longitude": meta.get("Longitude", ""),
            "elevation": meta.get("Elevation", ""),
            "wmo_region": meta.get("WMO Region", ""),
            "country": meta.get("Country/Territory", item["country"]),
            "wmo_category": meta.get("WMO Category", ""),
            "station_type": meta.get("Station Type", ""),
            "station_status": meta.get("Station Status", ""),
            "metadata_contributor": meta.get("Contributor", ""),
            "gas_species": meta.get("Gas Species", ""),
        })
    return merged


def main() -> None:
    EXP.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    metadata_url, meta_rows = current_metadata()
    with (EXP / "wdcgg_public_metadata.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(meta_rows[0]))
        writer.writeheader(); writer.writerows(meta_rows)
    candidates = discover(meta_rows)
    fields = list(candidates[0]) if candidates else []
    with (EXP / "candidate_availability.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(candidates)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "metadata_url": metadata_url,
        "candidate_station_products": len(candidates),
        "fixed_station_metadata_rows": sum(row.get("Station Type") == "fixed station" for row in meta_rows),
        "co2_candidates": sum(row["gas"] == "CO2" for row in candidates),
        "ch4_candidates": sum(row["gas"] == "CH4" for row in candidates),
        "hourly_accessible": sum(row["hourly"] and row["accessible"] for row in candidates),
        "hourly_accessible_co2": sum(row["hourly"] and row["accessible"] and row["gas"] == "CO2" for row in candidates),
        "hourly_accessible_ch4": sum(row["hourly"] and row["accessible"] and row["gas"] == "CH4" for row in candidates),
        "regions": sorted({row["wmo_region"] for row in candidates if row["hourly"] and row["accessible"]}),
    }
    (EXP / "candidate_discovery_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
