"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Button, Field, Input } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import type { Me } from "@/lib/types";

export function LoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await api<Me>("/api/auth/login", { body: { email, password } });
      const next = new URLSearchParams(window.location.search).get("next");
      const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : "/overview";
      router.replace(me.tenant && !me.tenant.onboarding_completed && me.role === "owner" ? "/onboarding" : safeNext);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  return (
    <>
      <h1>Sign in</h1>
      <p className="mk-auth__sub">Welcome back. Sign in to manage your receptionist.</p>
      <form onSubmit={submit} className="mk-form" noValidate>
        <Field label="E-mail" htmlFor="email">
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password" htmlFor="password">
          <div className="mk-pw">
            <Input
              id="password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button type="button" aria-pressed={showPassword} aria-label="Show password" onClick={() => setShowPassword((v) => !v)}>
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>
        </Field>
        {error && (
          <p role="alert" className="mk-form__error">
            {error}
          </p>
        )}
        <Button type="submit" variant="primary" className="w-full" loading={busy}>
          Sign in
        </Button>
      </form>
      <p className="mk-auth__alt">
        New here?{" "}
        <Link className="mk-text-link" href="/signup">
          Create your business account
        </Link>
      </p>
      <p className="mk-auth__alt">
        Can’t sign in?{" "}
        <Link className="mk-text-link" href="/contact?topic=support">
          Contact us
        </Link>
      </p>
    </>
  );
}
