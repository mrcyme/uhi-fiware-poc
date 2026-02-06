<template>
  <div class="layer-panel" :class="{ collapsed: isCollapsed }">
    <button class="toggle-btn" @click="isCollapsed = !isCollapsed">
      {{ isCollapsed ? '◀' : '▶' }} Layers
    </button>
    
    <div class="panel-content" v-show="!isCollapsed">
      <h3>Map Layers</h3>
      
      <div class="layer-list">
        <div 
          v-for="layer in layers" 
          :key="layer.id"
          class="layer-item"
          :class="{ active: layer.visible }"
        >
          <div class="layer-header">
            <label class="checkbox-wrapper">
              <input 
                type="checkbox" 
                :checked="layer.visible"
                @change="$emit('toggle-layer', layer.id)"
              />
              <span class="checkmark"></span>
              <span class="layer-name">{{ layer.name }}</span>
            </label>
          </div>
          
          <p class="layer-description">{{ layer.description }}</p>
          
          <div class="opacity-control" v-if="layer.visible">
            <label>Opacity: {{ Math.round(layer.opacity * 100) }}%</label>
            <input 
              type="range" 
              min="0" 
              max="1" 
              step="0.1"
              :value="layer.opacity"
              @input="$emit('set-opacity', layer.id, parseFloat($event.target.value))"
            />
          </div>
          
          <div class="legend" v-if="layer.legend && layer.visible">
            <div class="legend-gradient" :style="getLegendStyle(layer.legend)"></div>
            <div class="legend-labels">
              <span>{{ layer.legend.min.label }}</span>
              <span>{{ layer.legend.max.label }}</span>
            </div>
          </div>
        </div>
      </div>
      
      <div class="info-section">
        <h4>About</h4>
        <p>Urban Heat Island monitoring system for Brussels using FIWARE and satellite imagery.</p>
        <ul>
          <li><strong>NDVI:</strong> Vegetation density</li>
          <li><strong>NDWI:</strong> Water presence</li>
          <li><strong>UHI:</strong> Heat risk prediction</li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  layers: {
    type: Array,
    required: true
  },
  activeLayers: {
    type: Array,
    default: () => []
  }
})

defineEmits(['toggle-layer', 'set-opacity'])

const isCollapsed = ref(false)

function getLegendStyle(legend) {
  return {
    background: `linear-gradient(to right, ${legend.min.color}, ${legend.max.color})`
  }
}
</script>

<style scoped>
.layer-panel {
  position: absolute;
  top: 80px;
  right: 20px;
  width: 320px;
  max-height: calc(100vh - 120px);
  background: rgba(20, 25, 35, 0.92);
  backdrop-filter: blur(10px);
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  z-index: 1000;
  overflow: hidden;
  transition: width 0.3s ease;
}

.layer-panel.collapsed {
  width: auto;
}

.toggle-btn {
  display: block;
  width: 100%;
  padding: 12px 16px;
  background: rgba(255, 255, 255, 0.1);
  border: none;
  color: white;
  font-size: 0.95rem;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
  transition: background 0.2s;
}

.toggle-btn:hover {
  background: rgba(255, 255, 255, 0.15);
}

.panel-content {
  padding: 16px;
  overflow-y: auto;
  max-height: calc(100vh - 180px);
}

h3 {
  color: #fff;
  font-size: 1.1rem;
  margin-bottom: 16px;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.layer-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.layer-item {
  background: rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  padding: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  transition: all 0.2s;
}

.layer-item.active {
  background: rgba(66, 153, 225, 0.15);
  border-color: rgba(66, 153, 225, 0.3);
}

.layer-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.checkbox-wrapper {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  color: #fff;
  font-weight: 500;
}

.checkbox-wrapper input {
  display: none;
}

.checkmark {
  width: 20px;
  height: 20px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.checkbox-wrapper input:checked + .checkmark {
  background: #4299e1;
  border-color: #4299e1;
}

.checkbox-wrapper input:checked + .checkmark::after {
  content: '✓';
  color: white;
  font-size: 14px;
}

.layer-name {
  font-size: 0.95rem;
}

.layer-description {
  color: rgba(255, 255, 255, 0.6);
  font-size: 0.8rem;
  margin: 8px 0 0 30px;
}

.opacity-control {
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.opacity-control label {
  color: rgba(255, 255, 255, 0.7);
  font-size: 0.8rem;
  display: block;
  margin-bottom: 6px;
}

.opacity-control input[type="range"] {
  width: 100%;
  height: 4px;
  -webkit-appearance: none;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 2px;
  outline: none;
}

.opacity-control input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 16px;
  height: 16px;
  background: #4299e1;
  border-radius: 50%;
  cursor: pointer;
}

.legend {
  margin-top: 12px;
}

.legend-gradient {
  height: 12px;
  border-radius: 4px;
  margin-bottom: 4px;
}

.legend-labels {
  display: flex;
  justify-content: space-between;
  color: rgba(255, 255, 255, 0.6);
  font-size: 0.75rem;
}

.info-section {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.info-section h4 {
  color: #fff;
  font-size: 0.95rem;
  margin-bottom: 8px;
}

.info-section p {
  color: rgba(255, 255, 255, 0.7);
  font-size: 0.85rem;
  line-height: 1.5;
  margin-bottom: 12px;
}

.info-section ul {
  list-style: none;
  padding: 0;
}

.info-section li {
  color: rgba(255, 255, 255, 0.7);
  font-size: 0.8rem;
  padding: 4px 0;
}

.info-section li strong {
  color: #4299e1;
}
</style>


