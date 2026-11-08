import type { Metadata } from "next";
import Link from "next/link";
import { breadcrumbSchema, JsonLd } from "@/components/marketing/json-ld";
import { pageMetadata } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Features: What the AI Receptionist Does | OrbiTech",
  description:
    "Every OrbiTech AI receptionist feature, from answering calls and booking appointments to alerts and call records, with what each one needs to work.",
  path: "/features",
});

type Row = { name: string; does: string; needs?: string };

const GROUPS: { title: string; rows: Row[] }[] = [
  {
    title: "Answering calls",
    rows: [
      { name: "Voice and greeting", does: "Pick a name, voice, speaking style, speed, greeting and closing line." },
      { name: "Answers from your facts", does: "Hours, address, services, prices, and the FAQs, policies and directions you enter." },
      { name: "AI disclosure", does: "Tells callers it’s an automated assistant. On by default." },
      { name: "Emergency handling", does: "Says your emergency message first, then sends your team an urgent alert." },
      { name: "Human callback", does: "Takes a name and message when it can’t help, or after repeated misunderstandings." },
      { name: "Live transfer", does: "Passes a caller to the number you set, during open hours.", needs: "A phone line" },
      { name: "Browser test call", does: "Talk to your receptionist with your microphone, or type, before connecting a line." },
    ],
  },
  {
    title: "Booking",
    rows: [
      { name: "Book, reschedule, cancel", does: "Callers can do all three by voice. Your calendar updates as they do." },
      { name: "Availability rules", does: "Business hours, breaks, buffers, holidays, minimum notice and booking windows." },
      { name: "Staff calendars", does: "Each staff member has their own services and hours. It only offers times they’re free." },
      { name: "No double-booking", does: "Two callers can’t take the same slot, even at the same moment." },
      { name: "Read-back before booking", does: "Confirms name, number, date and time. The caller hears “booked” only after it’s saved." },
    ],
  },
  {
    title: "Alerts and reminders",
    rows: [
      { name: "In-app notifications", does: "Bookings, changes, cancellations and urgent calls, with sounds you can choose or upload." },
      { name: "Phone push alerts", does: "Delivered to the phones you allow.", needs: "Your permission. On iPhone, install to Home Screen." },
      { name: "E-mail alerts", does: "The same alerts, by e-mail.", needs: "E-mail delivery set up on the server" },
      { name: "Team reminders", does: "Appointment reminders for your team, at the times each person picks." },
      { name: "Quiet hours", does: "Hold routine alerts overnight. Urgent ones still get through." },
    ],
  },
  {
    title: "Records and data",
    rows: [
      { name: "Call log", does: "Every call with its transcript, summary and outcome." },
      { name: "Contacts", does: "Callers are matched by phone number, so one person stays one record." },
      { name: "Call recording", does: "Off by default. Turning it on requires a consent message.", needs: "Your choice" },
      { name: "Retention", does: "Transcripts are deleted after 90 days and recordings after 30. You can change both." },
    ],
  },
  {
    title: "How it’s built",
    rows: [
      { name: "Speech recognition", does: "Whisper, running on our server." },
      { name: "Voice detection", does: "Silero VAD, so it knows when a caller has finished talking." },
      { name: "Speech", does: "Kokoro-82M, an open-weight text-to-speech model." },
      { name: "Sign-in security", does: "Argon2id password hashing, HttpOnly session cookies, CSRF checks and rate limits." },
      { name: "Business separation", does: "Each business’s data is scoped on the server, never by what a browser sends." },
    ],
  },
];

export default function FeaturesPage() {
  return (
    <>
      <JsonLd data={breadcrumbSchema("Features", "/features")} />
      <header className="mk-wrap mk-pagehead">
        <h1>What it does, and what it needs.</h1>
        <p className="mk-pagehead__lede">
          The whole product on one page. Anything that depends on something outside OrbiTech says so in the last column.
        </p>
      </header>

      <div className="mk-wrap">
        {GROUPS.map((g) => (
          <div key={g.title} className="mk-spec-group">
            <table className="mk-table mk-table--spec">
              <caption>{g.title}</caption>
              <thead>
                <tr>
                  <th scope="col">Feature</th>
                  <th scope="col">What it does</th>
                  <th scope="col">Needs</th>
                </tr>
              </thead>
              <tbody>
                {g.rows.map((r) => (
                  <tr key={r.name}>
                    <th scope="row">{r.name}</th>
                    <td>{r.does}</td>
                    <td>{r.needs ? <span className="mk-tag mk-tag--needs">{r.needs}</span> : <span className="mk-tag">Included</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>

      <section className="mk-section" aria-labelledby="line-title">
        <div className="mk-wrap mk-two">
          <div>
            <h2 id="line-title">Connecting a phone line.</h2>
            <p className="mk-section__intro">
              Live calls need a phone number and SIP trunk from a provider you choose, or forwarding from the number you already have. Your
              line shows as connected only after a real call reaches the receptionist. Entering a number by itself doesn’t connect anything.
            </p>
          </div>
          <div>
            <h3>Not built yet</h3>
            <ul className="mk-note-list">
              <li>Text message (SMS) reminders.</li>
              <li>Languages other than English.</li>
              <li>A public price list. We quote during early access.</li>
            </ul>
          </div>
        </div>
      </section>

      <div className="mk-wrap">
        <div className="mk-close">
          <h2>Set it up and try a test call.</h2>
          <div className="mk-close__side">
            <div className="mk-close__actions">
              <Link href="/signup" className="mk-btn mk-btn--primary">
                Create your account
              </Link>
              <Link href="/pricing" className="mk-arrow">
                See how pricing works →
              </Link>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
