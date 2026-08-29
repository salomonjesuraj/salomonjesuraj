import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Kept separate from vite.config.ts rather than merged into it: that
// file's own `server` block (the /api and /ws dev-server proxies, the
// dedicated HMR port) is real dev-server config with no meaning for a
// jsdom test run, and vitest's own `test` field needs `defineConfig`
// imported from 'vitest/config' (a superset of Vite's own config type)
// rather than 'vite' to type-check at all.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // Deliberately NOT `globals: true` -- every test file imports
    // describe/it/expect/vi from 'vitest' explicitly instead. This
    // needs zero tsconfig.app.json changes (no `vitest/globals` types
    // entry to add to a config that otherwise only declares
    // `vite/client`), and matches this codebase's own preference for
    // explicit imports over ambient globals elsewhere.
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
