import type { Metadata } from "next";

export const SITE_URL = "https://voice.theorbitechsolution.com";
export const SITE_NAME = "OrbiTech AI Receptionist";
export const COMPANY_NAME = "OrbiTech Solution";
// Date the public pages were last revised. Used by the sitemap and the legal pages.
export const SITE_UPDATED = "2026-11-08";
export const SITE_UPDATED_LABEL = "November 8, 2026";

export const SITE_DESCRIPTION =
  "An AI receptionist for appointment-based businesses. It answers your phone, gives callers your hours and prices, and books real appointments on your calendar.";

const OG_IMAGE = {
  url: "/opengraph-image",
  width: 1200,
  height: 630,
  alt: "OrbiTech AI Receptionist: stop sending callers to voicemail.",
};

export function pageMetadata({
  title,
  description,
  path,
  index = true,
}: {
  title: string;
  description: string;
  path: string;
  index?: boolean;
}): Metadata {
  return {
    title: { absolute: title },
    description,
    alternates: { canonical: path },
    robots: index ? { index: true, follow: true } : { index: false, follow: true },
    openGraph: {
      type: "website",
      siteName: SITE_NAME,
      locale: "en_US",
      url: path,
      title,
      description,
      images: [OG_IMAGE],
    },
    twitter: { card: "summary_large_image", title, description, images: [OG_IMAGE.url] },
  };
}

export const NAV_LINKS = [
  { href: "/features", label: "Features" },
  { href: "/pricing", label: "Pricing" },
  { href: "/faq", label: "FAQ" },
  { href: "/contact", label: "Contact" },
] as const;
