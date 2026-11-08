import type { Viewport } from "next";
import type { ReactNode } from "react";
import "@/components/marketing/marketing.css";
import { newsreader } from "@/components/marketing/fonts";
import { JsonLd, siteSchema } from "@/components/marketing/json-ld";
import { SiteFooter, SiteHeader } from "@/components/marketing/site-chrome";

export const viewport: Viewport = { themeColor: "#f6f3ec" };

export default function MarketingLayout({ children }: { children: ReactNode }) {
  return (
    <div className={`mk ${newsreader.variable}`}>
      <JsonLd data={siteSchema} />
      <SiteHeader />
      <main className="mk-main">{children}</main>
      <SiteFooter />
    </div>
  );
}
