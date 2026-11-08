import type { Metadata } from "next";
import Link from "next/link";
import { LEDGER } from "@/components/marketing/content";
import { breadcrumbSchema, JsonLd } from "@/components/marketing/json-ld";
import { pageMetadata } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Pricing: Early Access Plans | OrbiTech AI Receptionist",
  description:
    "OrbiTech AI Receptionist is in early access, with pricing quoted per business. No per-call AI fees. See what’s included and what your phone provider bills.",
  path: "/pricing",
});

const INCLUDED = [
  "Your AI receptionist, with your greeting, voice and business facts",
  "Booking, rescheduling and cancelling by voice",
  "Calendar, staff schedules and double-booking protection",
  "Call log with transcripts and summaries",
  "In-app and push alerts, and reminders for your team",
  "Browser test calls before you connect a line",
];

const NEEDS = [
  "A phone number and SIP trunk from a provider, or forwarding from your current number",
  "Your hours, services and prices, which takes about 10 minutes to enter",
  "A check of the call-recording and AI-disclosure rules where you operate",
];

export default function PricingPage() {
  return (
    <>
      <JsonLd data={breadcrumbSchema("Pricing", "/pricing")} />
      <header className="mk-wrap mk-pagehead">
        <h1>We quote each business during early access.</h1>
        <p className="mk-pagehead__lede">
          There’s no public price list yet. We’re setting plans with our first businesses, so tell us about yours and we’ll send a quote.
        </p>
        <div className="mk-hero__actions">
          <Link href="/contact?topic=pricing" className="mk-btn mk-btn--primary">
            Request pricing
          </Link>
          <Link href="/signup" className="mk-arrow">
            Create your account →
          </Link>
        </div>
      </header>

      <section className="mk-section--tight" aria-labelledby="ledger-title">
        <div className="mk-wrap">
          <h2 id="ledger-title">Where the money goes.</h2>
          <table className="mk-table mk-table--ledger">
            <thead>
              <tr>
                <th scope="col">Cost</th>
                <th scope="col">Who you pay</th>
                <th scope="col">What to expect</th>
              </tr>
            </thead>
            <tbody>
              {LEDGER.map((r) => (
                <tr key={r.item}>
                  <th scope="row">{r.item}</th>
                  <td>{r.payee}</td>
                  <td>{r.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mk-section mk-section--band" aria-labelledby="scope-title">
        <div className="mk-wrap mk-two">
          <div>
            <h2 id="scope-title">What’s included.</h2>
            <ul className="mk-note-list">
              {INCLUDED.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </div>
          <div>
            <h2>What you’ll need.</h2>
            <ul className="mk-note-list">
              {NEEDS.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <div className="mk-wrap">
        <div className="mk-close">
          <h2>Tell us how many calls you get.</h2>
          <div className="mk-close__side">
            <p>Your business type and a rough call volume are enough for us to start a quote.</p>
            <div className="mk-close__actions">
              <Link href="/contact?topic=pricing" className="mk-btn mk-btn--primary">
                Request pricing
              </Link>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
