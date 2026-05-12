import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@agentcore-deployer/contracts"],
  async rewrites() {
    const backend = process.env.ORCHESTRATOR_BASE_URL ?? "http://localhost:8000";
    return [
      {
        source: "/api/backend/:path*",
        destination: `${backend.replace(/\/$/, "")}/:path*`,
      },
    ];
  },
};

export default nextConfig;
