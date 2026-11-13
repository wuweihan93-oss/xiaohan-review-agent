<script setup>
import { ArrowDownToLine, BookOpen, Check, CheckCheck, ChevronDown, CircleAlert, CircleCheck, FileCode2, FileText, LoaderCircle, Plus, Trash2, Upload, X } from 'lucide-vue-next'
import { ref } from 'vue'

const fileInput = ref(null)

defineProps({
  materials: { type: Array, default: () => [] },
  selectedIds: { type: Set, required: true },
  selectedCount: Number,
  totalChunks: Number,
  embedding: String,
  error: String,
  notice: String,
  importing: Boolean,
  uploading: Boolean,
  deletingId: String,
  formatDate: { type: Function, required: true },
  sourceIcon: { type: Function, required: true },
})
const emit = defineEmits(['import-tutorial', 'upload-files', 'toggle-material', 'select-all', 'clear-selection', 'remove-material', 'close-notice', 'review'])
</script>

<template>
  <section class="materials-page">
    <div class="page-heading">
      <div><div class="page-kicker"><BookOpen :size="14" /> 学习资料</div><h1>你的复习素材</h1><p>把学过的内容放进资料库，复习时选择想看的范围。</p></div>
      <button class="primary-button" :disabled="importing" @click="emit('import-tutorial')"><LoaderCircle v-if="importing" :size="16" class="spin" /><ArrowDownToLine v-else :size="16" />{{ importing ? '正在导入并建索引' : '导入笔记' }}</button>
    </div>
    <div class="material-stats">
      <div class="stat"><span>资料总数</span><strong>{{ materials.length }}</strong><small>份学习资料</small></div>
      <div class="stat"><span>已选复习</span><strong>{{ selectedCount }}</strong><small>份资料</small></div>
      <div class="stat"><span>Milvus 切片</span><strong>{{ totalChunks }}</strong><small>个向量片段</small></div>
      <div class="stat stat-model"><span>Embedding</span><strong>{{ embedding || '本地模型' }}</strong><small>在本机生成向量</small></div>
    </div>
    <div class="upload-strip">
      <div class="upload-symbol"><Upload :size="18" /></div>
      <div class="upload-description"><strong>添加自己的学习文件</strong><span>PDF、DOCX、Markdown、TXT、Python、CSV、JSON、IPYNB，单个文件不超过 25 MB</span></div>
      <input ref="fileInput" type="file" hidden multiple accept=".pdf,.docx,.md,.txt,.py,.csv,.json,.ipynb" @change="emit('upload-files', $event)" />
      <button class="secondary-button" :disabled="uploading" @click="fileInput?.click()"><LoaderCircle v-if="uploading" :size="15" class="spin" /><Plus v-else :size="15" />{{ uploading ? '处理中' : '选择文件' }}</button>
    </div>
    <div v-if="error || notice" class="notice-line" :class="{ 'notice-error': error }"><CircleAlert v-if="error" :size="15" /><CircleCheck v-else :size="15" /><span>{{ error || notice }}</span><button aria-label="关闭提示" @click="emit('close-notice')"><X :size="14" /></button></div>
    <div class="list-heading"><div><h2>资料库</h2><span>{{ materials.length }} 份</span></div><div class="list-tools"><button @click="emit('select-all')"><CheckCheck :size="14" />全选</button><button @click="emit('clear-selection')">清空选择</button></div></div>
    <div v-if="materials.length" class="materials-list">
      <article v-for="material in materials" :key="material.id" class="material-row" :class="{ checked: selectedIds.has(material.id) }">
        <button class="selection-box" :aria-label="selectedIds.has(material.id) ? '取消选择' : '选择资料'" @click="emit('toggle-material', material.id)"><Check v-if="selectedIds.has(material.id)" :size="14" /></button>
        <div class="file-kind"><component :is="sourceIcon(material.source)" :size="18" /></div>
        <div class="material-details"><div class="material-name"><strong>{{ material.title }}</strong><span class="origin-tag" :class="`origin-${material.origin}`">{{ material.origin === 'tutorial' ? '教程' : '上传' }}</span></div><div class="material-meta"><span :title="material.source">{{ material.source }}</span><i></i><span>{{ material.chunk_count }} 个切片</span><i></i><span>{{ formatDate(material.created_at) }}</span></div></div>
        <button class="delete-button" :disabled="deletingId === material.id" :aria-label="`删除 ${material.title}`" title="从项目资料库和向量索引中删除" @click="emit('remove-material', material)"><LoaderCircle v-if="deletingId === material.id" :size="16" class="spin" /><Trash2 v-else :size="16" /></button>
      </article>
    </div>
    <div v-else class="empty-library"><div><BookOpen :size="23" /></div><strong>资料库还是空的</strong><span>先导入教程笔记，或添加自己的学习文件。</span><button class="secondary-button" :disabled="importing" @click="emit('import-tutorial')"><ArrowDownToLine :size="15" />导入笔记</button></div>
    <div class="materials-footer"><span><CircleCheck :size="13" />原始教程笔记不会被修改</span><button :disabled="!selectedCount" @click="emit('review')">用 {{ selectedCount }} 份资料开始复习 <ChevronDown :size="14" /></button></div>
  </section>
</template>
