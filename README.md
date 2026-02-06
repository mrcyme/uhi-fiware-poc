# Urban Heat Island (UHI) Monitoring Platform

A FIWARE-based microservice platform for monitoring **Urban Heat Islands** in Brussels using aerial orthophotos. The system ingests RGB and NIR aerial imagery, computes vegetation and water indices (NDVI, NDWI), generates heat-risk predictions, and serves all layers through a 3D web viewer.

Built on [FIWARE Orion-LD](https://github.com/FIWARE/context.Orion-LD) (NGSI-LD Context Broker) as the central data backbone — all services communicate through Orion entities and subscriptions.

## Architecture

### Data Flow

This diagram traces the journey of data from raw input to the browser:

```mermaid
flowchart LR
    subgraph Input
        RGB[RGB Orthophoto<br/>6 GB GeoTIFF]
        NIR[NIR Orthophoto<br/>4 GB GeoTIFF]
    end

    subgraph "Ingestion Service"
        DL[Download & Extract]
        OV1[Build Overviews]
        NDVI_C[Compute NDVI<br/>windowed processing]
        NDWI_C[Compute NDWI<br/>windowed processing]
        COG[Output as COG<br/>uint8 + DEFLATE<br/>+ overviews]
    end

    subgraph "Orion-LD"
        E_RGB[GeoSpatialLayer<br/>RGB]
        E_NIR[GeoSpatialLayer<br/>NIR]
        E_NDVI[GeoSpatialLayer<br/>NDVI]
        E_NDWI[GeoSpatialLayer<br/>NDWI]
        E_UHI[UHIHeatMap]
    end

    subgraph "Prediction Service"
        RESOLVE[Resolve input paths<br/>from Orion entities]
        PREDICT[Generate heat-risk<br/>raster from NDVI]
    end

    subgraph "GeoServer Sync"
        PUB[Publish layers<br/>via REST API]
    end

    subgraph GeoServer
        WMS[WMS 1.3.0<br/>CRS:84]
    end

    subgraph Frontend
        CESIUM[CesiumJS Globe<br/>+ layer controls]
    end

    RGB --> DL
    NIR --> DL
    DL --> OV1 --> NDVI_C --> COG
    DL --> OV1
    OV1 --> NDWI_C --> COG

    COG --> E_NDVI
    COG --> E_NDWI
    OV1 --> E_RGB
    OV1 --> E_NIR

    E_NDVI -- "subscription" --> RESOLVE
    E_NDWI -- "subscription" --> RESOLVE
    RESOLVE --> PREDICT
    PREDICT --> E_UHI

    E_RGB -- "subscription" --> PUB
    E_NIR -- "subscription" --> PUB
    E_NDVI -- "subscription" --> PUB
    E_NDWI -- "subscription" --> PUB
    E_UHI -- "subscription" --> PUB

    PUB --> WMS --> CESIUM
```git

### Service Communication

The following diagram shows how the different microservices interact. Orion-LD acts as the central message bus: services register entities and subscribe to changes, enabling a fully **event-driven** architecture.

```mermaid
graph TB
    subgraph "Docker Compose Network"
        MONGO[(MongoDB 5.0)]
        ORION[Orion-LD<br/>Context Broker]
        GS[GeoServer<br/>WMS Server]

        ING[Ingestion Service<br/>FastAPI :8001]
        PRED[Prediction Service<br/>FastAPI :8002]
        SYNC[GeoServer Sync<br/>FastAPI :8003]
        FE[Frontend<br/>Vue + Cesium :3000]
    end

    MONGO --- ORION

    ING -- "1 create/update<br/>GeoSpatialLayer entities" --> ORION
    ORION -- "2 subscription notification<br/>(NDVI/NDWI changed)" --> PRED
    PRED -- "3 query entity filePath" --> ORION
    PRED -- "4 create/update<br/>UHIHeatMap entity" --> ORION
    ORION -- "5 subscription notification<br/>(publishToGeoserver=true)" --> SYNC
    SYNC -- "6 REST API<br/>publish layer" --> GS
    FE -- "WMS GetMap" --> GS
    FE -- "proxy /geoserver" --> GS

    style ORION fill:#ff6b35,color:#fff,stroke:#ff6b35
    style MONGO fill:#4db33d,color:#fff,stroke:#4db33d
    style GS fill:#5b8c5a,color:#fff,stroke:#5b8c5a
    style ING fill:#3498db,color:#fff,stroke:#3498db
    style PRED fill:#9b59b6,color:#fff,stroke:#9b59b6
    style SYNC fill:#e67e22,color:#fff,stroke:#e67e22
    style FE fill:#2ecc71,color:#fff,stroke:#2ecc71
```



## Project Structure

```
fiware-poc/
├── docker-compose.yml          # Orchestrates all 7 services
├── env.example                 # Template for environment variables
├── .gitignore
│
├── services/
│   ├── ingestion/              # Downloads orthophotos, computes indices
│   │   ├── main.py             # FastAPI app
│   │   ├── fiware/client.py    # Orion-LD NGSI-LD client
│   │   ├── processors/
│   │   │   ├── ndvi.py         # NDVI calculation (windowed)
│   │   │   ├── ndwi.py         # NDWI calculation (windowed)
│   │   │   └── cog.py          # Cloud-Optimized GeoTIFF utilities
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   ├── prediction/             # UHI heat-risk prediction
│   │   ├── main.py             # FastAPI app (resolves inputs from Orion)
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   ├── geoserver-sync/         # Auto-publishes layers to GeoServer
│   │   ├── main.py             # FastAPI app (listens to Orion notifications)
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   └── frontend/               # 3D web viewer
│       ├── src/
│       │   ├── App.vue
│       │   └── components/
│       │       ├── CesiumViewer.vue
│       │       └── LayerControls.vue
│       ├── nginx.conf          # Proxies GeoServer/Orion
│       ├── Dockerfile
│       └── package.json
│
├── data/                       # Mounted volumes (git-ignored)
│   ├── raw/                    # Downloaded orthophotos
│   └── processed/              # NDVI, NDWI, UHI prediction
│
└── config/
    └── geoserver/              # GeoServer workspace config (runtime, git-ignored)
```

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) v2+
- ~16 GB RAM (the orthophotos are large; windowed processing keeps peak usage manageable)
- ~25 GB disk space (for the raw + processed GeoTIFFs)

### 1. Clone and configure

```bash
git clone <repo-url>
cd fiware-poc
cp env.example .env
```

The default `.env` points to the Brussels 2024 UrbIS orthophotos. Edit if needed.

### 2. Start all services

```bash
docker compose up -d
```

This starts 7 containers:

| Service | Container | Port | Description |
|---|---|---|---|
| MongoDB | `uhi-mongo` | 27017 | Orion-LD database |
| Orion-LD | `uhi-orion` | 1026 | NGSI-LD Context Broker |
| GeoServer | `uhi-geoserver` | 8080 | WMS tile server |
| Ingestion | `uhi-ingestion` | 8001 | Data ingestion pipeline |
| Prediction | `uhi-prediction` | 8002 | UHI prediction engine |
| GeoServer Sync | `uhi-geoserver-sync` | _(internal)_ | Auto-publishes layers |
| Frontend | `uhi-frontend` | 3000 | CesiumJS web viewer |

### 3. Trigger the ingestion pipeline

```bash
curl -X POST http://localhost:8001/ingest/orthophotos \
  -H "Content-Type: application/json" -d '{}'
```

Monitor progress:

```bash
curl http://localhost:8001/status
```

The pipeline will:
1. Download RGB and NIR orthophotos (~10 GB, skipped if already present)
2. Build overviews on the raw files (for fast WMS serving)
3. Compute NDVI and NDWI as Cloud-Optimized GeoTIFFs
4. Register all 4 layers as NGSI-LD entities in Orion

This automatically triggers:
- **Prediction service** — generates UHI heat-risk map from NDVI
- **GeoServer Sync** — publishes all 5 layers to GeoServer

### 4. View the results

Open [http://localhost:3000](http://localhost:3000) to see the CesiumJS viewer with toggle-able WMS layers.

## NGSI-LD Data Model

### GeoSpatialLayer

Represents a geospatial raster layer (RGB, NIR, NDVI, NDWI):

| Property | Type | Description |
|---|---|---|
| `id` | string | `urn:ngsi-ld:GeoSpatialLayer:{type}:brussels:2024` |
| `type` | string | `GeoSpatialLayer` |
| `layerType` | Property | `RGB`, `NIR`, `NDVI`, `NDWI` |
| `name` | Property | Human-readable name |
| `filePath` | Property | Container-internal path to GeoTIFF |
| `geoserverLayer` | Property | GeoServer layer name (e.g. `uhi:ndvi`) |
| `publishToGeoserver` | Property | `true` if the layer should be served via WMS |
| `boundingBox` | GeoProperty | WGS84 bounding polygon |
| `resolution` | Property | Spatial resolution in cm |
| `spectralRange` | Property | `RGB`, `NIR`, or `computed` |

### UHIHeatMap

Represents a heat-risk prediction output:

| Property | Type | Description |
|---|---|---|
| `id` | string | `urn:ngsi-ld:UHIHeatMap:brussels:2024` |
| `type` | string | `UHIHeatMap` |
| `modelVersion` | Property | Model version identifier |
| `inputLayers` | Property | Array of input entity IDs |
| `filePath` | Property | Path to prediction GeoTIFF |
| `publishToGeoserver` | Property | `true` |
| `valueRange` | Property | `{min: 0, max: 1}` (0=cool, 1=hot) |
| `boundingBox` | GeoProperty | WGS84 bounding polygon |

## Key Design Decisions

### Event-driven via Orion subscriptions

No service calls another directly. Instead:
- Services **register entities** in Orion-LD
- Interested services **subscribe** to entity changes
- Orion delivers **notifications** to subscribers

This decouples the services and makes the system extensible.

### No hardcoded file paths in the prediction service

The prediction service knows only the **entity IDs** of its inputs (configurable via environment variables). At runtime it queries Orion for the `filePath` property. If Orion is down or the entities don't exist, the prediction **fails explicitly** rather than silently using stale data.

### Scoped subscriptions

The prediction service subscribes only to its specific input entities (NDVI, NDWI) — not to all `GeoSpatialLayer` entities. Adding unrelated layers won't trigger spurious predictions.

### Automatic GeoServer sync

Any entity with `publishToGeoserver: true` is automatically published to GeoServer by the **geoserver-sync** service. No manual scripts needed. Adding a new layer type is as simple as registering an entity with the right properties.

### Cloud-Optimized GeoTIFFs (COG)

All output rasters are saved as uint8 COGs with DEFLATE compression, 512×512 internal tiles, and multi-level overviews. This gives 10–100× faster WMS serving compared to raw float32 GeoTIFFs.

### Windowed processing

Large rasters (4–6 GB) are processed in 2048×2048 tiles to keep peak memory usage under control, avoiding OOM crashes.

## API Reference

### Ingestion Service (`:8001`)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/ingest/orthophotos` | Start ingestion pipeline (body optional) |
| `GET` | `/status` | Current pipeline status |
| `GET` | `/layers` | List registered layers from Orion |
| `GET` | `/health` | Health check |

### Prediction Service (`:8002`)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/predict` | Triggered by Orion subscription (auto) |
| `POST` | `/predict/manual` | Manual trigger (no body needed) |
| `GET` | `/health` | Health check |

### GeoServer Sync (internal)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/sync` | Orion notification handler |
| `POST` | `/sync/all` | Force full re-sync |
| `GET` | `/health` | Health check |

## Useful Commands

```bash
# Check all entities in Orion
curl -s "http://localhost:1026/ngsi-ld/v1/entities?local=true" \
  -H "Accept: application/json" | python3 -m json.tool

# Check subscriptions
curl -s "http://localhost:1026/ngsi-ld/v1/subscriptions" \
  -H "Accept: application/json" | python3 -m json.tool

# Check GeoServer layers
curl -s -u admin:geoserver \
  "http://localhost:8080/geoserver/rest/workspaces/uhi/coverages.json" | python3 -m json.tool

# View service logs
docker logs uhi-ingestion --tail 50
docker logs uhi-prediction --tail 50
docker logs uhi-geoserver-sync --tail 50

# Force re-publish all layers to GeoServer (from inside the Docker network)
docker exec uhi-geoserver-sync curl -s -X POST http://localhost:8000/sync/all

# Manually trigger a prediction
curl -X POST http://localhost:8002/predict/manual

# Clean restart (removes all data)
docker compose down -v
rm -rf data/raw data/processed config/geoserver
docker compose up -d
```

## Technology Stack

| Component | Technology | Role |
|---|---|---|
| Context Broker | [FIWARE Orion-LD](https://github.com/FIWARE/context.Orion-LD) 1.6.0 | NGSI-LD entity & subscription management |
| Database | MongoDB 5.0 | Orion-LD persistence |
| GIS Server | GeoServer 2.24.2 | WMS layer serving |
| Ingestion | Python 3.11 / FastAPI | Data download & raster processing |
| Prediction | Python 3.11 / FastAPI | UHI heat-risk model |
| GeoServer Sync | Python 3.11 / FastAPI | Automated GeoServer configuration |
| Frontend | Vue 3 / CesiumJS | 3D globe viewer |
| Base Map | OpenStreetMap | Base layer tiles |
| Raster Processing | Rasterio / NumPy | GeoTIFF I/O & computation |

## License

This project is developed by [FARI – AI for the Common Good Institute](https://fari.brussels).
