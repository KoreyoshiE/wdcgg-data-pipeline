"""Streaming parser for the official WDCGG v3-style Text layout."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from wdcgg_pipeline.units import convert_value
from wdcgg_pipeline.qc import map_wdcgg_qc
from wdcgg_pipeline.models import Species


FILL_FLOATS = {-999.999, -999999.999, -999.999999999}


@dataclass(frozen=True, slots=True)
class WDCGGTextLayout:
    metadata: dict[str, str]
    columns: tuple[str, ...]
    header_lines: int
    species: Species
    unit: str
    lst_to_utc_hours: float


@dataclass(frozen=True, slots=True)
class WDCGGParsedRow:
    timestamp_source: datetime
    timestamp_utc: datetime
    original_value: float | None
    value: float | None
    unit: str
    value_wmo_scale: float | None
    uncertainty: float | None
    qc_original: str
    qc_standard: str
    qc_valid: bool
    latitude: float | None
    longitude: float | None
    altitude_m: float | None
    elevation_m: float | None
    intake_height_m: float | None
    raw_line: str


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def parse_wdcgg_layout(path: Path, max_header_lines: int = 10_000) -> WDCGGTextLayout:
    metadata: dict[str, str] = {}
    columns: tuple[str, ...] | None = None
    header_lines = 0
    awaiting_order = False
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if number > max_header_lines:
                raise ValueError("WDCGG header exceeds configured bound")
            if not line.startswith("#"):
                break
            header_lines = number
            stripped = line.lstrip("#; ").strip()
            if not stripped:
                continue
            if stripped.upper() == "VARIABLE ORDER":
                awaiting_order = True
                continue
            if awaiting_order:
                columns = tuple(stripped.split())
                awaiting_order = False
                continue
            if " : " in stripped:
                key, value = stripped.split(" : ", 1)
                metadata[_normalize_key(key)] = value.strip()
            else:
                match = re.match(r"([^:=]+)\s*[:=]\s*(.+)$", stripped)
                if match:
                    metadata[_normalize_key(match.group(1))] = match.group(2).strip()
    if not columns:
        raise ValueError("WDCGG VARIABLE ORDER is missing")
    species_text = metadata.get("dataset_parameter", "").upper()
    try:
        species = Species(species_text)
    except ValueError as exc:
        raise ValueError(f"unknown WDCGG species: {species_text!r}") from exc
    unit = metadata.get("value_units")
    if not unit:
        raise ValueError("WDCGG value unit is missing")
    if "dataset_lst2utc" in metadata:
        lst_to_utc = float(metadata["dataset_lst2utc"])
    elif metadata.get("dataset_time_zone", "").upper() in {"UTC", "Z", "UTC+00:00"}:
        lst_to_utc = 0.0
    else:
        raise ValueError("WDCGG local-to-UTC offset is unverified")
    return WDCGGTextLayout(
        metadata=metadata,
        columns=columns,
        header_lines=header_lines,
        species=species,
        unit=unit,
        lst_to_utc_hours=lst_to_utc,
    )


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    if parsed in FILL_FLOATS or parsed <= -999999:
        return None
    return parsed


def iter_wdcgg_rows(path: Path, layout: WDCGGTextLayout | None = None) -> Iterator[WDCGGParsedRow]:
    layout = layout or parse_wdcgg_layout(path)
    with path.open(encoding="utf-8") as handle:
        for _ in range(layout.header_lines):
            next(handle)
        for line_number, line in enumerate(handle, start=layout.header_lines + 1):
            raw = line.strip()
            if not raw:
                continue
            parts = raw.split()
            if len(parts) != len(layout.columns):
                raise ValueError(
                    f"WDCGG row {line_number} has {len(parts)} fields; expected {len(layout.columns)}"
                )
            row = dict(zip(layout.columns, parts, strict=True))
            try:
                local = datetime(
                    int(row["st_year"]),
                    int(row["st_month"]),
                    int(row["st_day"]),
                    int(row["st_hour"]),
                    int(row["st_minute"]),
                    int(row["st_second"]),
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(f"invalid WDCGG time components on row {line_number}") from exc
            timestamp_utc = (local + timedelta(hours=layout.lst_to_utc_hours)).replace(
                tzinfo=UTC
            )
            original = _float(row.get("value"))
            converted = (
                convert_value(original, layout.unit, layout.species)[0]
                if original is not None
                else None
            )
            qc_original = row.get("ORG_QCflag", "")
            qc_raw = row.get("QCflag", "")
            qc = map_wdcgg_qc(qc_raw)
            yield WDCGGParsedRow(
                timestamp_source=local,
                timestamp_utc=timestamp_utc,
                original_value=original,
                value=converted,
                unit="ppm" if layout.species == Species.CO2 else "ppb",
                value_wmo_scale=_float(row.get("value_wmo_scale")),
                uncertainty=_float(row.get("value_sd")),
                qc_original=qc_original,
                qc_standard=qc.qc_standard,
                qc_valid=qc.retain,
                latitude=_float(row.get("latitude")),
                longitude=_float(row.get("longitude")),
                altitude_m=_float(row.get("altitude")),
                elevation_m=_float(row.get("elevation")),
                intake_height_m=_float(row.get("intake_height")),
                raw_line=raw,
            )
