"""Validated scientific boundary models used by the WDCGG pipeline."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Species(StrEnum):
    CO2 = "CO2"
    CH4 = "CH4"


class QCDecision(StrictModel):
    qc_original: str | None
    qc_standard: Literal["valid_background", "valid", "invalid", "unknown"]
    retain: bool
    rationale: str
    mapping_source: str
    human_confirmation_required: bool = False
