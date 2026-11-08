import Link from "next/link";
import { AuthLinks, MobileMenu, NavLinks } from "@/components/marketing/site-nav";
import { COMPANY_NAME, NAV_LINKS } from "@/lib/site";

export function BrandMark() {
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="var(--color-accent)" />
      <circle cx="32" cy="32" r="21.5" fill="none" stroke="var(--color-accent-ink)" strokeWidth="4.5" />
      <circle cx="19.2" cy="32" r="4.5" fill="var(--color-accent-ink)" />
      <circle cx="32" cy="32" r="4.5" fill="var(--color-accent-ink)" />
      <circle cx="44.8" cy="32" r="4.5" fill="var(--color-accent-ink)" />
    </svg>
  );
}

export function Brand() {
  return (
    <Link href="/" className="mk-brand" aria-label="OrbiTech AI Receptionist home">
      <BrandMark />
      OrbiTech
    </Link>
  );
}

export function SiteHeader() {
  return (
    <>
      <div className="mk-banner">
        <div className="mk-wrap mk-banner__inner">
          <span>
            Now in early access<span className="mk-banner__long"> for US appointment-based businesses</span>.
          </span>
          <Link href="/contact?topic=pricing">Ask about pricing</Link>
        </div>
      </div>
      <header className="mk-bar">
        <div className="mk-wrap mk-bar__inner">
          <Brand />
          <NavLinks />
          <div className="mk-bar__right">
            <AuthLinks />
            <MobileMenu />
          </div>
        </div>
      </header>
    </>
  );
}

export function SiteFooter() {
  return (
    <footer className="mk-foot">
      <div className="mk-wrap">
        <p className="mk-foot__mark">OrbiTech</p>
        <p className="mk-foot__tag">The AI receptionist for businesses that run on appointments.</p>
        <nav className="mk-foot__links" aria-label="Footer">
          {NAV_LINKS.map((l) => (
            <Link key={l.href} href={l.href}>
              {l.label}
            </Link>
          ))}
          <Link href="/signup">Create account</Link>
          <Link href="/login">Sign in</Link>
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms</Link>
        </nav>
        <p className="mk-foot__legal">© {COMPANY_NAME}. Speech runs on open-source models: Whisper, Silero VAD and Kokoro.</p>
      </div>
    </footer>
  );
}
