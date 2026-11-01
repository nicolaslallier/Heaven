<script setup>
defineProps({ status: String, summary: String, stale: String })
const icons = { ok: '✔', warning: '⚠', error: '✖', down: '⛔', unknown: '?' }
const labels = { ok: 'Tout va bien', warning: 'Attention', error: 'En défaut', down: 'Injoignable', unknown: 'Inconnu' }
</script>

<template>
  <header class="banner" :class="status">
    <h1>Heaven</h1>
    <p role="status">
      <span class="dot" aria-hidden="true">{{ icons[status] ?? icons.unknown }}</span>
      <strong>{{ labels[status] ?? labels.unknown }}</strong>
      <span v-if="summary"> — {{ summary }}</span>
    </p>
    <p v-if="stale" class="stale">{{ stale }}</p>
  </header>
</template>

<style scoped>
.banner { border-left: 6px solid var(--c); background: var(--card); padding: 1rem 1.25rem; border-radius: 8px; }
.ok { --c: var(--ok) } .warning { --c: var(--warn) } .error { --c: var(--bad) } .unknown { --c: var(--grey) } .down { --c: var(--bad); border-left-style: dashed }
h1 { margin: 0 0 .25rem; font-size: 1.1rem; color: var(--muted); }
p { margin: 0; font-size: 1.15rem; }
.dot { color: var(--c); margin-right: .4em; }
.stale { margin-top: .5rem; color: var(--bad); font-size: .95rem; }
</style>
