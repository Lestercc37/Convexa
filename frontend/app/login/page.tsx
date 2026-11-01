"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ApiError, login } from "@/lib/api";
import { DESKTOP_HOME, isDesktopApp } from "@/lib/desktop-app";
import { useLanguage } from "@/lib/i18n/language-context";
import { forgetUsername, readRememberedUsername, rememberUsername } from "@/lib/remembered-username";

export default function LoginPage() {
  const { t } = useLanguage();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);

  // A remembered username (this browser / this app only) is filled in and the cursor goes straight to the password.
  useEffect(() => {
    const saved = readRememberedUsername();
    if (saved) {
      setUsername(saved);
      passwordRef.current?.focus();
    }
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      if (remember) rememberUsername(username);
      else forgetUsername();
      // Back to the /vista/... window that sent us here (desktop app); only our own view routes are accepted, never an outside address.
      const next = new URLSearchParams(window.location.search).get("next");
      // In the desktop app a login without "next" (first login, after "Cerrar sesión") still lands on the app's chart, never on the web dashboard.
      router.replace(next && /^\/vista\/[a-z]+(\?[^\s]*)?$/.test(next) ? next : isDesktopApp() ? DESKTOP_HOME : "/");
    } catch (reason: unknown) {
      setError(
        reason instanceof ApiError && reason.status === 401
          ? t.login.invalidCredentials
          : t.login.genericError,
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <form className="login-form" onSubmit={handleSubmit}>
        <h1 className="login-title">{t.login.title}</h1>
        <label className="login-field">
          {t.login.usernameLabel}
          <input
            type="text"
            name="username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoFocus
            required
          />
        </label>
        <label className="login-field">
          {t.login.passwordLabel}
          <input
            ref={passwordRef}
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        <label className="login-remember">
          <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
          {t.login.rememberUsername}
        </label>
        {error && <p className="login-error">{error}</p>}
        <button type="submit" className="login-submit" disabled={submitting}>
          {t.login.submitButton}
        </button>
        <p className="login-hint">{t.login.forgotPassword}</p>
      </form>
    </main>
  );
}
