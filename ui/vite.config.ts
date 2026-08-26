import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Sniper HUD dev server. Proxies /api and /ws to the real backend
// exactly like services/dashboard/nginx.conf does for the legacy
// dashboard -- same upstreams, same paths, so every fetch/WS call in
// this app can use bare relative URLs in both dev and (once this is
// containerized behind its own nginx later) prod.
//
// api and ws-gateway are separate services/ports (see
// services/dashboard/nginx.conf's own two upstreams) -- each host is
// independently overridable so this works both run from the host
// machine (localhost:8000 / localhost:8001, Docker Compose's own
// published ports) and from inside the project's Docker network
// (service names api / ws-gateway).
const apiHost = process.env.VITE_API_HOST || 'localhost'
const wsHost = process.env.VITE_WS_HOST || 'localhost'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    // Vite's own HMR websocket and this app's /ws proxy both want to
    // handle the underlying HTTP server's "upgrade" event -- giving
    // HMR its own dedicated port keeps the two from fighting over it
    // (observed live: without this, Vite's HMR client would drop and
    // "poll for restart" repeatedly once a client connected to /ws).
    hmr: {
      port: 5174,
    },
    proxy: {
      '/api': {
        target: `http://${apiHost}:8000`,
        changeOrigin: true,
      },
      '/ws': {
        target: `ws://${wsHost}:8001`,
        ws: true,
      },
    },
  },
})
