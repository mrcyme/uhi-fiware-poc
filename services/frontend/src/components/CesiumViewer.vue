<template>
  <div ref="cesiumContainer" class="cesium-container"></div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, toRaw } from 'vue'
import * as Cesium from 'cesium'

const props = defineProps({
  layers: {
    type: Array,
    required: true
  },
  activeLayers: {
    type: Array,
    default: () => []
  }
})

const cesiumContainer = ref(null)
let viewer = null
const wmsLayers = new Map()

// GeoServer WMS URL - use proxy path when in Docker, direct URL for local dev
const GEOSERVER_URL = window.location.port === '3000' 
  ? '/geoserver'  // Proxied through nginx in Docker
  : 'http://localhost:8080/geoserver'  // Direct for local development

// Brussels center coordinates (Belgian Lambert 72 center converted to WGS84)
const BRUSSELS_CENTER = {
  longitude: 4.3517,
  latitude: 50.8503,
  height: 50000
}

onMounted(() => {
  initCesium()
})

onUnmounted(() => {
  if (viewer) {
    viewer.destroy()
  }
})

function initCesium() {
  // Initialize Cesium viewer
  viewer = new Cesium.Viewer(cesiumContainer.value, {
    baseLayerPicker: false, // Disable base layer picker since we're using OSM
    geocoder: true,
    homeButton: true,
    sceneModePicker: true,
    navigationHelpButton: false,
    animation: false,
    timeline: false,
    fullscreenButton: true,
    vrButton: false,
    infoBox: true,
    selectionIndicator: false,
    shadows: false,
    shouldAnimate: false
  })

  // Replace default satellite imagery with OpenStreetMap
  viewer.imageryLayers.removeAll()
  const osmProvider = new Cesium.UrlTemplateImageryProvider({
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    subdomains: ['a', 'b', 'c'],
    credit: '© OpenStreetMap contributors'
  })
  viewer.imageryLayers.addImageryProvider(osmProvider)

  // Set initial camera position to Brussels
  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(
      BRUSSELS_CENTER.longitude,
      BRUSSELS_CENTER.latitude,
      BRUSSELS_CENTER.height
    ),
    orientation: {
      heading: Cesium.Math.toRadians(0),
      pitch: Cesium.Math.toRadians(-90),
      roll: 0
    },
    duration: 0
  })

  // Add initial layers
  props.layers.forEach(layer => {
    if (layer.visible) {
      addWmsLayer(layer)
    }
  })
}

function addWmsLayer(layerConfig) {
  if (!viewer || wmsLayers.has(layerConfig.id)) return

  const provider = new Cesium.WebMapServiceImageryProvider({
    url: `${GEOSERVER_URL}/uhi/wms`,
    layers: layerConfig.wmsLayer,
    parameters: {
      service: 'WMS',
      version: '1.3.0',
      request: 'GetMap',
      format: 'image/png',
      transparent: true,
      styles: '',
      crs: 'CRS:84'
    },
    enablePickFeatures: true,
    credit: 'UHI Brussels - FARI'
  })

  const imageryLayer = viewer.imageryLayers.addImageryProvider(provider)
  imageryLayer.alpha = layerConfig.opacity

  wmsLayers.set(layerConfig.id, imageryLayer)
}

function removeWmsLayer(layerId) {
  if (!viewer || !wmsLayers.has(layerId)) return

  const layer = wmsLayers.get(layerId)
  viewer.imageryLayers.remove(layer)
  wmsLayers.delete(layerId)
}

function updateLayerOpacity(layerId, opacity) {
  if (!wmsLayers.has(layerId)) return
  
  const layer = wmsLayers.get(layerId)
  layer.alpha = opacity
}

// Watch for layer visibility changes
watch(() => props.layers.map(l => ({ id: l.id, visible: l.visible, opacity: l.opacity })), 
  (newLayers) => {
    newLayers.forEach(layer => {
      const existingLayer = wmsLayers.has(layer.id)
      const layerConfig = props.layers.find(l => l.id === layer.id)
      
      if (layer.visible && !existingLayer) {
        addWmsLayer(layerConfig)
      } else if (!layer.visible && existingLayer) {
        removeWmsLayer(layer.id)
      } else if (layer.visible && existingLayer) {
        updateLayerOpacity(layer.id, layer.opacity)
      }
    })
  },
  { deep: true }
)

// Expose methods for parent component
defineExpose({
  flyTo: (longitude, latitude, height) => {
    if (viewer) {
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(longitude, latitude, height)
      })
    }
  },
  getViewer: () => viewer
})
</script>

<style scoped>
.cesium-container {
  width: 100%;
  height: 100%;
}
</style>

