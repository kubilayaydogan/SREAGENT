import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Spring Boot /ui/** altında servis eder (file:/app/static-ui/ui)
export default defineConfig({
  base: '/ui/',
  plugins: [react()],
  build: { outDir: 'dist', sourcemap: false },
  test: { environment: 'node' },
} as never)
