<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { createId } from './id.js'
import {
  ArrowDownToLine,
  ArrowUp,
  BookOpen,
  Check,
  CheckCheck,
  ChevronDown,
  CircleAlert,
  CircleCheck,
  File,
  FileCode2,
  FileText,
  History,
  Layers3,
  LoaderCircle,
  Menu,
  MessageCircleMore,
  Plus,
  Sparkles,
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
const savedConversationId = localStorage.getItem('xiaohan-conversation')
const selectedIds = ref(new Set(storedJson('xiaohan-selected-materials', [])))
const messages = ref(storedJson('xiaohan-messages', []))
const conversationId = ref(savedConversationId || createId())
const conversations = ref([])
const historyTotal = ref(0)
const historyLoading = ref(false)
const historyError = ref('')
const activeConversationTitle = ref('')
const switchingConversation = ref(false)
const initializing = ref(true)
const mode = ref(localStorage.getItem('xiaohan-mode') || 'coach')
const question = ref('')
const systemPrompt = ref(localStorage.getItem('xiaohan-prompt') || '')
const health = ref({ model_configured: false, counts: { tutorial: 0, upload: 0 } })
const loading = ref(false)
const importing = ref(false)
const uploading = ref(false)
const deletingId = ref('')
const error = ref('')
const notice = ref('')
const mobileNav = ref(false)
const evidenceOpen = ref(false)
const chatBody = ref(null)
const fileInput = ref(null)

const busy = computed(() => loading.value || switchingConversation.value || initializing.value)
const selectedMaterials = computed(() => materials.value.filter((item) => selectedIds.value.has(item.id)))
const selectedCount = computed(() => selectedMaterials.value.length)
const totalChunks = computed(() => materials.value.reduce((sum, item) => sum + item.chunk_count, 0))
const latestSources = computed(() => [...messages.value].reverse().find((item) => item.role === 'assistant')?.sources || [])
const currentTitle = computed(() => activeConversationTitle.value || conversations.value.find((item) => item.id === conversationId.value)?.title || '今天想复习什么？')

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, options)
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
    const valid = new Set(materials.value.map((item) => item.id))
    selectedIds.value = new Set([...selectedIds.value].filter((id) => valid.has(id)))
  } catch (caught) {
    error.value = `连接复习服务失败：${caught.message}`
  }
}

function saveState() {
  localStorage.setItem('xiaohan-conversation', conversationId.value)
  localStorage.setItem('xiaohan-selected-materials', JSON.stringify([...selectedIds.value]))
  localStorage.setItem('xiaohan-messages', JSON.stringify(messages.value.slice(-40)))
  localStorage.setItem('xiaohan-prompt', systemPrompt.value)
  localStorage.setItem('xiaohan-mode', mode.value)
}

watch([selectedIds, messages, systemPrompt, conversationId, mode], saveState, { deep: true })

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
    await refresh()
    await loadHistory()
    const id = savedConversationId || conversations.value[0]?.id
    if (id && health.value.history_enabled) {
      try {
        const result = await api(`/conversations/${encodeURIComponent(id)}`)
        applyConversation(result.conversation)
      } catch (caught) {
        if (caught.status !== 404) error.value = `恢复复习记录失败：${caught.message}`
      }
    }
    await scrollToEnd()
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
  if (!selectedCount.value) {
    error.value = '先到“学习资料”选择至少一份资料。'
    return
  }
  error.value = ''
  notice.value = ''
  question.value = ''
  const userMessage = { role: 'user', content: value, id: createId() }
  messages.value.push(userMessage)
  loading.value = true
  await scrollToEnd()
  try {
    const result = await api('/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: value,
        conversation_id: conversationId.value,
        material_ids: [...selectedIds.value],
        study_mode: mode.value,
        prompt: systemPrompt.value || null,
      }),
    })
    if (result.user_message) {
      const index = messages.value.findIndex((item) => item.id === userMessage.id)
      if (index >= 0) messages.value[index] = result.user_message
    }
    messages.value.push(result.assistant_message || {
      role: 'assistant',
      content: result.answer,
      sources: result.sources,
      trace: result.trace,
      id: createId(),
    })
  } catch (caught) {
    error.value = caught.message
    messages.value.push({ role: 'assistant', content: `这次复习没有完成：${caught.message}`, failed: true, id: createId() })
  } finally {
    await loadHistory()
    loading.value = false
    await scrollToEnd()
  }
}

function startQuiz() {
  if (busy.value) return
  mode.value = 'quiz'
  ask('从我选中的学习资料里开始抽查。先出一道题，不要先告诉我答案。')
}

function newConversation() {
  if (busy.value) return
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

function formatDate(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit' }).format(new Date(value))
}

function sourceIcon(source) {
  return source.endsWith('.ipynb') || source.endsWith('.py') ? FileCode2 : FileText
}

onMounted(initialize)
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
        <button v-for="conversation in conversations" :key="conversation.id" class="history-item" :class="{ current: conversation.id === conversationId }" :disabled="busy" :aria-current="conversation.id === conversationId ? 'true' : undefined" :title="conversation.title" @click="openConversation(conversation.id)">
          <strong>{{ conversation.title }}</strong><span><time :datetime="conversation.updated_at">{{ formatDate(conversation.updated_at) }}</time><i>·</i>{{ conversation.study_mode === 'quiz' ? '抽查' : '复盘' }}<i>·</i>{{ conversation.message_count }} 条消息</span>
        </button>
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
          <button class="primary-button" :disabled="importing" @click="importTutorial"><LoaderCircle v-if="importing" :size="16" class="spin" /><ArrowDownToLine v-else :size="16" />{{ importing ? '正在导入并建索引' : '导入 LangChain 笔记' }}</button>
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
        <div v-else class="empty-library"><div><BookOpen :size="23" /></div><strong>资料库还是空的</strong><span>先导入教程笔记，或添加自己的学习文件。</span><button class="secondary-button" :disabled="importing" @click="importTutorial"><ArrowDownToLine :size="15" />导入 LangChain 笔记</button></div>

        <div class="materials-footer"><span><CircleCheck :size="13" />原始教程笔记不会被修改</span><button :disabled="!selectedCount" @click="activePage = 'review'">用 {{ selectedCount }} 份资料开始复习 <ChevronDown :size="14" /></button></div>
      </section>

      <section v-else class="review-page">
        <div class="review-toolbar" aria-label="复习设置">
          <div class="mode-control" role="group" aria-label="学习模式"><button :disabled="busy" :class="{ active: mode === 'coach' }" :aria-pressed="mode === 'coach'" @click="changeMode('coach')"><Sparkles :size="14" />带我复盘</button><button :disabled="busy" :class="{ active: mode === 'quiz' }" :aria-pressed="mode === 'quiz'" @click="changeMode('quiz')"><CheckCheck :size="14" />抽查我</button></div>
          <button class="material-scope" :title="selectedCount ? selectedMaterials.map((item) => item.title).join('、') : '选择本轮学习资料'" @click="activePage = 'materials'"><BookOpen :size="14" /><span>{{ selectedCount ? `${selectedCount} 份资料` : '选择资料' }}</span><ChevronDown :size="12" /></button>
          <button class="evidence-toggle" :aria-expanded="evidenceOpen" aria-controls="review-evidence-panel" @click="evidenceOpen = !evidenceOpen"><Layers3 :size="14" /><span>依据</span><ChevronDown :size="12" :class="{ 'chevron-open': evidenceOpen }" /></button>
        </div>

        <div v-if="notice" class="notice-line"><CircleCheck :size="15" /><span>{{ notice }}</span><button aria-label="关闭提示" @click="notice = ''"><X :size="14" /></button></div>

        <div class="conversation-layout">
          <div class="chat-column">
            <div ref="chatBody" class="chat-scroll">
              <div v-if="initializing || switchingConversation" class="conversation-loading" role="status"><LoaderCircle :size="18" class="spin" />正在恢复复习记录…</div>
              <div v-else-if="!messages.length" class="start-state">
                <div class="xiaohan-avatar"><Sparkles :size="22" /></div>
                <div class="start-label">小晗在这里</div>
                <h2>{{ mode === 'quiz' ? '准备好接受抽查了吗？' : '我们从你学过的开始。' }}</h2>
                <p>{{ selectedCount ? `已选 ${selectedCount} 份资料，选一个主题开始吧。` : '先选几份学习资料，小晗会围绕资料陪你复习。' }}</p>
                <div class="starter-prompts">
                  <button :disabled="!selectedCount" @click="ask('用简单的话帮我梳理所选资料里的核心概念，并结合代码讲解。')"><BookOpen :size="15" /><span>帮我梳理核心概念</span></button>
                  <button :disabled="!selectedCount" @click="ask('带我复习 RAG 的完整流程，从文档加载、切分到向量检索。')"><Layers3 :size="15" /><span>复盘 RAG 完整流程</span></button>
                  <button :disabled="!selectedCount" @click="startQuiz"><CheckCheck :size="15" /><span>从资料里抽查我</span></button>
                  <button :disabled="!selectedCount" @click="ask('用一个简单例子比较 LangChain Tool 和 MCP Tool。')"><Sparkles :size="15" /><span>比较 Tool 和 MCP</span></button>
                </div>
                <button v-if="!selectedCount" class="choose-materials" @click="activePage = 'materials'"><BookOpen :size="14" />去选择学习资料</button>
              </div>

              <div v-for="message in messages" :key="message.id" class="message-line" :class="`line-${message.role}`">
                <div v-if="message.role === 'assistant'" class="chat-avatar"><Sparkles :size="15" /></div>
                <div class="bubble-column"><span class="speaker">{{ message.role === 'user' ? '我' : '小晗' }}</span><div class="bubble" :class="{ failed: message.failed }">{{ message.content }}</div><div v-if="message.role === 'assistant' && message.sources?.length" class="citation-list"><div class="citation-label">参考资料</div><button v-for="(source, index) in message.sources" :key="`${source.source}-${index}`" class="citation" @click="activePage = 'materials'"><component :is="sourceIcon(source.source)" :size="13" /><span>{{ source.title }}</span><small>{{ Math.round(source.score * 100) }}%</small></button></div></div>
              </div>

              <div v-if="loading" class="message-line line-assistant"><div class="chat-avatar"><Sparkles :size="15" /></div><div class="typing"><LoaderCircle :size="14" class="spin" /><span>小晗正在翻看资料…</span></div></div>
            </div>

            <div class="composer-area">
              <div v-if="error" class="chat-error"><CircleAlert :size="14" /><span>{{ error }}</span><button @click="error = ''"><X :size="14" /></button></div>
              <form class="composer" @submit.prevent="ask()"><textarea v-model="question" rows="2" :disabled="busy" :placeholder="mode === 'quiz' ? '回答小晗的问题，或告诉她想抽查哪个主题…' : '问一个你想复习的概念…'" @keydown.enter.exact.prevent="ask()"></textarea><div class="composer-bottom"><div class="composer-left"><button type="button" class="prompt-toggle" :disabled="busy" @click="systemPrompt = systemPrompt ? '' : '你叫小晗，是耐心的学习搭子。先检索选定资料，用简单中文结合代码讲清楚概念，再一次只问一个问题检查理解。'">{{ systemPrompt ? '自定义提示词已启用' : '学习提示词' }}<ChevronDown :size="12" /></button><span>回车发送</span></div><button class="send-button" type="submit" :disabled="busy || !question.trim() || !selectedCount" aria-label="发送问题"><ArrowUp :size="18" /></button></div></form>
              <textarea v-if="systemPrompt" v-model="systemPrompt" class="prompt-edit" aria-label="自定义学习提示词" placeholder="写下你希望小晗如何带你复习…"></textarea>
              <div class="local-note"><CircleCheck :size="12" />资料切片和向量在本地；复习问题及检索片段会发送给 DeepSeek</div>
            </div>
          </div>

          <aside id="review-evidence-panel" class="evidence-panel" :class="{ 'evidence-open': evidenceOpen }" aria-label="依据与过程">
            <div class="evidence-head"><strong>依据与过程</strong><span class="agent-badge"><Sparkles :size="12" />Agent</span><button class="evidence-close icon-button" aria-label="收起依据" @click="evidenceOpen = false"><X :size="15" /></button></div>
            <div id="review-evidence" class="evidence-scroll">
              <div class="process-heading"><span>Agent 工具过程</span><span v-if="latestSources.length" class="done-label"><span></span>完成</span></div>
              <div v-if="messages.length && latestSources.length" class="process-list"><div class="process-step"><div class="step-mark"><Check :size="11" /></div><div><strong>创建学习 Agent</strong><span>LangChain create_agent</span></div></div><div class="process-step"><div class="step-mark"><Check :size="11" /></div><div><strong>通过 MCP 检索资料</strong><span>search_study_materials</span></div></div><div class="process-step"><div class="step-mark"><Check :size="11" /></div><div><strong>结合资料生成讲解</strong><span>{{ latestSources.length }} 个引用片段</span></div></div></div>
              <div v-else class="process-empty"><Layers3 :size="16" /><span>开始复习后会显示小晗检索的资料依据。</span></div>

              <div class="evidence-title"><span>检索到的片段</span><span>{{ latestSources.length }}</span></div>
              <div v-if="latestSources.length" class="evidence-list"><article v-for="(source, index) in latestSources" :key="`${source.source}-${index}`" class="evidence-card"><div class="evidence-card-title"><component :is="sourceIcon(source.source)" :size="13" /><strong>{{ source.title }}</strong><small>{{ Math.round(source.score * 100) }}%</small></div><p>{{ source.text }}</p><span class="evidence-path">{{ source.source }}</span></article></div>
              <div v-else class="no-evidence">还没有检索片段</div>

              <div class="selection-box-panel"><div><BookOpen :size="14" /><strong>资料范围</strong></div><span>{{ selectedCount }} 份资料</span><button @click="activePage = 'materials'">更改选择</button></div>
              <div class="model-note"><span class="model-dot" :class="{ ready: health.model_configured }"></span><span>{{ health.model_configured ? `${health.model} · OpenAI 兼容接口` : '请在项目 .env 配置 DeepSeek' }}</span></div>
            </div>
          </aside>
        </div>
      </section>
    </main>
  </div>
</template>
