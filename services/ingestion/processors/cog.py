"""
Cloud-Optimized GeoTIFF (COG) utilities.

Provides functions to:
- Build overviews on existing GeoTIFFs
- Convert GeoTIFFs to COG format with uint8 quantization
- Optimize files for fast WMS serving via GeoServer

A COG-like GeoTIFF with internal tiling, overviews, and DEFLATE compression
serves 10-100x faster than an unoptimized float32 GeoTIFF.
"""

import logging
import os
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.shutil import copy as rio_copy

logger = logging.getLogger(__name__)

# Overview levels: 2x, 4x, 8x, 16x, 32x downsampling
OVERVIEW_FACTORS = [2, 4, 8, 16, 32]

# Tile size for COG (512 is optimal for web serving)
COG_BLOCKSIZE = 512


def build_overviews(
    file_path: str,
    factors: list[int] = None,
    resampling: Resampling = Resampling.average,
) -> str:
    """
    Build internal overviews (pyramids) on an existing GeoTIFF.

    This is the single most impactful optimization for WMS serving.
    Without overviews, GeoServer must read the full-resolution raster
    for every zoom level.

    Args:
        file_path: Path to GeoTIFF (modified in-place)
        factors: Overview levels (default [2, 4, 8, 16, 32])
        resampling: Resampling method (average for continuous, nearest for discrete)

    Returns:
        The same file_path (modified in-place)
    """
    if factors is None:
        factors = OVERVIEW_FACTORS

    logger.info(f"Building overviews for: {file_path} (factors={factors})")

    with rasterio.open(file_path, "r+") as ds:
        existing = ds.overviews(1)
        if existing:
            logger.info(f"  Overviews already exist: {existing}, skipping")
            return file_path

        ds.build_overviews(factors, resampling)
        ds.update_tags(ns="rio_overview", resampling=resampling.name)

    logger.info(f"  ✓ Overviews built for {os.path.basename(file_path)}")
    return file_path


def create_cog_profile(src_profile: dict, dtype="uint8") -> dict:
    """
    Create a COG-optimized rasterio write profile.

    Args:
        src_profile: Source rasterio profile to base on
        dtype: Output data type (default uint8 for 4x size reduction)

    Returns:
        Optimized profile dict
    """
    profile = src_profile.copy()
    profile.update(
        driver="GTiff",
        dtype=dtype,
        count=1,
        compress="deflate",
        predictor=2,  # Horizontal differencing - great for continuous data
        tiled=True,
        blockxsize=COG_BLOCKSIZE,
        blockysize=COG_BLOCKSIZE,
        nodata=255 if dtype == "uint8" else -9999,
    )
    # Remove photometric if present (not needed for single-band)
    profile.pop("photometric", None)
    return profile

