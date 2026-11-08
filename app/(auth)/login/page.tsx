import type { Metadata } from "next";
import { LoginForm } from "./login-form";
import { pageMetadata } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Sign In | OrbiTech AI Receptionist",
  description: "Sign in to manage your AI receptionist, appointments, calls and alerts.",
  path: "/login",
  index: false,
});

export default function LoginPage() {
  return <LoginForm />;
}
