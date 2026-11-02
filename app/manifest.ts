import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "OrbiTech AI Receptionist",
    short_name: "Receptionist",
    description: "Calls, bookings and alerts from your AI receptionist.",
    start_url: "/overview",
    display: "standalone",
    background_color: "#f7f8fa",
    theme_color: "#2f5bd3",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/icon.svg", sizes: "any", type: "image/svg+xml" },
    ],
  };
}
