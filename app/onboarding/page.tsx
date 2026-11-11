"use client";

import { Bot, Check, ChevronLeft } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { HoursEditor } from "@/components/settings/hours-editor";
import { KnowledgeEditor } from "@/components/settings/knowledge-editor";
import { PersonalityForm } from "@/components/settings/personality-form";
import { PolicyForm } from "@/components/settings/policy-form";
import { ProfileForm } from "@/components/settings/profile-form";
import { ServicesEditor, StaffEditor } from "@/components/settings/services-staff";
import { PhoneSetup } from "@/components/phone-setup";
import { Button, Card, cx, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Me } from "@/lib/types";

const STEPS = [
  { title: "Business profile", desc: "Basic details callers ask about." },
  { title: "Working hours", desc: "When you're open, breaks and holidays." },
  { title: "Receptionist personality", desc: "Name, voice, tone and greeting." },
  { title: "Business knowledge", desc: "Services, prices and common questions." },
  { title: "Appointments", desc: "Staff calendars and booking rules." },
  { title: "Phone connection", desc: "Test in your browser, then connect a line." },
];

export default function Onboarding() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [step, setStep] = useState(1);
  const [phoneMode, setPhoneMode] = useState<"demo" | "live">("demo");
  const [finishing, setFinishing] = useState(false);

  useEffect(() => {
    api<Me>("/api/auth/me")
      .then((m) => {
        if (m.role !== "owner") return router.replace("/overview");
        setMe(m);
        setStep(Math.min(6, Math.max(1, m.tenant?.onboarding_step ?? 1)));
      })
      .catch((e) => e instanceof ApiError && e.status === 401 && router.replace("/login?next=/onboarding"));
  }, [router]);

  const go = async (n: number) => {
    setStep(n);
    window.scrollTo({ top: 0 });
    await api("/api/business/onboarding", { body: { step: n } }).catch(() => {});
  };
  const finish = async () => {
    setFinishing(true);
    await api("/api/business/onboarding", { body: { step: 7, completed: true } });
    router.replace(phoneMode === "demo" ? "/receptionist?tab=test" : "/overview");
  };

  if (!me) return <div className="grid min-h-screen place-items-center"><Spinner className="size-6" /></div>;

  const next = () => go(step + 1);
  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="grid size-8 place-items-center rounded-lg bg-accent text-accent-fg"><Bot className="size-4" /></div>
          <div>
            <div className="text-sm font-semibold">Set up {me.tenant?.name}</div>
            <div className="text-xs text-muted">Step {step} of {STEPS.length} · you can change everything later</div>
          </div>
        </div>
        <Button variant="ghost" size="sm" onClick={finish}>Skip for now</Button>
      </div>

      <ol className="mb-6 grid grid-cols-6 gap-1.5" aria-label="Setup progress">
        {STEPS.map((s, i) => (
          <li key={s.title}>
            <button onClick={() => go(i + 1)} className="w-full text-left" aria-current={step === i + 1 ? "step" : undefined}>
              <div className={cx("h-1.5 rounded-full", i + 1 < step ? "bg-accent" : i + 1 === step ? "bg-accent/60" : "bg-border")} />
              <div className={cx("mt-1.5 hidden text-xs sm:block", i + 1 === step ? "font-medium text-text" : "text-muted")}>{s.title}</div>
            </button>
          </li>
        ))}
      </ol>

      <Card title={`${step}. ${STEPS[step - 1].title}`} description={STEPS[step - 1].desc}>
        {step === 1 && <ProfileForm submitLabel="Save and continue" onSaved={next} />}
        {step === 2 && <HoursEditor submitLabel="Save and continue" onSaved={next} />}
        {step === 3 && <PersonalityForm compact submitLabel="Save and continue" onSaved={next} />}
        {step === 4 && (
          <div className="space-y-8">
            <section>
              <h3 className="mb-2 text-sm font-semibold">Services & prices</h3>
              <ServicesEditor />
            </section>
            <section>
              <h3 className="mb-2 text-sm font-semibold">Frequently asked questions & policies</h3>
              <KnowledgeEditor />
            </section>
            <Button variant="primary" onClick={next}>Continue</Button>
          </div>
        )}
        {step === 5 && (
          <div className="space-y-8">
            <section>
              <h3 className="mb-2 text-sm font-semibold">Staff & their availability</h3>
              <StaffEditor />
            </section>
            <section>
              <h3 className="mb-2 text-sm font-semibold">Booking rules</h3>
              <PolicyForm submitLabel="Save and continue" onSaved={next} />
            </section>
          </div>
        )}
        {step === 6 && (
          <div className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-2">
              {([
                ["demo", "Demo mode", "Test the receptionist with your browser's microphone. No phone number or provider needed. Free."],
                ["live", "Live phone mode", "Answer real calls on your business phone number. Needs a phone number from a phone provider (they bill you for it)."],
              ] as const).map(([id, title, desc]) => (
                <button key={id} onClick={() => setPhoneMode(id)}
                  className={cx("rounded-xl border p-4 text-left transition-colors", phoneMode === id ? "border-accent bg-accent-soft" : "border-border hover:bg-surface-2")}>
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{title}</span>
                    {phoneMode === id && <Check className="size-4 text-accent" />}
                  </div>
                  <p className="mt-1 text-[13px] text-muted">{desc}</p>
                </button>
              ))}
            </div>
            {phoneMode === "demo" ? (
              <p className="text-[13px] text-muted">After finishing you&apos;ll land on the test call screen. You can connect a phone line any time from <span className="font-medium text-text">Integrations</span>.</p>
            ) : (
              <PhoneSetup />
            )}
            <Button variant="primary" onClick={finish} loading={finishing}>Finish setup</Button>
          </div>
        )}
      </Card>
      {step > 1 && (
        <button onClick={() => go(step - 1)} className="mt-4 inline-flex items-center gap-1 text-[13px] text-muted hover:text-text">
          <ChevronLeft className="size-4" /> Back
        </button>
      )}
    </div>
  );
}
