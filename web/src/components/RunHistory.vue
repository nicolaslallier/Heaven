<script setup>
import { ref } from 'vue'
import { bytes, duration, exact } from '../format.js'
defineProps({ runs: Array })
const open = ref(null)
</script>

<template>
  <section>
    <h2>Historique des cycles</h2>
    <p v-if="!runs.length">Pas encore de cycle enregistré.</p>
    <div class="scroll" v-else>
      <table>
        <thead>
          <tr><th>Statut</th><th>Début</th><th>Durée</th><th>Écrit</th><th>Fichiers</th><th>Vérifié</th></tr>
        </thead>
        <tbody>
          <template v-for="run in runs" :key="run.started_at">
            <tr :class="run.status" @click="open = open === run.started_at ? null : run.started_at" tabindex="0"
                @keydown.enter="open = open === run.started_at ? null : run.started_at">
              <td>{{ run.status === 'ok' ? '✔ ok' : '✘ échec' }}</td>
              <td>{{ exact(run.started_at) }}</td>
              <td>{{ duration(run.duration_seconds) }}</td>
              <td>{{ bytes(run.bytes_stored) }}</td>
              <td>{{ run.files }}</td>
              <td>{{ run.verified ? 'oui' : 'non' }}</td>
            </tr>
            <tr v-if="open === run.started_at && (run.error || run.warnings.length)" class="detail">
              <td colspan="6">
                <p v-if="run.error"><strong>Erreur :</strong> {{ run.error }}</p>
                <ul v-if="run.warnings.length"><li v-for="w in run.warnings" :key="w">{{ w }}</li></ul>
              </td>
            </tr>
          </template>
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
tr.error td:first-child { color: var(--bad); font-weight: 600; }
tr.ok td:first-child { color: var(--ok); }
tbody tr:not(.detail) { cursor: pointer; border-top: 1px solid var(--line); }
.detail td { white-space: normal; background: var(--bg); }
</style>
