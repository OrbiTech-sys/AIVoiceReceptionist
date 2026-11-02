"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { AppShell } from "@/components/app-shell";
import { SessionProvider } from "@/components/session";
import { Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { registerServiceWorker } from "@/lib/push";
import type { Me } from "@/lib/types";

export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api<Me>("/api/auth/me")
      .then((m) => {
        if (!alive) return;
        if (!m.tenant) {
          setError("Your account is not linked to a business yet.");
          return;
        }
        if (!m.tenant.onboarding_completed && m.role === "owner") {
          router.replace("/onboarding");
          return;
        }
        setMe(m);
        void registerServiceWorker();
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          router.replace(`/login?next=${encodeURIComponent(window.location.pathname)}`);
        } else if (alive) setError((e as Error).message);
      });
    return () => {
      alive = false;
    };
  }, [router]);

  if (error) return <div className="grid min-h-screen place-items-center p-6 text-sm text-muted">{error}</div>;
  if (!me)
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner className="size-6" />
      </div>
    );
  return (
    <SessionProvider me={me}>
      <AppShell>{children}</AppShell>
    </SessionProvider>
  );
}
