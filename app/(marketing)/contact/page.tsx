import type { Metadata } from "next";
import Link from "next/link";
import { ContactForm } from "@/components/marketing/contact-form";
import { breadcrumbSchema, JsonLd } from "@/components/marketing/json-ld";
import { pageMetadata, SITE_URL } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Contact OrbiTech: Ask About Pricing or Setup",
  description:
    "Ask about early access pricing, getting your phone line connected, or anything else about OrbiTech AI Receptionist. Send us a message and we’ll reply by e-mail.",
  path: "/contact",
});

const contactSchema = {
  "@context": "https://schema.org",
  "@type": "ContactPage",
  name: "Contact OrbiTech",
  url: `${SITE_URL}/contact`,
  about: { "@id": `${SITE_URL}/#organization` },
};

export default function ContactPage() {
  return (
    <>
      <JsonLd data={breadcrumbSchema("Contact", "/contact")} />
      <JsonLd data={contactSchema} />
      <header className="mk-wrap mk-pagehead">
        <h1>Tell us about your front desk.</h1>
        <p className="mk-pagehead__lede">Pricing, setup, or a question the site didn’t answer. We reply by e-mail.</p>
      </header>
      <div className="mk-wrap mk-two" style={{ paddingBottom: "var(--space-3xl)" }}>
        <ContactForm />
        <aside>
          <h2 style={{ fontSize: "var(--text-lg)" }}>What happens next</h2>
          <ul className="mk-note-list">
            <li>Your message goes to the OrbiTech team and is stored with what you typed here, nothing more.</li>
            <li>For pricing, your business type and rough call volume help us quote.</li>
            <li>
              We use your details only to reply. See the{" "}
              <Link href="/privacy" className="mk-text-link">
                privacy policy
              </Link>
              .
            </li>
          </ul>
          <h2 style={{ fontSize: "var(--text-lg)", marginTop: "var(--space-xl)" }}>Already have an account?</h2>
          <p style={{ marginTop: "var(--space-xs)" }}>
            <Link href="/login" className="mk-text-link">
              Sign in to your dashboard
            </Link>
          </p>
        </aside>
      </div>
    </>
  );
}
