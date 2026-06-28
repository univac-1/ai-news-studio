import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              const creds = Buffer.from(
                `${env.API_USERNAME ?? 'admin'}:${env.API_PASSWORD ?? 'change-me'}`
              ).toString('base64')
              proxyReq.setHeader('Authorization', `Basic ${creds}`)
            })
          },
        },
      },
    },
  }
})
