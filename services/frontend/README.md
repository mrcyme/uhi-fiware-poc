# Frontend — UHI Viewer

A **Vue 3 + CesiumJS** web application for visualizing Urban Heat Island data on an interactive 3D globe. Displays WMS layers served by GeoServer over an OpenStreetMap base map.

## Features

- 3D globe powered by [CesiumJS](https://cesium.com/cesiumjs/)
- OpenStreetMap base map
- Toggle-able WMS layers: RGB, NIR, NDVI, NDWI, UHI Prediction
- Per-layer opacity control
- Color legend for index layers
- Automatic camera positioning over Brussels
- Nginx reverse proxy for GeoServer and Orion API (avoids CORS issues)

## Available Layers

| Layer | WMS Name | Description |
|---|---|---|
| RGB Orthophoto | `uhi:rgb` | True-color aerial imagery |
| NIR Orthophoto | `uhi:nir` | Near-infrared imagery |
| NDVI | `uhi:ndvi` | Vegetation index (−1 to 1) |
| NDWI | `uhi:ndwi` | Water index (−1 to 1) |
| UHI Heat Risk | `uhi:uhi_prediction` | Heat risk prediction (0 to 1) |

## Architecture

```
Browser ──► Nginx (:80)
              ├── /              → Vue SPA (static files)
              ├── /geoserver/*   → proxy to GeoServer (:8080)
              └── /orion/*       → proxy to Orion-LD (:1026)
```

The Nginx reverse proxy means the frontend can call GeoServer and Orion using relative paths, avoiding mixed-origin issues.

## Development

### Local development (without Docker)

```bash
cd services/frontend
npm install
npm run dev
# → http://localhost:5173
```

Note: for local dev, the CesiumViewer auto-detects the port and uses `http://localhost:8080/geoserver` directly.

### Production build

The Dockerfile uses a multi-stage build:

1. **Build stage**: `node:20-alpine` runs `npm run build`
2. **Serve stage**: `nginx:alpine` serves the static files

```bash
docker build -t uhi-frontend .
docker run -p 3000:80 uhi-frontend
```

## Project Structure

```
frontend/
├── src/
│   ├── main.js                   # Vue app entry point
│   ├── App.vue                   # Root component, layer definitions
│   └── components/
│       ├── CesiumViewer.vue      # CesiumJS globe with WMS layer management
│       └── LayerControls.vue     # Layer toggle panel with opacity sliders
├── index.html                    # HTML entry point
├── nginx.conf                    # Reverse proxy config
├── vite.config.js                # Vite + Cesium plugin config
├── package.json
├── Dockerfile
└── .dockerignore
```

## Key Components

### `CesiumViewer.vue`

- Initializes CesiumJS Viewer with OSM base layer
- Manages WMS imagery layers via `WebMapServiceImageryProvider`
- Uses WMS 1.3.0 with `CRS:84` (longitude-first axis order)
- Watches for layer visibility/opacity changes from the parent

### `LayerControls.vue`

- Collapsible side panel
- Checkbox toggles per layer
- Range slider for opacity
- Gradient legend for NDVI/NDWI/UHI layers

## Dependencies

- **Vue 3** — reactive UI framework
- **CesiumJS** — 3D globe rendering
- **Vite** — build tool
- **vite-plugin-cesium** — copies Cesium assets at build time

