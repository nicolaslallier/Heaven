<script setup>
import { computed } from 'vue'
import { bytes } from '../format.js'
const props = defineProps({ runs: Array })

// Un point par cycle, du plus ancien au plus récent.
const points = computed(() =>
  props.runs.filter((run) => run.repository_bytes != null).map((run) => run.repository_bytes).reverse(),
)
const path = computed(() => {
  const values = points.value
  if (values.length < 2) return ''
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = max - min || 1
  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * 300
      const y = 55 - ((value - min) / span) * 50
      return `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
})
</script>

<template>
  <section>
    <h2>Taille du dépôt</h2>
    <svg v-if="path" viewBox="0 0 300 60" preserveAspectRatio="none" role="img"
         :aria-label="`Taille du dépôt sur ${points.length} cycles, de ${bytes(points[0])} à ${bytes(points[points.length - 1])}`">
      <path :d="path" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke" />
    </svg>
    <p v-else>Pas assez de cycles pour tracer une courbe.</p>
  </section>
</template>

<style scoped>
section { background: var(--card); border-radius: 8px; padding: 1rem; }
h2 { margin: 0 0 .5rem; font-size: 1rem; }
svg { width: 100%; height: 6rem; }
</style>
