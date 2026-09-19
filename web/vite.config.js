import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// En développement, /api/ va à un « heaven serve --listen 127.0.0.1:8000 » local,
// comme nginx le fait en production.
export default defineConfig({
  plugins: [vue()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
