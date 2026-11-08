import type { Metadata } from "next";
import Link from "next/link";
import { FAQ } from "@/components/marketing/content";
import { breadcrumbSchema, faqSchema, JsonLd } from "@/components/marketing/json-ld";
import { pageMetadata } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "AI Receptionist FAQ: Calls, Booking, Privacy | OrbiTech",
  description:
    "Straight answers about OrbiTech AI Receptionist: AI disclosure, keeping your number, cost, emergencies, languages, reminders, call recording and HIPAA.",
  path: "/faq",
});

export default function FaqPage() {
  return (
    <>
      <JsonLd data={breadcrumbSchema("FAQ", "/faq")} />
      <JsonLd data={faqSchema(FAQ)} />
      <header className="mk-wrap mk-pagehead">
        <h1>Straight answers.</h1>
        <p className="mk-pagehead__lede">What owners and office managers ask before they let software pick up the phone.</p>
      </header>
      <div className="mk-wrap">
        <div className="mk-faq" style={{ marginTop: 0 }}>
          {FAQ.map((f, i) => (
            <details key={f.q} open={i < 3}>
              <summary>{f.q}</summary>
              <p>{f.a}</p>
            </details>
          ))}
        </div>
        <div className="mk-close" style={{ marginTop: "var(--space-2xl)" }}>
          <h2>Didn’t see your question?</h2>
          <div className="mk-close__side">
            <div className="mk-close__actions">
              <Link href="/contact" className="mk-btn mk-btn--primary">
                Ask us directly
              </Link>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
