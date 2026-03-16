/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    // When running on host (not in Docker), backend is at localhost:8002
    // When running in Docker, backend is at backend:8000
    const backendUrl = process.env.BACKEND_INTERNAL_URL || 'http://127.0.0.1:8002';
    return [
      {
        source: '/api/:path*',
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
