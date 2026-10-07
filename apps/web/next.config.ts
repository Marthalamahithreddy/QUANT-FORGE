import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The fixture-backed browser tests build into their OWN directory (`npm run e2e:build`), because that
  // build has the fixture API's address fixed into it. A build for a real API stays in `.next`, so the
  // two can never be mistaken for each other.
  distDir: process.env.QF_DIST_DIR ?? ".next",
};

export default nextConfig;
