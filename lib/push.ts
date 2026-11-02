"use client";

import { api } from "./api";

export function pushSupported() {
  return typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

export async function registerServiceWorker() {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) return null;
  try {
    return await navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" });
  } catch {
    return null;
  }
}

function urlB64ToUint8Array(base64: string) {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export async function currentSubscription() {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

export async function subscribePush(): Promise<{ ok: boolean; message: string }> {
  if (!pushSupported()) return { ok: false, message: "This browser does not support push notifications." };
  if (!window.isSecureContext) return { ok: false, message: "Push notifications need HTTPS (or localhost)." };
  const perm = await Notification.requestPermission();
  if (perm !== "granted") return { ok: false, message: "Notification permission was not granted." };
  const reg = (await registerServiceWorker()) ?? (await navigator.serviceWorker.ready);
  const { public_key } = await api<{ public_key: string }>("/api/notifications/push/key");
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8Array(public_key) });
  }
  const json = sub.toJSON();
  await api("/api/notifications/push/subscribe", { body: { endpoint: json.endpoint, keys: json.keys ?? {} } });
  return { ok: true, message: "Push notifications are on for this device." };
}

export async function unsubscribePush() {
  const sub = await currentSubscription();
  if (!sub) return;
  const json = sub.toJSON();
  await api("/api/notifications/push/unsubscribe", { body: { endpoint: json.endpoint, keys: json.keys ?? {} } }).catch(() => {});
  await sub.unsubscribe();
}
