/** @type {import('next').NextConfig} */
const apiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiBase}/api/:path*` },
      { source: "/ws/:path*", destination: `${apiBase}/ws/:path*` },
    ];
  },
};
export default nextConfig;
