import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: [
        "/api/",
        "/login",
        "/onboarding",
        "/overview",
        "/calls",
        "/appointments",
        "/contacts",
        "/notifications",
        "/receptionist",
        "/integrations",
        "/settings",
        "/admin",
      ],
    },
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
