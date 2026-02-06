<template>
  <div class="app-container">
    <CesiumViewer 
      ref="viewer"
      :layers="layers"
      :activeLayers="activeLayers"
    />
    <LayerControls 
      :layers="layers"
      :activeLayers="activeLayers"
      @toggle-layer="toggleLayer"
      @set-opacity="setOpacity"
    />
    <div class="header">
      <h1>🌡️ Brussels Urban Heat Island Monitor</h1>
      <p>Visualizing NDVI, NDWI, and UHI predictions</p>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import CesiumViewer from './components/CesiumViewer.vue'
import LayerControls from './components/LayerControls.vue'

// WMS layer definitions
const layers = reactive([
  {
    id: 'rgb',
    name: 'RGB Orthophoto',
    description: 'True color aerial imagery',
    wmsLayer: 'uhi:rgb',
    visible: false,
    opacity: 1.0,
    legend: null
  },
  {
    id: 'nir',
    name: 'NIR Orthophoto',
    description: 'Near-infrared imagery',
    wmsLayer: 'uhi:nir',
    visible: false,
    opacity: 1.0,
    legend: null
  },
  {
    id: 'ndvi',
    name: 'NDVI',
    description: 'Vegetation Index (-1 to 1)',
    wmsLayer: 'uhi:ndvi',
    visible: true,
    opacity: 0.7,
    legend: {
      min: { value: -1, color: '#d73027', label: 'No vegetation' },
      max: { value: 1, color: '#1a9850', label: 'Dense vegetation' }
    }
  },
  {
    id: 'ndwi',
    name: 'NDWI',
    description: 'Water Index (-1 to 1)',
    wmsLayer: 'uhi:ndwi',
    visible: false,
    opacity: 0.7,
    legend: {
      min: { value: -1, color: '#8c510a', label: 'No water' },
      max: { value: 1, color: '#01665e', label: 'Water' }
    }
  },
  {
    id: 'uhi_prediction',
    name: 'UHI Heat Risk',
    description: 'Heat island prediction (0-1)',
    wmsLayer: 'uhi:uhi_prediction',
    visible: false,
    opacity: 0.7,
    legend: {
      min: { value: 0, color: '#2166ac', label: 'Cool' },
      max: { value: 1, color: '#b2182b', label: 'Hot' }
    }
  }
])

const activeLayers = ref(['ndvi'])
const viewer = ref(null)

function toggleLayer(layerId) {
  const layer = layers.find(l => l.id === layerId)
  if (layer) {
    layer.visible = !layer.visible
    if (layer.visible) {
      if (!activeLayers.value.includes(layerId)) {
        activeLayers.value.push(layerId)
      }
    } else {
      activeLayers.value = activeLayers.value.filter(id => id !== layerId)
    }
  }
}

function setOpacity(layerId, opacity) {
  const layer = layers.find(l => l.id === layerId)
  if (layer) {
    layer.opacity = opacity
  }
}
</script>

<style>
.app-container {
  width: 100%;
  height: 100%;
  position: relative;
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
}

.header {
  position: absolute;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  text-align: center;
  color: white;
  text-shadow: 0 2px 4px rgba(0, 0, 0, 0.5);
  pointer-events: none;
  z-index: 100;
}

.header h1 {
  font-size: 1.8rem;
  font-weight: 600;
  margin-bottom: 4px;
}

.header p {
  font-size: 0.95rem;
  opacity: 0.9;
}
</style>


