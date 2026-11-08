import type { Metadata } from "next";
import Link from "next/link";
import { BellStage } from "@/components/marketing/bell-stage";
import { FAQ, LEDGER } from "@/components/marketing/content";
import { JsonLd, softwareSchema } from "@/components/marketing/json-ld";
import { pageMetadata, SITE_DESCRIPTION } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "AI Receptionist for Appointment-Based Businesses | OrbiTech",
  description: SITE_DESCRIPTION,
  path: "/",
});

const STAGES = [
  {
    time: "7:42 PM",
    title: "The phone rings after you’ve gone home.",
    body: "Calls reach the receptionist through your phone line or forwarding. It picks up with the greeting and voice you chose, and says up front that it’s automated.",
    lines: [{ who: "Receptionist", say: "Thank you for calling Maple Street Dental. I’m an automated assistant. How can I help you today?" }],
  },
  {
    time: "7:42 PM",
    title: "It answers from what you told it.",
    body: "Hours, address, services, prices and your own answers to common questions. If the answer isn’t in there, it says so and offers a callback. It doesn’t guess.",
    lines: [
      { who: "Caller", say: "How much is a cleaning?" },
      { who: "Receptionist", say: "A dental cleaning is $120. Is there anything else I can help with?" },
    ],
  },
  {
    time: "7:43 PM",
    title: "It books a real slot.",
    body: "It checks your hours, breaks, buffers, holidays, notice rules and each staff calendar before offering a time. Name and number get read back. The caller hears a confirmation only after the booking is saved, and two callers can’t take the same slot.",
    lines: [
      { who: "Caller", say: "Can I come in next Tuesday?" },
      { who: "Receptionist", say: "Tuesday, I have 9:00 AM, 11:30 AM, or 2:30 PM. Which works best for you?" },
      { who: "Caller", say: "2:30, please." },
    ],
  },
  {
    time: "7:44 PM",
    title: "You get the summary.",
    body: "The call lands in your dashboard with a transcript and a one-line summary. The alert reaches you in the app, by push on your phone, or by e-mail, whichever you turned on.",
    lines: [{ who: "Your alert", say: "New appointment: cleaning, Tuesday at 2:30 PM." }],
  },
  {
    time: "Next week",
    title: "Plans change, and it handles that too.",
    body: "The same caller can phone back to reschedule or cancel. Your calendar updates, the freed slot opens up, and your team’s reminders move with it.",
    lines: [
      { who: "Caller", say: "I need to move my appointment." },
      { who: "Receptionist", say: "Sure. What day works better for you?" },
    ],
  },
];

const LIMITS = [
  { head: "It never invents a price, a policy or an open time.", body: "If you haven’t given it the answer, it says it doesn’t have one." },
  { head: "It doesn’t give medical, dental or legal advice.", body: "It offers a booking or a callback instead." },
  { head: "Emergencies come first.", body: "It says the message you wrote before anything else and sends your team an urgent alert." },
  { head: "Callers can’t talk it out of its rules.", body: "Requests to reveal data, change its instructions or act for another business are ignored." },
  { head: "When it can’t understand someone, it gets a person involved.", body: "After repeated misunderstandings it takes a message so your team can call back." },
  { head: "English only, for now.", body: "It’s better to say so here than to have a caller find out." },
];

export default function HomePage() {
  return (
    <>
      <JsonLd data={softwareSchema} />

      <section className="mk-hero" aria-labelledby="hero-title">
        <div className="mk-wrap mk-hero__grid">
          <div>
            <h1 id="hero-title">Stop sending callers to voicemail.</h1>
            <p className="mk-hero__lede mk-rise" style={{ "--i": 1 } as React.CSSProperties}>
              OrbiTech is an AI receptionist for businesses that run on appointments. It answers your phone, gives callers the facts you
              set, books a real slot on your calendar, then tells you what happened.
            </p>
            <div className="mk-hero__actions mk-rise" style={{ "--i": 2 } as React.CSSProperties}>
              <Link href="/signup" className="mk-btn mk-btn--primary">
                Create your account
              </Link>
              <Link href="/contact?topic=pricing" className="mk-arrow">
                Ask about pricing →
              </Link>
            </div>
            <p className="mk-hero__note mk-rise" style={{ "--i": 3 } as React.CSSProperties}>
              No credit card or AI API keys needed.
            </p>
          </div>
          <BellStage />
        </div>
      </section>

      <section className="mk-section--tight" aria-labelledby="call-title">
        <div className="mk-wrap">
          <h2 id="call-title">One call, start to finish.</h2>
          <p className="mk-section__intro">
            An example call to a dental office after closing time. Maple Street Dental is made up. The steps are what the product does.
          </p>
          <ol className="mk-call">
            {STAGES.map((s) => (
              <li key={s.title} className="mk-call__stage">
                <div>
                  <span className="mk-call__clock">{s.time}</span>
                  <h3>{s.title}</h3>
                  <p>{s.body}</p>
                </div>
                <div className="mk-script">
                  {s.lines.map((l) => (
                    <p key={l.say}>
                      <span className="mk-script__who">{l.who}</span>
                      <span className="mk-script__say">“{l.say}”</span>
                    </p>
                  ))}
                </div>
              </li>
            ))}
          </ol>
          <p>
            <Link href="/features" className="mk-arrow">
              See every feature and what it needs →
            </Link>
          </p>
        </div>
      </section>

      <section className="mk-section mk-section--band" aria-labelledby="limits-title">
        <div className="mk-wrap mk-limits">
          <div className="mk-limits__head">
            <h2 id="limits-title">Where it stops, on purpose.</h2>
            <p className="mk-section__intro">These rules live in the conversation engine, not in a prompt a caller could argue with.</p>
          </div>
          <ul className="mk-limits__list">
            {LIMITS.map((l) => (
              <li key={l.head}>
                <strong>{l.head}</strong> {l.body}
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="mk-section" aria-labelledby="fits-title">
        <div className="mk-wrap">
          <h2 id="fits-title" className="sr-only">
            Businesses it’s made for
          </h2>
          <p className="mk-fits">
            Made for dental offices, medical clinics, wellness and therapy practices, salons and spas, and property managers.{" "}
            <span>If your phone turns into appointments, it fits.</span>
          </p>
        </div>
      </section>

      <section className="mk-section--tight" aria-labelledby="cost-title">
        <div className="mk-wrap">
          <h2 id="cost-title">What you pay for, and what you don’t.</h2>
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
          <p style={{ marginTop: "var(--space-md)" }}>
            <Link href="/pricing" className="mk-arrow">
              How early access pricing works →
            </Link>
          </p>
        </div>
      </section>

      <section className="mk-section--tight" aria-labelledby="faq-title">
        <div className="mk-wrap">
          <h2 id="faq-title">Questions owners ask first.</h2>
          <div className="mk-faq">
            {FAQ.slice(0, 4).map((f) => (
              <details key={f.q}>
                <summary>{f.q}</summary>
                <p>{f.a}</p>
              </details>
            ))}
          </div>
          <p style={{ marginTop: "var(--space-md)" }}>
            <Link href="/faq" className="mk-arrow">
              Read all the questions →
            </Link>
          </p>
        </div>
      </section>

      <section aria-labelledby="close-title">
        <div className="mk-wrap">
          <div className="mk-close">
            <h2 id="close-title">Try it on a test call first.</h2>
            <div className="mk-close__side">
              <p>Add your hours and services, then talk to your receptionist from your browser. Connect a phone line when you’re happy with it.</p>
              <div className="mk-close__actions">
                <Link href="/signup" className="mk-btn mk-btn--primary">
                  Create your account
                </Link>
                <Link href="/contact" className="mk-arrow">
                  Talk to us →
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
