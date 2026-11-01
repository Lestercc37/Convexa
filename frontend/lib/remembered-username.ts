// "Recordar usuario" on the login page (Lester 2026-10-31: a teammate forgot his username). Only the username is kept, never the password,
// in this browser / this desktop app only. Storage can be missing or blocked: every access is guarded and the login works without it.
const KEY = "convexa.rememberedUsername";

export function readRememberedUsername(): string {
  try {
    return window.localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function rememberUsername(username: string): void {
  try {
    window.localStorage.setItem(KEY, username.trim());
  } catch {
    // A convenience only.
  }
}

export function forgetUsername(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // A convenience only.
  }
}
