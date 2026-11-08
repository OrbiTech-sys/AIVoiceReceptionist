import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";
import "@/components/marketing/marketing.css";
import bellPoster from "@/components/marketing/assets/bell.webp";
import { newsreader } from "@/components/marketing/fonts";
import { Brand } from "@/components/marketing/site-chrome";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className={`mk ${newsreader.variable}`}>
      <div className="mk-auth">
        <div className="mk-auth__panel">
          <div>
            <Brand />
          </div>
          <main className="mk-auth__body">{children}</main>
          <nav className="mk-auth__foot" aria-label="Footer">
            <Link href="/">Home</Link>
            <Link href="/privacy">Privacy</Link>
            <Link href="/terms">Terms</Link>
            <Link href="/contact">Contact</Link>
          </nav>
        </div>
        <aside className="mk-auth__side">
          <Image src={bellPoster} alt="" className="mk-auth__image" sizes="448px" />
          <p className="mk-auth__line">Your front desk, still answering.</p>
          <p className="mk-auth__small">Calls, bookings and alerts from your AI receptionist, all in one place.</p>
        </aside>
      </div>
    </div>
  );
}
