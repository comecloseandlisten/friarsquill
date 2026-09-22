const { app, BrowserWindow, Menu, ipcMain, dialog, shell } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

/**
 * Prefer project .venv or LOCALVIDEOTRANSCRIBER_PYTHON so Windows policy (WDAC)
 * blocks fewer paths than the global Python install under AppData.
 */
function resolvePythonCmd() {
  if (process.env.LOCALVIDEOTRANSCRIBER_PYTHON) {
    return process.env.LOCALVIDEOTRANSCRIBER_PYTHON;
  }
  const appRoot = path.join(__dirname, '..');
  const winVenv = path.join(appRoot, '.venv', 'Scripts', 'python.exe');
  const unixVenv = path.join(appRoot, '.venv', 'bin', 'python');
  if (process.platform === 'win32' && fs.existsSync(winVenv)) {
    return winVenv;
  }
  if (process.platform !== 'win32' && fs.existsSync(unixVenv)) {
    return unixVenv;
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

let pythonProcess = null;
let pythonReady = false;
let mainWindow = null;
let pendingErrors = [];

// Pending RPC requests keyed by request id. Populated by ipcMain handlers
// that need a typed response back from the Python backend (list_models,
// estimate_eta). Resolved inside the stdout parser when a matching id comes
// back. Stream-style messages (progress, eta runtime events, etc.) still
// flow to the renderer as normal.
const pendingRpc = new Map();

function sendToRenderer(msg) {
  // If this message corresponds to an awaiting RPC call, resolve that first.
  if (msg && msg.id != null && pendingRpc.has(msg.id)) {
    const { resolve, timer } = pendingRpc.get(msg.id);
    pendingRpc.delete(msg.id);
    if (timer) clearTimeout(timer);
    try { resolve(msg); } catch (_) {}
    // Do not return — still forward so the renderer's generic listener
    // stays consistent if it cares about the message.
  }
  if (mainWindow?.webContents) {
    mainWindow.webContents.send('python-message', msg);
  } else {
    pendingErrors.push(msg);
  }
}

function rpcCall(method, params, timeoutMs = 8000) {
  return new Promise((resolve) => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    const timer = setTimeout(() => {
      if (pendingRpc.has(id)) {
        pendingRpc.delete(id);
        resolve({ type: 'error', message: `${method} timed out` });
      }
    }, timeoutMs);
    pendingRpc.set(id, { resolve, timer });
    sendToPython({ id, method, params: params || {} });
  });
}

function startPython() {
  const pythonCmd = resolvePythonCmd();
  const backendScript = path.join(__dirname, '..', 'backend', 'main.py');
  console.log('[Python] Using interpreter:', pythonCmd);

  pythonProcess = spawn(pythonCmd, ['-u', backendScript], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });

  pythonReady = true;

  let buffer = '';
  pythonProcess.stdout.on('data', (data) => {
    buffer += data.toString('utf-8');
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      if (line.trim()) {
        try {
          const msg = JSON.parse(line);
          sendToRenderer(msg);
        } catch (e) {
          // ignore non-JSON output
        }
      }
    }
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error('[Python]', data.toString());
  });

  pythonProcess.on('error', (err) => {
    console.error('[Python] spawn error:', err.message);
    pythonReady = false;
    sendToRenderer({
      type: 'error',
      message: `Failed to start Python: ${err.message}. Install Python 3.10+ and ensure it is on PATH.`,
    });
  });

  pythonProcess.on('exit', (code) => {
    console.log(`[Python] exited with code ${code}`);
    pythonReady = false;
    if (code !== null && code !== 0) {
      sendToRenderer({
        type: 'error',
        message: `Python backend exited unexpectedly (code ${code}). Check that all dependencies are installed: pip install -r backend/requirements.txt`,
      });
    }
  });
}

function sendToPython(msg) {
  if (!pythonReady || !pythonProcess || !pythonProcess.stdin.writable) {
    sendToRenderer({
      type: 'error',
      message: 'Python backend is not running. Ensure Python 3.10+ is installed and on PATH, then restart the app.',
    });
    return;
  }
  pythonProcess.stdin.write(JSON.stringify(msg) + '\n');
}

app.whenReady().then(() => {
  // Strip the default OS menu (File / Edit / View / Window / Help)
  Menu.setApplicationMenu(null);

  const iconFile = process.platform === 'win32' ? 'icon.ico' : 'icon.png';
  const iconPath = path.join(__dirname, 'renderer', iconFile);

  mainWindow = new BrowserWindow({
    width: 900,
    height: 750,
    title: "Friar's Quill",
    icon: iconPath,
    autoHideMenuBar: true,
    show: false, // delay show until maximized
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.setMenuBarVisibility(false);

  // Start maximized (keeps title bar with min/max/close buttons)
  mainWindow.maximize();
  mainWindow.once('ready-to-show', () => mainWindow.show());

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  // Flush any errors that occurred before renderer was ready
  mainWindow.webContents.on('did-finish-load', () => {
    for (const msg of pendingErrors) {
      mainWindow.webContents.send('python-message', msg);
    }
    pendingErrors = [];
  });

  startPython();
});

// IPC handlers
ipcMain.handle('select-file', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    filters: [
      {
        name: 'Video / Audio',
        extensions: [
          'mp4', 'mkv', 'avi', 'webm', 'mov', 'm4v', 'flv', 'wmv',
          'mpg', 'mpeg', 'ts', 'm4a', 'mp3', 'wav', 'ogg', 'flac',
        ],
      },
    ],
  });
  return result.filePaths[0] || null;
});

ipcMain.handle('select-output-dir', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
  });
  return result.filePaths[0] || null;
});

ipcMain.on('start-process', (_, params) => {
  sendToPython({ id: Date.now(), method: 'process', params });
});

ipcMain.on('cancel-process', () => {
  sendToPython({ id: Date.now(), method: 'cancel' });
});

// Oracle chat — params is forwarded to Python; renderer also supplies its own id.
ipcMain.on('oracle-chat', (_, params) => {
  const id = (params && params.id) || Date.now();
  sendToPython({ id, method: 'oracle_chat', params: params || {} });
});

ipcMain.on('oracle-cancel', () => {
  sendToPython({ id: Date.now(), method: 'oracle_cancel' });
});

ipcMain.on('oracle-close', () => {
  sendToPython({ id: Date.now(), method: 'oracle_close' });
});

ipcMain.handle('open-file', async (_, filePath) => {
  await shell.openPath(filePath);
});

ipcMain.handle('open-folder', async (_, filePath) => {
  shell.showItemInFolder(filePath);
});

ipcMain.handle('list-models', async () => {
  const msg = await rpcCall('list_models', {}, 8000);
  if (msg.type === 'error') {
    console.error('[list-models] error:', msg.message);
    return {};
  }
  return msg.models || {};
});

ipcMain.handle('get-gpu-info', async () => {
  const msg = await rpcCall('get_gpu_info', {}, 5000);
  if (msg.type === 'error') {
    return { cuda: false, device: '', vram_mb: 0 };
  }
  return { cuda: msg.cuda || false, device: msg.device || '', vram_mb: msg.vram_mb || 0 };
});

ipcMain.handle('estimate-eta', async (_, params) => {
  // params expected shape: { duration_sec, config }
  const msg = await rpcCall('estimate_eta', params || {}, 8000);
  if (msg.type === 'error') {
    console.error('[estimate-eta] error:', msg.message);
    return {};
  }
  return msg.eta || {};
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('quit', () => {
  if (pythonProcess) {
    pythonProcess.kill();
    pythonProcess = null;
  }
});
