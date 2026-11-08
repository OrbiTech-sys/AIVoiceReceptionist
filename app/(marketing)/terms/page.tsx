import type { Metadata } from "next";
import Link from "next/link";
import { breadcrumbSchema, JsonLd } from "@/components/marketing/json-ld";
import { COMPANY_NAME, pageMetadata, SITE_UPDATED_LABEL } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Terms of Service | OrbiTech AI Receptionist",
  description:
    "The terms for using OrbiTech AI Receptionist during early access, including your responsibilities for phone lines, call recording and AI disclosure.",
  path: "/terms",
});

export default function TermsPage() {
  return (
    <>
      <JsonLd data={breadcrumbSchema("Terms of Service", "/terms")} />
      <header className="mk-wrap mk-pagehead">
        <h1>Terms of service</h1>
        <p className="mk-doc__date" style={{ marginTop: "var(--space-sm)" }}>
          Last updated {SITE_UPDATED_LABEL}
        </p>
      </header>
      <article className="mk-wrap">
        <div className="mk-doc">
          <p>
            These terms are an agreement between you and {COMPANY_NAME} (“we”) for your use of OrbiTech AI Receptionist. By creating an
            account or using the service, you accept them on behalf of yourself and your business.
          </p>

          <h2>Early access</h2>
          <p>
            The service is in early access. Features may change, and it may have errors or interruptions. Don’t rely on it as your only way
            of being reached.
          </p>

          <h2>Your account</h2>
          <p>
            Keep your sign-in details secure and your business information accurate. You’re responsible for what happens under your account
            and for anyone you let use it.
          </p>

          <h2>Fees</h2>
          <p>
            Fees for the service are set out in a written quote. You owe nothing to us unless you’ve accepted one. Your phone provider, and
            any other third party you use, bills you separately under its own terms.
          </p>

          <h2>Phone lines</h2>
          <p>
            Live calls need a phone number and service from a third-party provider. We don’t provide phone numbers and aren’t responsible
            for your provider’s service or charges.
          </p>

          <h2>Your legal responsibilities</h2>
          <p>
            You’re responsible for following the laws that apply to your business and your calls, including rules on call recording, consent,
            AI disclosure, consumer protection, privacy and health information. The settings in the product help with this, but turning
            one on doesn’t make you compliant. We don’t claim HIPAA compliance, so don’t use the service for protected health information
            unless we’ve agreed to that in writing.
          </p>

          <h2>What the receptionist isn’t</h2>
          <p>
            It isn’t an emergency service and it doesn’t give medical, dental, legal or other professional advice. It can misunderstand
            people. Check your calendar and your call log, and tell callers how to reach emergency services where that matters.
          </p>

          <h2>Acceptable use</h2>
          <p>
            Don’t use the service to break the law, to harass or deceive people, to place unsolicited calls, to interfere with the service
            or to reach data that isn’t yours.
          </p>

          <h2>Your data</h2>
          <p>
            Your business’s data stays yours. You allow us to process it only as needed to run the service, as described in our{" "}
            <Link href="/privacy" className="mk-text-link">
              privacy policy
            </Link>
            .
          </p>

          <h2>Open-source components</h2>
          <p>The service uses open-source software and models that remain under their own licenses.</p>

          <h2>Disclaimers</h2>
          <p>
            To the extent the law allows, the service is provided “as is”, without warranties of any kind, including that it will answer
            every call or understand every caller.
          </p>

          <h2>Limit of liability</h2>
          <p>
            To the extent the law allows, we aren’t liable for indirect or consequential losses, such as lost bookings, lost profits or
            lost data. Our total liability is limited to the fees you paid us in the three months before the claim.
          </p>

          <h2>Ending your use</h2>
          <p>
            You can stop using the service at any time. We may suspend or end access if you break these terms or if we discontinue the
            service. Ask us through the{" "}
            <Link href="/contact" className="mk-text-link">
              contact page
            </Link>{" "}
            if you want your data deleted.
          </p>

          <h2>Changes to these terms</h2>
          <p>If we change these terms, we’ll update the date above. Continuing to use the service means you accept the new version.</p>

          <h2>Governing law</h2>
          <p>These terms are governed by the laws of the place where {COMPANY_NAME} is established, except where your local law says otherwise.</p>
        </div>
      </article>
    </>
  );
}
