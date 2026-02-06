# Ingestion Service

FastAPI microservice that downloads orthophotos from [UrbIS](https://datastore.brussels), computes spectral indices (NDVI, NDWI), and registers all layers as NGSI-LD entities in Orion-LD.

## Responsibilities

1. **Download** RGB and NIR orthophotos (ZIP archives with GeoTIFF inside)
2. **Build overviews** on the raw files for fast WMS serving
3. **Compute NDVI** — Normalized Difference Vegetation Index
4. **Compute NDWI** — Normalized Difference Water Index
5. **Register entities** in Orion-LD (4 `GeoSpatialLayer` entities: RGB, NIR, NDVI, NDWI)

All outputs are Cloud-Optimized GeoTIFFs (uint8, DEFLATE compressed, internally tiled, with overviews).

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/ingest/orthophotos` | Trigger the ingestion pipeline. Body is optional — if omitted, uses `RGB_URL` / `NIR_URL` from environment. |
| `GET` | `/status` | Returns current pipeline status (`running`, `progress`, `layers_created`). |
| `GET` | `/layers` | Lists all `GeoSpatialLayer` entities from Orion. |
| `GET` | `/health` | Health check. |

### Trigger ingestion

```bash
# Use default URLs from env
curl -X POST http://localhost:8001/ingest/orthophotos \
  -H "Content-Type: application/json" -d '{}'

# Custom URLs
curl -X POST http://localhost:8001/ingest/orthophotos \
  -H "Content-Type: application/json" \
  -d '{"rgb_url": "https://...", "nir_url": "https://..."}'
```

### Monitor progress

```bash
curl http://localhost:8001/status
# {"running": true, "progress": "Calculating NDVI...", "layers_created": ["RGB", "NIR"]}
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ORION_URL` | `http://orion:1026` | Orion-LD broker URL |
| `DATA_RAW_PATH` | `/data/raw` | Path for downloaded files |
| `DATA_PROCESSED_PATH` | `/data/processed` | Path for computed outputs |
| `RGB_URL` | _(from .env)_ | Default download URL for RGB orthophoto |
| `NIR_URL` | _(from .env)_ | Default download URL for NIR orthophoto |

## Processing Pipeline

```
RGB.zip ──► extract ──► RGB.tif ──► build overviews ──┬──► NDVI (COG uint8)
                                                       │
NIR.zip ──► extract ──► NIR.tif ──► build overviews ──┤
                                                       │
                                                       └──► NDWI (COG uint8)
```

### Smart download

If the `data/raw/rgb/` and `data/raw/nir/` directories already contain TIFF files, the download step is **skipped entirely**. This avoids re-downloading ~10 GB on every restart.

### Windowed processing

Both NDVI and NDWI are computed in 2048×2048 pixel tiles to avoid loading the full 6 GB raster into memory.

### COG output format

| Property | Value |
|---|---|
| Data type | `uint8` (0–254 data, 255 nodata) |
| Compression | DEFLATE with horizontal predictor |
| Tile size | 512×512 |
| Overviews | 2×, 4×, 8×, 16×, 32× |
| Encoding | `[-1, 1] → [0, 254]` |

Decode formula: `value = (pixel / 254) * 2 - 1`

## Project Structure

```
ingestion/
├── main.py                  # FastAPI app, pipeline orchestration
├── fiware/
│   ├── __init__.py
│   └── client.py            # OrionClient + GeoSpatialLayer dataclass
├── processors/
│   ├── __init__.py
│   ├── ndvi.py              # NDVI = (NIR - Red) / (NIR + Red)
│   ├── ndwi.py              # NDWI = (Green - NIR) / (Green + NIR)
│   └── cog.py               # COG utilities (overviews, profile)
├── Dockerfile
└── requirements.txt
```

## NGSI-LD Entity Output

Each layer is registered as a `GeoSpatialLayer` entity with:

- `filePath` — container-internal path to the GeoTIFF
- `geoserverLayer` — expected GeoServer layer name (e.g. `uhi:ndvi`)
- `publishToGeoserver: true` — signals geoserver-sync to publish it
- `boundingBox` — WGS84 bounding polygon (reprojected from Belgian Lambert 72)
- `layerType`, `spectralRange`, `resolution`, etc.

## Dependencies

- **FastAPI** / **Uvicorn** — async web framework
- **Rasterio** / **GDAL** — GeoTIFF I/O
- **NumPy** — raster computation
- **httpx** — async HTTP client (for downloads and Orion API)

