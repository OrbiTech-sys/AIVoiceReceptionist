import { Newsreader } from "next/font/google";

// Loaded only on the public and sign-in pages, so the signed-in app doesn't pay for it.
export const newsreader = Newsreader({
  subsets: ["latin"],
  axes: ["opsz"],
  variable: "--font-newsreader",
  display: "swap",
});
