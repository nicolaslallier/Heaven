<script setup>
import { bytes, exact } from '../format.js'
defineProps({ snapshots: Array })
</script>

<template>
  <section>
    <h2>Instantanés</h2>
    <p v-if="!snapshots.length">Aucun instantané.</p>
    <div class="scroll" v-else>
      <table>
        <thead><tr><th>Identifiant</th><th>Date</th><th>Tag</th><th>Fichiers</th><th>Taille</th><th>Restaurer</th></tr></thead>
        <tbody>
          <tr v-for="s in [...snapshots].sort((a, b) => b.id.localeCompare(a.id))" :key="s.id">
            <td><code>{{ s.id }}</code></td>
            <td>{{ exact(s.created_at) }}</td>
            <td>{{ s.tag ?? '—' }}</td>
            <td>{{ s.files }}</td>
            <td>{{ bytes(s.size) }}</td>
            <td><code>heaven restore {{ s.id }}</code></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
section { background: var(--card); border-radius: 8px; padding: 1rem; }
h2 { margin: 0 0 .5rem; font-size: 1rem; }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: .95rem; }
th, td { text-align: left; padding: .35rem .6rem; white-space: nowrap; }
th { color: var(--muted); font-weight: 500; }
tbody tr { border-top: 1px solid var(--line); }
</style>
