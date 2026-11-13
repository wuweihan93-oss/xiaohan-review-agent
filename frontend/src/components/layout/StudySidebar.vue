<script setup>
import { BookOpen, Check, History, Layers3, LoaderCircle, MessageCircleMore, Plus, Sparkles, Trash2, X } from 'lucide-vue-next'

defineProps({
  activePage: String,
  mobileNav: Boolean,
  materials: { type: Array, default: () => [] },
  selectedMaterials: { type: Array, default: () => [] },
  selectedCount: Number,
  historyTotal: Number,
  historyLoading: Boolean,
  historyError: String,
  conversations: { type: Array, default: () => [] },
  conversationId: String,
  health: { type: Object, required: true },
  totalChunks: Number,
  busy: Boolean,
  formatDate: { type: Function, required: true },
  sourceIcon: { type: Function, required: true },
})
const emit = defineEmits(['navigate', 'load-history', 'open-conversation', 'delete-conversation', 'close-mobile'])
</script>

<template>
  <div v-if="mobileNav" class="scrim" @click="emit('close-mobile')"></div>
  <aside class="rail" :class="{ 'rail-open': mobileNav }">
    <div class="identity">
      <div class="mascot"><Sparkles :size="19" /></div>
      <div class="identity-text"><strong>小晗</strong><span>学习搭子</span></div>
      <button class="icon-button rail-close" aria-label="关闭导航" @click="emit('close-mobile')"><X :size="18" /></button>
    </div>
    <div class="nav-caption">工作台</div>
    <nav class="primary-nav" aria-label="主导航">
      <button :class="{ current: activePage === 'review' }" @click="emit('navigate', 'review')"><MessageCircleMore :size="17" /><span>复盘学习</span><span v-if="selectedCount" class="nav-count">{{ selectedCount }}</span></button>
      <button :class="{ current: activePage === 'materials' }" @click="emit('navigate', 'materials')"><BookOpen :size="17" /><span>学习资料</span><span class="nav-count">{{ materials.length }}</span></button>
    </nav>
    <div class="material-nav-head"><span>本次复习</span><button @click="emit('navigate', 'materials')">管理</button></div>
    <div class="selected-nav-list">
      <button v-for="material in selectedMaterials.slice(0, 6)" :key="material.id" class="selected-nav-item" :title="material.title" @click="emit('navigate', 'materials')">
        <component :is="sourceIcon(material.source)" :size="14" /><span>{{ material.title }}</span><Check :size="12" class="selected-check" />
      </button>
      <div v-if="!selectedCount" class="empty-selection">还没有选择资料</div>
    </div>
    <div class="history-head"><span><History :size="13" />历史复习 <small>{{ historyTotal }}</small></span><button :disabled="historyLoading || busy" @click="emit('load-history')">刷新</button></div>
    <div class="history-list" aria-label="历史复习列表" :aria-busy="historyLoading">
      <div v-if="historyError" class="history-empty history-error"><span>{{ historyError }}</span><button :disabled="historyLoading" @click="emit('load-history')">重新加载</button></div>
      <div v-else-if="historyLoading && !conversations.length" class="history-empty"><LoaderCircle :size="13" class="spin" />正在读取历史记录…</div>
      <div v-else-if="!conversations.length" class="history-empty">{{ health.history_enabled ? '开始一轮复习后，会自动保存在这里。' : '配置 PostgreSQL 后即可保存历史复习。' }}</div>
      <div v-for="conversation in conversations" :key="conversation.id" class="history-row" :class="{ current: conversation.id === conversationId }">
        <button class="history-item" :disabled="busy" :aria-current="conversation.id === conversationId ? 'true' : undefined" :title="conversation.title" @click="emit('open-conversation', conversation.id)">
          <strong>{{ conversation.title }}</strong><span><time :datetime="conversation.updated_at">{{ formatDate(conversation.updated_at) }}</time><i>·</i>{{ conversation.study_mode === 'quiz' ? '抽查' : '复盘' }}<i>·</i>{{ conversation.message_count }} 条消息</span>
        </button>
        <button class="history-delete icon-button" :disabled="busy || historyLoading" :aria-label="`删除会话：${conversation.title}`" title="删除会话" @click="emit('delete-conversation', conversation)"><Trash2 :size="14" /></button>
      </div>
      <button v-if="conversations.length < historyTotal" class="history-more" :disabled="historyLoading || busy" @click="emit('load-history', true)">{{ historyLoading ? '正在读取…' : '加载更多' }}</button>
    </div>
    <div class="rail-bottom">
      <div class="local-model"><span class="model-dot" :class="{ ready: health.model_configured }"></span><div><strong>{{ health.model || 'DeepSeek' }}</strong><span>{{ health.model_configured ? '对话模型可用' : '请检查项目 .env' }}</span></div></div>
      <div class="storage-line"><Layers3 :size="14" /><span>Milvus Lite</span><strong>{{ totalChunks }}</strong><span>切片</span></div>
    </div>
  </aside>
</template>
