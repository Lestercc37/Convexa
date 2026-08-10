"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { en } from "./en";
import { es } from "./es";
import type { Translations } from "./translations";

export type Language = "es" | "en";

const STORAGE_KEY = "convexa-language";
const DEFAULT_LANGUAGE: Language = "es";

const DICTIONARIES: Record<Language, Translations> = { es, en };

type LanguageContextValue = {
  language: Language;
  setLanguage: (language: Language) => void;
  t: Translations;
};

const LanguageContext = createContext<LanguageContextValue | null>(null);

function isLanguage(value: string | null): value is Language {
  return value === "es" || value === "en";
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(DEFAULT_LANGUAGE);

  useEffect(() => {
    // localStorage doesn't exist during server rendering, so the initial
    // render (server and client, pre-hydration) must both use
    // DEFAULT_LANGUAGE — reading the real stored value any earlier (e.g. a
    // lazy useState initializer) would render a different language on the
    // client than what the server sent, a hydration mismatch. Reading here
    // post-mount is the standard fix, at the cost of a one-frame flash if
    // the stored preference differs — same "no SSR i18n plumbing" tradeoff
    // that ruled out next-intl for this dashboard.
    const stored = window.localStorage.getItem(STORAGE_KEY);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- see above
    if (isLanguage(stored)) setLanguageState(stored);
  }, []);

  const setLanguage = (next: Language) => {
    setLanguageState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
  };

  return (
    <LanguageContext.Provider value={{ language, setLanguage, t: DICTIONARIES[language] }}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage(): LanguageContextValue {
  const context = useContext(LanguageContext);
  if (!context) throw new Error("useLanguage must be used within a LanguageProvider");
  return context;
}
