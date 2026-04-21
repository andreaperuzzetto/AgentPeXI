import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        headers: { 'X-Personal-Key': 'agentpexi_local_2026' },
      },
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
