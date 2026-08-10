import type { Metadata } from "next";
import { LanguageProvider } from "@/lib/i18n/language-context";
import "./globals.css";

export const metadata: Metadata = {
  title: "Convexa | Gamma Dashboard",
  description: "Régimen gamma y niveles de gravitación para opciones.",
};

// `<html lang>` and the metadata above stay static Spanish — server-rendered,
// build-time content with no reactive plumbing to a client-only
// localStorage language choice, same "no SEO concern, no next-intl" call
// that shaped this whole feature.
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>
        <LanguageProvider>{children}</LanguageProvider>
      </body>
    </html>
  );
}
