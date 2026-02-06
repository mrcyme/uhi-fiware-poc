"""Processors for geospatial data."""

from .ndvi import calculate_ndvi
from .ndwi import calculate_ndwi

__all__ = ["calculate_ndvi", "calculate_ndwi"]



