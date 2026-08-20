import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // File-change events don't propagate across Docker bind mounts on
    // Windows — poll instead so hot-reload actually fires in the container.
    watch: {
      usePolling: true,
    },
  },
})
