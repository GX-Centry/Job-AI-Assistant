
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// /api 代理目标可配置:VITE_API_PROXY_TARGET(env 文件或进程环境均可)。
// 默认值保持 http://127.0.0.1:8000 —— 兼容当前在线环境(dev 容器手工 uvicorn);
// 全新安装/隔离验收按 frontend/.env.example 与 README 指向 Compose api(18000)。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [vue()],
    server: {
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  }
})
