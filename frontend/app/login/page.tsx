"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ApiError, login } from "@/lib/api";
import { useLanguage } from "@/lib/i18n/language-context";

export default function LoginPage() {
  const { t } = useLanguage();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      router.replace("/");
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
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoFocus
            required
          />
        </label>
        <label className="login-field">
          {t.login.passwordLabel}
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        {error && <p className="login-error">{error}</p>}
        <button type="submit" className="login-submit" disabled={submitting}>
          {t.login.submitButton}
        </button>
      </form>
    </main>
  );
}
