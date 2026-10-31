// The Convexa desktop app (Electron, desktop/) loads this same site; its windows live under /vista/. Electron puts "Electron/<version>" in the user agent.
export const DESKTOP_HOME = "/vista/grafico";

export function isDesktopApp(): boolean {
  return typeof navigator !== "undefined" && /\bElectron\//.test(navigator.userAgent);
}
