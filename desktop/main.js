// Convexa desktop shell, phase 1 (docs/diseno-app-escritorio-2026-10-26.txt section 10 steps 1-2; Lester 2026-10-29 "one window per layer").
// Every window loads the dashboard's own per-view route from our server (no copy of the frontend inside the app): the main window the plain chart,
// each layer its own window on the main window's symbol / timeframe / range / tab. Login is the site's /login, kept in one persistent session
// shared by all windows; the app never sees or stores the password.
const fs = require("node:fs");
const path = require("node:path");
const { app, BrowserWindow, Menu, screen, session, shell } = require("electron");
const { LAYERS, layerUrl, needsSync, isOwnOrigin, usableBounds } = require("./windows");

// CONVEXA_APP_URL points the app at another copy of the site (e.g. http://localhost:3000 while testing a branch).
const BASE = (process.env.CONVEXA_APP_URL || "https://dashboard.convexatrading.com").replace(/\/+$/, "");
const PARTITION = "persist:convexa";
const STATE_FILE = path.join(app.getPath("userData"), "window-state.json");

let state = readState();
let mainWindow = null;
const layerWindows = new Map();

function readState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
  } catch {
    return { windows: {}, mainQuery: "" };
  }
}

function saveState() {
  try {
    fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
  } catch {
    // Remembering sizes is a convenience: a failed write never stops the app.
  }
}

function rememberBounds(key, win) {
  const save = () => {
    if (win.isDestroyed() || win.isMinimized()) return;
    state.windows[key] = { ...win.getNormalBounds(), maximized: win.isMaximized() };
    saveState();
  };
  win.on("resized", save);
  win.on("moved", save);
  win.on("close", save);
}

function createWindow(key, title, url) {
  const saved = state.windows[key];
  const bounds = usableBounds(saved, screen.getAllDisplays().map((display) => display.workArea)) || { width: 1400, height: 860 };
  const win = new BrowserWindow({
    ...bounds,
    title,
    backgroundColor: "#000000",
    icon: path.join(__dirname, "build", "icon.ico"),
    autoHideMenuBar: false,
    webPreferences: {
      partition: PARTITION,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });
  if (saved && saved.maximized) win.maximize();
  // Our site only: any other address opens in the system browser, and pop-ups never open inside the app.
  win.webContents.on("will-navigate", (event, target) => {
    if (!isOwnOrigin(BASE, target)) {
      event.preventDefault();
      if (target.startsWith("https://")) void shell.openExternal(target);
    }
  });
  win.webContents.setWindowOpenHandler(({ url: target }) => {
    // The page's "Ventanas -> abrir aparte" and the chart's ⧉ (frontend PR #368) call window.open on our own /vista/grafico: open the app's own window instead.
    if (isOwnOrigin(BASE, target) && new URL(target).pathname === "/vista/grafico") {
      const layer = LAYERS.find((entry) => entry.id === new URL(target).searchParams.get("capa"));
      if (layer) openLayerWindow(layer);
      else openExtraChartWindow(target);
      return { action: "deny" };
    }
    // Panel windows from "Ventanas" (Alertas, Dealers): one window per panel, focused if already open.
    const panel = isOwnOrigin(BASE, target) ? PANEL_TITLES[new URL(target).pathname] : undefined;
    if (panel) {
      openPanelWindow(new URL(target).pathname, panel, target);
      return { action: "deny" };
    }
    if (target.startsWith("https://") && !isOwnOrigin(BASE, target)) void shell.openExternal(target);
    return { action: "deny" };
  });
  // Keep the window's own title (the page title would replace it).
  win.on("page-title-updated", (event) => event.preventDefault());
  rememberBounds(key, win);
  void win.loadURL(url);
  return win;
}

function openMainWindow() {
  const query = state.mainQuery ? `?${state.mainQuery}` : "";
  mainWindow = createWindow("main", "Convexa", `${BASE}/vista/grafico${query}`);
  const follow = () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    const url = mainWindow.webContents.getURL();
    if (!url.startsWith(`${BASE}/vista/grafico`)) return;
    state.mainQuery = new URL(url).searchParams.toString();
    saveState();
    // Layer windows follow the main window's symbol / timeframe / range / tab.
    for (const [layer, win] of layerWindows) {
      if (!win.isDestroyed() && needsSync(url, win.webContents.getURL())) void win.loadURL(layerUrl(BASE, url, layer));
    }
  };
  mainWindow.webContents.on("did-navigate-in-page", follow);
  mainWindow.webContents.on("did-navigate", follow);
  mainWindow.on("closed", () => {
    mainWindow = null;
    app.quit();
  });
}

function openLayerWindow(layer) {
  const existing = layerWindows.get(layer.id);
  if (existing && !existing.isDestroyed()) {
    existing.show();
    existing.focus();
    return;
  }
  const mainUrl = mainWindow && !mainWindow.isDestroyed() ? mainWindow.webContents.getURL() : null;
  const win = createWindow(`layer:${layer.id}`, `Convexa — ${layer.label}`, layerUrl(BASE, mainUrl, layer.id));
  layerWindows.set(layer.id, win);
  win.on("closed", () => layerWindows.delete(layer.id));
}

const PANEL_TITLES = { "/vista/alertas": "Convexa — Alertas", "/vista/dealers": "Convexa — Dealers" };
const panelWindows = new Map();
function openPanelWindow(path, title, url) {
  const existing = panelWindows.get(path);
  if (existing && !existing.isDestroyed()) {
    void existing.loadURL(url);
    existing.show();
    existing.focus();
    return;
  }
  const win = createWindow(`panel:${path}`, title, url);
  panelWindows.set(path, win);
  win.on("closed", () => panelWindows.delete(path));
}

let extraCharts = 0;
function openExtraChartWindow(url) {
  // A second plain chart (the ⧉ of the chart head), e.g. another symbol on another screen. Its size is remembered per slot (chart-2, chart-3, ...).
  extraCharts += 1;
  createWindow(`chart-${extraCharts + 1}`, "Convexa", url);
}

function buildMenu() {
  const focusedToggle = (item, win) => {
    if (win) win.setAlwaysOnTop(item.checked);
  };
  const template = [
    {
      label: "Capas",
      submenu: LAYERS.map((layer) => ({ label: `${layer.label} — abrir en otra ventana`, click: () => openLayerWindow(layer) })),
    },
    {
      label: "Ventana",
      submenu: [
        { label: "Siempre encima", type: "checkbox", click: (item, win) => focusedToggle(item, win) },
        { label: "Recargar", accelerator: "F5", click: (_item, win) => win && win.webContents.reload() },
        { label: "Pantalla completa", accelerator: "F11", click: (_item, win) => win && win.setFullScreen(!win.isFullScreen()) },
        { type: "separator" },
        ...(app.isPackaged ? [] : [{ label: "Herramientas de desarrollo", accelerator: "F12", click: (_item, win) => win && win.webContents.toggleDevTools() }]),
        { role: "minimize", label: "Minimizar" },
        { role: "close", label: "Cerrar ventana" },
      ],
    },
    {
      label: "Editar",
      submenu: [
        { role: "copy", label: "Copiar" },
        { role: "paste", label: "Pegar" },
        { role: "selectAll", label: "Seleccionar todo" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.whenReady().then(() => {
    // The site asks for nothing on the computer (camera, microphone, location, notifications): every permission request is refused.
    session.fromPartition(PARTITION).setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    buildMenu();
    openMainWindow();
  });
  app.on("window-all-closed", () => app.quit());
}
