"""Explicit deterministic unit conversion for dry-air mole fractions."""

from __future__ import annotations

from wdcgg_pipeline.models import Species


def convert_value(value: float, source_unit: str, species: Species) -> tuple[float, str]:
    unit = source_unit.strip().lower().replace("µ", "u")
    target = "ppm" if species == Species.CO2 else "ppb"
    factors = {
        ("mol/mol", "ppm"): 1_000_000.0,
        ("mol mol-1", "ppm"): 1_000_000.0,
        ("ppm", "ppm"): 1.0,
        ("umol/mol", "ppm"): 1.0,
        ("mol/mol", "ppb"): 1_000_000_000.0,
        ("mol mol-1", "ppb"): 1_000_000_000.0,
        ("ppb", "ppb"): 1.0,
        ("nmol/mol", "ppb"): 1.0,
        ("ppm", "ppb"): 1_000.0,
    }
    try:
        return value * factors[(unit, target)], target
    except KeyError as exc:
        raise ValueError(
            f"unconfirmed conversion from {source_unit!r} to {target} for {species}"
        ) from exc
