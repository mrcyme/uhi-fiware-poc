"""
FIWARE Orion-LD NGSI-LD Client

Client for interacting with Orion-LD Context Broker to manage
GeoSpatialLayer entities for the UHI system.
"""

import logging
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

import httpx
import rasterio
from rasterio.warp import transform_bounds

logger = logging.getLogger(__name__)

# NGSI-LD context
NGSI_LD_CONTEXT = "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld"


@dataclass
class GeoSpatialLayer:
    """Represents a GeoSpatialLayer entity for NGSI-LD."""
    
    layer_type: str  # RGB, NIR, NDVI, NDWI
    name: str
    spectral_range: str  # RGB, NIR, computed
    file_path: str
    resolution: int = 40  # cm
    publish_to_geoserver: bool = True
    date_created: Optional[str] = None
    bounding_box: Optional[dict] = None
    
    def __post_init__(self):
        if self.date_created is None:
            self.date_created = datetime.utcnow().isoformat() + "Z"
        
        # Try to extract bounding box from file if not provided
        if self.bounding_box is None:
            self.bounding_box = self._extract_bbox()
    
    def _extract_bbox(self) -> Optional[dict]:
        """Extract bounding box from GeoTIFF file, reprojected to WGS84."""
        try:
            with rasterio.open(self.file_path) as src:
                # Reproject bounds from native CRS (e.g. EPSG:31370) to WGS84
                left, bottom, right, top = transform_bounds(
                    src.crs, "EPSG:4326",
                    src.bounds.left, src.bounds.bottom,
                    src.bounds.right, src.bounds.top
                )
                # Create GeoJSON Polygon for bounding box (lon/lat)
                return {
                    "type": "Polygon",
                    "coordinates": [[
                        [left, bottom],
                        [right, bottom],
                        [right, top],
                        [left, top],
                        [left, bottom]
                    ]]
                }
        except Exception as e:
            logger.warning(f"Could not extract bbox from {self.file_path}: {e}")
            return None
    
    @property
    def entity_id(self) -> str:
        """Generate NGSI-LD entity ID."""
        return f"urn:ngsi-ld:GeoSpatialLayer:{self.layer_type}:brussels:2024"
    
    @property
    def geoserver_layer(self) -> str:
        """Generate GeoServer layer name."""
        return f"uhi:{self.layer_type.lower()}"
    
    def to_ngsi_ld(self) -> dict:
        """Convert to NGSI-LD entity format."""
        entity = {
            "@context": NGSI_LD_CONTEXT,
            "id": self.entity_id,
            "type": "GeoSpatialLayer",
            "name": {
                "type": "Property",
                "value": self.name
            },
            "layerType": {
                "type": "Property",
                "value": self.layer_type
            },
            "spectralRange": {
                "type": "Property",
                "value": self.spectral_range
            },
            "dateCreated": {
                "type": "Property",
                "value": self.date_created
            },
            "resolution": {
                "type": "Property",
                "value": self.resolution,
                "unitCode": "CMT"
            },
            "filePath": {
                "type": "Property",
                "value": self.file_path
            },
            "geoserverLayer": {
                "type": "Property",
                "value": self.geoserver_layer
            },
            "publishToGeoserver": {
                "type": "Property",
                "value": self.publish_to_geoserver
            }
        }
        
        if self.bounding_box:
            entity["boundingBox"] = {
                "type": "GeoProperty",
                "value": self.bounding_box
            }
        
        return entity


class OrionClient:
    """Client for Orion-LD Context Broker."""
    
    def __init__(self, base_url: str):
        """
        Initialize Orion client.
        
        Args:
            base_url: Base URL for Orion-LD (e.g., http://orion:1026)
        """
        self.base_url = base_url.rstrip("/")
        self.entities_url = f"{self.base_url}/ngsi-ld/v1/entities"
        self.subscriptions_url = f"{self.base_url}/ngsi-ld/v1/subscriptions"
        
        self.headers = {
            "Content-Type": "application/ld+json",
            "Accept": "application/ld+json"
        }
    
    async def health_check(self) -> bool:
        """Check if Orion-LD is healthy."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/version")
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Orion health check failed: {e}")
            return False
    
    async def create_entity(self, entity: dict) -> bool:
        """
        Create a new NGSI-LD entity.
        
        Args:
            entity: NGSI-LD entity dictionary
            
        Returns:
            True if created successfully
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.entities_url,
                    json=entity,
                    headers=self.headers
                )
                
                if response.status_code == 201:
                    logger.info(f"Created entity: {entity.get('id')}")
                    return True
                elif response.status_code == 409:
                    logger.warning(f"Entity already exists: {entity.get('id')}")
                    return False
                else:
                    logger.error(f"Failed to create entity: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error creating entity: {e}")
            return False
    
    async def update_entity(self, entity_id: str, attributes: dict) -> bool:
        """
        Update an existing NGSI-LD entity.
        
        Args:
            entity_id: Entity ID to update
            attributes: Attributes to update
            
        Returns:
            True if updated successfully
        """
        try:
            # Remove @context and id from attributes for PATCH
            update_data = {k: v for k, v in attributes.items() 
                         if k not in ["@context", "id", "type"]}
            update_data["@context"] = NGSI_LD_CONTEXT
            
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.entities_url}/{entity_id}/attrs",
                    json=update_data,
                    headers=self.headers
                )
                
                if response.status_code in [200, 204]:
                    logger.info(f"Updated entity: {entity_id}")
                    return True
                else:
                    logger.error(f"Failed to update entity: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error updating entity: {e}")
            return False
    
    async def get_entity(self, entity_id: str) -> Optional[dict]:
        """
        Get an NGSI-LD entity by ID.
        
        Args:
            entity_id: Entity ID to retrieve
            
        Returns:
            Entity dictionary or None
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.entities_url}/{entity_id}",
                    headers={"Accept": "application/ld+json"}
                )
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    return None
                else:
                    logger.error(f"Failed to get entity: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting entity: {e}")
            return None
    
    async def delete_entity(self, entity_id: str) -> bool:
        """
        Delete an NGSI-LD entity.
        
        Args:
            entity_id: Entity ID to delete
            
        Returns:
            True if deleted successfully
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.delete(
                    f"{self.entities_url}/{entity_id}"
                )
                
                if response.status_code == 204:
                    logger.info(f"Deleted entity: {entity_id}")
                    return True
                else:
                    logger.error(f"Failed to delete entity: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error deleting entity: {e}")
            return False
    
    async def create_or_update_layer(self, layer: GeoSpatialLayer) -> bool:
        """
        Create or update a GeoSpatialLayer entity.
        
        Args:
            layer: GeoSpatialLayer instance
            
        Returns:
            True if operation successful
        """
        entity = layer.to_ngsi_ld()
        
        # Check if entity exists
        existing = await self.get_entity(layer.entity_id)
        
        if existing:
            return await self.update_entity(layer.entity_id, entity)
        else:
            return await self.create_entity(entity)
    
    async def get_all_layers(self) -> list[dict]:
        """
        Get all GeoSpatialLayer entities.
        
        Returns:
            List of entity dictionaries
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.entities_url,
                    params={"type": "GeoSpatialLayer"},
                    headers={"Accept": "application/ld+json"}
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"Failed to get layers: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error getting layers: {e}")
            return []
    
    async def create_subscription(
        self,
        subscription_id: str,
        entity_types: list[str],
        notification_url: str,
        watched_attributes: Optional[list[str]] = None
    ) -> bool:
        """
        Create an NGSI-LD subscription.
        
        Args:
            subscription_id: Unique subscription ID
            entity_types: List of entity types to watch
            notification_url: URL to send notifications
            watched_attributes: Optional list of attributes to watch
            
        Returns:
            True if created successfully
        """
        subscription = {
            "@context": NGSI_LD_CONTEXT,
            "id": f"urn:ngsi-ld:Subscription:{subscription_id}",
            "type": "Subscription",
            "entities": [{"type": t} for t in entity_types],
            "notification": {
                "endpoint": {
                    "uri": notification_url,
                    "accept": "application/json"
                }
            }
        }
        
        if watched_attributes:
            subscription["watchedAttributes"] = watched_attributes
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.subscriptions_url,
                    json=subscription,
                    headers=self.headers
                )
                
                if response.status_code == 201:
                    logger.info(f"Created subscription: {subscription_id}")
                    return True
                elif response.status_code == 409:
                    logger.warning(f"Subscription already exists: {subscription_id}")
                    return True
                else:
                    logger.error(f"Failed to create subscription: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error creating subscription: {e}")
            return False



