import { Bot } from "lucide-react";
import type { ReactNode } from "react";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="flex flex-col justify-center px-4 py-10 sm:px-10">
        <div className="mx-auto w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2">
            <div className="grid size-8 place-items-center rounded-lg bg-accent text-accent-fg">
              <Bot className="size-4" aria-hidden />
            </div>
            <span className="font-semibold">OrbiTech AI Receptionist</span>
          </div>
          {children}
        </div>
      </div>
      <div className="hidden border-l border-border bg-surface lg:flex lg:flex-col lg:justify-center lg:px-14">
        <p className="max-w-md text-2xl font-semibold leading-snug tracking-tight">Never miss another customer call.</p>
        <p className="mt-3 max-w-md text-muted">
          Your AI receptionist answers questions, books appointments and keeps your business organized — even when your team is busy.
        </p>
        <ul className="mt-8 space-y-3 text-sm text-muted">
          <li>• Runs on local, open-source speech models — no paid AI APIs</li>
          <li>• Real appointment booking with conflict protection</li>
          <li>• Instant alerts for bookings, cancellations and urgent calls</li>
        </ul>
      </div>
    </div>
  );
}
