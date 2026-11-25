import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './' lets the same build work at username.github.io/realist/ or a custom domain
export default defineConfig({
  plugins: [react()],
  base: './',
  server: { port: 3000 },
  build: {
    // One stylesheet for the whole site: pages load lazily, but their CSS keeps the same cascade order as before.
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        // React and Leaflet in their own files: their names stay the same across deploys that only change our code,
        // and the pages without a map (Market, Sold, Drivers, Data Engine, About) never download Leaflet.
        manualChunks(id) {
          if (/node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'react'
          if (/node_modules[\\/]leaflet[\\/].*\.js$/.test(id)) return 'leaflet'   // not leaflet.css, which main.tsx imports
        },
      },
    },
  },
})
