const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  selectFile: () => ipcRenderer.invoke('select-file'),
  selectOutputDir: () => ipcRenderer.invoke('select-output-dir'),
  startProcess: (params) => ipcRenderer.send('start-process', params),
  cancelProcess: () => ipcRenderer.send('cancel-process'),
  onMessage: (callback) => {
    ipcRenderer.on('python-message', (_, msg) => callback(msg));
  },
  openFile: (filePath) => ipcRenderer.invoke('open-file', filePath),
  openFolder: (filePath) => ipcRenderer.invoke('open-folder', filePath),
  listModels: () => ipcRenderer.invoke('list-models'),
  getGpuInfo: () => ipcRenderer.invoke('get-gpu-info'),
  estimateEta: (config) => ipcRenderer.invoke('estimate-eta', config),
  // Oracle (post-transcription chat)
  oracleChat: (params) => ipcRenderer.send('oracle-chat', params),
  oracleCancel: () => ipcRenderer.send('oracle-cancel'),
  oracleClose: () => ipcRenderer.send('oracle-close'),
});
