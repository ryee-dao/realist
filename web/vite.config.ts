import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './' lets the same build work at username.github.io/realist/ or a custom domain
export default defineConfig({
  plugins: [react()],
  base: './',
  server: { port: 3000 },
})
