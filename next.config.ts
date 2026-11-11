import type { NextConfig } from "next";

// The FastAPI backend. Proxying /api through Next keeps the session cookie same-origin.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const isProd = process.env.NODE_ENV === "production";

// What a page may load. Scripts only from this site (inline scripts are allowed because Next.js writes its own page data
// inline; a nonce would need every page rendered per request), so a script injected from elsewhere is blocked. Pages can't
// be framed, can't send forms elsewhere and can't load plug-ins. Development also needs eval and a websocket for hot reload.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isProd ? "" : " 'unsafe-eval'"}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "media-src 'self' blob: https://platform.r2.fish.audio", // voice previews: Fish Audio's sample clips
  // Live calls use a websocket: wss on the real domain; on a machine's own localhost the site (port 3000) talks to the API on 8000.
  `connect-src 'self' wss: ws://localhost:${isProd ? "8000" : "*"} ws://127.0.0.1:${isProd ? "8000" : "*"}`,
  "worker-src 'self'",
  "manifest-src 'self'",
  "object-src 'none'",
  "frame-src 'none'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const nextConfig: NextConfig = {
  // Lets a production build run beside a running dev server: NEXT_DIST_DIR=.next-check npm run build
  distDir: process.env.NEXT_DIST_DIR || ".next",
  experimental: {
    agentFeedback: true,
    // Signed-in pages are client components that render only after the browser loads the session, so Next.js can't
    // check them for instant navigation. Without this, every one of them shows an error in the dev overlay.
    instantInsights: {
      validationLevel: "manual-warning",
    },
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "microphone=(self), camera=(), geolocation=(), payment=(), usb=()" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          ...(isProd ? [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" }] : []),
        ],
      },
      {
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Content-Type", value: "application/javascript; charset=utf-8" },
        ],
      },
    ];
  },
};

export default nextConfig;
