"use client";

import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { acceptInvite, ApiError, getInvitePreview } from "@/lib/api";
import { useLanguage } from "@/lib/i18n/language-context";

function SignupForm() {
  const { t } = useLanguage();
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [status, setStatus] = useState<"loading" | "ready" | "invalid">("loading");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) {
      setStatus("invalid");
      return;
    }
    const controller = new AbortController();
    getInvitePreview(token, controller.signal)
      .then((preview) => {
        setUsername(preview.username);
        setStatus("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setStatus("invalid");
      });
    return () => controller.abort();
  }, [token]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError(t.signup.passwordTooShort);
      return;
    }
    if (password !== confirmPassword) {
      setError(t.signup.passwordMismatch);
      return;
    }
    setSubmitting(true);
    try {
      await acceptInvite(token, password);
      router.replace("/");
    } catch (reason: unknown) {
      setError(
        reason instanceof ApiError && reason.status === 404
          ? t.signup.invalidOrExpiredInvite
          : t.signup.genericError,
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (status === "loading") {
    return (
      <main className="login-page">
        <p>{t.signup.loading}</p>
      </main>
    );
  }

  if (status === "invalid") {
    return (
      <main className="login-page">
        <p className="login-error">{t.signup.invalidOrExpiredInvite}</p>
      </main>
    );
  }

  return (
    <main className="login-page">
      <form className="login-form" onSubmit={handleSubmit}>
        <h1 className="login-title">{t.signup.title}</h1>
        <p className="login-field">{t.signup.creatingAccountFor(username)}</p>
        <label className="login-field">
          {t.signup.passwordLabel}
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoFocus
            required
          />
        </label>
        <label className="login-field">
          {t.signup.confirmPasswordLabel}
          <input
            type="password"
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            required
          />
        </label>
        {error && <p className="login-error">{error}</p>}
        <button type="submit" className="login-submit" disabled={submitting}>
          {t.signup.submitButton}
        </button>
      </form>
    </main>
  );
}

export default function SignupPage() {
  return (
    <Suspense fallback={null}>
      <SignupForm />
    </Suspense>
  );
}
