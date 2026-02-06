# Prediction Service

FastAPI microservice that generates **Urban Heat Island (UHI) heat-risk predictions** from spectral index layers. It resolves its input file paths entirely from Orion-LD at runtime — no paths are hardcoded.

## Architecture Principles

1. **Input paths come from Orion** — the service queries Orion-LD entities to discover where the NDVI/NDWI files live on disk. If Orion is unreachable or the entities don't exist, the prediction **fails explicitly**.
2. **Only entity IDs are configured** — the environment variables `NDVI_ENTITY_ID` and `NDWI_ENTITY_ID` tell the service _which_ Orion entities to query. Everything else (file paths, bounding boxes) is resolved at runtime.
3. **Scoped subscription** — the Orion subscription watches only the specific NDVI and NDWI entities, not all `GeoSpatialLayer` entities. Adding unrelated layers will not trigger a prediction.
4. **Debounce lock** — an `asyncio.Lock` prevents concurrent predictions. If a second notification arrives while a prediction is running, it is skipped.
5. **Self-registering** — the service registers its own Orion subscription on startup, requiring no external setup script.

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/predict` | Called automatically by Orion subscription. Includes debounce. |
| `POST` | `/predict/manual` | Manual trigger (always runs, no debounce). No body needed. |
| `GET` | `/health` | Health check. |

### Manual trigger

```bash
curl -X POST http://localhost:8002/predict/manual
```

### Response

```json
{
  "status": "success",
  "message": "UHI prediction generated (placeholder: NDVI-based)",
  "prediction_path": "/data/processed/uhi_prediction_brussels_2024.tif",
  "entity_id": "urn:ngsi-ld:UHIHeatMap:brussels:2024",
  "input_layers": {
    "ndvi": "/data/processed/ndvi_brussels_2024.tif",
    "ndwi": "/data/processed/ndwi_brussels_2024.tif"
  }
}
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ORION_URL` | `http://orion:1026` | Orion-LD broker URL |
| `DATA_PROCESSED_PATH` | `/data/processed` | Output directory for prediction GeoTIFF |
| `SELF_URL` | `http://prediction:8000` | URL where Orion can reach this service |
| `NDVI_ENTITY_ID` | `urn:ngsi-ld:GeoSpatialLayer:NDVI:brussels:2024` | Orion entity ID for NDVI input |
| `NDWI_ENTITY_ID` | `urn:ngsi-ld:GeoSpatialLayer:NDWI:brussels:2024` | Orion entity ID for NDWI input |

## How It Works

```
Orion notification (NDVI or NDWI changed)
    │
    ▼
Debounce check ── already running? ──► skip (200 OK)
    │
    ▼
Resolve input layers from Orion
  ├── GET /entities/{NDVI_ENTITY_ID} → extract filePath
  └── GET /entities/{NDWI_ENTITY_ID} → extract filePath
    │
    ▼
Generate UHI prediction (windowed processing)
  └── heat_risk = 1 - (ndvi + 1) / 2
    │
    ▼
Register UHIHeatMap entity in Orion
  └── publishToGeoserver: true
    │
    ▼
GeoServer Sync picks up notification ──► layer published
```

## Current Model (Placeholder)

The current implementation is a **placeholder** that transforms NDVI to a heat-risk index:

```
heat_risk = 1 - (ndvi + 1) / 2
```

- Low vegetation (low NDVI) → high heat risk
- Dense vegetation (high NDVI) → low heat risk

### Future Model

The architecture is designed to accommodate an ML model combining:
- NDVI (vegetation index)
- NDWI (water index)
- Building height data (DSM)
- Digital Terrain Model (DTM)
- Weather data (temperature, humidity)

Swapping the model requires changing only the `generate_uhi_prediction()` function.

## Output Format

| Property | Value |
|---|---|
| Data type | `uint8` (0–254 data, 255 nodata) |
| Compression | DEFLATE |
| Tile size | 512×512 |
| Overviews | 2×, 4×, 8×, 16×, 32× |
| Value range | `[0, 1]` — 0 = cool, 1 = hot |

Decode formula: `heat_risk = pixel / 254`

## Project Structure

```
prediction/
├── main.py              # FastAPI app, Orion integration, prediction engine
├── Dockerfile
└── requirements.txt
```

## NGSI-LD Entity Output

The prediction registers a `UHIHeatMap` entity with:

- `filePath` — path to the prediction GeoTIFF
- `inputLayers` — array of entity IDs used as inputs
- `publishToGeoserver: true`
- `modelVersion`, `valueRange`, `boundingBox`

## Dependencies

- **FastAPI** / **Uvicorn** — async web framework
- **Rasterio** / **GDAL** — GeoTIFF I/O
- **NumPy** — raster computation
- **httpx** — async HTTP client (for Orion API)

