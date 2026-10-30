import { JetBrains_Mono, Manrope } from "next/font/google";
import "./v2.css";

// The desktop app's windows (/vista/...) wear the approved v2 look (docs/maqueta_app_escritorio_v2_2026-10-27.html, Lester 2026-10-27 "si"): black, Manrope for the
// interface and JetBrains Mono for numbers. Scoped to these routes on purpose: the web dashboard stays frozen as it is (Lester 2026-10-29).
const manrope = Manrope({ subsets: ["latin"], display: "swap", variable: "--font-v2-ui", weight: ["400", "500", "600", "700", "800"] });
const mono = JetBrains_Mono({ subsets: ["latin"], display: "swap", variable: "--font-v2-mono", weight: ["400", "500", "600"] });

export default function VistaLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <div className={`app-v2 ${manrope.variable} ${mono.variable}`}>{children}</div>;
}
