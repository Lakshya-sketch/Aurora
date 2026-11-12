/**
 * Aurora v2 — Electron main process
 *
 * Responsibilities (Phase 1):
 *   1. Launch the Python backend (`python -m aurora_core`) as a child process.
 *   2. Create the BrowserWindow and load renderer/index.html.
 *   3. Expose native Windows APIs via ipcMain:
 *        - dialog:openFile   → Electron dialog.showOpenDialog
 *        - dialog:openFolder → Electron dialog.showOpenDialog (directory)
 *        - shell:notify      → Electron Notification
 *   4. Clean shutdown: kill the Python backend when the window closes.
 *
 * See AGENTS.md rule #4 (native UX first) and rule #6 (no untrackable
 * background services — we keep the Python PID and kill it on exit).
 */

const { app, BrowserWindow, ipcMain, dialog, Notification, shell } = require('electron');
const path  = require('path');
const { spawn } = require('child_process');

// ── Config ────────────────────────────────────────────────────────────
const REPO_ROOT   = path.resolve(__dirname, '..');
const BACKEND_URL = 'http://127.0.0.1:8765';
const WS_URL      = 'ws://127.0.0.1:8765/stream';
const POLL_MS     = 500;   // backend readiness poll interval
const POLL_MAX    = 40;    // max polls (~20 s total)

// ── Backend process handle ────────────────────────────────────────────
let backendProc = null;

const fs = require('fs');

function startBackend() {
  const venvPy = process.platform === 'win32'
    ? path.join(REPO_ROOT, 'venv', 'Scripts', 'python.exe')
    : path.join(REPO_ROOT, 'venv', 'bin', 'python3');

  const pyExe = fs.existsSync(venvPy) ? venvPy : (process.platform === 'win32' ? 'python' : 'python3');

  console.log('[aurora:main] spawning backend:', pyExe, '-m aurora_core');

  try {
    backendProc = spawn(pyExe, ['-m', 'aurora_core'], {
      cwd:   REPO_ROOT,
      stdio: ['ignore', 'pipe', 'pipe'],
      env:   { ...process.env, PYTHONUNBUFFERED: '1' },
    });

    if (backendProc.stdout) {
      backendProc.stdout.on('data', d => process.stdout.write(`[py] ${d}`));
    }
    if (backendProc.stderr) {
      backendProc.stderr.on('data', d => process.stderr.write(`[py:err] ${d}`));
    }

    backendProc.on('error', err => {
      console.error('[aurora:main] failed to spawn python backend process:', err.message);
      backendProc = null;
    });

    backendProc.on('exit', (code, sig) => {
      console.log(`[aurora:main] backend exited code=${code} sig=${sig}`);
      backendProc = null;
    });
  } catch (err) {
    console.error('[aurora:main] spawn exception:', err.message);
    backendProc = null;
  }
}

function stopBackend() {
  if (!backendProc) return;
  console.log('[aurora:main] stopping backend (pid=%d)', backendProc.pid);
  backendProc.kill('SIGTERM');
  backendProc = null;
}

// ── Wait for backend /health ──────────────────────────────────────────
async function waitForBackend() {
  const http = require('http');
  for (let i = 0; i < POLL_MAX; i++) {
    await new Promise(r => setTimeout(r, POLL_MS));
    try {
      const ok = await new Promise((resolve) => {
        const req = http.get(`${BACKEND_URL}/health`, res => {
          resolve(res.statusCode === 200);
          res.resume();
        });
        req.on('error', () => resolve(false));
        req.setTimeout(400, () => { req.destroy(); resolve(false); });
      });
      if (ok) { console.log('[aurora:main] backend ready'); return true; }
    } catch (_) { /* keep polling */ }
  }
  console.error('[aurora:main] backend did not start in time');
  return false;
}

// ── BrowserWindow ─────────────────────────────────────────────────────
let mainWindow = null;

async function createWindow() {
  mainWindow = new BrowserWindow({
    width:           1180,
    height:          780,
    minWidth:        900,
    minHeight:       600,
    title:           'Aurora',
    backgroundColor: '#ffffff',
    show:            false,   // shown after ready-to-show
    webPreferences: {
      preload:            path.join(__dirname, 'preload.js'),
      contextIsolation:   true,
      nodeIntegration:    false,
      sandbox:            false,
    },
  });

  const isDev = process.argv.includes('--dev') || Boolean(process.env.VITE_DEV_SERVER_URL);
  if (isDev) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173');
    mainWindow.webContents.openDevTools();
  } else {
    const distPath = path.join(REPO_ROOT, 'renderer', 'dist', 'index.html');
    const fallbackPath = path.join(REPO_ROOT, 'renderer', 'index.html');
    const fs = require('fs');
    if (fs.existsSync(distPath)) {
      mainWindow.loadFile(distPath);
    } else {
      mainWindow.loadFile(fallbackPath);
    }
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

// ── IPC handlers (native UX — see AGENTS.md rule #4) ─────────────────

/** Open a native file-picker (multi-select supported). */
ipcMain.handle('dialog:openFile', async (_evt, opts = {}) => {
  if (!mainWindow) return { canceled: true, filePaths: [] };
  return dialog.showOpenDialog(mainWindow, {
    title:      opts.title      || 'Open File',
    properties: opts.properties || ['openFile', 'multiSelections'],
    filters:    opts.filters    || [
      { name: 'All Supported Files', extensions: ['pdf', 'txt', 'md', 'docx', 'jpg', 'jpeg', 'png', 'webp', 'bmp', 'py', 'js', 'ts', 'jsx', 'tsx', 'json', 'csv'] },
      { name: 'Images', extensions: ['jpg', 'jpeg', 'png', 'webp', 'bmp'] },
      { name: 'Documents', extensions: ['pdf', 'txt', 'md', 'docx'] },
      { name: 'Code Files', extensions: ['py', 'js', 'ts', 'jsx', 'tsx', 'json', 'csv'] },
      { name: 'All Files', extensions: ['*'] },
    ],
  });
});

/** Open a native folder-picker. */
ipcMain.handle('dialog:openFolder', async (_evt, opts = {}) => {
  if (!mainWindow) return { canceled: true, filePaths: [] };
  return dialog.showOpenDialog(mainWindow, {
    title:      opts.title || 'Select Folder',
    properties: ['openDirectory'],
  });
});

/** Show a native Windows notification. */
ipcMain.handle('shell:notify', (_evt, { title, body }) => {
  if (Notification.isSupported()) {
    new Notification({ title: title || 'Aurora', body: body || '' }).show();
  }
});

/** Expose the backend URLs to the renderer (read-only). */
ipcMain.handle('aurora:getConfig', () => ({
  backendUrl: BACKEND_URL,
  wsUrl:      WS_URL,
}));

async function checkHealthOnce() {
  const http = require('http');
  return new Promise((resolve) => {
    const req = http.get(`${BACKEND_URL}/health`, res => {
      resolve(res.statusCode === 200);
      res.resume();
    });
    req.on('error', () => resolve(false));
    req.setTimeout(300, () => { req.destroy(); resolve(false); });
  });
}

// ── App lifecycle ─────────────────────────────────────────────────────
app.whenReady().then(async () => {
  const alreadyRunning = await checkHealthOnce();
  if (!alreadyRunning) {
    startBackend();
  } else {
    console.log('[aurora:main] backend already running on', BACKEND_URL);
  }
  const ready = await waitForBackend();
  if (!ready) {
    console.warn('[aurora:main] opening window without confirmed backend');
  }
  await createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  stopBackend();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', stopBackend);
