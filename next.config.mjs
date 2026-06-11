/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    serverActions: {
      bodySizeLimit: "10mb",
    },
  },
  // /api is served by the Python FastAPI function (api/index.py).
  // In dev, proxy to a locally running uvicorn server; on Vercel, route to
  // the deployed serverless function (which receives the original path).
  rewrites: async () => [
    {
      source: "/api/:path*",
      destination:
        process.env.NODE_ENV === "development"
          ? "http://127.0.0.1:8000/api/:path*"
          : "/api/index",
    },
  ],
};

export default nextConfig;
