const { app, BrowserWindow, Menu, Tray, ipcMain } = require('electron')
const http = require('http')
const path = require('path')

const BACKEND = { host: '127.0.0.1', port: 8000 }
const BACKEND_LABEL = `http://${BACKEND.host}:${BACKEND.port}`

let mainWindow = null
let tray = null
// 只有它为 true 时关窗口才是真退出，否则一律收进托盘
let quitting = false

/**
 * 把后端的错误响应翻译成界面能看懂的一句话。
 */
function describeError(text, statusCode) {
  let parsed = null
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = { detail: text }
    }
  }

  const detail = parsed && parsed.detail
  if (typeof detail === 'string') return detail
  if (detail) return JSON.stringify(detail)
  return `HTTP ${statusCode}`
}

/**
 * 统一的后端请求封装：渲染进程不直接发 HTTP，全部经主进程转发，
 * 顺带把连接错误翻译成界面能看懂的中文提示。
 */
function request(method, pathname, body) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : JSON.stringify(body)

    const req = http.request(
      {
        host: BACKEND.host,
        port: BACKEND.port,
        path: pathname,
        method,
        headers: payload
          ? {
              'Content-Type': 'application/json',
              'Content-Length': Buffer.byteLength(payload)
            }
          : {}
      },
      (res) => {
        let data = ''
        res.setEncoding('utf8')
        res.on('data', (chunk) => (data += chunk))
        res.on('end', () => {
          let parsed = null
          if (data) {
            try {
              parsed = JSON.parse(data)
            } catch {
              parsed = { detail: data }
            }
          }

          if (res.statusCode >= 400) {
            reject(new Error(describeError(data, res.statusCode)))
            return
          }
          resolve(parsed)
        })
      }
    )

    req.on('error', (err) => {
      if (err.code === 'ECONNREFUSED') {
        reject(new Error(`后端未启动，请先运行 wy-agent-server（${BACKEND_LABEL}）`))
      } else {
        reject(new Error(err.message))
      }
    })

    if (payload) req.write(payload)
    req.end()
  })
}

/**
 * 拆一个 SSE 帧（块与块之间用空行分隔）：event 决定类型，data 是 JSON 文本。
 */
function parseFrame(raw) {
  let event = 'message'
  const dataLines = []

  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }

  if (!dataLines.length) return null

  const text = dataLines.join('\n')
  let data
  try {
    data = JSON.parse(text)
  } catch {
    data = { text }
  }
  return { event, data }
}

/**
 * 流式请求：每收到一帧就回调 onEvent，整个流结束后 resolve。
 * resolve 形如 { ok: true, model } 或 { ok: false, error }；
 * 只有连接层失败（后端没起来）才 reject。
 */
function requestStream(pathname, body, onEvent) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body)

    const req = http.request(
      {
        host: BACKEND.host,
        port: BACKEND.port,
        path: pathname,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload),
          Accept: 'text/event-stream'
        }
      },
      (res) => {
        // 流开始前的错误（404 / 400 / 502）还是普通 JSON
        if (res.statusCode >= 400) {
          let data = ''
          res.setEncoding('utf8')
          res.on('data', (chunk) => (data += chunk))
          res.on('end', () => reject(new Error(describeError(data, res.statusCode))))
          return
        }

        res.setEncoding('utf8')
        let buffer = ''
        let result = { ok: true, model: null }

        res.on('data', (chunk) => {
          buffer += chunk
          let sep
          while ((sep = buffer.indexOf('\n\n')) >= 0) {
            const frame = parseFrame(buffer.slice(0, sep))
            buffer = buffer.slice(sep + 2)
            if (!frame) continue

            if (frame.event === 'error') {
              result = { ok: false, error: frame.data.message || '模型调用失败' }
            } else if (frame.event === 'done') {
              result = { ...result, model: frame.data.model ?? null }
            }
            onEvent(frame)
          }
        })

        // 流被中途断开时也要给个结论，不能让渲染进程一直等
        res.on('end', () => resolve(result))
        res.on('aborted', () =>
          resolve(result.ok ? { ok: false, error: '连接已中断' } : result)
        )
      }
    )

    req.on('error', (err) => {
      if (err.code === 'ECONNREFUSED') {
        reject(new Error(`后端未启动，请先运行 wy-agent-server（${BACKEND_LABEL}）`))
      } else {
        reject(new Error(err.message))
      }
    })

    req.write(payload)
    req.end()
  })
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 880,
    minHeight: 600,
    backgroundColor: '#0f1115',
    title: 'WY Agent',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true
    }
  })

  // 去掉系统菜单后 DevTools 快捷键也跟着没了，这里补回 F12 / Ctrl+Shift+I
  win.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown') return
    const wantsDevTools =
      input.key === 'F12' ||
      (input.control && input.shift && input.key.toLowerCase() === 'i')
    if (!wantsDevTools) return
    win.webContents.toggleDevTools()
    event.preventDefault()
  })

  // 点关闭只是收进托盘，真正退出走托盘菜单
  win.on('close', (event) => {
    if (quitting) return
    event.preventDefault()
    win.hide()
  })

  win.on('closed', () => {
    mainWindow = null
  })

  win.loadFile('index.html')
  mainWindow = win
  return win
}

function showWindow() {
  if (!mainWindow) {
    createWindow()
    return
  }
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
}

/**
 * 托盘：右下角常驻图标，左键切换显示/隐藏，右键出菜单。
 * 少了这段，程序就只是个普通窗口，装完哪都留不下痕迹。
 */
function createTray() {
  tray = new Tray(path.join(__dirname, 'assets', 'tray.ico'))
  tray.setToolTip('WY Agent')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: '打开主界面', click: showWindow },
      { type: 'separator' },
      {
        label: '退出',
        click: () => {
          quitting = true
          app.quit()
        }
      }
    ])
  )

  tray.on('click', () => {
    if (mainWindow && mainWindow.isVisible() && !mainWindow.isMinimized()) {
      mainWindow.hide()
    } else {
      showWindow()
    }
  })
}

// ---------- 基础信息 ----------
ipcMain.handle('backend:info', () => ({ base: BACKEND_LABEL }))
ipcMain.handle('backend:health', () => request('GET', '/health'))

// ---------- 供应商（后端注册表驱动） ----------
ipcMain.handle('providers:list', () => request('GET', '/api/providers'))

// ---------- 模型配置 ----------
ipcMain.handle('models:list', () => request('GET', '/api/models'))
ipcMain.handle('models:create', (_, payload) => request('POST', '/api/models', payload))
ipcMain.handle('models:update', (_, { id, payload }) =>
  request('PUT', `/api/models/${id}`, payload)
)
ipcMain.handle('models:delete', (_, id) => request('DELETE', `/api/models/${id}`))
ipcMain.handle('models:activate', (_, id) =>
  request('POST', `/api/models/${id}/activate`)
)

// ---------- 对话 ----------
ipcMain.handle('agent:chat:stream', (event, { message, modelId, requestId }) => {
  const sender = event.sender

  return requestStream(
    '/agent/chat/stream',
    { message, model_id: modelId ?? null },
    (frame) => {
      // 窗口关掉后就不再往渲染进程推了
      if (sender.isDestroyed()) return
      sender.send('agent:chat:stream:event', {
        requestId,
        event: frame.event,
        data: frame.data
      })
    }
  ).then((result) => {
    if (!result.ok) throw new Error(result.error)
    return result
  })
})

// 再双击一次快捷方式时把已有窗口拉出来，而不是多开一个托盘图标
const gotLock = app.requestSingleInstanceLock()

if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', showWindow)

  app.whenReady().then(() => {
    // 不显示窗口顶部的系统菜单栏（File / Edit / View / Window / Help）
    Menu.setApplicationMenu(null)
    createWindow()
    createTray()
  })

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
      app.quit()
    }
  })

  app.on('activate', showWindow)

  app.on('before-quit', () => {
    quitting = true
  })
}
