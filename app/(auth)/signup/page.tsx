import type { Metadata } from "next";
import { SignupForm } from "./signup-form";
import { pageMetadata } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Create Your Account | OrbiTech AI Receptionist",
  description: "Create an OrbiTech account, add your hours and services, and try your AI receptionist on a test call. No credit card or AI API keys needed.",
  path: "/signup",
});

export default function SignupPage() {
  return <SignupForm />;
}
