"""
NDVI (Normalized Difference Vegetation Index) Processor

NDVI = (NIR - Red) / (NIR + Red)

Values range from -1 to 1:
- High values (0.6 to 1.0): Dense vegetation
- Medium values (0.2 to 0.6): Moderate vegetation
- Low values (-0.1 to 0.2): Sparse vegetation, soil
- Negative values: Water, clouds, snow

Output is a Cloud-Optimized GeoTIFF (COG) with:
- uint8 dtype: values scaled from [-1, 1] to [0, 254], 255 = nodata
- DEFLATE compression with horizontal predictor
- 512x512 internal tiles
- Internal overviews (pyramids) at 2x, 4x, 8x, 16x, 32x

Uses windowed processing for large files to avoid memory issues.
"""

import logging
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window

from processors.cog import build_overviews, create_cog_profile, COG_BLOCKSIZE

logger = logging.getLogger(__name__)

# Process in tiles of this size (adjust based on available memory)
TILE_SIZE = 2048


def calculate_ndvi(
    rgb_path: str,
    nir_path: str,
    output_path: str,
    red_band_index: int = 1,  # Red is band 1 in RGB (1-indexed)
    tile_size: int = TILE_SIZE
) -> str:
    """
    Calculate NDVI from RGB and NIR orthophotos using windowed processing.
    Output is a COG-optimized uint8 GeoTIFF for fast WMS serving.
    
    Args:
        rgb_path: Path to RGB GeoTIFF file
        nir_path: Path to NIR GeoTIFF file  
        output_path: Path for output NDVI GeoTIFF
        red_band_index: Index of red band in RGB file (1-indexed, default 1 for R)
        tile_size: Size of processing tiles (default 2048)
    
    Returns:
        Path to the created NDVI GeoTIFF
    
    Encoding:
        NDVI [-1, 1] -> uint8 [0, 254], 255 = nodata
        To decode: ndvi = (pixel_value / 254) * 2 - 1
    """
    logger.info(f"Calculating NDVI from RGB: {rgb_path} and NIR: {nir_path}")
    logger.info(f"Output: uint8 COG with overviews, tile size: {tile_size}")
    
    with rasterio.open(rgb_path) as rgb_src:
        with rasterio.open(nir_path) as nir_src:
            # Use the smaller dimensions
            height = min(rgb_src.height, nir_src.height)
            width = min(rgb_src.width, nir_src.width)
            
            logger.info(f"Output dimensions: {width} x {height}")
            
            # Create COG-optimized output profile (uint8)
            profile = rgb_src.profile.copy()
            profile.update(
                height=height,
                width=width,
            )
            profile = create_cog_profile(profile, dtype="uint8")
            
            with rasterio.open(output_path, 'w', **profile) as dst:
                # Process in tiles
                total_tiles = ((height + tile_size - 1) // tile_size) * ((width + tile_size - 1) // tile_size)
                tile_count = 0
                
                for row_off in range(0, height, tile_size):
                    for col_off in range(0, width, tile_size):
                        # Calculate actual tile size (may be smaller at edges)
                        win_height = min(tile_size, height - row_off)
                        win_width = min(tile_size, width - col_off)
                        
                        window = Window(col_off, row_off, win_width, win_height)
                        
                        # Read tiles
                        red = rgb_src.read(red_band_index, window=window).astype(np.float32)
                        nir = nir_src.read(1, window=window).astype(np.float32)
                        
                        # Calculate NDVI
                        denominator = nir + red
                        ndvi = np.where(
                            denominator != 0,
                            (nir - red) / denominator,
                            0
                        )
                        ndvi = np.clip(ndvi, -1, 1)
                        
                        # Scale to uint8: [-1, 1] -> [0, 254], 255 = nodata
                        ndvi_uint8 = ((ndvi + 1) / 2 * 254).astype(np.uint8)
                        
                        # Write tile
                        dst.write(ndvi_uint8, 1, window=window)
                        
                        tile_count += 1
                        if tile_count % 100 == 0:
                            logger.info(f"NDVI progress: {tile_count}/{total_tiles} tiles")
                
                # Add metadata
                dst.update_tags(
                    LAYER_TYPE="NDVI",
                    FORMULA="(NIR - Red) / (NIR + Red)",
                    SOURCE_RGB=rgb_path,
                    SOURCE_NIR=nir_path,
                    VALUE_RANGE="-1 to 1",
                    ENCODING="uint8: [0,254] maps to [-1,1], 255=nodata",
                    DECODE_FORMULA="ndvi = (pixel / 254) * 2 - 1"
                )
    
    # Build overviews for fast multi-zoom WMS serving
    build_overviews(output_path, resampling=Resampling.average)
    
    logger.info(f"NDVI COG saved to: {output_path}")
    return output_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 4:
        calculate_ndvi(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        print("Usage: python ndvi.py <rgb_path> <nir_path> <output_path>")
