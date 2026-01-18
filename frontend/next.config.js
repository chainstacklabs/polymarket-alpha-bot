/** @type {import('next').NextConfig} */
const nextConfig = {
  // Enable React strict mode for development
  reactStrictMode: true,

  // Proxy WebSocket connections to backend
  async rewrites() {
    return [
      {
        source: '/ws/:path*',
        destination: 'http://localhost:8000/:path*',
      },
    ]
  },
}

module.exports = nextConfig
