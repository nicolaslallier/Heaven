<script setup>
import { computed, ref, watch } from 'vue'
import { bytes, exact } from '../format.js'

const props = defineProps({ id: String })
defineEmits(['close'])

const PAGE = 200
const path = ref('')
const query = ref('')
const listing = ref(null)
const offset = ref(0)
const error = ref('')

const crumbs = computed(() => {
  const parts = path.value ? path.value.split('/') : []
  return parts.map((name, index) => ({ name, path: parts.slice(0, index + 1).join('/') }))
})

async function load() {
  const params = new URLSearchParams({ path: path.value, limit: PAGE, offset: offset.value })
  if (query.value) params.set('q', query.value)
  try {
    const response = await fetch(`/api/snapshots/${encodeURIComponent(props.id)}/files?${params}`)
    const body = await response.json()
    if (response.ok) {
      listing.value = body
      error.value = ''
    } else error.value = body.error
  } catch {
    error.value = 'Heaven injoignable'
  }
}

function go(next) {
  path.value = next
  query.value = ''
  offset.value = 0
}

let timer = null
watch(query, () => {
  clearTimeout(timer)
  timer = setTimeout(() => {
    offset.value = 0
    load()
  }, 250)
})
watch([path, offset, () => props.id], load, { immediate: true })
</script>

<template>
  <section>
    <header>
      <h2>Instantané <code>{{ id }}</code></h2>
      <button @click="$emit('close')">Fermer</button>
    </header>
    <nav aria-label="Chemin">
      <button class="link" @click="go('')">racine</button>
      <template v-for="crumb in crumbs" :key="crumb.path">
        / <button class="link" @click="go(crumb.path)">{{ crumb.name }}</button>
      </template>
    </nav>
    <input v-model="query" type="search" placeholder="Rechercher par nom" aria-label="Rechercher par nom" />
    <p v-if="error" class="error">{{ error }}</p>
    <div v-else-if="listing" class="scroll">
      <table>
        <thead><tr><th>Nom</th><th>Taille</th><th>Modifié</th><th>Droits</th></tr></thead>
        <tbody>
          <tr v-for="entry in listing.entries" :key="entry.name">
            <td>
              <button v-if="entry.type === 'dir'" class="link"
                      @click="go(path ? `${path}/${entry.name}` : entry.name)">📁 {{ entry.name }}</button>
              <span v-else>{{ entry.name }}</span>
            </td>
            <td>{{ entry.type === 'dir' ? '' : bytes(entry.size) }}</td>
            <td>{{ entry.mtime ? exact(entry.mtime) : '' }}</td>
            <td><code>{{ entry.mode }}</code></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!listing.entries.length">Aucun résultat.</p>
      <p class="pager">
        {{ listing.total }} élément(s)
        <button :disabled="offset === 0" @click="offset = Math.max(0, offset - PAGE)">Précédent</button>
        <button :disabled="offset + PAGE >= listing.total" @click="offset += PAGE">Suivant</button>
      </p>
    </div>
  </section>
</template>

<style scoped>
section { background: var(--card); border-radius: 8px; padding: 1rem; }
header { display: flex; justify-content: space-between; align-items: center; }
h2 { margin: 0; font-size: 1rem; }
nav { margin: .5rem 0; }
input { width: 100%; box-sizing: border-box; padding: .4rem; margin-bottom: .5rem; }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: .95rem; }
th, td { text-align: left; padding: .3rem .6rem; white-space: nowrap; }
th { color: var(--muted); font-weight: 500; }
tbody tr { border-top: 1px solid var(--line); }
.link { background: none; border: 0; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
.error { color: var(--bad); }
.pager button { margin-left: .5rem; }
</style>
