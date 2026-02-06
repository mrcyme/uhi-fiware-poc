"""
UHI Prediction Service

FastAPI service for Urban Heat Island predictions.

Architecture principle:
  - Input layer file paths are NEVER hardcoded.
  - They are resolved at runtime by querying Orion-LD entities.
  - The only "hardcoded" knowledge is the ENTITY IDs of the input layers
    (configurable via environment variables).
"""

import os
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import Window

# ---------------------------------------------------------------------------
# Processing constants
# ---------------------------------------------------------------------------
TILE_SIZE = 2048
OVERVIEW_FACTORS = [2, 4, 8, 16, 32]
COG_BLOCKSIZE = 512

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ORION_URL = os.getenv("ORION_URL", "http://orion:1026")

# Output directory – the prediction service's own concern
OUTPUT_PATH = Path(os.getenv("DATA_PROCESSED_PATH", "/data/processed"))

# Entity IDs of the input layers – the ONLY "hardcoded" knowledge.
# These identify which Orion entities hold the file paths for the inputs.
NDVI_ENTITY_ID = os.getenv(
    "NDVI_ENTITY_ID",
    "urn:ngsi-ld:GeoSpatialLayer:NDVI:brussels:2024",
)
NDWI_ENTITY_ID = os.getenv(
    "NDWI_ENTITY_ID",
    "urn:ngsi-ld:GeoSpatialLayer:NDWI:brussels:2024",
)

NGSI_LD_CONTEXT = "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld"

# Subscription ID for the prediction trigger
PREDICTION_SUBSCRIPTION_ID = "urn:ngsi-ld:Subscription:uhi-prediction-trigger"
SELF_URL = os.getenv("SELF_URL", "http://prediction:8000")

# ---------------------------------------------------------------------------
# Prediction lock – prevents redundant concurrent predictions
# ---------------------------------------------------------------------------
_prediction_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
async def _register_prediction_subscription() -> None:
    """
    Register (or re-create) the Orion-LD subscription that triggers
    this service when its *specific* input layers change.

    Only watches the entity IDs declared in NDVI_ENTITY_ID / NDWI_ENTITY_ID,
    so adding unrelated GeoSpatialLayer entities will NOT trigger a prediction.
    """
    subscription = {
        "@context": NGSI_LD_CONTEXT,
        "id": PREDICTION_SUBSCRIPTION_ID,
        "type": "Subscription",
        "description": "Trigger UHI prediction when NDVI or NDWI input layers change",
        "entities": [
            {"id": NDVI_ENTITY_ID, "type": "GeoSpatialLayer"},
            {"id": NDWI_ENTITY_ID, "type": "GeoSpatialLayer"},
        ],
        "watchedAttributes": ["filePath"],
        "notification": {
            "endpoint": {
                "uri": f"{SELF_URL}/predict",
                "accept": "application/json",
            }
        },
    }
    headers = {
        "Content-Type": "application/ld+json",
        "Accept": "application/ld+json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{ORION_URL}/ngsi-ld/v1/subscriptions",
                json=subscription,
                headers=headers,
            )
            if r.status_code == 201:
                logger.info(f"Created prediction subscription {PREDICTION_SUBSCRIPTION_ID}")
                return
            if r.status_code == 409:
                # Already exists — delete and recreate to ensure it's up-to-date
                await client.delete(
                    f"{ORION_URL}/ngsi-ld/v1/subscriptions/{PREDICTION_SUBSCRIPTION_ID}"
                )
                r = await client.post(
                    f"{ORION_URL}/ngsi-ld/v1/subscriptions",
                    json=subscription,
                    headers=headers,
                )
                if r.status_code == 201:
                    logger.info(f"Re-created prediction subscription {PREDICTION_SUBSCRIPTION_ID}")
                    return
            logger.error(f"Failed to register prediction subscription: {r.status_code} {r.text}")
    except Exception as exc:
        logger.warning(f"Could not register prediction subscription: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting UHI Prediction Service")
    logger.info(f"  Orion URL       : {ORION_URL}")
    logger.info(f"  Output path     : {OUTPUT_PATH}")
    logger.info(f"  NDVI entity ID  : {NDVI_ENTITY_ID}")
    logger.info(f"  NDWI entity ID  : {NDWI_ENTITY_ID}")
    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)

    # Self-register Orion subscription (retry a few times if Orion is slow)
    for attempt in range(5):
        try:
            await _register_prediction_subscription()
            break
        except Exception as exc:
            logger.warning(f"Subscription registration attempt {attempt + 1} failed: {exc}")
            await asyncio.sleep(3)

    yield
    logger.info("Shutting down UHI Prediction Service")


app = FastAPI(
    title="UHI Prediction Service",
    description=(
        "Urban Heat Island prediction service. "
        "Input layer paths are resolved from Orion-LD at runtime."
    ),
    version="2.0.0",
    lifespan=lifespan,
)


# ===================================================================
# Orion-LD helpers
# ===================================================================

async def get_entity_from_orion(entity_id: str) -> dict:
    """
    Fetch an NGSI-LD entity from Orion by ID.

    Raises HTTPException (503/504/502/404) when Orion is unreachable,
    times out, returns an error, or the entity is not found.
    """
    url = f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                url,
                params={"local": "true"},
                headers={"Accept": "application/json"},
            )
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to Orion at {ORION_URL}: {exc}",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail=f"Timeout connecting to Orion at {ORION_URL}",
        )

    if response.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Entity {entity_id} not found in Orion. "
                "Has the ingestion pipeline run?"
            ),
        )
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Orion returned {response.status_code} for entity "
                f"{entity_id}: {response.text}"
            ),
        )

    return response.json()


def extract_file_path(entity: dict) -> Path:
    """
    Extract and validate the ``filePath`` property from an NGSI-LD entity.

    Raises HTTPException if the property is missing or the file doesn't exist.
    """
    file_path_value = entity.get("filePath", {}).get("value")
    if not file_path_value:
        raise HTTPException(
            status_code=502,
            detail=f"Entity {entity.get('id')} has no filePath property",
        )
    path = Path(file_path_value)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"File {path} referenced by entity {entity.get('id')} "
                "does not exist on disk"
            ),
        )
    return path


async def resolve_input_layers() -> dict[str, Path]:
    """
    Resolve **all** required input-layer paths by querying Orion.

    Returns
    -------
    dict  mapping  layer-name → local file path

    Raises
    ------
    HTTPException  if Orion is unreachable or a *required* entity is missing.
    """
    logger.info("Resolving input layers from Orion …")

    # --- NDVI (required) ---
    ndvi_entity = await get_entity_from_orion(NDVI_ENTITY_ID)
    ndvi_path = extract_file_path(ndvi_entity)
    logger.info(f"  NDVI → {ndvi_path}")

    layers: dict[str, Path] = {"ndvi": ndvi_path}

    # --- NDWI (optional – logged but not blocking) ---
    try:
        ndwi_entity = await get_entity_from_orion(NDWI_ENTITY_ID)
        ndwi_path = extract_file_path(ndwi_entity)
        logger.info(f"  NDWI → {ndwi_path}")
        layers["ndwi"] = ndwi_path
    except HTTPException as exc:
        logger.warning(f"  NDWI layer not available (non-blocking): {exc.detail}")

    return layers


# ===================================================================
# Pydantic models
# ===================================================================

class NotificationData(BaseModel):
    """NGSI-LD notification data model."""
    id: str
    type: str
    subscriptionId: Optional[str] = None
    notifiedAt: Optional[str] = None
    data: list[dict] = []


class PredictionResponse(BaseModel):
    """Prediction response."""
    status: str
    message: str
    prediction_path: Optional[str] = None
    entity_id: Optional[str] = None
    input_layers: Optional[dict[str, str]] = None


# ===================================================================
# Endpoints
# ===================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "prediction"}


@app.post("/predict", response_model=PredictionResponse)
async def predict(notification: Optional[NotificationData] = None):
    """
    Generate UHI prediction.

    Triggered by an Orion-LD subscription notification or called directly.
    Input file paths are resolved from Orion — if Orion is down or the
    required entities are missing, this endpoint **will fail**.

    A lock prevents concurrent predictions: if a prediction is already
    running (e.g. triggered by the NDVI notification), a second notification
    (e.g. NDWI) will be skipped with a 200 response.
    """
    logger.info("Prediction endpoint called")
    if notification:
        logger.info(f"Received notification: {notification.id}")

    # If a prediction is already in progress, skip this one
    if _prediction_lock.locked():
        logger.info("⏭  Prediction already in progress — skipping duplicate trigger")
        return PredictionResponse(
            status="skipped",
            message="A prediction is already running. This notification was deduplicated.",
        )

    return await _run_prediction()


@app.post("/predict/manual", response_model=PredictionResponse)
async def predict_manual():
    """
    Manually trigger UHI prediction (always runs, ignores debounce).

    Input file paths are resolved from Orion entities.
    No request body is required.
    """
    return await _run_prediction()


async def _run_prediction() -> PredictionResponse:
    """
    Core prediction logic (guarded by ``_prediction_lock``):
      1. Resolve input paths from Orion  (fails fast if unavailable)
      2. Generate the UHI heat-risk raster
      3. Register the output entity back in Orion
    """
    async with _prediction_lock:
        # Step 1 — resolve inputs from Orion (no hardcoded paths)
        layers = await resolve_input_layers()
        ndvi_path = layers["ndvi"]

        # Step 2 — generate prediction
        prediction_path = await generate_uhi_prediction(ndvi_path)

        # Step 3 — register output entity in Orion
        entity_id = await register_prediction_entity(
            prediction_path,
            input_entity_ids=[NDVI_ENTITY_ID]
            + ([NDWI_ENTITY_ID] if "ndwi" in layers else []),
        )

        return PredictionResponse(
            status="success",
            message="UHI prediction generated (placeholder: NDVI-based)",
            prediction_path=str(prediction_path),
            entity_id=entity_id,
            input_layers={k: str(v) for k, v in layers.items()},
        )


# ===================================================================
# Prediction engine
# ===================================================================

async def generate_uhi_prediction(
    ndvi_path: Path,
    tile_size: int = TILE_SIZE,
) -> Path:
    """
    Generate UHI prediction from input layers using windowed processing.
    Output is a COG-optimized uint8 GeoTIFF for fast WMS serving.

    PLACEHOLDER IMPLEMENTATION:
    Currently transforms NDVI to heat risk index.
    The input NDVI is now uint8 encoded: [0, 254] maps to [-1, 1].

    FUTURE IMPLEMENTATION:
    Will use ML model combining:
    - NDVI (vegetation index)
    - NDWI (water index)
    - Building height data
    - DTM (terrain)
    - Weather data (temperature, humidity)

    Encoding:
        Heat risk [0, 1] -> uint8 [0, 254], 255 = nodata
        To decode: heat_risk = pixel_value / 254
    """
    logger.info(f"Generating UHI prediction from NDVI: {ndvi_path}")
    logger.info(f"Output: uint8 COG with overviews, tile size: {tile_size}")

    prediction_path = OUTPUT_PATH / "uhi_prediction_brussels_2024.tif"

    with rasterio.open(ndvi_path) as src:
        height = src.height
        width = src.width
        input_dtype = src.dtypes[0]
        input_nodata = src.nodata

        logger.info(
            f"Input dimensions: {width} x {height}, dtype: {input_dtype}"
        )

        profile = src.profile.copy()
        profile.update(
            driver="GTiff",
            dtype="uint8",
            count=1,
            nodata=255,
            compress="deflate",
            predictor=2,
            tiled=True,
            blockxsize=COG_BLOCKSIZE,
            blockysize=COG_BLOCKSIZE,
        )
        profile.pop("photometric", None)

        with rasterio.open(prediction_path, "w", **profile) as dst:
            total_tiles = (
                ((height + tile_size - 1) // tile_size)
                * ((width + tile_size - 1) // tile_size)
            )
            tile_count = 0

            for row_off in range(0, height, tile_size):
                for col_off in range(0, width, tile_size):
                    win_height = min(tile_size, height - row_off)
                    win_width = min(tile_size, width - col_off)
                    window = Window(col_off, row_off, win_width, win_height)

                    ndvi_raw = src.read(1, window=window)

                    # Decode NDVI based on input dtype
                    if input_dtype == "uint8":
                        nodata_mask = ndvi_raw == 255
                        ndvi = (ndvi_raw.astype(np.float32) / 254) * 2 - 1
                    else:
                        nodata_mask = (
                            (ndvi_raw == input_nodata)
                            if input_nodata is not None
                            else np.zeros_like(ndvi_raw, dtype=bool)
                        )
                        ndvi = ndvi_raw.astype(np.float32)

                    # Placeholder: heat_risk = 1 - (ndvi + 1) / 2
                    heat_risk = 1 - (ndvi + 1) / 2
                    heat_risk = np.clip(heat_risk, 0, 1)

                    heat_uint8 = (heat_risk * 254).astype(np.uint8)
                    heat_uint8[nodata_mask] = 255

                    dst.write(heat_uint8, 1, window=window)

                    tile_count += 1
                    if tile_count % 100 == 0:
                        logger.info(
                            f"UHI prediction progress: "
                            f"{tile_count}/{total_tiles} tiles"
                        )

            dst.update_tags(
                LAYER_TYPE="UHI_PREDICTION",
                MODEL_VERSION="placeholder_v1",
                INPUT_LAYERS="NDVI",
                FORMULA="heat_risk = 1 - (ndvi + 1) / 2",
                VALUE_RANGE="0 to 1 (0=cool, 1=hot)",
                ENCODING="uint8: [0,254] maps to [0,1], 255=nodata",
                DECODE_FORMULA="heat_risk = pixel / 254",
                GENERATED_AT=datetime.utcnow().isoformat(),
            )

    # Build overviews for fast multi-zoom WMS serving
    logger.info("Building overviews for UHI prediction...")
    with rasterio.open(prediction_path, "r+") as ds:
        ds.build_overviews(OVERVIEW_FACTORS, Resampling.average)
        ds.update_tags(ns="rio_overview", resampling="average")

    logger.info(f"UHI prediction COG saved to: {prediction_path}")
    return prediction_path


# ===================================================================
# Orion entity registration (output)
# ===================================================================

async def register_prediction_entity(
    prediction_path: Path,
    input_entity_ids: Optional[list[str]] = None,
) -> str:
    """
    Register (or update) the UHI prediction as an NGSI-LD entity in Orion.

    Args:
        prediction_path: Path to prediction GeoTIFF
        input_entity_ids: Entity IDs of the layers used as inputs

    Returns:
        Entity ID
    """
    entity_id = "urn:ngsi-ld:UHIHeatMap:brussels:2024"

    # Extract bounding box from file, reprojected to WGS84
    bbox = None
    try:
        with rasterio.open(prediction_path) as src:
            left, bottom, right, top = transform_bounds(
                src.crs,
                "EPSG:4326",
                src.bounds.left,
                src.bounds.bottom,
                src.bounds.right,
                src.bounds.top,
            )
            bbox = {
                "type": "Polygon",
                "coordinates": [
                    [
                        [left, bottom],
                        [right, bottom],
                        [right, top],
                        [left, top],
                        [left, bottom],
                    ]
                ],
            }
    except Exception as e:
        logger.warning(f"Could not extract bbox: {e}")

    entity = {
        "@context": NGSI_LD_CONTEXT,
        "id": entity_id,
        "type": "UHIHeatMap",
        "name": {
            "type": "Property",
            "value": "UHI Prediction Brussels 2024",
        },
        "modelVersion": {
            "type": "Property",
            "value": "placeholder_v1",
        },
        "dateGenerated": {
            "type": "Property",
            "value": datetime.utcnow().isoformat() + "Z",
        },
        "inputLayers": {
            "type": "Property",
            "value": input_entity_ids or [],
        },
        "filePath": {
            "type": "Property",
            "value": str(prediction_path),
        },
        "geoserverLayer": {
            "type": "Property",
            "value": "uhi:uhi_prediction",
        },
        "publishToGeoserver": {
            "type": "Property",
            "value": True,
        },
        "valueRange": {
            "type": "Property",
            "value": {
                "min": 0,
                "max": 1,
                "description": "0=cool, 1=hot",
            },
        },
    }

    if bbox:
        entity["boundingBox"] = {
            "type": "GeoProperty",
            "value": bbox,
        }

    # Create or update in Orion
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{ORION_URL}/ngsi-ld/v1/entities",
                json=entity,
                headers={
                    "Content-Type": "application/ld+json",
                    "Accept": "application/ld+json",
                },
            )

            if response.status_code == 201:
                logger.info(f"Created UHI prediction entity: {entity_id}")
            elif response.status_code == 409:
                update_data = {
                    k: v
                    for k, v in entity.items()
                    if k not in ("@context", "id", "type")
                }
                update_data["@context"] = NGSI_LD_CONTEXT

                response = await client.patch(
                    f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}/attrs",
                    json=update_data,
                    headers={
                        "Content-Type": "application/ld+json",
                        "Accept": "application/ld+json",
                    },
                )
                if response.status_code in (200, 204):
                    logger.info(f"Updated UHI prediction entity: {entity_id}")
                else:
                    logger.error(
                        f"Failed to update entity: "
                        f"{response.status_code} - {response.text}"
                    )
            else:
                logger.error(
                    f"Failed to create entity: "
                    f"{response.status_code} - {response.text}"
                )
    except Exception as e:
        logger.error(f"Error registering prediction entity: {e}")

    return entity_id


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
