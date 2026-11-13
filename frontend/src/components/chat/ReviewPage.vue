<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowUp, BookOpen, Check, CheckCheck, ChevronDown, CircleAlert, CircleCheck, ExternalLink, FileCode2, FileText, Globe, Layers3, LoaderCircle, Sparkles, Square, X } from 'lucide-vue-next'
import AssistantAnswer from './AssistantAnswer.vue'
import { sourceScoreLabel, webSourceUrl } from '../../sources.js'

const props = defineProps({
  materials: { type: Array, default: () => [] }, selectedIds: { type: Set, required: true },
  selectedMaterials: { type: Array, default: () => [] }, selectedCount: Number, filteredMaterials: { type: Array, default: () => [] },
  messages: { type: Array, default: () => [] }, mode: String, question: String, systemPrompt: String,
  webSearchEnabled: Boolean, materialQuery: String, evidenceOpen: Boolean,
  health: { type: Object, required: true }, busy: Boolean, loading: Boolean,
  stopping: Boolean, initializing: Boolean, switchingConversation: Boolean, notice: String, error: String,
  canAsk: Boolean, webSearchHint: String, latestSources: { type: Array, default: () => [] }, latestTrace: { type: Array, default: () => [] },
  citationIcon: { type: Function, required: true },
})
const emit = defineEmits(['update:question', 'update:system-prompt', 'update:web-search-enabled', 'update:material-query', 'ask', 'quiz', 'stop', 'mode', 'toggle-material', 'select-all', 'clear-selection', 'navigate', 'notice', 'error', 'citation', 'evidence', 'scroll'])
const materialPicker = ref(null)
const chatBody = ref(null)

function openMaterialPicker() {
  if (props.busy) return
  emit('evidence', false)
  if (materialPicker.value) {
    materialPicker.value.open = true
    nextTick(() => materialPicker.value?.querySelector('input[type="search"]')?.focus())
  }
}
function closeMaterialPicker() { if (materialPicker.value) materialPicker.value.open = false }
function escapeMaterialPicker() { closeMaterialPicker(); materialPicker.value?.querySelector('summary')?.focus() }
function trackScroll() { emit('scroll', chatBody.value ? chatBody.value.scrollHeight - chatBody.value.scrollTop - chatBody.value.clientHeight < 100 : true) }
function closeOnOutside(event) { if (materialPicker.value && !materialPicker.value.contains(event.target)) closeMaterialPicker() }
function sourceIcon(source = '') { return source.endsWith('.ipynb') || source.endsWith('.py') ? FileCode2 : FileText }
function iconForSource(source) { return source.type === 'web' ? Globe : sourceIcon(source.source) }
onMounted(() => document.addEventListener('pointerdown', closeOnOutside))
onBeforeUnmount(() => document.removeEventListener('pointerdown', closeOnOutside))
watch(() => props.busy, (busy) => { if (busy) closeMaterialPicker() })
defineExpose({ scrollToEnd: async () => { await new Promise(resolve => requestAnimationFrame(resolve)); if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight } })
</script>

<template>
  <section class="review-page">
    <div class="review-toolbar" aria-label="复习设置">
      <div class="mode-control" role="group" aria-label="学习模式"><button :disabled="busy" :class="{ active: mode === 'coach' }" :aria-pressed="mode === 'coach'" @click="emit('mode', 'coach')"><Sparkles :size="14" />带我复盘</button><button :disabled="busy" :class="{ active: mode === 'quiz' }" :aria-pressed="mode === 'quiz'" @click="emit('mode', 'quiz')"><CheckCheck :size="14" />抽查我</button></div>
      <details ref="materialPicker" class="material-picker" @keydown.esc.stop="escapeMaterialPicker">
        <summary class="material-scope" :aria-disabled="busy" aria-controls="conversation-materials" :title="selectedCount ? selectedMaterials.map((item) => item.title).join('、') : '选择本轮学习资料'" @click="busy && $event.preventDefault()"><BookOpen :size="14" /><span>{{ selectedCount ? `${selectedCount} 份资料` : '选择资料' }}</span><ChevronDown :size="12" /></summary>
        <div id="conversation-materials" class="material-dropdown" role="group" aria-label="当前会话的学习资料">
          <div class="material-dropdown-head"><div><strong>本次对话的资料</strong><span>已选 {{ selectedCount }} / {{ materials.length }} 份，调整后用于下一条消息</span></div><button class="icon-button" aria-label="收起资料选择" @click="escapeMaterialPicker"><X :size="15" /></button></div>
          <input :value="materialQuery" type="search" class="material-filter" aria-label="搜索学习资料" placeholder="搜索资料名称…" @input="emit('update:material-query', $event.target.value)" />
          <div class="material-dropdown-actions"><button :disabled="busy || !materials.length" @click="emit('select-all')">全选</button><button :disabled="busy || !selectedCount" @click="emit('clear-selection')">清空选择</button></div>
          <div class="material-dropdown-list">
            <label v-for="material in filteredMaterials" :key="material.id" class="material-option" :class="{ selected: selectedIds.has(material.id) }"><input type="checkbox" :checked="selectedIds.has(material.id)" :disabled="busy" @change="emit('toggle-material', material.id)" /><component :is="sourceIcon(material.source)" :size="15" /><span><strong>{{ material.title }}</strong><small>{{ material.chunk_count }} 个片段 · {{ material.origin === 'upload' ? '上传资料' : '学习笔记' }}</small></span></label>
            <p v-if="!filteredMaterials.length" class="material-dropdown-empty">{{ materials.length ? '没有找到匹配的资料' : '还没有学习资料，可以先上传文件。' }}</p>
          </div>
          <div class="material-dropdown-foot"><span>{{ webSearchEnabled ? '优先查所选资料，资料不足或未选时自动联网。' : '联网已关闭，仅查询所选资料。' }}</span><button @click="emit('navigate', 'materials')">上传 / 管理资料</button></div>
        </div>
      </details>
      <button class="evidence-toggle" :aria-expanded="evidenceOpen" aria-controls="review-evidence-panel" @click="emit('evidence', !evidenceOpen); closeMaterialPicker()"><Layers3 :size="14" /><span>依据</span><ChevronDown :size="12" :class="{ 'chevron-open': evidenceOpen }" /></button>
    </div>
    <div v-if="notice" class="notice-line"><CircleCheck :size="15" /><span>{{ notice }}</span><button aria-label="关闭提示" @click="emit('notice', '')"><X :size="14" /></button></div>
    <div class="conversation-layout">
      <div class="chat-column">
        <div ref="chatBody" class="chat-scroll" @scroll="trackScroll">
          <div v-if="initializing || switchingConversation" class="conversation-loading" role="status"><LoaderCircle :size="18" class="spin" />正在恢复复习记录…</div>
          <div v-else-if="!messages.length" class="start-state">
            <div class="xiaohan-avatar"><Sparkles :size="22" /></div><div class="start-label">小晗在这里</div><h2>{{ mode === 'quiz' ? '准备好接受抽查了吗？' : '我们从你学过的开始。' }}</h2>
            <p>{{ selectedCount ? `已选 ${selectedCount} 份资料，选一个主题开始吧。` : webSearchEnabled ? '联网搜索已开启，告诉小晗你想了解的主题。' : '先选几份学习资料，或开启联网搜索。' }}</p>
            <div class="starter-prompts">
              <button :disabled="!canAsk" @click="emit('ask', selectedCount ? '用简单的话帮我梳理所选资料里的核心概念，并结合代码讲解。' : '联网查询 LangChain Agent 官方文档，帮我梳理核心概念。')"><BookOpen :size="15" /><span>帮我梳理核心概念</span></button>
              <button :disabled="!canAsk" @click="emit('ask', '带我复习 RAG 的完整流程，从文档加载、切分到向量检索。')"><Layers3 :size="15" /><span>复盘 RAG 完整流程</span></button>
              <button :disabled="!canAsk" @click="emit('quiz')"><CheckCheck :size="15" /><span>{{ selectedCount ? '从资料里抽查我' : '搜官方资料抽查我' }}</span></button>
              <button v-if="webSearchEnabled" :disabled="!canAsk" @click="emit('ask', '联网查找 LangChain 最新的官方 Agent 文档，讲讲 create_agent 的用法，并附上来源链接。')"><Globe :size="15" /><span>查最新官方文档</span></button>
              <button v-else :disabled="!canAsk" @click="emit('ask', '用一个简单例子比较 LangChain Tool 和 MCP Tool。')"><Sparkles :size="15" /><span>比较 Tool 和 MCP</span></button>
            </div>
            <button v-if="!selectedCount" class="choose-materials" :disabled="busy" @click="openMaterialPicker"><BookOpen :size="14" />选择本次资料</button>
          </div>
          <div v-for="message in messages" :key="message.id" class="message-line" :class="`line-${message.role}`">
            <div v-if="message.role === 'assistant'" class="chat-avatar"><Sparkles :size="15" /></div><div class="bubble-column"><span class="speaker">{{ message.role === 'user' ? '我' : '小晗' }}</span>
              <div v-if="message.role === 'user'" class="bubble">{{ message.content }}</div>
              <AssistantAnswer v-else :content="message.content" :failed="message.failed" :streaming="message.streaming">
                <div v-if="message.streaming" class="typing" role="status"><LoaderCircle :size="13" class="spin" /><span>{{ message.content ? '正在生成…' : message.trace?.at(-1)?.label || '小晗正在准备回答…' }}</span></div>
                <div v-if="message.sources?.length" class="citation-list"><div class="citation-label">参考来源</div><component :is="webSourceUrl(source) ? 'a' : 'button'" v-for="(source, index) in message.sources" :key="`${source.source}-${index}`" class="citation" :href="webSourceUrl(source) || undefined" :target="webSourceUrl(source) ? '_blank' : undefined" rel="noopener noreferrer" @click="emit('citation', source)"><component :is="citationIcon(source)" :size="13" /><span>{{ source.title || source.source }}</span><small v-if="source.type === 'web'">网页</small><small v-else-if="sourceScoreLabel(source)">{{ sourceScoreLabel(source) }}</small><ExternalLink v-if="webSourceUrl(source)" :size="11" /></component></div>
              </AssistantAnswer>
            </div>
          </div>
        </div>
        <div class="composer-area">
          <div v-if="error" class="chat-error"><CircleAlert :size="14" /><span>{{ error }}</span><button @click="emit('error', '')"><X :size="14" /></button></div>
          <form class="composer" @submit.prevent="emit('ask', question)">
            <textarea :value="question" rows="2" :disabled="busy" :placeholder="mode === 'quiz' ? '回答小晗的问题，或告诉她想抽查哪个主题…' : webSearchEnabled ? '问一个想了解的主题，也可以查最新资料…' : '问一个你想复习的概念…'" @input="emit('update:question', $event.target.value)" @keydown.enter.exact.prevent="emit('ask', question)"></textarea>
            <div class="composer-bottom"><div class="composer-left"><button type="button" class="web-search-toggle" :class="{ active: webSearchEnabled }" :aria-pressed="webSearchEnabled" :disabled="busy || !health.web_search_configured" :title="webSearchHint" @click="emit('update:web-search-enabled', !webSearchEnabled)"><Globe :size="13" /><span>{{ webSearchEnabled ? '联网已开' : '联网搜索' }}</span></button><button type="button" class="prompt-toggle" :disabled="busy" @click="emit('update:system-prompt', systemPrompt ? '' : '你叫小晗，是耐心的学习搭子。按照本轮检索规则查询来源，用简单中文结合代码讲清楚概念，再一次只问一个问题检查理解。')">{{ systemPrompt ? '自定义提示词已启用' : '学习提示词' }}<ChevronDown :size="12" /></button><span class="enter-hint">回车发送</span></div><button v-if="loading" class="send-button" type="button" :disabled="stopping" aria-label="停止生成" title="停止生成" @click="emit('stop')"><Square :size="13" fill="currentColor" /></button><button v-else class="send-button" type="submit" :disabled="busy || !question.trim() || !canAsk" aria-label="发送问题"><ArrowUp :size="18" /></button></div>
          </form>
          <textarea v-if="systemPrompt" :value="systemPrompt" class="prompt-edit" aria-label="自定义学习提示词" placeholder="写下你希望小晗如何带你复习…" @input="emit('update:system-prompt', $event.target.value)"></textarea>
          <div class="local-note"><CircleCheck :size="12" /><span>{{ webSearchEnabled ? '联网查询会发送给 Tavily，检索摘要与问题交给 DeepSeek 整理' : '资料切片和向量在本地；复习问题及检索片段会发送给 DeepSeek' }}<template v-if="health.history_enabled">；对话及 IP 将保存，管理员可查看。</template></span></div>
        </div>
      </div>
      <aside id="review-evidence-panel" class="evidence-panel" :class="{ 'evidence-open': evidenceOpen }" aria-label="依据与过程">
        <div class="evidence-head"><strong>依据与过程</strong><span class="agent-badge"><Sparkles :size="12" />Agent</span><button class="evidence-close icon-button" aria-label="收起依据" @click="emit('evidence', false)"><X :size="15" /></button></div>
        <div id="review-evidence" class="evidence-scroll">
          <div class="process-heading"><span>Agent 工具过程</span><span v-if="latestTrace.length" class="done-label"><span></span>本轮记录</span></div>
          <div v-if="latestTrace.length" class="process-list"><div v-for="(step, index) in latestTrace" :key="index" class="process-step" :class="{ 'process-failed': step.status === 'error' }"><div class="step-mark"><CircleAlert v-if="step.status === 'error'" :size="11" /><Check v-else :size="11" /></div><div><strong>{{ step.label }}</strong><span>{{ step.detail }}</span></div></div></div><div v-else class="process-empty"><Layers3 :size="16" /><span>新的问答会在这里显示实际工具调用记录。</span></div>
          <div class="evidence-title"><span>资料与网页来源</span><span>{{ latestSources.length }}</span></div>
          <div v-if="latestSources.length" class="evidence-list"><article v-for="(source, index) in latestSources" :key="`${source.source}-${index}`" class="evidence-card"><div class="evidence-card-title"><component :is="iconForSource(source)" :size="13" /><strong>{{ source.title || source.source }}</strong><small v-if="source.type === 'web'">网页</small><small v-else-if="sourceScoreLabel(source)">{{ sourceScoreLabel(source) }}</small></div><p>{{ source.text }}</p><a v-if="webSourceUrl(source)" class="evidence-path evidence-link" :href="webSourceUrl(source)" target="_blank" rel="noopener noreferrer">{{ source.url }} ↗</a><span v-else class="evidence-path">{{ source.source }}</span></article></div><div v-else class="no-evidence">还没有检索片段</div>
          <div class="selection-box-panel"><div><BookOpen :size="14" /><strong>资料范围</strong></div><span>{{ selectedCount }} 份资料</span><button :disabled="busy" @click="openMaterialPicker">更改选择</button></div>
          <div class="model-note"><span class="model-dot" :class="{ ready: health.model_configured }"></span><span>{{ health.model_configured ? `${health.model} · OpenAI 兼容接口` : '请在项目 .env 配置 DeepSeek' }}</span></div>
        </div>
      </aside>
    </div>
  </section>
</template>
