"""
GeoServer Sync Service

Listens to Orion-LD notifications for GeoSpatialLayer and UHIHeatMap
entities.  When an entity has ``publishToGeoserver=true``, this service
ensures the corresponding layer is published in GeoServer.

Architecture:
  - On startup  : wait for GeoServer → ensure workspace → initial sync → register subscription
  - On notification : publish / update layer in GeoServer
  - No hardcoded file paths or layer names – everything is derived from Orion entities.
"""

import os
import asyncio
import logging
import time
from typing import Optional
from contextlib import asynccontextmanager
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI
from pydantic import BaseModel
import httpx

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ORION_URL = os.getenv("ORION_URL", "http://orion:1026")
GEOSERVER_URL = os.getenv("GEOSERVER_URL", "http://geoserver:8080/geoserver")
GEOSERVER_USER = os.getenv("GEOSERVER_USER", "admin")
GEOSERVER_PASSWORD = os.getenv("GEOSERVER_PASSWORD", "geoserver")
GEOSERVER_WORKSPACE = os.getenv("GEOSERVER_WORKSPACE", "uhi")

# URL that Orion uses to reach this service (Docker-internal)
SELF_URL = os.getenv("SELF_URL", "http://geoserver-sync:8000")

NGSI_LD_CONTEXT = "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld"
SUBSCRIPTION_ID = "urn:ngsi-ld:Subscription:geoserver-sync"

# ---------------------------------------------------------------------------
# Path mapping
#
# Files stored by the ingestion / prediction containers live under paths
# like /data/processed/… or /data/raw/….  GeoServer sees the *same* host
# directories mounted at different internal paths.
#
# Env-var format:  PATH_MAP_<label>=<source_prefix>:<geoserver_prefix>
# Example:         PATH_MAP_PROCESSED=/data/processed:/opt/geoserver_data/data/uhi_processed
# ---------------------------------------------------------------------------
PATH_MAPPINGS: dict[str, str] = {}

for _key, _value in os.environ.items():
    if _key.startswith("PATH_MAP_"):
        parts = _value.split(":", 1)
        if len(parts) == 2:
            PATH_MAPPINGS[parts[0]] = parts[1]

if not PATH_MAPPINGS:
    PATH_MAPPINGS = {
        "/data/processed": "/opt/geoserver_data/data/uhi_processed",
        "/data/raw": "/opt/geoserver_data/data/uhi_raw",
    }


def translate_path(container_path: str) -> str:
    """Translate a container file path to GeoServer's internal path."""
    for src, dst in sorted(PATH_MAPPINGS.items(), key=lambda x: -len(x[0])):
        if container_path.startswith(src):
            return container_path.replace(src, dst, 1)
    logger.warning(f"No path mapping found for {container_path}, using as-is")
    return container_path


# ===================================================================
# GeoServer REST client
# ===================================================================

class GeoServerClient:
    """Thin wrapper around the GeoServer REST API."""

    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        workspace: str,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth = (user, password)
        self.workspace = workspace

    # -- helpers ----------------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=30.0,
            auth=self.auth,
            follow_redirects=True,
        )

    # -- readiness --------------------------------------------------------

    def wait_until_ready(self, timeout: int = 180) -> None:
        """Block until the GeoServer REST API responds."""
        logger.info(f"Waiting for GeoServer at {self.base_url} …")
        start = time.time()
        while time.time() - start < timeout:
            try:
                with self._client() as c:
                    r = c.get(f"{self.base_url}/rest/about/version.json")
                    if r.status_code == 200:
                        logger.info("GeoServer is ready")
                        return
            except Exception:
                pass
            time.sleep(3)
        raise RuntimeError(
            f"GeoServer at {self.base_url} did not become ready "
            f"within {timeout}s"
        )

    # -- workspace --------------------------------------------------------

    def ensure_workspace(self) -> None:
        """Create the workspace if it does not exist yet."""
        with self._client() as c:
            r = c.get(f"{self.base_url}/rest/workspaces/{self.workspace}")
            if r.status_code == 200:
                logger.info(f"Workspace '{self.workspace}' already exists")
                return

            xml = f"<workspace><name>{self.workspace}</name></workspace>"
            r = c.post(
                f"{self.base_url}/rest/workspaces",
                content=xml,
                headers={"Content-Type": "application/xml"},
            )
            if r.status_code == 201:
                logger.info(f"Created workspace '{self.workspace}'")
            else:
                logger.error(
                    f"Failed to create workspace: {r.status_code} {r.text}"
                )

    # -- publish ----------------------------------------------------------

    def publish_layer(
        self,
        store_name: str,
        coverage_name: str,
        title: str,
        file_path: str,
    ) -> bool:
        """
        Idempotently create a GeoTIFF coverage-store + coverage.

        Returns True on success.
        """
        ws = self.workspace
        safe_title = xml_escape(title)

        with self._client() as c:
            # 1) Coverage store — create or update
            store_url = (
                f"{self.base_url}/rest/workspaces/{ws}"
                f"/coveragestores/{store_name}"
            )
            r = c.get(store_url)

            store_xml = (
                f"<coverageStore>"
                f"  <name>{store_name}</name>"
                f"  <workspace><name>{ws}</name></workspace>"
                f"  <enabled>true</enabled>"
                f"  <type>GeoTIFF</type>"
                f"  <url>file:{file_path}</url>"
                f"</coverageStore>"
            )

            if r.status_code != 200:
                # create
                r = c.post(
                    f"{self.base_url}/rest/workspaces/{ws}/coveragestores",
                    content=store_xml,
                    headers={"Content-Type": "application/xml"},
                )
                if r.status_code not in (200, 201):
                    logger.error(
                        f"Failed to create store '{store_name}': "
                        f"{r.status_code} {r.text}"
                    )
                    return False
                logger.info(f"Created coverage store '{store_name}'")
            else:
                # update (e.g. file path changed)
                r = c.put(
                    store_url,
                    content=store_xml,
                    headers={"Content-Type": "application/xml"},
                )
                if r.status_code in (200, 201):
                    logger.info(f"Updated coverage store '{store_name}'")

            # 2) Coverage — create if missing
            cov_url = (
                f"{self.base_url}/rest/workspaces/{ws}"
                f"/coveragestores/{store_name}/coverages/{coverage_name}"
            )
            r = c.get(cov_url)
            if r.status_code == 200:
                logger.info(
                    f"Coverage '{ws}:{coverage_name}' already published"
                )
                return True

            cov_xml = (
                f"<coverage>"
                f"  <name>{coverage_name}</name>"
                f"  <title>{safe_title}</title>"
                f"  <enabled>true</enabled>"
                f"</coverage>"
            )
            r = c.post(
                f"{self.base_url}/rest/workspaces/{ws}"
                f"/coveragestores/{store_name}/coverages",
                content=cov_xml,
                headers={"Content-Type": "application/xml"},
            )
            if r.status_code == 201:
                logger.info(f"Published coverage '{ws}:{coverage_name}'")
                return True
            else:
                logger.error(
                    f"Failed to publish coverage '{coverage_name}': "
                    f"{r.status_code} {r.text}"
                )
                return False


# Singleton
geoserver = GeoServerClient(
    GEOSERVER_URL,
    GEOSERVER_USER,
    GEOSERVER_PASSWORD,
    GEOSERVER_WORKSPACE,
)


# ===================================================================
# Entity → layer info
# ===================================================================

def derive_layer_info(entity: dict) -> Optional[dict]:
    """
    Read an NGSI-LD entity and return the info needed to publish it
    in GeoServer, or *None* if it should not be published.
    """
    publish = entity.get("publishToGeoserver", {}).get("value", False)
    if not publish:
        return None

    file_path = entity.get("filePath", {}).get("value")
    if not file_path:
        logger.warning(
            f"Entity {entity.get('id')} has publishToGeoserver=true "
            "but no filePath — skipping"
        )
        return None

    entity_type = entity.get("type")

    if entity_type == "GeoSpatialLayer":
        layer_type = entity.get("layerType", {}).get("value", "unknown")
        name = layer_type.lower()
        title = entity.get("name", {}).get("value", name)
    elif entity_type == "UHIHeatMap":
        name = "uhi_prediction"
        title = entity.get("name", {}).get("value", "UHI Heat Risk Prediction")
    else:
        # Try to derive from geoserverLayer property
        gs_layer = entity.get("geoserverLayer", {}).get("value", "")
        if ":" in gs_layer:
            name = gs_layer.split(":", 1)[1]
        else:
            name = gs_layer or entity.get("id", "unknown").split(":")[-1]
        title = entity.get("name", {}).get("value", name)

    geoserver_path = translate_path(file_path)

    return {
        "name": name,
        "store_name": f"store_{name}",
        "title": title,
        "geoserver_path": geoserver_path,
    }


def sync_entity(entity: dict) -> bool:
    """Publish a single entity to GeoServer (if applicable)."""
    info = derive_layer_info(entity)
    if info is None:
        return False

    logger.info(
        f"Syncing entity {entity.get('id')} → "
        f"{GEOSERVER_WORKSPACE}:{info['name']} "
        f"(file: {info['geoserver_path']})"
    )
    return geoserver.publish_layer(
        store_name=info["store_name"],
        coverage_name=info["name"],
        title=info["title"],
        file_path=info["geoserver_path"],
    )


# ===================================================================
# Orion-LD helpers
# ===================================================================

async def register_subscription() -> None:
    """
    Register (or re-create) the Orion-LD subscription that notifies
    this service when GeoSpatialLayer or UHIHeatMap entities change.
    """
    subscription = {
        "@context": NGSI_LD_CONTEXT,
        "id": SUBSCRIPTION_ID,
        "type": "Subscription",
        "description": "Sync publishable layers to GeoServer",
        "entities": [
            {"type": "GeoSpatialLayer"},
            {"type": "UHIHeatMap"},
        ],
        "watchedAttributes": ["publishToGeoserver", "filePath"],
        "notification": {
            "endpoint": {
                "uri": f"{SELF_URL}/sync",
                "accept": "application/json",
            }
        },
    }

    headers = {
        "Content-Type": "application/ld+json",
        "Accept": "application/ld+json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Try to create
        r = await client.post(
            f"{ORION_URL}/ngsi-ld/v1/subscriptions",
            json=subscription,
            headers=headers,
        )
        if r.status_code == 201:
            logger.info(f"Created subscription {SUBSCRIPTION_ID}")
            return

        if r.status_code == 409:
            # Already exists — delete and recreate to update endpoint / attrs
            await client.delete(
                f"{ORION_URL}/ngsi-ld/v1/subscriptions/{SUBSCRIPTION_ID}"
            )
            r = await client.post(
                f"{ORION_URL}/ngsi-ld/v1/subscriptions",
                json=subscription,
                headers=headers,
            )
            if r.status_code == 201:
                logger.info(f"Re-created subscription {SUBSCRIPTION_ID}")
                return

        logger.error(
            f"Failed to register subscription: {r.status_code} {r.text}"
        )


async def initial_sync() -> None:
    """
    Query Orion for all existing entities and sync those with
    ``publishToGeoserver=true`` to GeoServer.
    """
    logger.info("Running initial sync of existing entities …")
    synced = 0

    for entity_type in ("GeoSpatialLayer", "UHIHeatMap"):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    f"{ORION_URL}/ngsi-ld/v1/entities",
                    params={"type": entity_type, "local": "true"},
                    headers={"Accept": "application/json"},
                )
            if r.status_code != 200:
                logger.warning(
                    f"Could not query {entity_type}: {r.status_code}"
                )
                continue

            entities = r.json()
            for entity in entities:
                if sync_entity(entity):
                    synced += 1

        except Exception as exc:
            logger.warning(f"Initial sync for {entity_type} failed: {exc}")

    logger.info(f"Initial sync complete — {synced} layer(s) published")


# ===================================================================
# FastAPI app
# ===================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup sequence: GeoServer ready → workspace → sync → subscribe."""
    logger.info("Starting GeoServer Sync Service")
    logger.info(f"  Orion URL        : {ORION_URL}")
    logger.info(f"  GeoServer URL    : {GEOSERVER_URL}")
    logger.info(f"  Workspace        : {GEOSERVER_WORKSPACE}")
    logger.info(f"  Path mappings    : {PATH_MAPPINGS}")

    # Run blocking GeoServer readiness check in a thread
    await asyncio.to_thread(geoserver.wait_until_ready)
    await asyncio.to_thread(geoserver.ensure_workspace)

    # Initial sync + subscription (Orion may not be ready yet — retry)
    for attempt in range(10):
        try:
            await initial_sync()
            await register_subscription()
            break
        except Exception as exc:
            logger.warning(
                f"Startup sync/subscribe attempt {attempt + 1} failed: {exc}"
            )
            await asyncio.sleep(5)

    yield
    logger.info("Shutting down GeoServer Sync Service")


app = FastAPI(
    title="GeoServer Sync Service",
    description=(
        "Keeps GeoServer in sync with Orion-LD entities. "
        "Layers with publishToGeoserver=true are automatically published."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ===================================================================
# Pydantic models
# ===================================================================

class NotificationData(BaseModel):
    """Orion-LD notification payload."""
    id: str
    type: str
    subscriptionId: Optional[str] = None
    notifiedAt: Optional[str] = None
    data: list[dict] = []


# ===================================================================
# Endpoints
# ===================================================================

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "geoserver-sync"}


@app.post("/sync")
async def handle_notification(notification: NotificationData):
    """
    Receive an Orion-LD notification and sync the affected entities
    to GeoServer.
    """
    logger.info(
        f"Received notification {notification.id} "
        f"with {len(notification.data)} entit(ies)"
    )

    results = []
    for entity in notification.data:
        entity_id = entity.get("id", "?")
        ok = await asyncio.to_thread(sync_entity, entity)
        results.append({"entity": entity_id, "synced": ok})
        if ok:
            logger.info(f"  ✓ Synced {entity_id}")
        else:
            logger.info(f"  – Skipped {entity_id} (publishToGeoserver != true or no filePath)")

    return {"status": "ok", "results": results}


@app.post("/sync/all")
async def force_sync_all():
    """Force a full re-sync of all Orion entities to GeoServer."""
    await initial_sync()
    return {"status": "ok", "message": "Full re-sync completed"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

