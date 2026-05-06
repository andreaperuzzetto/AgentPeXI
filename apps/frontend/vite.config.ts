import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react(), tailwindcss()],
    worker: { format: 'es' },  // Web Worker ES module support (Three.js orb worker)
    server: {
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          headers: { 'X-Personal-Key': env.VITE_PERSONAL_KEY ?? '' },
        },
        '/ws': { target: 'ws://localhost:8000', ws: true },
      },
    },
    test: {
      exclude: ['**/._*', '**/node_modules/**'],
    },
  }
})
