<script setup>
import { ref } from 'vue'

const status = ref('connexion à Heaven…')
fetch('/api/health')
  .then((response) => response.json())
  .then((body) => (status.value = body.reason ?? body.error))
  .catch(() => (status.value = 'Heaven injoignable'))
</script>

<template>
  <main>
    <h1>Heaven</h1>
    <p>{{ status }}</p>
  </main>
</template>

<style>
body { font-family: system-ui, sans-serif; margin: 2rem; }
</style>
