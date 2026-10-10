<script setup>
import { bytes, exact, relative, duration } from '../format.js'
defineProps({ health: Object, repository: Object, runs: Array })

function last(runs) {
  return runs && runs.length ? runs[0] : null
}
</script>

<template>
  <section class="cards">
    <article>
      <h2>Dernière sauvegarde</h2>
      <template v-if="last(runs)">
        <p class="big" :class="last(runs).status">
          {{ last(runs).status === 'ok' ? '✔' : '✘' }}
          <time :title="exact(last(runs).finished_at)">{{ relative(last(runs).finished_at) }}</time>
        </p>
        <p>{{ last(runs).files }} fichiers · {{ duration(last(runs).duration_seconds) }}</p>
        <p v-if="last(runs).warnings.length">{{ last(runs).warnings.length }} avertissement(s)</p>
      </template>
      <p v-else>Aucune sauvegarde pour l'instant.</p>
    </article>
    <article>
      <h2>Prochaine sauvegarde</h2>
      <p class="big">
        <time :title="exact(health.state?.next_run)">{{ relative(health.state?.next_run) }}</time>
      </p>
      <p>Planification : {{ health.state?.schedule }}</p>
    </article>
    <article>
      <h2>Dernière vérification</h2>
      <template v-if="health.last_verify">
        <p class="big" :class="health.last_verify.ok ? 'ok' : 'error'">
          {{ health.last_verify.ok ? '✔' : '✘' }}
          <time :title="exact(health.last_verify.at)">{{ relative(health.last_verify.at) }}</time>
        </p>
        <p>
          {{ health.last_verify.objects_checked }} objets · {{ health.last_verify.missing }} manquant(s) ·
          {{ health.last_verify.corrupted }} corrompu(s)
        </p>
      </template>
      <p v-else class="never">Jamais vérifié</p>
    </article>
    <article>
      <h2>Dépôt</h2>
      <p class="big">{{ bytes(repository?.bytes) }}</p>
      <p>{{ repository?.snapshots ?? '—' }} instantanés · {{ repository?.objects ?? '—' }} objets</p>
      <p v-if="repository?.disk">
        {{ repository.disk.free_percent }} % libre ({{ bytes(repository.disk.free) }})
        <template v-if="repository.disk.days_left != null"> · ~{{ repository.disk.days_left }} j restants</template>
      </p>
    </article>
    <article v-if="health.notifications">
      <h2>Notifications</h2>
      <p>Courriel : {{ health.notifications.email ?? 'désactivé' }}</p>
      <p>Push : {{ health.notifications.push ? 'actif' : 'désactivé' }}</p>
      <p v-if="health.notifications.last_attempt">
        Dernier envoi : <time :title="exact(health.notifications.last_attempt)">{{ relative(health.notifications.last_attempt) }}</time>
        ({{ health.notifications.last_result }})
      </p>
    </article>
  </section>
</template>

<style scoped>
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: 1rem; }
article { background: var(--card); border-radius: 8px; padding: 1rem; }
h2 { margin: 0 0 .5rem; font-size: .85rem; text-transform: uppercase; color: var(--muted); letter-spacing: .04em; }
p { margin: .25rem 0; }
.big { font-size: 1.4rem; font-weight: 600; }
.ok { color: var(--ok) } .error { color: var(--bad) } .never { color: var(--warn); font-weight: 600; }
</style>
