"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { NAV_LINKS } from "@/lib/site";

export function NavLinks() {
  const pathname = usePathname();
  return (
    <nav className="mk-bar__links" aria-label="Main">
      {NAV_LINKS.map((l) => (
        <Link key={l.href} href={l.href} aria-current={pathname === l.href ? "page" : undefined}>
          {l.label}
        </Link>
      ))}
    </nav>
  );
}

export function AuthLinks() {
  return (
    <div className="mk-bar__auth">
      <Link href="/login" className="mk-signin">
        Sign in
      </Link>
      <Link href="/signup" className="mk-btn mk-btn--primary mk-btn--sm">
        Sign up
      </Link>
    </div>
  );
}

export function MobileMenu() {
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        toggle.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const close = () => setOpen(false);

  return (
    <>
      <button
        ref={toggle}
        type="button"
        className="mk-menu-toggle"
        aria-expanded={open}
        aria-controls="mk-menu"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "Close" : "Menu"}
      </button>
      {open && (
        <nav id="mk-menu" className="mk-menu" aria-label="Menu">
          <ul className="mk-wrap">
            {NAV_LINKS.map((l) => (
              <li key={l.href}>
                <Link href={l.href} onClick={close}>
                  {l.label}
                </Link>
              </li>
            ))}
            <li>
              <Link href="/login" onClick={close}>
                Sign in
              </Link>
            </li>
            <li>
              <Link href="/signup" className="mk-btn mk-btn--primary" onClick={close}>
                Sign up
              </Link>
            </li>
          </ul>
        </nav>
      )}
    </>
  );
}
