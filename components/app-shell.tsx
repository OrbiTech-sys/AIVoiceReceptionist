"use client";

import {
  Bell,
  Bot,
  Building2,
  CalendarDays,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  PhoneCall,
  Plug,
  ServerCog,
  Sun,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { useSession } from "./session";
import { cx } from "./ui";

const NAV = [
  { href: "/overview", label: "Overview", icon: LayoutDashboard },
  { href: "/calls", label: "Calls", icon: PhoneCall },
  { href: "/appointments", label: "Appointments", icon: CalendarDays },
  { href: "/contacts", label: "Contacts", icon: Users },
  { href: "/receptionist", label: "AI Receptionist", icon: Bot },
  { href: "/notifications", label: "Notifications", icon: Bell },
  { href: "/settings", label: "Business Settings", icon: Building2 },
  { href: "/integrations", label: "Integrations", icon: Plug },
];

const STATUS: Record<string, { label: string; dot: string; text: string }> = {
  online: { label: "Online", dot: "bg-success", text: "text-success" },
  demo: { label: "Demo mode", dot: "bg-accent", text: "text-accent" },
  offline: { label: "Offline", dot: "bg-danger", text: "text-danger" },
  setup_required: { label: "Setup required", dot: "bg-warning", text: "text-warning" },
};

export function ReceptionistStatus({ compact }: { compact?: boolean }) {
  const { status } = useSession();
  const s = STATUS[status?.state ?? ""] ?? { label: "Checking…", dot: "bg-subtle", text: "text-muted" };
  return (
    <Link
      href="/receptionist"
      title={status?.detail}
      className={cx("inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1 text-[13px] font-medium", s.text)}
    >
      <span className={cx("size-2 rounded-full", s.dot, status?.state === "online" && "vr-pulse")} aria-hidden />
      {compact ? s.label : <>Receptionist: {s.label}</>}
    </Link>
  );
}

function ThemeToggle() {
  const [dark, setDark] = useState(() => typeof document !== "undefined" && document.documentElement.classList.contains("dark"));
  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("vr-theme", next ? "dark" : "light");
    } catch {}
  };
  return (
    <button onClick={toggle} className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-text" aria-label={dark ? "Use light theme" : "Use dark theme"}>
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  const { unread, me } = useSession();
  const items = me.user.is_platform_admin ? [...NAV, { href: "/admin", label: "Platform admin", icon: ServerCog }] : NAV;
  return (
    <nav className="space-y-0.5" aria-label="Primary">
      {items.map(({ href, label, icon: Icon }) => {
        const active = path === href || path.startsWith(href + "/");
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cx(
              "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors",
              active ? "bg-accent-soft font-medium text-accent" : "text-muted hover:bg-surface-2 hover:text-text",
            )}
          >
            <Icon className="size-4 shrink-0" aria-hidden />
            <span className="flex-1">{label}</span>
            {href === "/notifications" && unread > 0 && (
              <span className="rounded-full bg-accent px-1.5 text-[11px] font-semibold leading-4.5 text-accent-fg">{unread > 99 ? "99+" : unread}</span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { me } = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const logout = async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    router.replace("/login");
  };
  const brand = (
    <div className="flex items-center gap-2 px-2.5">
      <div className="grid size-7 place-items-center rounded-lg bg-accent text-accent-fg">
        <Bot className="size-4" aria-hidden />
      </div>
      <div className="min-w-0 leading-tight">
        <div className="truncate text-[13px] font-semibold">{me.tenant?.name ?? "OrbiTech"}</div>
        <div className="text-[11px] text-muted">AI Receptionist</div>
      </div>
    </div>
  );
  const footer = (
    <div className="space-y-2 border-t border-border pt-3">
      <div className="px-2.5 text-[12px] text-muted">
        <div className="truncate font-medium text-text">{me.user.full_name || me.user.email}</div>
        <div className="truncate">{me.user.email} · {me.role === "owner" ? "Owner" : "Staff"}</div>
      </div>
      <button onClick={logout} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-muted hover:bg-surface-2 hover:text-text">
        <LogOut className="size-4" aria-hidden /> Sign out
      </button>
    </div>
  );
  return (
    <div className="min-h-screen lg:pl-60">
      <aside className="fixed inset-y-0 left-0 hidden w-60 flex-col gap-5 border-r border-border bg-surface px-3 py-4 lg:flex">
        {brand}
        <div className="flex-1 overflow-y-auto">
          <NavLinks />
        </div>
        {footer}
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/30" onClick={() => setOpen(false)} />
          <aside className="vr-fade-in absolute inset-y-0 left-0 flex w-72 max-w-[85%] flex-col gap-5 bg-surface px-3 py-4 shadow-xl">
            <div className="flex items-center justify-between">
              {brand}
              <button onClick={() => setOpen(false)} className="rounded-lg p-2 text-muted" aria-label="Close menu">
                <X className="size-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              <NavLinks onNavigate={() => setOpen(false)} />
            </div>
            {footer}
          </aside>
        </div>
      )}

      <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border bg-bg/90 px-4 backdrop-blur sm:px-6">
        <button onClick={() => setOpen(true)} className="-ml-2 rounded-lg p-2 text-muted lg:hidden" aria-label="Open menu">
          <Menu className="size-5" />
        </button>
        <div className="flex-1 truncate text-sm font-medium lg:hidden">{me.tenant?.name}</div>
        <div className="hidden flex-1 lg:block">
          {me.tenant?.is_demo && <span className="text-[12px] text-muted">Demo business — sample data is labelled as demo</span>}
        </div>
        <div className="hidden sm:block">
          <ReceptionistStatus />
        </div>
        <div className="sm:hidden">
          <ReceptionistStatus compact />
        </div>
        <Link href="/notifications" className="relative rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-text" aria-label="Notifications">
          <Bell className="size-4" />
          <UnreadDot />
        </Link>
        <ThemeToggle />
      </header>
      <main className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6">{children}</main>
    </div>
  );
}

function UnreadDot() {
  const { unread } = useSession();
  if (!unread) return null;
  return <span className="absolute right-1.5 top-1.5 size-2 rounded-full bg-danger" aria-label={`${unread} unread`} />;
}
