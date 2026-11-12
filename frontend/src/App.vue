<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { createId } from './id.js'
import { prepareVisitorCache } from './visitor-cache.js'
import { sourceScoreLabel, webSourceUrl } from './sources.js'
import { readEventStream } from './stream.js'
import AssistantAnswer from './AssistantAnswer.vue'
import {
  ArrowDownToLine,
  ArrowUp,
  BookOpen,
  Check,
  CheckCheck,
  ChevronDown,
  CircleAlert,
  CircleCheck,
  ExternalLink,
  File,
  FileCode2,
  FileText,
  Globe,
  History,
  Layers3,
  LoaderCircle,
  Menu,
  MessageCircleMore,
  Plus,
  Sparkles,
  Square,
  Trash2,
  Upload,
  X,
} from 'lucide-vue-next'

const API = '/api'

function storedJson(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback
  } catch {
    return fallback
  }
}

const activePage = ref('review')
const materials = ref([])
let savedConversationId = null
let visitorScope = ''
const visitorReady = ref(false)
const selectedIds = ref(new Set())
const messages = ref([])
const conversationId = ref(createId())
const conversations = ref([])
const historyTotal = ref(0)
const historyLoading = ref(false)
const historyError = ref('')
const deletingConversationId = ref('')
const deleteCandidate = ref(null)
const deleteDialog = ref(null)
let previousFocus = null
const activeConversationTitle = ref('')
const switchingConversation = ref(false)
const initializing = ref(true)
const mode = ref('coach')
const question = ref('')
const systemPrompt = ref('')
const webSearchEnabled = ref(false)
const health = ref({ model_configured: false, counts: { tutorial: 0, upload: 0 } })
const loading = ref(false)
const importing = ref(false)
const uploading = ref(false)
const deletingId = ref('')
const error = ref('')
const notice = ref('')
const mobileNav = ref(false)
const evidenceOpen = ref(false)
const materialPicker = ref(null)
const materialQuery = ref('')
const chatBody = ref(null)
const fileInput = ref(null)
const followAnswer = ref(true)
const stopping = ref(false)
let streamController = null
let saveTimer

const busy = computed(() => !visitorReady.value || loading.value || switchingConversation.value || initializing.value || Boolean(deletingConversationId.value))
const selectedMaterials = computed(() => materials.value.filter((item) => selectedIds.value.has(item.id)))
const selectedCount = computed(() => selectedMaterials.value.length)
const filteredMaterials = computed(() => {
  const query = materialQuery.value.trim().toLocaleLowerCase()
  return materials.value.filter((item) => `${item.title} ${item.source}`.toLocaleLowerCase().includes(query))
})
const totalChunks = computed(() => materials.value.reduce((sum, item) => sum + item.chunk_count, 0))
const latestSources = computed(() => [...messages.value].reverse().find((item) => item.role === 'assistant')?.sources || [])
const latestTrace = computed(() => [...messages.value].reverse().find((item) => item.role === 'assistant')?.trace || [])
const canAsk = computed(() => selectedCount.value > 0 || (webSearchEnabled.value && health.value.web_search_configured))
const webSearchHint = computed(() => !health.value.web_search_configured ? '请在项目 .env 配置 TAVILY_API_KEY 后重启后端' : webSearchEnabled.value ? '已开启：优先查资料，资料不足或未选资料时自动联网' : '开启后，资料不足或未选资料时可自动联网')
const currentTitle = computed(() => activeConversationTitle.value || conversations.value.find((item) => item.id === conversationId.value)?.title || '今天想复习什么？')

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, { credentials: 'same-origin', ...options })
  let payload
  try {
    payload = await response.json()
  } catch {
    payload = {}
  }
  if (!response.ok) {
    const failure = new Error(payload.detail || `请求失败（${response.status}）`)
    failure.status = response.status
    throw failure
  }
  return payload
}

async function refresh() {
  try {
    const [materialData, status] = await Promise.all([api('/materials'), api('/health')])
    materials.value = materialData.materials
    health.value = status
    if (!status.web_search_configured) webSearchEnabled.value = false
    const valid = new Set(materials.value.map((item) => item.id))
    selectedIds.value = new Set([...selectedIds.value].filter((id) => valid.has(id)))
  } catch (caught) {
    error.value = `连接复习服务失败：${caught.message}`
  }
}

function saveState() {
  if (!visitorReady.value || initializing.value) return
  // 其他标签页已切换身份时，旧页面不能覆盖新访客的缓存。
  if (localStorage.getItem('xiaohan-visitor-scope') !== visitorScope) return
  localStorage.setItem('xiaohan-conversation', conversationId.value)
  localStorage.setItem('xiaohan-selected-materials', JSON.stringify([...selectedIds.value]))
  localStorage.setItem('xiaohan-messages', JSON.stringify(messages.value.slice(-40)))
  localStorage.setItem('xiaohan-prompt', systemPrompt.value)
  localStorage.setItem('xiaohan-mode', mode.value)
  localStorage.setItem('xiaohan-web-search', JSON.stringify(webSearchEnabled.value))
}

watch([selectedIds, messages, systemPrompt, conversationId, mode, webSearchEnabled], () => {
  if (saveTimer) return
  saveTimer = setTimeout(() => {
    saveTimer = undefined
    saveState()
  }, 250)
}, { deep: true })

watch(deleteCandidate, async (candidate) => {
  if (candidate) {
    previousFocus = document.activeElement
    await nextTick()
    deleteDialog.value?.querySelector('button')?.focus()
  } else {
    previousFocus?.focus()
  }
})

watch([conversationId, activePage, busy], () => {
  if (materialPicker.value) materialPicker.value.open = false
  materialQuery.value = ''
})

function closeMaterialPicker(event) {
  if (materialPicker.value && !materialPicker.value.contains(event.target)) materialPicker.value.open = false
}

async function openMaterialPicker() {
  if (busy.value) return
  evidenceOpen.value = false
  if (materialPicker.value) {
    materialPicker.value.open = true
    await nextTick()
    materialPicker.value.querySelector('input[type="search"]')?.focus()
  }
}

function escapeMaterialPicker() {
  materialPicker.value.open = false
  materialPicker.value.querySelector('summary')?.focus()
}

function dialogKeydown(event) {
  if (event.key === 'Escape' && !deletingConversationId.value) deleteCandidate.value = null
  if (event.key !== 'Tab') return
  const buttons = [...deleteDialog.value.querySelectorAll('button:not(:disabled)')]
  const next = event.shiftKey ? buttons.at(-1) : buttons[0]
  if (!buttons.length || document.activeElement === (event.shiftKey ? buttons[0] : buttons.at(-1))) {
    event.preventDefault()
    next?.focus()
  }
}

async function removeConversation() {
  const conversation = deleteCandidate.value
  if (!conversation || busy.value) return
  deletingConversationId.value = conversation.id
  error.value = ''
  try {
    await api(`/conversations/${encodeURIComponent(conversation.id)}`, { method: 'DELETE' })
    if (conversation.id === conversationId.value) resetConversation()
    conversations.value = conversations.value.filter((item) => item.id !== conversation.id)
    historyTotal.value = Math.max(0, historyTotal.value - 1)
    deleteCandidate.value = null
    notice.value = '已删除这段会话及其对话记忆。'
    await loadHistory()
    saveState()
  } catch (caught) {
    error.value = caught.message
  } finally {
    deletingConversationId.value = ''
  }
}

async function loadHistory(append = false) {
  if (historyLoading.value) return
  historyLoading.value = true
  historyError.value = ''
  try {
    const result = await api(`/conversations?limit=50&offset=${append ? conversations.value.length : 0}`)
    conversations.value = append ? [...conversations.value, ...result.conversations] : result.conversations
    historyTotal.value = result.total
  } catch (caught) {
    historyError.value = caught.message
  } finally {
    historyLoading.value = false
  }
}

function applyConversation(conversation) {
  conversationId.value = conversation.id
  activeConversationTitle.value = conversation.title
  messages.value = conversation.messages
  mode.value = conversation.study_mode
  systemPrompt.value = conversation.prompt || ''
  webSearchEnabled.value = Boolean(conversation.web_search_enabled && health.value.web_search_configured)
  const valid = new Set(materials.value.map((item) => item.id))
  selectedIds.value = new Set(conversation.material_ids.filter((id) => valid.has(id)))
  question.value = ''
  if (selectedIds.value.size < conversation.material_ids.length) {
    notice.value = '这段对话的部分资料已被移除。旧记录仍可查看，继续复习前请调整资料范围。'
  }
}

async function openConversation(id) {
  if (busy.value) return
  switchingConversation.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await api(`/conversations/${encodeURIComponent(id)}`)
    applyConversation(result.conversation)
    activePage.value = 'review'
    mobileNav.value = false
    await scrollToEnd()
  } catch (caught) {
    error.value = caught.message
  } finally {
    switchingConversation.value = false
  }
}

async function initialize() {
  try {
    // 先串行建立 Cookie，再读取会话，避免首次并发请求各自产生不同身份。
    const first = await api('/visitor')
    const confirmed = await api('/visitor')
    if (first.cache_scope !== confirmed.cache_scope) {
      throw new Error('浏览器未保存匿名身份，请允许本站 Cookie 后刷新页面。')
    }
    prepareVisitorCache(localStorage, confirmed.cache_scope)
    visitorScope = confirmed.cache_scope
    savedConversationId = localStorage.getItem('xiaohan-conversation')
    conversationId.value = savedConversationId || createId()
    selectedIds.value = new Set(storedJson('xiaohan-selected-materials', []))
    mode.value = localStorage.getItem('xiaohan-mode') || 'coach'
    systemPrompt.value = localStorage.getItem('xiaohan-prompt') || ''
    webSearchEnabled.value = storedJson('xiaohan-web-search', false) === true
    visitorReady.value = true
    await refresh()
    // 有数据库时只恢复经过后端归属校验的记录；无数据库时使用本浏览器缓存。
    if (!health.value.history_enabled) {
      messages.value = storedJson('xiaohan-messages', []).map((message) => message.streaming ? {
        ...message, streaming: false, failed: true,
        content: `${message.content || ''}\n\n[上次生成已中断]`.trim(),
      } : message)
    }
    await loadHistory()
    const id = savedConversationId || conversations.value[0]?.id
    if (id && health.value.history_enabled) {
      try {
        const result = await api(`/conversations/${encodeURIComponent(id)}`)
        applyConversation(result.conversation)
      } catch (caught) {
        if (caught.status === 404) resetConversation()
        else error.value = `恢复复习记录失败：${caught.message}`
      }
    }
    await scrollToEnd()
  } catch (caught) {
    visitorReady.value = false
    error.value = `初始化复习服务失败：${caught.message}`
  } finally {
    initializing.value = false
  }
}

function toggleMaterial(id) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

function selectAll() {
  selectedIds.value = new Set(materials.value.map((item) => item.id))
}

function clearSelection() {
  selectedIds.value = new Set()
}

async function importTutorial() {
  if (importing.value) return
  importing.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await api('/materials/import-tutorial', { method: 'POST' })
    await refresh()
    for (const item of result.materials || []) selectedIds.value.add(item.id)
    selectedIds.value = new Set(selectedIds.value)
    notice.value = `已导入 ${result.imported} 份教程资料，共 ${result.chunks} 个知识切片。`
    activePage.value = 'materials'
  } catch (caught) {
    error.value = caught.message
  } finally {
    importing.value = false
  }
}

async function uploadFiles(event) {
  const files = [...(event.target?.files || [])]
  if (!files.length || uploading.value) return
  uploading.value = true
  error.value = ''
  notice.value = ''
  const imported = []
  try {
    for (const file of files) {
      const body = new FormData()
      body.append('file', file)
      const result = await api('/materials/upload', { method: 'POST', body })
      imported.push(result.material)
    }
    await refresh()
    selectedIds.value = new Set([...selectedIds.value, ...imported.map((item) => item.id)])
    notice.value = `已处理 ${imported.length} 份学习资料并写入向量库。`
  } catch (caught) {
    error.value = caught.message
    await refresh()
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

async function removeMaterial(material) {
  if (deletingId.value) return
  deletingId.value = material.id
  error.value = ''
  try {
    await api(`/materials/${material.id}`, { method: 'DELETE' })
    selectedIds.value.delete(material.id)
    selectedIds.value = new Set(selectedIds.value)
    materials.value = materials.value.filter((item) => item.id !== material.id)
    notice.value = `已从学习资料和向量库中移除“${material.title}”。`
  } catch (caught) {
    error.value = caught.message
  } finally {
    deletingId.value = ''
  }
}

async function ask(text = question.value) {
  const value = text.trim()
  if (!value || busy.value) return
  if (!canAsk.value) {
    error.value = '请选择至少一份学习资料，或开启联网搜索。'
    return
  }
  error.value = ''
  notice.value = ''
  question.value = ''
  const userMessage = { role: 'user', content: value, id: createId() }
  const assistantId = createId()
  messages.value.push(userMessage)
  messages.value.push({ role: 'assistant', content: '', id: assistantId, sources: [], trace: [], streaming: true })
  if (!activeConversationTitle.value) activeConversationTitle.value = value.slice(0, 40)
  loading.value = true
  stopping.value = false
  followAnswer.value = true
  streamController = new AbortController()
  let terminal = false
  const assistant = () => messages.value.find((item) => item.id === assistantId)
  await scrollToEnd()
  try {
    const response = await fetch(`${API}/review/stream`, {
      credentials: 'same-origin',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: streamController.signal,
      body: JSON.stringify({
        question: value,
        conversation_id: conversationId.value,
        material_ids: [...selectedIds.value],
        study_mode: mode.value,
        prompt: systemPrompt.value || null,
        web_search_enabled: webSearchEnabled.value,
      }),
    })
    await readEventStream(response, (event, result) => {
      const message = assistant()
      if (result.user_message) {
        const index = messages.value.findIndex((item) => item.id === userMessage.id)
        if (index >= 0) messages.value[index] = result.user_message
      }
      if (event === 'token') message.content += result.text
      else if (event === 'reset') message.content = ''
      else if (event === 'state') Object.assign(message, result)
      else if (event === 'done' || event === 'error') {
        terminal = true
        Object.assign(message, result.assistant_message || {
          content: event === 'done' ? result.answer : result.content,
          sources: result.sources || [], trace: result.trace || [], failed: event === 'error',
        }, { streaming: false })
        if (event === 'error') error.value = result.detail
      }
      if (followAnswer.value) scrollToEnd()
    })
    if (!terminal) throw new Error('回答连接已中断，请稍后重试。')
  } catch (caught) {
    if (!terminal) {
      const message = assistant()
      const stopped = caught.name === 'AbortError'
      const detail = stopped ? '已停止生成' : caught.message
      if (!stopped) error.value = detail
      message.content = stopped
        ? (message.content ? `${message.content}\n\n[已停止生成]` : '已停止生成。')
        : `${message.content ? message.content + '\n\n' : ''}这次复习没有完成：${detail}`
      message.failed = true
      message.streaming = false
    }
  } finally {
    streamController = null
    loading.value = false
    stopping.value = false
    saveState()
    await loadHistory()
    if (followAnswer.value) await scrollToEnd()
  }
}

function stopAnswer() {
  if (!streamController || stopping.value) return
  stopping.value = true
  streamController.abort()
}

function startQuiz() {
  if (busy.value) return
  mode.value = 'quiz'
  ask(selectedCount.value ? '从我选中的学习资料里开始抽查。先出一道题，不要先告诉我答案。' : '先联网搜索 LangChain Agent 的官方资料，再出一道题，不要先告诉我答案。')
}

function newConversation() {
  if (busy.value) return
  resetConversation()
}

function resetConversation() {
  messages.value = []
  conversationId.value = createId()
  activeConversationTitle.value = ''
  activePage.value = 'review'
  question.value = ''
  error.value = ''
  notice.value = ''
}

function changeMode(value) {
  if (busy.value) return
  mode.value = value
}

async function scrollToEnd() {
  await nextTick()
  if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight
}

function trackScroll() {
  const element = chatBody.value
  if (element) followAnswer.value = element.scrollHeight - element.scrollTop - element.clientHeight < 100
}

function formatDate(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit' }).format(new Date(value))
}

function sourceIcon(source = '') {
  return source.endsWith('.ipynb') || source.endsWith('.py') ? FileCode2 : FileText
}

function citationIcon(source) {
  return source.type === 'web' ? Globe : sourceIcon(source.source)
}

function openCitation(source) {
  if (source.type !== 'web') activePage.value = 'materials'
}

onMounted(() => {
  document.addEventListener('pointerdown', closeMaterialPicker)
  initialize()
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', closeMaterialPicker)
  streamController?.abort()
  clearTimeout(saveTimer)
  saveState()
})
</script>

<template>
  <div class="shell">
    <div v-if="mobileNav" class="scrim" @click="mobileNav = false"></div>
    <aside class="rail" :class="{ 'rail-open': mobileNav }">
      <div class="identity">
        <div class="mascot"><Sparkles :size="19" /></div>
        <div class="identity-text"><strong>小晗</strong><span>学习搭子</span></div>
        <button class="icon-button rail-close" aria-label="关闭导航" @click="mobileNav = false"><X :size="18" /></button>
      </div>

      <div class="nav-caption">工作台</div>
      <nav class="primary-nav" aria-label="主导航">
        <button :class="{ current: activePage === 'review' }" @click="activePage = 'review'; mobileNav = false"><MessageCircleMore :size="17" /><span>复盘学习</span><span v-if="selectedCount" class="nav-count">{{ selectedCount }}</span></button>
        <button :class="{ current: activePage === 'materials' }" @click="activePage = 'materials'; mobileNav = false"><BookOpen :size="17" /><span>学习资料</span><span class="nav-count">{{ materials.length }}</span></button>
      </nav>

      <div class="material-nav-head"><span>本次复习</span><button @click="activePage = 'materials'">管理</button></div>
      <div class="selected-nav-list">
        <button v-for="material in selectedMaterials.slice(0, 6)" :key="material.id" class="selected-nav-item" :title="material.title" @click="activePage = 'materials'">
          <component :is="sourceIcon(material.source)" :size="14" /><span>{{ material.title }}</span><Check :size="12" class="selected-check" />
        </button>
        <div v-if="!selectedCount" class="empty-selection">还没有选择资料</div>
      </div>

      <div class="history-head"><span><History :size="13" />历史复习 <small>{{ historyTotal }}</small></span><button :disabled="historyLoading || busy" @click="loadHistory()">刷新</button></div>
      <div class="history-list" aria-label="历史复习列表" :aria-busy="historyLoading">
        <div v-if="historyError" class="history-empty history-error"><span>{{ historyError }}</span><button :disabled="historyLoading" @click="loadHistory()">重新加载</button></div>
        <div v-else-if="historyLoading && !conversations.length" class="history-empty"><LoaderCircle :size="13" class="spin" />正在读取历史记录…</div>
        <div v-else-if="!conversations.length" class="history-empty">{{ health.history_enabled ? '开始一轮复习后，会自动保存在这里。' : '配置 PostgreSQL 后即可保存历史复习。' }}</div>
        <div v-for="conversation in conversations" :key="conversation.id" class="history-row" :class="{ current: conversation.id === conversationId }">
          <button class="history-item" :disabled="busy" :aria-current="conversation.id === conversationId ? 'true' : undefined" :title="conversation.title" @click="openConversation(conversation.id)">
            <strong>{{ conversation.title }}</strong><span><time :datetime="conversation.updated_at">{{ formatDate(conversation.updated_at) }}</time><i>·</i>{{ conversation.study_mode === 'quiz' ? '抽查' : '复盘' }}<i>·</i>{{ conversation.message_count }} 条消息</span>
          </button>
          <button class="history-delete icon-button" :disabled="busy || historyLoading" :aria-label="`删除会话：${conversation.title}`" title="删除会话" @click="deleteCandidate = conversation; error = ''"><Trash2 :size="14" /></button>
        </div>
        <button v-if="conversations.length < historyTotal" class="history-more" :disabled="historyLoading || busy" @click="loadHistory(true)">{{ historyLoading ? '正在读取…' : '加载更多' }}</button>
      </div>

      <div class="rail-bottom">
        <div class="local-model"><span class="model-dot" :class="{ ready: health.model_configured }"></span><div><strong>{{ health.model || 'DeepSeek' }}</strong><span>{{ health.model_configured ? '对话模型可用' : '请检查项目 .env' }}</span></div></div>
        <div class="storage-line"><Layers3 :size="14" /><span>Milvus Lite</span><strong>{{ totalChunks }}</strong><span>切片</span></div>
      </div>
    </aside>

    <main class="main-area">
      <header class="topbar">
        <div class="topbar-left"><button class="icon-button mobile-menu" aria-label="打开导航" @click="mobileNav = true"><Menu :size="18" /></button><h1 class="topbar-title" :title="activePage === 'materials' ? '学习资料' : currentTitle">{{ activePage === 'materials' ? '学习资料' : currentTitle }}</h1></div>
        <div class="topbar-right"><span class="connection"><span class="model-dot" :class="{ ready: health.model_configured }"></span>{{ health.model_configured ? 'DeepSeek 已连接' : '模型未连接' }}</span><button v-if="activePage === 'review'" class="new-session" :disabled="busy" @click="newConversation"><Plus :size="15" /> 新复习</button></div>
      </header>

      <section v-if="activePage === 'materials'" class="materials-page">
        <div class="page-heading">
          <div><div class="page-kicker"><BookOpen :size="14" /> 学习资料</div><h1>你的复习素材</h1><p>把学过的内容放进资料库，复习时选择想看的范围。</p></div>
          <button class="primary-button" :disabled="importing" @click="importTutorial"><LoaderCircle v-if="importing" :size="16" class="spin" /><ArrowDownToLine v-else :size="16" />{{ importing ? '正在导入并建索引' : '导入笔记' }}</button>
        </div>

        <div class="material-stats">
          <div class="stat"><span>资料总数</span><strong>{{ materials.length }}</strong><small>份学习资料</small></div>
          <div class="stat"><span>已选复习</span><strong>{{ selectedCount }}</strong><small>份资料</small></div>
          <div class="stat"><span>Milvus 切片</span><strong>{{ totalChunks }}</strong><small>个向量片段</small></div>
          <div class="stat stat-model"><span>Embedding</span><strong>{{ health.embedding || '本地模型' }}</strong><small>在本机生成向量</small></div>
        </div>

        <div class="upload-strip">
          <div class="upload-symbol"><Upload :size="18" /></div>
          <div class="upload-description"><strong>添加自己的学习文件</strong><span>PDF、DOCX、Markdown、TXT、Python、CSV、JSON、IPYNB，单个文件不超过 25 MB</span></div>
          <input ref="fileInput" type="file" hidden multiple accept=".pdf,.docx,.md,.txt,.py,.csv,.json,.ipynb" @change="uploadFiles" />
          <button class="secondary-button" :disabled="uploading" @click="fileInput?.click()"><LoaderCircle v-if="uploading" :size="15" class="spin" /><Plus v-else :size="15" />{{ uploading ? '处理中' : '选择文件' }}</button>
        </div>

        <div v-if="error || notice" class="notice-line" :class="{ 'notice-error': error }"><CircleAlert v-if="error" :size="15" /><CircleCheck v-else :size="15" /><span>{{ error || notice }}</span><button aria-label="关闭提示" @click="error = ''; notice = ''"><X :size="14" /></button></div>

        <div class="list-heading"><div><h2>资料库</h2><span>{{ materials.length }} 份</span></div><div class="list-tools"><button @click="selectAll"><CheckCheck :size="14" />全选</button><button @click="clearSelection">清空选择</button></div></div>

        <div v-if="materials.length" class="materials-list">
          <article v-for="material in materials" :key="material.id" class="material-row" :class="{ checked: selectedIds.has(material.id) }">
            <button class="selection-box" :aria-label="selectedIds.has(material.id) ? '取消选择' : '选择资料'" @click="toggleMaterial(material.id)"><Check v-if="selectedIds.has(material.id)" :size="14" /></button>
            <div class="file-kind"><component :is="sourceIcon(material.source)" :size="18" /></div>
            <div class="material-details"><div class="material-name"><strong>{{ material.title }}</strong><span class="origin-tag" :class="`origin-${material.origin}`">{{ material.origin === 'tutorial' ? '教程' : '上传' }}</span></div><div class="material-meta"><span :title="material.source">{{ material.source }}</span><i></i><span>{{ material.chunk_count }} 个切片</span><i></i><span>{{ formatDate(material.created_at) }}</span></div></div>
            <button class="delete-button" :disabled="deletingId === material.id" :aria-label="`删除 ${material.title}`" title="从项目资料库和向量索引中删除" @click="removeMaterial(material)"><LoaderCircle v-if="deletingId === material.id" :size="16" class="spin" /><Trash2 v-else :size="16" /></button>
          </article>
        </div>
        <div v-else class="empty-library"><div><BookOpen :size="23" /></div><strong>资料库还是空的</strong><span>先导入教程笔记，或添加自己的学习文件。</span><button class="secondary-button" :disabled="importing" @click="importTutorial"><ArrowDownToLine :size="15" />导入笔记</button></div>

        <div class="materials-footer"><span><CircleCheck :size="13" />原始教程笔记不会被修改</span><button :disabled="!selectedCount" @click="activePage = 'review'">用 {{ selectedCount }} 份资料开始复习 <ChevronDown :size="14" /></button></div>
      </section>

      <section v-else class="review-page">
        <div class="review-toolbar" aria-label="复习设置">
          <div class="mode-control" role="group" aria-label="学习模式"><button :disabled="busy" :class="{ active: mode === 'coach' }" :aria-pressed="mode === 'coach'" @click="changeMode('coach')"><Sparkles :size="14" />带我复盘</button><button :disabled="busy" :class="{ active: mode === 'quiz' }" :aria-pressed="mode === 'quiz'" @click="changeMode('quiz')"><CheckCheck :size="14" />抽查我</button></div>
          <details ref="materialPicker" class="material-picker" @keydown.esc.stop="escapeMaterialPicker">
            <summary class="material-scope" :aria-disabled="busy" aria-controls="conversation-materials" :title="selectedCount ? selectedMaterials.map((item) => item.title).join('、') : '选择本轮学习资料'" @click="busy && $event.preventDefault()"><BookOpen :size="14" /><span>{{ selectedCount ? `${selectedCount} 份资料` : '选择资料' }}</span><ChevronDown :size="12" /></summary>
            <div id="conversation-materials" class="material-dropdown" role="group" aria-label="当前会话的学习资料">
              <div class="material-dropdown-head"><div><strong>本次对话的资料</strong><span>已选 {{ selectedCount }} / {{ materials.length }} 份，调整后用于下一条消息</span></div><button class="icon-button" aria-label="收起资料选择" @click="escapeMaterialPicker"><X :size="15" /></button></div>
              <input v-model="materialQuery" type="search" class="material-filter" aria-label="搜索学习资料" placeholder="搜索资料名称…" />
              <div class="material-dropdown-actions"><button :disabled="busy || !materials.length" @click="selectAll">全选</button><button :disabled="busy || !selectedCount" @click="clearSelection">清空选择</button></div>
              <div class="material-dropdown-list">
                <label v-for="material in filteredMaterials" :key="material.id" class="material-option" :class="{ selected: selectedIds.has(material.id) }"><input type="checkbox" :checked="selectedIds.has(material.id)" :disabled="busy" @change="toggleMaterial(material.id)" /><component :is="sourceIcon(material.source)" :size="15" /><span><strong>{{ material.title }}</strong><small>{{ material.chunk_count }} 个片段 · {{ material.origin === 'upload' ? '上传资料' : '学习笔记' }}</small></span></label>
                <p v-if="!filteredMaterials.length" class="material-dropdown-empty">{{ materials.length ? '没有找到匹配的资料' : '还没有学习资料，可以先上传文件。' }}</p>
              </div>
              <div class="material-dropdown-foot"><span>{{ webSearchEnabled ? '优先查所选资料，资料不足或未选时自动联网。' : '联网已关闭，仅查询所选资料。' }}</span><button @click="activePage = 'materials'">上传 / 管理资料</button></div>
            </div>
          </details>
          <button class="evidence-toggle" :aria-expanded="evidenceOpen" aria-controls="review-evidence-panel" @click="evidenceOpen = !evidenceOpen; materialPicker.open = false"><Layers3 :size="14" /><span>依据</span><ChevronDown :size="12" :class="{ 'chevron-open': evidenceOpen }" /></button>
        </div>

        <div v-if="notice" class="notice-line"><CircleCheck :size="15" /><span>{{ notice }}</span><button aria-label="关闭提示" @click="notice = ''"><X :size="14" /></button></div>

        <div class="conversation-layout">
          <div class="chat-column">
            <div ref="chatBody" class="chat-scroll" @scroll="trackScroll">
              <div v-if="initializing || switchingConversation" class="conversation-loading" role="status"><LoaderCircle :size="18" class="spin" />正在恢复复习记录…</div>
              <div v-else-if="!messages.length" class="start-state">
                <div class="xiaohan-avatar"><Sparkles :size="22" /></div>
                <div class="start-label">小晗在这里</div>
                <h2>{{ mode === 'quiz' ? '准备好接受抽查了吗？' : '我们从你学过的开始。' }}</h2>
                <p>{{ selectedCount ? `已选 ${selectedCount} 份资料，选一个主题开始吧。` : webSearchEnabled ? '联网搜索已开启，告诉小晗你想了解的主题。' : '先选几份学习资料，或开启联网搜索。' }}</p>
                <div class="starter-prompts">
                  <button :disabled="!canAsk" @click="ask(selectedCount ? '用简单的话帮我梳理所选资料里的核心概念，并结合代码讲解。' : '联网查询 LangChain Agent 官方文档，帮我梳理核心概念。')"><BookOpen :size="15" /><span>帮我梳理核心概念</span></button>
                  <button :disabled="!canAsk" @click="ask('带我复习 RAG 的完整流程，从文档加载、切分到向量检索。')"><Layers3 :size="15" /><span>复盘 RAG 完整流程</span></button>
                  <button :disabled="!canAsk" @click="startQuiz"><CheckCheck :size="15" /><span>{{ selectedCount ? '从资料里抽查我' : '搜官方资料抽查我' }}</span></button>
                  <button v-if="webSearchEnabled" :disabled="!canAsk" @click="ask('联网查找 LangChain 最新的官方 Agent 文档，讲讲 create_agent 的用法，并附上来源链接。')"><Globe :size="15" /><span>查最新官方文档</span></button>
                  <button v-else :disabled="!canAsk" @click="ask('用一个简单例子比较 LangChain Tool 和 MCP Tool。')"><Sparkles :size="15" /><span>比较 Tool 和 MCP</span></button>
                </div>
                <button v-if="!selectedCount" class="choose-materials" :disabled="busy" @click="openMaterialPicker"><BookOpen :size="14" />选择本次资料</button>
              </div>

              <div v-for="message in messages" :key="message.id" class="message-line" :class="`line-${message.role}`">
                <div v-if="message.role === 'assistant'" class="chat-avatar"><Sparkles :size="15" /></div>
                <div class="bubble-column">
                  <span class="speaker">{{ message.role === 'user' ? '我' : '小晗' }}</span>
                  <div v-if="message.role === 'user'" class="bubble">{{ message.content }}</div>
                  <AssistantAnswer v-else :content="message.content" :failed="message.failed" :streaming="message.streaming">
                    <div v-if="message.streaming" class="typing" role="status"><LoaderCircle :size="13" class="spin" /><span>{{ message.content ? '正在生成…' : message.trace?.at(-1)?.label || '小晗正在准备回答…' }}</span></div>
                    <div v-if="message.sources?.length" class="citation-list">
                      <div class="citation-label">参考来源</div>
                      <component :is="webSourceUrl(source) ? 'a' : 'button'" v-for="(source, index) in message.sources" :key="`${source.source}-${index}`" class="citation" :href="webSourceUrl(source) || undefined" :target="webSourceUrl(source) ? '_blank' : undefined" rel="noopener noreferrer" @click="openCitation(source)">
                        <component :is="citationIcon(source)" :size="13" />
                        <span>{{ source.title || source.source }}</span>
                        <small v-if="source.type === 'web'">网页</small><small v-else-if="sourceScoreLabel(source)">{{ sourceScoreLabel(source) }}</small>
                        <ExternalLink v-if="webSourceUrl(source)" :size="11" />
                      </component>
                    </div>
                  </AssistantAnswer>
                </div>
              </div>

            </div>

            <div class="composer-area">
              <div v-if="error" class="chat-error"><CircleAlert :size="14" /><span>{{ error }}</span><button @click="error = ''"><X :size="14" /></button></div>
              <form class="composer" @submit.prevent="ask()">
                <textarea v-model="question" rows="2" :disabled="busy" :placeholder="mode === 'quiz' ? '回答小晗的问题，或告诉她想抽查哪个主题…' : webSearchEnabled ? '问一个想了解的主题，也可以查最新资料…' : '问一个你想复习的概念…'" @keydown.enter.exact.prevent="ask()"></textarea>
                <div class="composer-bottom"><div class="composer-left">
                  <button type="button" class="web-search-toggle" :class="{ active: webSearchEnabled }" :aria-pressed="webSearchEnabled" :disabled="busy || !health.web_search_configured" :title="webSearchHint" @click="webSearchEnabled = !webSearchEnabled"><Globe :size="13" /><span>{{ webSearchEnabled ? '联网已开' : '联网搜索' }}</span></button>
                  <button type="button" class="prompt-toggle" :disabled="busy" @click="systemPrompt = systemPrompt ? '' : '你叫小晗，是耐心的学习搭子。按照本轮检索规则查询来源，用简单中文结合代码讲清楚概念，再一次只问一个问题检查理解。'">{{ systemPrompt ? '自定义提示词已启用' : '学习提示词' }}<ChevronDown :size="12" /></button>
                  <span class="enter-hint">回车发送</span>
                </div><button v-if="loading" class="send-button" type="button" :disabled="stopping" aria-label="停止生成" title="停止生成" @click="stopAnswer"><Square :size="13" fill="currentColor" /></button><button v-else class="send-button" type="submit" :disabled="busy || !question.trim() || !canAsk" aria-label="发送问题"><ArrowUp :size="18" /></button></div>
              </form>
              <textarea v-if="systemPrompt" v-model="systemPrompt" class="prompt-edit" aria-label="自定义学习提示词" placeholder="写下你希望小晗如何带你复习…"></textarea>
              <div class="local-note"><CircleCheck :size="12" /><span>{{ webSearchEnabled ? '联网查询会发送给 Tavily，检索摘要与问题交给 DeepSeek 整理' : '资料切片和向量在本地；复习问题及检索片段会发送给 DeepSeek' }}<template v-if="health.history_enabled">；对话及 IP 将保存，管理员可查看。</template></span></div>
            </div>
          </div>

          <aside id="review-evidence-panel" class="evidence-panel" :class="{ 'evidence-open': evidenceOpen }" aria-label="依据与过程">
            <div class="evidence-head"><strong>依据与过程</strong><span class="agent-badge"><Sparkles :size="12" />Agent</span><button class="evidence-close icon-button" aria-label="收起依据" @click="evidenceOpen = false"><X :size="15" /></button></div>
            <div id="review-evidence" class="evidence-scroll">
              <div class="process-heading"><span>Agent 工具过程</span><span v-if="latestTrace.length" class="done-label"><span></span>本轮记录</span></div>
              <div v-if="latestTrace.length" class="process-list"><div v-for="(step, index) in latestTrace" :key="index" class="process-step" :class="{ 'process-failed': step.status === 'error' }"><div class="step-mark"><CircleAlert v-if="step.status === 'error'" :size="11" /><Check v-else :size="11" /></div><div><strong>{{ step.label }}</strong><span>{{ step.detail }}</span></div></div></div>
              <div v-else class="process-empty"><Layers3 :size="16" /><span>新的问答会在这里显示实际工具调用记录。</span></div>

              <div class="evidence-title"><span>资料与网页来源</span><span>{{ latestSources.length }}</span></div>
              <div v-if="latestSources.length" class="evidence-list"><article v-for="(source, index) in latestSources" :key="`${source.source}-${index}`" class="evidence-card"><div class="evidence-card-title"><component :is="citationIcon(source)" :size="13" /><strong>{{ source.title || source.source }}</strong><small v-if="source.type === 'web'">网页</small><small v-else-if="sourceScoreLabel(source)">{{ sourceScoreLabel(source) }}</small></div><p>{{ source.text }}</p><a v-if="webSourceUrl(source)" class="evidence-path evidence-link" :href="webSourceUrl(source)" target="_blank" rel="noopener noreferrer">{{ source.url }} ↗</a><span v-else class="evidence-path">{{ source.source }}</span></article></div>
              <div v-else class="no-evidence">还没有检索片段</div>

              <div class="selection-box-panel"><div><BookOpen :size="14" /><strong>资料范围</strong></div><span>{{ selectedCount }} 份资料</span><button :disabled="busy" @click="openMaterialPicker">更改选择</button></div>
              <div class="model-note"><span class="model-dot" :class="{ ready: health.model_configured }"></span><span>{{ health.model_configured ? `${health.model} · OpenAI 兼容接口` : '请在项目 .env 配置 DeepSeek' }}</span></div>
            </div>
          </aside>
        </div>
      </section>
    </main>
    <div v-if="deleteCandidate" class="dialog-backdrop" @click.self="!deletingConversationId && (deleteCandidate = null)" @keydown="dialogKeydown">
      <section ref="deleteDialog" class="delete-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-dialog-title">
        <h2 id="delete-dialog-title">删除这段会话？</h2>
        <p>“{{ deleteCandidate.title }}”的消息和对话记忆将一并删除，无法恢复。上传的学习资料会保留。</p>
        <p v-if="error" class="dialog-error" role="alert">{{ error }}</p>
        <div class="dialog-actions"><button class="secondary-button" :disabled="Boolean(deletingConversationId)" autofocus @click="deleteCandidate = null">取消</button><button class="primary-button" :disabled="Boolean(deletingConversationId)" @click="removeConversation"><LoaderCircle v-if="deletingConversationId" :size="14" class="spin" />{{ deletingConversationId ? '正在删除…' : '确认删除' }}</button></div>
      </section>
    </div>
  </div>
</template>
