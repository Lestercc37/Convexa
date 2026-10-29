# Convexa desktop (phase 1)

Electron shell around the dashboard's per-view route `/vista/grafico` (PR #361). Design: `docs/diseno-app-escritorio-2026-10-26.txt`.

- Main window: the plain chart (candles, EMAs, VWAP, level lines), on the last symbol / timeframe used.
- Menu **Capas → … abrir en otra ventana**: one window per layer (Heatmap Gamma / Abs / Total / Delta / Vanna / Charm / Calls / Puts, Perfil GEX, Smile, Flujo, Cubos). A layer window follows the main window's symbol / timeframe / range / tab; opening a layer twice focuses the existing window.
- Login: the site's own `/login`, in one persistent session (`persist:convexa`) shared by every window. The app never stores the password.
- Each window's size and place are remembered (`%APPDATA%/Convexa/window-state.json`); a window whose monitor is gone opens on the main one.
- Only our own site loads inside the app; other links open in the system browser; every permission request (camera, location, notifications…) is refused.

```bash
npm install
npm start                                          # against https://dashboard.convexatrading.com
CONVEXA_APP_URL=http://localhost:3000 npm start    # against a local frontend (a branch not deployed yet)
npm test                                           # helper tests (node --test)
npm run dist                                       # unsigned Windows installer in dist/
```

Not yet: data hub (one request per symbol shared by all windows), synced crosshair, faint candles in layer windows, workspaces, auto-update, signing, macOS build.
