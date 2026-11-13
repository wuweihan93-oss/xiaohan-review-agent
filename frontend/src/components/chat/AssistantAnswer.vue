<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Check, Copy, LoaderCircle } from 'lucide-vue-next'
import { renderMarkdown } from '../../markdown.js'
import { copyText } from '../../clipboard.js'

const props = defineProps({
  content: { type: String, default: '' },
  streaming: Boolean,
  failed: Boolean,
})
const html = computed(() => renderMarkdown(props.content))
const copyState = ref('idle')
const copyError = ref('')
let copiedTimer

async function copyAnswer() {
  if (copyState.value === 'copying') return
  clearTimeout(copiedTimer)
  copyError.value = ''
  copyState.value = 'copying'
  try {
    await copyText(props.content)
    copyState.value = 'copied'
    copiedTimer = setTimeout(() => { copyState.value = 'idle' }, 2000)
  } catch (caught) {
    copyState.value = 'idle'
    copyError.value = caught.message
  }
}

watch(() => props.content, () => {
  clearTimeout(copiedTimer)
  copyState.value = 'idle'
  copyError.value = ''
})
onBeforeUnmount(() => clearTimeout(copiedTimer))
</script>

<template>
  <div class="bubble markdown-body" :class="{ failed }" v-html="html"></div>
  <slot />
  <div v-if="!streaming && content.trim()" class="answer-actions">
    <button type="button" class="copy-answer" :disabled="copyState === 'copying'" :title="copyState === 'copied' ? '已复制' : '复制完整回答，保留 Markdown 格式'" :aria-label="copyState === 'copied' ? '已复制回答' : '复制回答'" @click="copyAnswer">
      <Check v-if="copyState === 'copied'" :size="13" /><LoaderCircle v-else-if="copyState === 'copying'" :size="13" class="spin" /><Copy v-else :size="13" />
      <span aria-live="polite">{{ copyState === 'copied' ? '已复制' : copyState === 'copying' ? '正在复制…' : '复制' }}</span>
    </button>
    <span v-if="copyError" class="copy-error" role="alert">{{ copyError }}</span>
  </div>
</template>
