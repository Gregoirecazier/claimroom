import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    plugins: [react()],
    server: env.DEV_API_PROXY_TARGET ? {
      proxy: {
        '/api': {
          target: env.DEV_API_PROXY_TARGET,
          changeOrigin: true,
          rewrite: path => path.replace(/^\/api/, ''),
        },
      },
    } : undefined,
  }
})
