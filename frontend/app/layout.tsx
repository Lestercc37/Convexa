import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Convexa | Gamma Dashboard",
  description: "Régimen gamma y niveles de gravitación para opciones.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
