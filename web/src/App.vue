<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import StatusBanner from './components/StatusBanner.vue'
import SummaryCards from './components/SummaryCards.vue'
import RunHistory from './components/RunHistory.vue'
import RepositoryChart from './components/RepositoryChart.vue'
import SnapshotList from './components/SnapshotList.vue'

const REFRESH_MS = 30_000

const health = ref(null)
const repository = ref(null)
const runs = ref([])
const snapshots = ref([])
const stale = ref('')
let timer = null

async function get(path) {
  const response = await fetch(path)
  return { code: response.status, body: await response.json() }
}

async function refresh() {
  try {
    const [h, r, u, s] = await Promise.all([
      get('/api/health'),
      get('/api/repository'),
      get('/api/runs?limit=30'),
      get('/api/snapshots'),
    ])
    // 503 : le service démarre, ce n'est pas une panne.
    health.value = h.code === 200 ? h.body : { status: 'unknown', summary: h.body.error }
    repository.value = r.code === 200 ? r.body : null
    runs.value = u.code === 200 ? u.body : []
    snapshots.value = s.code === 200 ? s.body : []
    stale.value = ''
  } catch {
    // On garde les dernières données affichées et on le dit.
    const at = new Date().toLocaleTimeString('fr-CA', { timeStyle: 'short' })
    stale.value = health.value
      ? `Données antérieures à ${at} — Heaven injoignable`
      : 'Heaven injoignable'
    if (!health.value) health.value = { status: 'error', summary: stale.value }
  }
}

function schedule() {
  clearInterval(timer)
  if (!document.hidden) timer = setInterval(refresh, REFRESH_MS)
}
function onVisibility() {
  if (!document.hidden) refresh()
  schedule()
}

onMounted(() => {
  refresh()
  schedule()
  document.addEventListener('visibilitychange', onVisibility)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  document.removeEventListener('visibilitychange', onVisibility)
})
</script>

<template>
  <main v-if="health">
    <StatusBanner :status="health.status" :summary="health.summary" :stale="stale" />
    <SummaryCards v-if="health.state" :health="health" :repository="repository" :runs="runs" />
    <RepositoryChart :runs="runs" />
    <RunHistory :runs="runs" />
    <SnapshotList :snapshots="snapshots" />
  </main>
  <p v-else class="loading">Connexion à Heaven…</p>
</template>

<style>
:root {
  --bg: #f4f5f7; --card: #fff; --text: #1c2025; --muted: #626b76; --line: #e3e6ea;
  --ok: #1a7f37; --warn: #b35900; --bad: #c62828; --grey: #6b7280; --accent: #2563eb;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a; --card: #1e2227; --text: #e8eaed; --muted: #9aa3ad; --line: #30363d;
    --ok: #4cc26a; --warn: #f0a04b; --bad: #ff6b6b; --grey: #9ca3af; --accent: #6ea8ff;
  }
}
body { font-family: system-ui, sans-serif; margin: 0; background: var(--bg); color: var(--text); }
main { max-width: 64rem; margin: 0 auto; padding: 1rem; display: grid; gap: 1rem; }
.loading { padding: 2rem; }
code { font-size: .85em; }
</style>
