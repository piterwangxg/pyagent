const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  backendInfo: () => ipcRenderer.invoke('backend:info'),
  health: () => ipcRenderer.invoke('backend:health'),

  listProviders: () => ipcRenderer.invoke('providers:list'),
  listModels: () => ipcRenderer.invoke('models:list'),
  createModel: (payload) => ipcRenderer.invoke('models:create', payload),
  updateModel: (id, payload) => ipcRenderer.invoke('models:update', { id, payload }),
  deleteModel: (id) => ipcRenderer.invoke('models:delete', id),
  activateModel: (id) => ipcRenderer.invoke('models:activate', id),

  chatStream: (message, modelId, requestId) =>
    ipcRenderer.invoke('agent:chat:stream', {
      message,
      modelId: modelId ?? null,
      requestId
    }),

  /** 订阅流式增量（delta / done / error），返回取消订阅函数 */
  onChatStreamEvent: (handler) => {
    const listener = (_event, payload) => handler(payload)
    ipcRenderer.on('agent:chat:stream:event', listener)
    return () => ipcRenderer.removeListener('agent:chat:stream:event', listener)
  }
})
