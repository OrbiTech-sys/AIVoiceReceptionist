import type { Metadata } from "next";
import Link from "next/link";
import "@/components/marketing/marketing.css";
import { newsreader } from "@/components/marketing/fonts";
import { SiteFooter, SiteHeader } from "@/components/marketing/site-chrome";

export const metadata: Metadata = {
  title: { absolute: "Page not found | OrbiTech AI Receptionist" },
  robots: { index: false, follow: true },
};

export default function NotFound() {
  return (
    <div className={`mk ${newsreader.variable}`}>
      <SiteHeader />
      <main className="mk-main">
        <div className="mk-wrap mk-pagehead">
          <h1>That page isn’t here.</h1>
          <p className="mk-pagehead__lede">The link may be old, or the address may have a typo.</p>
          <div className="mk-hero__actions">
            <Link href="/" className="mk-btn mk-btn--primary">
              Go to the homepage
            </Link>
            <Link href="/contact" className="mk-arrow">
              Contact us →
            </Link>
          </div>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}
