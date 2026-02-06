"""
UHI Data Ingestion Service

FastAPI service for downloading orthophotos from UrbIS, 
processing NDVI/NDWI indices, and registering layers in Orion-LD.
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel

from processors.ndvi import calculate_ndvi
from processors.ndwi import calculate_ndwi
from processors.cog import build_overviews
from fiware.client import OrionClient, GeoSpatialLayer

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
ORION_URL = os.getenv("ORION_URL", "http://orion:1026")
DATA_RAW_PATH = Path(os.getenv("DATA_RAW_PATH", "/data/raw"))
DATA_PROCESSED_PATH = Path(os.getenv("DATA_PROCESSED_PATH", "/data/processed"))
RGB_URL = os.getenv("RGB_URL", "")
NIR_URL = os.getenv("NIR_URL", "")

# Ensure directories exist
DATA_RAW_PATH.mkdir(parents=True, exist_ok=True)
DATA_PROCESSED_PATH.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting UHI Ingestion Service")
    logger.info(f"Orion URL: {ORION_URL}")
    logger.info(f"Raw data path: {DATA_RAW_PATH}")
    logger.info(f"Processed data path: {DATA_PROCESSED_PATH}")
    yield
    logger.info("Shutting down UHI Ingestion Service")


app = FastAPI(
    title="UHI Data Ingestion Service",
    description="Download and process orthophotos, register layers in FIWARE Orion-LD",
    version="1.0.0",
    lifespan=lifespan,
)

# Initialize Orion client
orion_client = OrionClient(ORION_URL)


class IngestRequest(BaseModel):
    """Request model for orthophoto ingestion."""
    rgb_url: Optional[str] = None
    nir_url: Optional[str] = None


class IngestResponse(BaseModel):
    """Response model for ingestion status."""
    status: str
    message: str
    layers: list[str] = []


class LayerInfo(BaseModel):
    """Information about a registered layer."""
    id: str
    name: str
    layer_type: str
    file_path: str
    geoserver_layer: str


# Track ingestion status
ingestion_status = {
    "running": False,
    "progress": "",
    "layers_created": []
}


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "ingestion"}


@app.get("/status")
async def get_status():
    """Get current ingestion status."""
    return ingestion_status


@app.get("/layers", response_model=list[LayerInfo])
async def list_layers():
    """List all registered GeoSpatialLayer entities from Orion."""
    try:
        layers = await orion_client.get_all_layers()
        return [
            LayerInfo(
                id=layer.get("id", ""),
                name=layer.get("name", {}).get("value", ""),
                layer_type=layer.get("layerType", {}).get("value", ""),
                file_path=layer.get("filePath", {}).get("value", ""),
                geoserver_layer=layer.get("geoserverLayer", {}).get("value", "")
            )
            for layer in layers
        ]
    except Exception as e:
        logger.error(f"Failed to list layers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/orthophotos", response_model=IngestResponse)
async def ingest_orthophotos(
    background_tasks: BackgroundTasks,
    request: Optional[IngestRequest] = None
):
    """
    Download and process orthophotos from UrbIS.
    
    This endpoint triggers the full ingestion pipeline:
    1. Download RGB and NIR orthophotos
    2. Extract GeoTIFF files
    3. Calculate NDVI and NDWI indices
    4. Register all layers in Orion-LD
    
    Can be called without a body to use default URLs from environment.
    """
    if ingestion_status["running"]:
        raise HTTPException(
            status_code=409,
            detail="Ingestion already in progress"
        )
    
    rgb_url = (request.rgb_url if request else None) or RGB_URL
    nir_url = (request.nir_url if request else None) or NIR_URL
    
    if not rgb_url or not nir_url:
        raise HTTPException(
            status_code=400,
            detail="RGB and NIR URLs are required"
        )
    
    # Start background ingestion
    background_tasks.add_task(
        run_ingestion_pipeline,
        rgb_url,
        nir_url
    )
    
    return IngestResponse(
        status="started",
        message="Ingestion pipeline started in background",
        layers=[]
    )


async def run_ingestion_pipeline(rgb_url: str, nir_url: str):
    """Run the full ingestion pipeline."""
    global ingestion_status
    
    ingestion_status = {
        "running": True,
        "progress": "Starting ingestion...",
        "layers_created": []
    }
    
    try:
        # Step 1: Download orthophotos
        ingestion_status["progress"] = "Downloading RGB orthophoto..."
        logger.info(f"Downloading RGB from: {rgb_url}")
        rgb_path = await download_and_extract(rgb_url, "rgb")
        
        ingestion_status["progress"] = "Downloading NIR orthophoto..."
        logger.info(f"Downloading NIR from: {nir_url}")
        nir_path = await download_and_extract(nir_url, "nir")
        
        if not rgb_path or not nir_path:
            raise Exception("Failed to download orthophotos")
        
        # Use raw files directly (no copying to save disk space and memory)
        # The raw files are 6GB+ each, so we process directly from them
        rgb_processed = rgb_path  # Keep reference to raw file
        nir_processed = nir_path  # Keep reference to raw file
        
        logger.info(f"Using RGB directly from: {rgb_processed}")
        logger.info(f"Using NIR directly from: {nir_processed}")
        
        # Step 1b: Build overviews for RGB and NIR (critical for WMS performance)
        # Without overviews, GeoServer reads the full 6GB file at every zoom level
        ingestion_status["progress"] = "Building RGB overviews for fast WMS serving..."
        await asyncio.to_thread(build_overviews, str(rgb_processed))
        
        ingestion_status["progress"] = "Building NIR overviews for fast WMS serving..."
        await asyncio.to_thread(build_overviews, str(nir_processed))
        
        # Step 2: Calculate NDVI (using windowed processing for large files)
        ingestion_status["progress"] = "Calculating NDVI (windowed processing)..."
        ndvi_path = DATA_PROCESSED_PATH / "ndvi_brussels_2024.tif"
        await asyncio.to_thread(
            calculate_ndvi,
            str(rgb_processed),
            str(nir_processed),
            str(ndvi_path)
        )
        logger.info(f"NDVI calculated: {ndvi_path}")
        
        # Step 3: Calculate NDWI (using windowed processing for large files)
        ingestion_status["progress"] = "Calculating NDWI (windowed processing)..."
        ndwi_path = DATA_PROCESSED_PATH / "ndwi_brussels_2024.tif"
        await asyncio.to_thread(
            calculate_ndwi,
            str(rgb_processed),
            str(nir_processed),
            str(ndwi_path)
        )
        logger.info(f"NDWI calculated: {ndwi_path}")
        
        # Step 5: Register layers in Orion
        ingestion_status["progress"] = "Registering layers in Orion..."
        
        layers_to_register = [
            GeoSpatialLayer(
                layer_type="RGB",
                name="RGB Brussels 2024",
                spectral_range="RGB",
                file_path=str(rgb_processed),
                resolution=40
            ),
            GeoSpatialLayer(
                layer_type="NIR",
                name="NIR Brussels 2024",
                spectral_range="NIR",
                file_path=str(nir_processed),
                resolution=40
            ),
            GeoSpatialLayer(
                layer_type="NDVI",
                name="NDVI Brussels 2024",
                spectral_range="computed",
                file_path=str(ndvi_path),
                resolution=40
            ),
            GeoSpatialLayer(
                layer_type="NDWI",
                name="NDWI Brussels 2024",
                spectral_range="computed",
                file_path=str(ndwi_path),
                resolution=40
            ),
        ]
        
        for layer in layers_to_register:
            try:
                await orion_client.create_or_update_layer(layer)
                ingestion_status["layers_created"].append(layer.layer_type)
                logger.info(f"Registered layer: {layer.layer_type}")
            except Exception as e:
                logger.error(f"Failed to register {layer.layer_type}: {e}")
        
        ingestion_status["progress"] = "Ingestion complete!"
        logger.info("Ingestion pipeline completed successfully")
        
    except Exception as e:
        logger.error(f"Ingestion pipeline failed: {e}")
        ingestion_status["progress"] = f"Error: {str(e)}"
    finally:
        ingestion_status["running"] = False


async def download_and_extract(url: str, name: str) -> Optional[Path]:
    """Download a ZIP file and extract the GeoTIFF. Skip if already exists."""
    import httpx
    import zipfile
    
    zip_path = DATA_RAW_PATH / f"{name}.zip"
    extract_dir = DATA_RAW_PATH / name
    
    try:
        # Check if already extracted and has TIFF files
        if extract_dir.exists():
            tiff_files = list(extract_dir.rglob("*.tif")) + list(extract_dir.rglob("*.tiff"))
            if tiff_files:
                logger.info(f"Skipping download for {name}: found existing TIFF at {tiff_files[0]}")
                return tiff_files[0]
        
        # Download the file
        async with httpx.AsyncClient(timeout=600.0) as client:
            logger.info(f"Starting download: {url}")
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(zip_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
        
        logger.info(f"Downloaded: {zip_path}")
        
        # Extract the ZIP
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        logger.info(f"Extracted to: {extract_dir}")
        
        # Find the TIFF file
        tiff_files = list(extract_dir.rglob("*.tif")) + list(extract_dir.rglob("*.tiff"))
        if tiff_files:
            return tiff_files[0]
        
        logger.error(f"No TIFF files found in {extract_dir}")
        return None
        
    except Exception as e:
        logger.error(f"Failed to download/extract {url}: {e}")
        return None


async def copy_geotiff(src: Path, dst: Path):
    """Copy a GeoTIFF file."""
    import shutil
    await asyncio.to_thread(shutil.copy2, src, dst)
    logger.info(f"Copied {src} to {dst}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

