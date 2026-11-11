const api = window.electronAPI
const $ = (id) => document.getElementById(id)

/** 供应商列表由后端 /api/providers 提供（注册表驱动）；接口不可用时退回这份内置表 */
const FALLBACK_PROVIDERS = [
  { provider: 'deepseek', label: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', default_model: 'deepseek-chat' },
  { provider: 'openai', label: 'OpenAI', base_url: 'https://api.openai.com/v1', default_model: 'gpt-4o-mini' },
  { provider: 'moonshot', label: 'Moonshot', base_url: 'https://api.moonshot.cn/v1', default_model: 'moonshot-v1-8k' },
  { provider: 'qwen', label: '通义千问', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', default_model: 'qwen-plus' },
  { provider: 'custom', label: '自定义（OpenAI 兼容）', base_url: '', default_model: '' }
]

const state = {
  view: 'chat',
  providers: [],
  models: [],
  activeId: null,
  editingId: null,
  sending: false
}

/* ---------------------------------------------------------------- 工具 */

function escapeHtml(value) {
  return String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  )
}

function toast(message, type = 'ok') {
  const el = document.createElement('div')
  el.className = `toast is-${type}`
  el.textContent = message
  $('toastWrap').appendChild(el)
  setTimeout(() => {
    el.style.opacity = '0'
    el.style.transition = 'opacity .2s'
    setTimeout(() => el.remove(), 220)
  }, 2600)
}

function fmtTime(iso) {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 16)
}

/* ---------------------------------------------------------------- 视图切换 */

const VIEW_META = {
  chat: { title: '对话', sub: '与 Agent 交互，消息会带上当前启用的模型配置' },
  models: { title: '模型配置', sub: '管理可用的模型接入信息' }
}

function setView(name) {
  state.view = name
  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.view === name)
  })
  document.querySelectorAll('.view').forEach((section) => {
    section.classList.toggle('is-active', section.id === `view-${name}`)
  })
  $('viewTitle').textContent = VIEW_META[name].title
  $('viewSub').textContent = VIEW_META[name].sub
  if (name === 'models') renderModels()
  if (name === 'chat') $('chatInput').focus()
}

/* ---------------------------------------------------------------- 后端状态 */

async function checkHealth() {
  const dot = $('healthDot')
  const text = $('healthText')
  const sub = $('healthSub')

  try {
    const info = await api.health()
    dot.className = 'dot is-ok'
    text.textContent = '后端已连接'
    sub.textContent = info && info.db ? info.db : ''
    sub.title = sub.textContent
  } catch (err) {
    dot.className = 'dot is-bad'
    text.textContent = '后端未连接'
    sub.textContent = '127.0.0.1:8000'
    sub.title = err.message
  }
}

/* ---------------------------------------------------------------- 模型列表 */

function renderEmpty() {
  $('modelGrid').innerHTML = `
    <div class="empty-state" style="grid-column: 1 / -1">
      <div class="empty-icon">◎</div>
      <p>还没有模型配置</p>
      <span>点击右上角「新建配置」添加第一个，例如 DeepSeek / OpenAI</span>
    </div>`
}

function modelCard(item) {
  const activeClass = item.is_active ? 'card is-active' : 'card'
  const badge = item.is_active
    ? '<span class="badge is-active">已启用</span>'
    : `<span class="badge">${escapeHtml(item.provider)}</span>`

  return `
    <article class="${activeClass}" data-id="${escapeHtml(item.id)}">
      <div class="card-head">
        <span class="card-title" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
        ${badge}
      </div>
      <dl class="kv">
        <dt>模型</dt><dd title="${escapeHtml(item.model)}">${escapeHtml(item.model || '—')}</dd>
        <dt>Base</dt><dd title="${escapeHtml(item.base_url)}">${escapeHtml(item.base_url || '—')}</dd>
        <dt>Key</dt><dd>${escapeHtml(item.api_key || '未填写')}</dd>
        <dt>参数</dt><dd>temp ${escapeHtml(item.temperature)} · ${escapeHtml(item.max_tokens)} tokens</dd>
      </dl>
      <div class="card-actions">
        <button class="btn btn-sm" data-action="activate" ${item.is_active ? 'disabled' : ''}>启用</button>
        <button class="btn btn-sm" data-action="edit">编辑</button>
        <button class="btn btn-sm btn-danger" data-action="delete">删除</button>
      </div>
    </article>`
}

function renderModels() {
  const grid = $('modelGrid')
  if (!state.models.length) {
    renderEmpty()
    return
  }
  grid.innerHTML = state.models.map(modelCard).join('')
}

function updateActiveChip() {
  const active = state.models.find((m) => m.is_active)
  const chip = $('activeChip')
  if (!active) {
    chip.hidden = true
    return
  }
  chip.hidden = false
  $('activeChipText').textContent = active.model
    ? `${active.name} · ${active.model}`
    : active.name
}

async function loadModels() {
  try {
    const data = await api.listModels()
    state.models = data.items || []
    state.activeId = data.active_id
    renderModels()
    updateActiveChip()
  } catch (err) {
    state.models = []
    state.activeId = null
    renderEmpty()
    updateActiveChip()
    toast(err.message, 'error')
  }
}

async function loadProviders() {
  try {
    const data = await api.listProviders()
    state.providers =
      data && data.items && data.items.length ? data.items : FALLBACK_PROVIDERS
  } catch {
    // 后端未启动时用内置表兜底，下拉框不至于空白
    state.providers = FALLBACK_PROVIDERS
  }
  renderProviderOptions()
}

/* ---------------------------------------------------------------- 抽屉表单 */

function presetOf(provider) {
  const item = state.providers.find((p) => p.provider === provider)
  return { baseUrl: item ? item.base_url : '', model: item ? item.default_model : '' }
}

function renderProviderOptions() {
  const select = $('f-provider')
  const current = select.value
  select.innerHTML = state.providers
    .map(
      (p) =>
        `<option value="${escapeHtml(p.provider)}">${escapeHtml(p.label)}</option>`
    )
    .join('')
  if (current && state.providers.some((p) => p.provider === current)) {
    select.value = current
  }
}

function fillPreset(provider) {
  const preset = presetOf(provider)
  const baseUrl = $('f-baseUrl')
  const model = $('f-model')
  if (!baseUrl.value.trim()) baseUrl.placeholder = preset.baseUrl || 'https://...'
  if (!model.value.trim()) model.placeholder = preset.model || '模型名称'
}

function openDrawer(model) {
  state.editingId = model ? model.id : null
  $('drawerTitle').textContent = model ? '编辑模型配置' : '新建模型配置'
  $('keyHint').hidden = !model
  $('f-apiKey').placeholder = model
    ? model.api_key
      ? `${model.api_key}（留空不修改）`
      : 'sk-...'
    : 'sk-...'

  $('f-name').value = model ? model.name : ''
  const provider = model ? model.provider : state.providers[0] ? state.providers[0].provider : 'deepseek'
  const preset = presetOf(provider)
  $('f-provider').value = provider
  $('f-baseUrl').value = model ? model.base_url : preset.baseUrl
  $('f-model').value = model ? model.model : preset.model
  $('f-apiKey').value = ''
  $('f-temp').value = model ? model.temperature : 0.7
  $('f-tempVal').textContent = $('f-temp').value
  $('f-maxTokens').value = model ? model.max_tokens : 2048

  $('drawer').hidden = false
  $('drawerMask').hidden = false
  $('f-name').focus()
}

function closeDrawer() {
  $('drawer').hidden = true
  $('drawerMask').hidden = true
  state.editingId = null
}

async function submitModel(event) {
  event.preventDefault()

  const name = $('f-name').value.trim()
  if (!name) {
    toast('请填写配置名称', 'error')
    return
  }

  const payload = {
    name,
    provider: $('f-provider').value,
    base_url: $('f-baseUrl').value.trim(),
    api_key: $('f-apiKey').value.trim(),
    model: $('f-model').value.trim(),
    temperature: Number($('f-temp').value),
    max_tokens: Number($('f-maxTokens').value) || 2048
  }

  const saveBtn = $('saveBtn')
  saveBtn.disabled = true
  saveBtn.textContent = '保存中…'

  try {
    if (state.editingId) {
      await api.updateModel(state.editingId, payload)
      toast('配置已更新')
    } else {
      await api.createModel(payload)
      toast('配置已创建')
    }
    closeDrawer()
    await loadModels()
  } catch (err) {
    toast(err.message, 'error')
  } finally {
    saveBtn.disabled = false
    saveBtn.textContent = '保存'
  }
}

/* ---------------------------------------------------------------- 卡片操作 */

async function onCardAction(event) {
  const btn = event.target.closest('button[data-action]')
  if (!btn) return

  const card = event.target.closest('.card')
  const id = Number(card.dataset.id)
  const model = state.models.find((m) => m.id === id)
  if (!model) return

  const action = btn.dataset.action

  try {
    if (action === 'edit') {
      openDrawer(model)
      return
    }

    if (action === 'activate') {
      btn.disabled = true
      await api.activateModel(id)
      await loadModels()
      toast(`已启用「${model.name}」`)
      return
    }

    if (action === 'delete') {
      if (!window.confirm(`确定删除「${model.name}」？`)) return
      await api.deleteModel(id)
      await loadModels()
      toast('已删除')
    }
  } catch (err) {
    toast(err.message, 'error')
    await loadModels()
  }
}

/* ---------------------------------------------------------------- 对话 */

function clearEmptyState() {
  const empty = $('chatLog').querySelector('.empty-state')
  if (empty) empty.remove()
}

function appendMessage(role, text) {
  clearEmptyState()

  const wrap = document.createElement('div')
  wrap.className = `msg ${role}`

  const avatar = document.createElement('div')
  avatar.className = 'avatar'
  avatar.textContent = role === 'user' ? '你' : 'AI'

  const bubble = document.createElement('div')
  bubble.className = 'bubble'
  bubble.textContent = text

  wrap.append(avatar, bubble)
  $('chatLog').appendChild(wrap)
  $('chatLog').scrollTop = $('chatLog').scrollHeight
  return bubble
}

/**
 * 打字机输出：模型有时一秒内就把整段话吐完，直接赋值会变成“唰”地一次出现。
 * 这里把增量放进队列，按固定节奏逐字上屏，视觉上才是打字机。
 * 到达速度与显示速度解耦，后端一次性返回时同样有打字效果。
 */
function createTypewriter(el, { interval = 22, onTick } = {}) {
  el.textContent = ''
  const node = document.createTextNode('')
  el.appendChild(node)

  let queue = ''
  let closed = false
  let timer = null
  let settle = null
  const drained = new Promise((resolve) => (settle = resolve))

  function stop() {
    if (timer) clearInterval(timer)
    timer = null
    settle?.()
  }

  function tick() {
    if (queue) {
      // 积压越多吐得越快：短回答保持字字分明，长回答不至于被拖成慢动作
      const take = queue.length > 300 ? 8 : queue.length > 120 ? 3 : 1
      node.appendData(queue.slice(0, take))
      queue = queue.slice(take)
      onTick?.()
    }
    if (!queue.length && closed) stop()
  }

  timer = setInterval(tick, interval)

  return {
    push(text) {
      queue += text
    },
    /** 数据流已结束：返回一个等队列全部吐完才 resolve 的 Promise */
    close() {
      closed = true
      if (!queue.length) stop()
      return drained
    },
    /** 中断时把剩下的内容一次性铺上，不丢字 */
    flush() {
      if (queue) {
        node.appendData(queue)
        queue = ''
        onTick?.()
      }
      closed = true
      stop()
    }
  }
}

async function sendMessage(event) {
  event?.preventDefault()
  if (state.sending) return

  const input = $('chatInput')
  const message = input.value.trim()
  if (!message) return

  state.sending = true
  $('sendBtn').disabled = true
  input.value = ''
  input.style.height = 'auto'

  appendMessage('user', message)
  const pending = appendMessage('assistant', '思考中…')
  const bubble = pending.closest('.msg')
  const log = $('chatLog')

  // requestId 用来过滤不属于这一轮的推送（旧请求可能还在吐字）
  const requestId = `${Date.now()}-${Math.random().toString(16).slice(2)}`
  let answer = ''
  let typer = null

  /** 第一个增量到达时才把「思考中…」换成打字机，避免首字前的空转 */
  function ensureTyper() {
    if (!typer) {
      pending.classList.add('is-typing')
      typer = createTypewriter(pending, {
        onTick: () => (log.scrollTop = log.scrollHeight)
      })
    }
    return typer
  }

  const unsubscribe = api.onChatStreamEvent((payload) => {
    if (payload.requestId !== requestId || payload.event !== 'delta') return
    const text = payload.data.text || ''
    if (!text) return
    answer += text
    ensureTyper().push(text)
  })

  try {
    await api.chatStream(message, state.activeId, requestId)
    if (!answer) {
      pending.textContent = '（模型没有返回内容）'
    } else {
      // 等最后一个字打完再收尾，否则按钮先亮、字还在冒
      await typer?.close()
    }
  } catch (err) {
    typer?.flush()
    bubble.classList.add('error')
    // 已经收到部分内容时保留它，只把中断原因接在后面
    const shown = pending.textContent
    pending.textContent = shown ? `${shown}\n\n[中断] ${err.message}` : err.message
  } finally {
    unsubscribe?.()
    pending.classList.remove('is-typing')
    state.sending = false
    $('sendBtn').disabled = false
    log.scrollTop = log.scrollHeight
    input.focus()
  }
}

function autoGrow() {
  const input = $('chatInput')
  input.style.height = 'auto'
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`
}

/* ---------------------------------------------------------------- 绑定 */

function bind() {
  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => setView(btn.dataset.view))
  })

  $('newModelBtn').addEventListener('click', () => openDrawer(null))
  $('drawerClose').addEventListener('click', closeDrawer)
  $('cancelBtn').addEventListener('click', closeDrawer)
  $('drawerMask').addEventListener('click', closeDrawer)
  $('modelForm').addEventListener('submit', submitModel)
  $('modelGrid').addEventListener('click', onCardAction)

  $('f-provider').addEventListener('change', (e) => {
    const preset = presetOf(e.target.value)
    $('f-baseUrl').placeholder = preset.baseUrl || 'https://...'
    $('f-model').placeholder = preset.model || '模型名称'
    if (!state.editingId) {
      $('f-baseUrl').value = preset.baseUrl
      $('f-model').value = preset.model
    }
  })

  $('f-temp').addEventListener('input', (e) => {
    $('f-tempVal').textContent = e.target.value
  })

  $('chatForm').addEventListener('submit', sendMessage)
  $('chatInput').addEventListener('input', autoGrow)
  $('chatInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  })

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('drawer').hidden) closeDrawer()
  })
}

async function boot() {
  bind()
  await loadProviders()
  fillPreset($('f-provider').value || 'deepseek')
  await checkHealth()
  await loadModels()
  setInterval(checkHealth, 15000)
}

boot()
