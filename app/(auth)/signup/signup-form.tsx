"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Button, Field, Input, Select } from "@/components/ui";
import { api, ApiError, errorMessage } from "@/lib/api";

const TYPES: Record<string, string> = {
  dental: "Dentist / dental clinic",
  medical: "Medical clinic",
  wellness: "Wellness / therapy",
  salon: "Salon / spa",
  property: "Property management",
  other: "Other appointment-based business",
};

function browserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function SignupForm() {
  const router = useRouter();
  const [form, setForm] = useState(() => ({
    full_name: "", email: "", password: "", business_name: "", business_type: "dental", timezone: browserTimezone(),
  }));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (!form.full_name.trim()) errs.full_name = "Enter your name.";
    if (!/^\S+@\S+\.\S+$/.test(form.email)) errs.email = "Enter a valid e-mail.";
    if (form.password.length < 10) errs.password = "At least 10 characters, with upper/lower case and a number.";
    if (form.business_name.trim().length < 2) errs.business_name = "Enter your business name.";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    setError(null);
    try {
      await api("/api/auth/signup", { body: form });
      router.replace("/onboarding");
    } catch (err) {
      if (err instanceof ApiError && Array.isArray(err.details)) {
        const fe: Record<string, string> = {};
        for (const d of err.details as { field: string; message: string }[]) fe[d.field] = d.message.replace(/^Value error, /, "");
        setErrors(fe);
      }
      setError(errorMessage(err).replace(/^\w+: Value error, /, ""));
      setBusy(false);
    }
  };

  return (
    <>
      <h1>Create your account</h1>
      <p className="mk-auth__sub">Setup takes about 10 minutes. No credit card or AI API keys needed.</p>
      <form onSubmit={submit} className="mk-form" noValidate>
        <Field label="Your name" htmlFor="name" error={errors.full_name}>
          <Input id="name" autoComplete="name" value={form.full_name} onChange={set("full_name")} />
        </Field>
        <Field label="Work e-mail" htmlFor="email" error={errors.email}>
          <Input id="email" type="email" autoComplete="email" value={form.email} onChange={set("email")} />
        </Field>
        <Field label="Password" htmlFor="pw" error={errors.password} hint="At least 10 characters, with upper/lower case and a number.">
          <Input id="pw" type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />
        </Field>
        <Field label="Business name" htmlFor="biz" error={errors.business_name}>
          <Input id="biz" autoComplete="organization" value={form.business_name} onChange={set("business_name")} />
        </Field>
        <Field label="Business type" htmlFor="type">
          <Select id="type" value={form.business_type} onChange={set("business_type")}>
            {Object.entries(TYPES).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </Select>
        </Field>
        {error && (
          <p role="alert" className="mk-form__error">
            {error}
          </p>
        )}
        <Button type="submit" variant="primary" className="w-full" loading={busy}>
          Create account
        </Button>
      </form>
      <p className="mk-auth__alt">
        By creating an account you accept our{" "}
        <Link className="mk-text-link" href="/terms">
          terms
        </Link>{" "}
        and{" "}
        <Link className="mk-text-link" href="/privacy">
          privacy policy
        </Link>
        .
      </p>
      <p className="mk-auth__alt">
        Already have an account?{" "}
        <Link className="mk-text-link" href="/login">
          Sign in
        </Link>
      </p>
    </>
  );
}
