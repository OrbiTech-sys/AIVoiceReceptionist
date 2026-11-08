import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { ImageResponse } from "next/og";

export const alt = "OrbiTech AI Receptionist: stop sending callers to voicemail.";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

// Read while the module loads, so the image is generated once at build time.
const [font, bell] = await Promise.all([
  readFile(join(process.cwd(), "app/_og/newsreader-latin-400-normal.woff")),
  readFile(join(process.cwd(), "components/marketing/assets/bell-og.png")),
]);

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div style={{ display: "flex", width: "100%", height: "100%", padding: "72px", background: "#f6f3ec", color: "#161b2b" }}>
        <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", width: "640px" }}>
          <div style={{ display: "flex", fontSize: 40, letterSpacing: "-0.01em" }}>OrbiTech</div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <div style={{ display: "flex", fontSize: 92, lineHeight: 1.02, letterSpacing: "-0.03em" }}>Stop sending callers to voicemail.</div>
            <div style={{ display: "flex", marginTop: 28, fontSize: 34, color: "#55524c" }}>The AI receptionist for appointment-based businesses.</div>
          </div>
        </div>
        {/* eslint-disable-next-line @next/next/no-img-element -- rendered to a PNG by ImageResponse, never shipped as HTML */}
        <img
          alt=""
          width={560}
          height={560}
          src={`data:image/png;base64,${bell.toString("base64")}`}
          style={{ position: "absolute", right: -6, top: 48 }}
        />
      </div>
    ),
    { ...size, fonts: [{ name: "Newsreader", data: font, weight: 400, style: "normal" }] },
  );
}
