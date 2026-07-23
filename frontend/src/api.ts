export class AuthError extends Error {}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const opts: RequestInit = { method, headers, credentials: "same-origin" };
  if (method !== "GET") headers["X-Solar-CSRF"] = "1";
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (res.status === 401) throw new AuthError("unauthorized");
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface Plant {
  id: number; name: string; platform: "solaredge" | "growatt" | "sma";
  auth_mode: "password" | "token"; username: string | null;
  has_password: boolean; has_token: boolean;
  tariff_per_kwh: number | null; currency: string | null; enabled: boolean;
  last_test_at: string | null; last_test_ok: boolean | null; last_test_error: string | null;
}
export interface Settings { model: string | null; max_input_tokens: number; output_language: string; }
export interface Schedule {
  id: number; time_of_day: string; days_of_week: string;
  time_range: TimeRange; enabled: boolean;
}
export type TimeRange = "snapshot" | "30d" | "12mo" | "all";
export type RunStatus = "running" | "success" | "partial" | "failed" | "cancelled" | "interrupted";
export interface Run {
  id: number; status: RunStatus; trigger: "manual" | "scheduled";
  time_range: TimeRange; started_at: string; finished_at: string | null;
  report_path: string | null; log_path: string; plant_id: number | null;
  plants_summary: { name: string; ok: boolean; reason?: string }[] | null;
  skipped_plants: { name: string; reason: string }[] | null;
  notes: Record<string, unknown> | null; error: string | null;
  progress?: { plants: Record<string, string>; status: string };
}
export interface DeviceSnapshot {
  device_id: string;
  device_type: string;
  model: string | null;
  manufacturer: string | null;
  status: string;
  current_power_kw: number | null;
  energy_lifetime_kwh: number | null;
  temperature_c: number | null;
  last_seen_local: string | null;
  fetched_at_utc: string;
}
export interface AlertSnapshot {
  alert_id: string;
  severity: string;
  code: string | null;
  message: string | null;
  timestamp_local: string | null;
  resolved: number | null;
  fetched_at_utc: string;
}
export interface SeriesPoint {
  timestamp_local: string;
  power_kw?: number | null;
  energy_kwh?: number | null;
}

export const api = {
  status: () => req<{ setup_required: boolean; authenticated: boolean }>("GET", "/api/auth/status"),
  setup: (token: string, password: string) => req("POST", "/api/auth/setup", { token, password }),
  login: (password: string) => req("POST", "/api/auth/login", { password }),
  logout: () => req("POST", "/api/auth/logout"),
  changePassword: (oldPw: string, newPw: string) => req("PUT", "/api/auth/password", { old: oldPw, new: newPw }),

  plants: () => req<Plant[]>("GET", "/api/plants"),
  createPlant: (data: Partial<Plant> & { password?: string; token?: string }) =>
    req<{ id: number }>("POST", "/api/plants", data),
  updatePlant: (id: number, data: Partial<Plant> & { password?: string; token?: string }) =>
    req("PUT", `/api/plants/${id}`, data),
  deletePlant: (id: number) => req("DELETE", `/api/plants/${id}`),
  testPlant: (id: number) => req<{ ok: boolean; error: string | null }>("POST", `/api/plants/${id}/test`),

  settings: () => req<Settings>("GET", "/api/settings"),
  saveSettings: (s: Settings) => req("PUT", "/api/settings", s),

  schedules: () => req<Schedule[]>("GET", "/api/schedules"),
  createSchedule: (s: Omit<Schedule, "id">) => req<{ id: number }>("POST", "/api/schedules", s),
  updateSchedule: (id: number, s: Partial<Schedule>) => req("PUT", `/api/schedules/${id}`, s),
  deleteSchedule: (id: number) => req("DELETE", `/api/schedules/${id}`),

  runs: () => req<Run[]>("GET", "/api/runs"),
  run: (id: number) => req<Run>("GET", `/api/runs/${id}`),
  startRun: (time_range: TimeRange, plantId?: number | null) =>
    req<{ id: number }>("POST", "/api/runs", { time_range, plant_id: plantId ?? null }),
  cancelRun: (id: number) => req<{ cancelled: boolean }>("POST", `/api/runs/${id}/cancel`),
  runLog: (id: number) => req<{ log: string }>("GET", `/api/runs/${id}/log`),
  reportUrl: (id: number) => `/api/runs/${id}/report`,

  runImport: () => req<Record<string, unknown>>("POST", "/api/import"),

  plantDevices: (id: number) => req<DeviceSnapshot[]>("GET", `/api/plants/${id}/devices`),
  plantAlerts: (id: number, limit = 100) =>
    req<AlertSnapshot[]>("GET", `/api/plants/${id}/alerts?limit=${limit}`),
  plantPower: (id: number, since?: string) =>
    req<SeriesPoint[]>("GET", `/api/plants/${id}/power${since ? `?since=${since}` : ""}`),
  plantEnergy: (id: number, since?: string, granularity?: string) => {
    const params = new URLSearchParams();
    if (since) params.set("since", since);
    if (granularity) params.set("granularity", granularity);
    const qs = params.toString();
    return req<SeriesPoint[]>("GET", `/api/plants/${id}/energy${qs ? `?${qs}` : ""}`);
  },
};
