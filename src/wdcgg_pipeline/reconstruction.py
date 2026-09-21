"""Recovered five-method gap-reconstruction experiment."""
from wdcgg_pipeline.experiments import fill_gap

METHODS = ("linear", "pchip", "local_level_kalman", "seasonal_median", "seasonal_residual_linear")
__all__ = ["METHODS", "fill_gap"]
