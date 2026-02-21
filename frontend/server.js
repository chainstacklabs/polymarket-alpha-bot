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
  on: {
    error: (err, req, res) => {
      console.error(`[Proxy Error] ${err.message}`)
    },
  },
})

app.prepare().then(() => {
  const server = createServer(async (req, res) => {
    const parsedUrl = parse(req.url, true)

    // Skip WebSocket paths - they should only be handled by upgrade event
    if (parsedUrl.pathname?.startsWith('/ws')) {
      console.log(
        `HTTP request to WS path (should be upgrade): ${parsedUrl.pathname}`
      )
      res.writeHead(426, { 'Content-Type': 'text/plain' })
      res.end('Upgrade Required')
      return
    }

    // Let Next.js handle all other HTTP requests
    await handle(req, res, parsedUrl)
  })

  // Handle WebSocket upgrade requests
  server.on('upgrade', (req, socket, head) => {
    const { pathname } = parse(req.url)

    // Only proxy our API WebSockets (/ws/*)
    // Let Next.js handle its own WebSockets (/_next/webpack-hmr for HMR)
    if (pathname?.startsWith('/ws')) {
      console.log(`WebSocket upgrade: ${pathname}`)
      wsProxy.upgrade(req, socket, head)
    }
    // Don't destroy other sockets - Next.js needs them for HMR
  })

  server.listen(port, () => {
    console.log(`> Ready on http://${hostname}:${port}`)
    console.log(`> Backend proxy: ${backendUrl}`)
    console.log(`> WebSocket proxy: /ws/* -> ${backendUrl}/*`)
  })
})
