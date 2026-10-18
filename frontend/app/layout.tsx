import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { LanguageProvider } from "@/lib/i18n/language-context";
import "./globals.css";

// Inter, the face the approved prototype was drawn with. next/font fetches it at build time and serves it from our own server (no request to Google from the
// browser). Before this the stylesheet only NAMED Inter, so a PC without it installed fell back to Segoe UI.
const inter = Inter({ subsets: ["latin"], display: "swap", variable: "--font-inter" });

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
      <body className={inter.variable}>
        <LanguageProvider>{children}</LanguageProvider>
      </body>
    </html>
  );
}
