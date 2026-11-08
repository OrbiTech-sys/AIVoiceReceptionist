import { COMPANY_NAME, SITE_DESCRIPTION, SITE_NAME, SITE_URL } from "@/lib/site";

export function JsonLd({ data }: { data: Record<string, unknown> }) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data).replace(/</g, "\\u003c") }}
    />
  );
}

const ORGANIZATION_ID = `${SITE_URL}/#organization`;

export const siteSchema = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": ORGANIZATION_ID,
      name: COMPANY_NAME,
      url: SITE_URL,
      logo: `${SITE_URL}/icons/icon-512.png`,
      contactPoint: { "@type": "ContactPoint", contactType: "customer support", url: `${SITE_URL}/contact`, availableLanguage: "English" },
    },
    {
      "@type": "WebSite",
      "@id": `${SITE_URL}/#website`,
      name: SITE_NAME,
      url: SITE_URL,
      inLanguage: "en-US",
      publisher: { "@id": ORGANIZATION_ID },
    },
  ],
};

export const softwareSchema = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: SITE_NAME,
  url: SITE_URL,
  applicationCategory: "BusinessApplication",
  applicationSubCategory: "AI voice receptionist",
  operatingSystem: "Web",
  description: SITE_DESCRIPTION,
  inLanguage: "en-US",
  publisher: { "@id": ORGANIZATION_ID },
  featureList: [
    "Answers incoming phone calls with an AI voice",
    "Answers questions from your hours, services, prices and knowledge entries",
    "Books, reschedules and cancels appointments",
    "Prevents double-booking",
    "Sends urgent alerts when a caller describes an emergency",
    "Notifies your team in the app, by push and by e-mail",
  ],
};

export function breadcrumbSchema(name: string, path: string) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "Home", item: `${SITE_URL}/` },
      { "@type": "ListItem", position: 2, name, item: `${SITE_URL}${path}` },
    ],
  };
}

export function faqSchema(items: readonly { q: string; a: string }[]) {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: items.map(({ q, a }) => ({
      "@type": "Question",
      name: q,
      acceptedAnswer: { "@type": "Answer", text: a },
    })),
  };
}
