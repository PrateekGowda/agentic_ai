import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@agentcore-deployer/contracts"],
};

export default nextConfig;
