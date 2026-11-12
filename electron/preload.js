/**
 * Aurora v2 — Electron preload script
 *
 * Exposes a minimal, typed API to the renderer via contextBridge.
 * The renderer MUST NOT have access to Node.js directly (contextIsolation=true).
 *
 * Exposed surface (window.aurora):
 *
 *   aurora.getConfig()           → { backendUrl, wsUrl }
 *   aurora.dialog.openFile(opts) → { canceled, filePaths }
 *   aurora.dialog.openFolder()   → { canceled, filePaths }
 *   aurora.notify(title, body)   → void
 *
 * The WebSocket connection itself is managed by renderer/app.js using
 * the native browser WebSocket API — no Node.js WS library needed.
 *
 * See AGENTS.md rule #4 (native UX first) and architecture.md §IPC.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('aurora', {

  /** Returns backend + websocket URLs from main process config. */
  getConfig: () => ipcRenderer.invoke('aurora:getConfig'),

  dialog: {
    /**
     * Open a native Windows file picker.
     * @param {object} opts - { title, properties, filters }
     * @returns {Promise<{canceled: boolean, filePaths: string[]}>}
     */
    openFile: (opts = {}) => ipcRenderer.invoke('dialog:openFile', opts),

    /**
     * Open a native Windows folder picker.
     * @param {object} opts - { title }
     * @returns {Promise<{canceled: boolean, filePaths: string[]}>}
     */
    openFolder: (opts = {}) => ipcRenderer.invoke('dialog:openFolder', opts),
  },

  /**
   * Show a native Windows notification.
   * @param {string} title
   * @param {string} body
   */
  notify: (title, body) => ipcRenderer.invoke('shell:notify', { title, body }),
});
