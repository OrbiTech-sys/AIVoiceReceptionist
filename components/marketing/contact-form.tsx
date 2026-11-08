"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Field, Input, Select, Textarea } from "@/components/ui";
import { api, ApiError, errorMessage } from "@/lib/api";

const TOPICS: Record<string, string> = {
  pricing: "Pricing",
  setup: "Connecting my phone line",
  support: "Help with my account",
  other: "Something else",
};

export function ContactForm() {
  const topicRef = useRef<HTMLSelectElement>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);

  // Links such as /contact?topic=pricing preselect the subject. Reading the URL here keeps the form in the static HTML.
  useEffect(() => {
    const topic = new URLSearchParams(window.location.search).get("topic");
    if (topic && topic in TOPICS && topicRef.current) topicRef.current.value = topic;
  }, []);

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget).entries()) as Record<string, string>;
    const errs: Record<string, string> = {};
    if (!data.name?.trim()) errs.name = "Enter your name.";
    if (!/^\S+@\S+\.\S+$/.test(data.email ?? "")) errs.email = "Enter a valid e-mail address so we can reply.";
    if ((data.message ?? "").trim().length < 10) errs.message = "Add a little more detail, at least 10 characters.";
    setErrors(errs);
    setError(null);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      await api("/api/contact", { body: data });
      setSentTo(data.email);
    } catch (err) {
      if (err instanceof ApiError && Array.isArray(err.details)) {
        const fe: Record<string, string> = {};
        for (const d of err.details as { field: string; message: string }[]) fe[d.field] = d.message.replace(/^Value error, /, "");
        setErrors(fe);
      }
      setError(`We couldn’t send your message. ${errorMessage(err)}`);
      setBusy(false);
    }
  };

  if (sentTo) {
    return (
      <div className="mk-form__done" role="status">
        <h2>Message received.</h2>
        <p>
          We’ll reply to <strong>{sentTo}</strong>.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="mk-form" noValidate>
      <div className="mk-form__pair">
        <Field label="Your name" htmlFor="c-name" error={errors.name}>
          <Input id="c-name" name="name" autoComplete="name" maxLength={120} aria-invalid={!!errors.name} />
        </Field>
        <Field label="Work e-mail" htmlFor="c-email" error={errors.email}>
          <Input id="c-email" name="email" type="email" autoComplete="email" maxLength={254} aria-invalid={!!errors.email} />
        </Field>
      </div>
      <div className="mk-form__pair">
        <Field label="Business name (optional)" htmlFor="c-business" error={errors.business}>
          <Input id="c-business" name="business" autoComplete="organization" maxLength={160} />
        </Field>
        <Field label="What’s this about?" htmlFor="c-topic">
          <Select id="c-topic" name="topic" ref={topicRef} defaultValue="other">
            {Object.entries(TOPICS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <Field label="Message" htmlFor="c-message" error={errors.message}>
        <Textarea id="c-message" name="message" rows={6} maxLength={4000} aria-invalid={!!errors.message} />
      </Field>
      {/* Left empty by people; bots that fill it are dropped by the server. */}
      <div className="mk-form__trap" aria-hidden="true">
        <label htmlFor="c-website">Website</label>
        <input id="c-website" name="website" type="text" tabIndex={-1} autoComplete="off" />
      </div>
      {error && (
        <p role="alert" className="mk-form__error">
          {error}
        </p>
      )}
      <div>
        <button type="submit" className="mk-btn mk-btn--primary" disabled={busy}>
          {busy ? "Sending…" : "Send message"}
        </button>
      </div>
    </form>
  );
}
