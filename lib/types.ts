export type Role = "owner" | "staff";

export interface Me {
  user: { id: string; email: string; full_name: string; is_platform_admin: boolean };
  memberships: { tenant_id: string; tenant_name: string; role: Role }[];
  tenant: null | {
    id: string;
    name: string;
    business_type: string;
    timezone: string;
    onboarding_completed: boolean;
    onboarding_step: number;
    is_demo: boolean;
  };
  role: Role | null;
  csrf_token: string;
}

export interface Contact {
  id: string;
  name: string;
  phone: string | null;
  phone_display: string;
  email: string;
  notes: string;
  follow_up_status: "none" | "needed" | "done";
  last_interaction_at: string | null;
  created_at: string;
  appointment_count?: number;
}

export type AppointmentStatus = "pending" | "confirmed" | "completed" | "cancelled" | "no_show";

export interface Appointment {
  id: string;
  status: AppointmentStatus;
  source: "ai_call" | "dashboard" | "manual";
  start_at: string;
  end_at: string;
  start_local: string;
  end_local: string;
  service: { id: string; name: string; duration_min: number };
  staff: { id: string; name: string } | null;
  contact: Contact;
  notes: string;
  cancel_reason: string;
  call_id: string | null;
  created_at: string;
}

export interface Call {
  id: string;
  channel: "browser_demo" | "phone";
  caller_number: string | null;
  caller_number_display: string;
  caller_name: string;
  contact_id: string | null;
  started_at: string;
  ended_at: string | null;
  duration_sec: number | null;
  status: "in_progress" | "completed" | "failed";
  outcome: "answered" | "missed" | "transferred" | "failed" | null;
  intent: string;
  summary: string;
  follow_up_status: "none" | "needed" | "done";
  error_detail: string;
  appointment_id: string | null;
  is_demo: boolean;
  has_recording: boolean;
}

export interface CallDetail extends Call {
  events: { ts: string; kind: string; text: string; data: Record<string, unknown> | null }[];
  appointment: Appointment | null;
}

export interface Service {
  id: string;
  name: string;
  description: string;
  duration_min: number;
  buffer_min: number | null;
  price_text: string;
  is_active: boolean;
}

export interface Staff {
  id: string;
  name: string;
  title: string;
  email: string;
  is_active: boolean;
  service_ids: string[];
  availability: { weekday: number; start: string; end: string }[];
}

export interface Notification {
  id: string;
  kind: string;
  severity: "info" | "urgent";
  title: string;
  body: string;
  link: string;
  entity_type: string;
  entity_id: string;
  event_time: string | null;
  created_at: string;
  read: boolean;
  push_status: string;
}

export interface Slot {
  start_at: string;
  end_at: string;
  start_local: string;
  staff_id: string | null;
  staff_name: string | null;
}

export interface Readiness {
  asr: { ready: boolean; model: string | null; error: string; latency_ms: number | null };
  tts: { ready: boolean; model: string | null; error: string; latency_ms: number | null; chars_left?: number | null; chars_cap?: number | null };
  vad: { ready: boolean; model: string };
  llm: { ready: boolean; configured: boolean; provider?: string; error: string; latency_ms: number | null };
  voice_ready: boolean;
}

export interface Voice {
  id: string;
  label: string;
  gender: string;
  language: string;
  verified: boolean;
}

export interface Paged<T> {
  items: T[];
  total: number;
}
