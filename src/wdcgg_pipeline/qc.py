"""Official WDCGG QC mapping from format specification v3.0."""

from __future__ import annotations

from wdcgg_pipeline.models import QCDecision


def map_wdcgg_qc(value: str | int | None) -> QCDecision:
    normalized = "" if value is None else str(value).strip()
    mapping = {
        "1": ("valid_background", True, "WDCGG QC 1: valid background"),
        "2": ("valid", True, "WDCGG QC 2: valid other/all valid"),
        "3": ("invalid", False, "WDCGG QC 3: invalid/unfit"),
    }
    if normalized not in mapping:
        return QCDecision(
            qc_original=normalized or None,
            qc_standard="unknown",
            retain=False,
            rationale="QC meaning is not confirmed",
            mapping_source="WDCGG Data Format v3.0",
            human_confirmation_required=True,
        )
    standard, retain, rationale = mapping[normalized]
    return QCDecision(
        qc_original=normalized,
        qc_standard=standard,
        retain=retain,
        rationale=rationale,
        mapping_source="WDCGG Data Format v3.0",
    )
