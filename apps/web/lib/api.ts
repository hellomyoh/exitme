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

/** 새로고침 후 세션 복원 — refresh 쿠키(1시간 롤링)로 access 재발급. */
export async function ensureSession(): Promise<boolean> {
  if (accessToken) return true;
  try {
    return await tryRefresh();
  } catch {
    return false;
  }
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
  if (res.status === 401 && retry) {
    if (await tryRefresh()) return apiFetch(path, init, false);
    // refresh 쿠키(1시간 롤링)까지 만료 — "invalid token" 원문 노출 대신 재로그인 안내 (2026-08-29)
    accessToken = null;
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login?expired=1";
    }
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

/** 로그아웃 — 서버 refresh 쿠키 삭제 + 메모리 토큰 폐기. */
export async function logout(): Promise<void> {
  try { await fetch("/api/auth/logout", { method: "POST", credentials: "include" }); } catch { /* ignore */ }
  accessToken = null;
}
