import type { Metadata } from "next";
import Link from "next/link";
import { breadcrumbSchema, JsonLd } from "@/components/marketing/json-ld";
import { COMPANY_NAME, pageMetadata, SITE_UPDATED_LABEL } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Privacy Policy | OrbiTech AI Receptionist",
  description:
    "How OrbiTech Solution collects, uses, stores and deletes account data, call transcripts, recordings and website messages for OrbiTech AI Receptionist.",
  path: "/privacy",
});

export default function PrivacyPage() {
  return (
    <>
      <JsonLd data={breadcrumbSchema("Privacy Policy", "/privacy")} />
      <header className="mk-wrap mk-pagehead">
        <h1>Privacy policy</h1>
        <p className="mk-doc__date" style={{ marginTop: "var(--space-sm)" }}>
          Last updated {SITE_UPDATED_LABEL}
        </p>
      </header>
      <article className="mk-wrap">
        <div className="mk-doc">
          <p>
            This policy explains what {COMPANY_NAME} (“we”) does with information handled by OrbiTech AI Receptionist. It applies to this
            website, to accounts on the service, and to the calls the service answers for the businesses that use it.
          </p>

          <h2>Businesses and their callers</h2>
          <p>
            When a business uses the service, that business decides what happens to its callers’ information, and we process it on the
            business’s behalf. If you called a business that uses OrbiTech, contact that business first about your information.
          </p>

          <h2>What we collect</h2>
          <ul className="mk-note-list">
            <li>Account details: your name, e-mail address, password (stored only as a salted hash), and business name and type.</li>
            <li>Business settings: hours, address, services, prices, staff schedules and the answers you enter.</li>
            <li>Appointment and contact records created by you or by the receptionist.</li>
            <li>Call records: the caller’s number, a transcript, a summary and the outcome of each call.</li>
            <li>Call recordings, only if the business turns recording on. It’s off by default and requires a consent message.</li>
            <li>Website messages: what you type into our contact form.</li>
            <li>Technical records: sign-in sessions, browser type, and security and audit events.</li>
          </ul>

          <h2>How we use it</h2>
          <p>
            We use this information to run the service, answer and record calls as each business has configured, send the alerts you
            choose, reply to your messages, keep the service secure and meet legal obligations. We don’t sell personal information, and we
            don’t use it for advertising.
          </p>

          <h2>Speech processing</h2>
          <p>
            Speech recognition and speech synthesis run on our own server using open-source models. Caller audio isn’t sent to a paid AI
            service for processing.
          </p>

          <h2>Who else handles information</h2>
          <ul className="mk-note-list">
            <li>The phone provider a business connects carries its calls and has its own privacy terms.</li>
            <li>Browser push services deliver push notifications to the devices you allow.</li>
            <li>If e-mail delivery is enabled, an e-mail provider delivers alerts and replies.</li>
            <li>We may disclose information when the law requires it.</li>
          </ul>

          <h2>Cookies</h2>
          <p>
            We use two cookies, both to keep you signed in safely: a session cookie and a security token. Your browser also remembers your
            light or dark theme. This website has no advertising or analytics cookies.
          </p>

          <h2>How long we keep it</h2>
          <p>
            Each business controls its own retention. By default, transcripts are deleted after 90 days and recordings after 30 days.
            Website messages are kept until they’ve been dealt with and are then deleted on request. Other account data is kept while the
            account is active.
          </p>

          <h2>Security</h2>
          <p>
            Passwords are hashed with Argon2id, sessions use HttpOnly cookies, stored telephony credentials are encrypted, and each
            business’s data is kept separate on the server. No system is perfectly secure, so we can’t promise that information will never
            be exposed.
          </p>

          <h2>Your choices and rights</h2>
          <p>
            You can see and change most of your information in your account. To ask for a copy, a correction or deletion, send us a message
            through the{" "}
            <Link href="/contact" className="mk-text-link">
              contact page
            </Link>
            . Depending on where you live, including some US states, you may have further rights under local law.
          </p>

          <h2>Children</h2>
          <p>The service is meant for businesses and isn’t directed to children under 13.</p>

          <h2>Changes</h2>
          <p>If we change this policy, we’ll update the date at the top of this page.</p>
        </div>
      </article>
    </>
  );
}
