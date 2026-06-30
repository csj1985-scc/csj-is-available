const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const http = require('http');

let mainWindow = null;

// ── 后端 API 调用 ──────────────────────────────
const BACKEND_URL = 'http://localhost:8002';

function apiCall(method, apiPath, body) {
  return new Promise((resolve, reject) => {
    const url = new URL(apiPath, BACKEND_URL);
    const opts = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + url.search,
      method,
      headers: { 'Content-Type': 'application/json' },
      timeout: 15000,
    };
    const req = http.request(opts, (res) => {
      let data = '';
      res.on('data', (c) => data += c);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch { resolve({ raw: data }); }
      });
    });
    req.on('error', (e) => reject(e));
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

// ── IPC 处理 ──────────────────────────────────
ipcMain.handle('send-chat', async (_, message) => {
  try {
    const r = await apiCall('POST', '/chat/stream', {
      message: message,
      session_id: 'desktop',
    });
    return r;
  } catch (e) {
    return { reply: '连接后端失败，请确认悟道服务已启动', error: e.message };
  }
});

ipcMain.handle('get-version', () => '0.7.2');

ipcMain.handle('health-check', async () => {
  try {
    const r = await apiCall('GET', '/health');
    return r;
  } catch {
    return { status: 'down' };
  }
});

// 窗口控制
ipcMain.on('win-minimize', () => mainWindow?.minimize());
ipcMain.on('win-maximize', () => {
  if (!mainWindow) return;
  mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
});
ipcMain.on('win-close', () => mainWindow?.close());
ipcMain.on('win-hide', () => mainWindow?.hide());
ipcMain.on('win-is-maximized', (e) => { e.returnValue = mainWindow?.isMaximized() ?? false; });

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 700,
    title: '悟道 v0.7.2',
    icon: path.join(__dirname, 'assets', 'wudao_logo.jpg'),
    backgroundColor: '#1A1A1A',
    frame: false,
    titleBarStyle: 'hidden',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: false,
      nodeIntegration: false,
      webSecurity: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'src', 'index.html'));
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
