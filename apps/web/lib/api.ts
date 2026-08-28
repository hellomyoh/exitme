/**
 * API 클라이언트 — access 토큰은 메모리 보관(ARCHITECTURE §6), 만료 시 /api/auth/refresh 재발급.
 * 모든 호출은 same-origin /api/* (nginx 프록시).
 */
let accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function hasToken(): boolean {
  return accessToken !== null;
}

async function tryRefresh(): Promise<boolean> {
  const res = await fetch("/api/auth/refresh", { method: "POST", credentials: "include" });
  if (!res.ok) return false;
  const body = (await res.json()) as { access_token: string };
  accessToken = body.access_token;
  return true;
}

export async function apiFetch(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(`/api${path}`, { ...init, headers, credentials: "include" });
  if (res.status === 401 && retry && (await tryRefresh())) {
    return apiFetch(path, init, false);
  }
  return res;
}

export async function login(email: string, password: string): Promise<boolean> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
    credentials: "include",
  });
  if (!res.ok) return false;
  accessToken = ((await res.json()) as { access_token: string }).access_token;
  return true;
}

export async function register(email: string, password: string): Promise<boolean> {
  const res = await fetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
    credentials: "include",
  });
  if (!res.ok) return false;
  accessToken = ((await res.json()) as { access_token: string }).access_token;
  return true;
}
