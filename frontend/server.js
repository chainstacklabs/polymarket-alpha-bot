const { createServer } = require('http')
const { parse } = require('url')
const next = require('next')
const { createProxyMiddleware } = require('http-proxy-middleware')

const dev = process.env.NODE_ENV !== 'production'
const hostname = 'localhost'
const port = parseInt(process.env.PORT, 10) || 3000
const backendUrl = process.env.BACKEND_URL || 'http://localhost:8000'

const app = next({ dev, hostname, port })
const handle = app.getRequestHandler()

// WebSocket proxy configuration
const wsProxy = createProxyMiddleware({
  target: backendUrl,
  changeOrigin: true,
  ws: true,
  pathRewrite: {
    '^/ws': '', // Remove /ws prefix: /ws/portfolios/ws -> /portfolios/ws
  },
  logger: console,
})

app.prepare().then(() => {
  const server = createServer(async (req, res) => {
    const parsedUrl = parse(req.url, true)

    // Let Next.js handle all HTTP requests
    await handle(req, res, parsedUrl)
  })

  // Handle WebSocket upgrade requests
  server.on('upgrade', (req, socket, head) => {
    const { pathname } = parse(req.url)

    if (pathname?.startsWith('/ws')) {
      console.log(`WebSocket upgrade: ${pathname}`)
      wsProxy.upgrade(req, socket, head)
    } else {
      socket.destroy()
    }
  })

  server.listen(port, () => {
    console.log(`> Ready on http://${hostname}:${port}`)
    console.log(`> Backend proxy: ${backendUrl}`)
    console.log(`> WebSocket proxy: /ws/* -> ${backendUrl}/*`)
  })
})
