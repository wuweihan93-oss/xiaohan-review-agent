<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { createId } from '../id.js'
import { prepareVisitorCache } from '../visitor-cache.js'
import { sourceScoreLabel } from '../sources.js'
import * as studyApi from '../api/study.js'
import { streamReview } from '../api/review.js'
import ReviewPage from '../components/chat/ReviewPage.vue'
import StudySidebar from '../components/layout/StudySidebar.vue'
import StudyTopbar from '../components/layout/StudyTopbar.vue'
import MaterialsPage from '../components/materials/MaterialsPage.vue'
import {
  FileCode2,
  FileText,
  Globe,
  LoaderCircle,
} from 'lucide-vue-next'

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
const fileInput = ref(null)
const reviewPage = ref(null)
const materialQuery = ref('')
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

async function refresh() {
  try {
    const [materialData, status] = await Promise.all([studyApi.getMaterials(), studyApi.getHealth()])
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
  materialQuery.value = ''
})

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
    await studyApi.deleteConversation(conversation.id)
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
    const result = await studyApi.getConversations({ limit: 50, offset: append ? conversations.value.length : 0 })
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
    const result = await studyApi.getConversation(id)
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
    const first = await studyApi.getVisitor()
    const confirmed = await studyApi.getVisitor()
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
        const result = await studyApi.getConversation(id)
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
    const result = await studyApi.importTutorialMaterials()
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
      const result = await studyApi.uploadMaterial(file)
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
    await studyApi.deleteMaterial(material.id)
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
    await streamReview({
        question: value,
        conversation_id: conversationId.value,
        material_ids: [...selectedIds.value],
        study_mode: mode.value,
        prompt: systemPrompt.value || null,
        web_search_enabled: webSearchEnabled.value,
    }, streamController.signal, (event, result) => {
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
  reviewPage.value?.scrollToEnd()
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
  initialize()
})
onBeforeUnmount(() => {
  streamController?.abort()
  clearTimeout(saveTimer)
  saveState()
})
</script>

<template>
  <div class="shell">
    <StudySidebar
      :active-page="activePage" :mobile-nav="mobileNav" :materials="materials"
      :selected-materials="selectedMaterials" :selected-count="selectedCount"
      :history-total="historyTotal" :history-loading="historyLoading" :history-error="historyError"
      :conversations="conversations" :conversation-id="conversationId" :health="health"
      :total-chunks="totalChunks" :busy="busy" :format-date="formatDate" :source-icon="sourceIcon"
      @navigate="(page) => { activePage = page; mobileNav = false }"
      @load-history="loadHistory" @open-conversation="openConversation"
      @delete-conversation="(item) => { deleteCandidate = item; error = '' }"
      @close-mobile="mobileNav = false"
    />

    <main class="main-area">
      <StudyTopbar :title="activePage === 'materials' ? '学习资料' : currentTitle" :active-page="activePage" :busy="busy" :model-configured="health.model_configured" @open-menu="mobileNav = true" @new-conversation="newConversation" />

      <MaterialsPage
        v-if="activePage === 'materials'" :materials="materials" :selected-ids="selectedIds"
        :selected-count="selectedCount" :total-chunks="totalChunks" :embedding="health.embedding"
        :error="error" :notice="notice" :importing="importing" :uploading="uploading"
        :deleting-id="deletingId" :format-date="formatDate" :source-icon="sourceIcon"
        @import-tutorial="importTutorial" @upload-files="uploadFiles" @toggle-material="toggleMaterial"
        @select-all="selectAll" @clear-selection="clearSelection" @remove-material="removeMaterial"
        @close-notice="error = ''; notice = ''" @review="activePage = 'review'"
      />

      <ReviewPage
        v-else ref="reviewPage" :materials="materials" :selected-ids="selectedIds"
        :selected-materials="selectedMaterials" :selected-count="selectedCount" :filtered-materials="filteredMaterials"
        :messages="messages" :mode="mode" :question="question" :system-prompt="systemPrompt"
        :web-search-enabled="webSearchEnabled" :material-query="materialQuery" :evidence-open="evidenceOpen"
        :health="health" :busy="busy" :loading="loading" :stopping="stopping" :initializing="initializing"
        :switching-conversation="switchingConversation" :notice="notice" :error="error" :can-ask="canAsk"
        :web-search-hint="webSearchHint" :latest-sources="latestSources" :latest-trace="latestTrace"
        :citation-icon="citationIcon"
        @update:question="question = $event" @update:system-prompt="systemPrompt = $event"
        @update:web-search-enabled="webSearchEnabled = $event" @update:material-query="materialQuery = $event"
        @scroll="followAnswer = $event" @evidence="evidenceOpen = $event"
        @ask="ask" @quiz="startQuiz" @stop="stopAnswer" @mode="changeMode"
        @toggle-material="toggleMaterial" @select-all="selectAll" @clear-selection="clearSelection"
        @navigate="activePage = $event" @notice="notice = $event" @error="error = $event"
        @citation="openCitation"
      />
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
