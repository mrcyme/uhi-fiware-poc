# GeoServer Sync Service

FastAPI microservice that **automatically publishes geospatial layers to GeoServer** based on Orion-LD entity state. Any NGSI-LD entity with `publishToGeoserver: true` gets a corresponding GeoServer coverage store and coverage created via the GeoServer REST API.

## How It Works

1. **On startup**: waits for GeoServer → ensures workspace exists → syncs all existing Orion entities → registers its Orion subscription
2. **On notification**: receives entity data from Orion, checks `publishToGeoserver`, and creates/updates the GeoServer layer
3. **No hardcoded layers**: layer names, file paths, and store names are all derived from the entity properties

```
Orion-LD                          GeoServer Sync                    GeoServer
   │                                    │                               │
   │  notification (entity changed)     │                               │
   │ ─────────────────────────────────► │                               │
   │                                    │  derive store name, file path │
   │                                    │  from entity properties       │
   │                                    │                               │
   │                                    │  REST API: create store       │
   │                                    │ ────────────────────────────► │
   │                                    │  REST API: publish coverage   │
   │                                    │ ────────────────────────────► │
   │                                    │                               │
```

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/sync` | Orion notification handler (called automatically) |
| `POST` | `/sync/all` | Force a full re-sync of all Orion entities to GeoServer |
| `GET` | `/health` | Health check |

### Force re-sync

The service is not exposed to the host by default. Use `docker exec` to trigger a re-sync:

```bash
docker exec uhi-geoserver-sync curl -s -X POST http://localhost:8000/sync/all
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ORION_URL` | `http://orion:1026` | Orion-LD broker URL |
| `GEOSERVER_URL` | `http://geoserver:8080/geoserver` | GeoServer REST API URL |
| `GEOSERVER_USER` | `admin` | GeoServer admin username |
| `GEOSERVER_PASSWORD` | `geoserver` | GeoServer admin password |
| `GEOSERVER_WORKSPACE` | `uhi` | GeoServer workspace name |
| `SELF_URL` | `http://geoserver-sync:8000` | URL where Orion can reach this service |
| `PATH_MAP_PROCESSED` | `/data/processed:/opt/geoserver_data/data/uhi_processed` | Maps container path → GeoServer path |
| `PATH_MAP_RAW` | `/data/raw:/opt/geoserver_data/data/uhi_raw` | Maps container path → GeoServer path |

## Path Mapping

Files written by the ingestion/prediction services live at paths like `/data/processed/ndvi.tif`. GeoServer sees the same host directories mounted at different internal paths (e.g. `/opt/geoserver_data/data/uhi_processed/ndvi.tif`).

The `PATH_MAP_*` environment variables translate between these two path namespaces:

```
Container path:   /data/processed/ndvi_brussels_2024.tif
                            ↓  (PATH_MAP_PROCESSED)
GeoServer path:   /opt/geoserver_data/data/uhi_processed/ndvi_brussels_2024.tif
```

## Orion Subscription

The service subscribes to changes on:

| Entity Type | Watched Attributes |
|---|---|
| `GeoSpatialLayer` | `publishToGeoserver`, `filePath` |
| `UHIHeatMap` | `publishToGeoserver`, `filePath` |

## Layer Derivation Logic

The service derives GeoServer layer parameters from entity properties:

| Entity Type | Store Name | Coverage Name | Title |
|---|---|---|---|
| `GeoSpatialLayer` | `store_{layerType}` | `{layerType}` (lowercase) | `name` property |
| `UHIHeatMap` | `store_uhi_prediction` | `uhi_prediction` | `name` property |

## Project Structure

```
geoserver-sync/
├── main.py              # FastAPI app, GeoServerClient, Orion integration
├── Dockerfile
└── requirements.txt
```

## Dependencies

- **FastAPI** / **Uvicorn** — async web framework
- **httpx** — HTTP client (sync for GeoServer REST API, async for Orion)

