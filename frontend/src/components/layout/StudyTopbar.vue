<script setup>
import { Menu, Plus } from 'lucide-vue-next'

defineProps({ title: String, activePage: String, busy: Boolean, modelConfigured: Boolean })
const emit = defineEmits(['open-menu', 'new-conversation'])
</script>

<template>
  <header class="topbar">
    <div class="topbar-left"><button class="icon-button mobile-menu" aria-label="打开导航" @click="emit('open-menu')"><Menu :size="18" /></button><h1 class="topbar-title" :title="title">{{ title }}</h1></div>
    <div class="topbar-right"><span class="connection"><span class="model-dot" :class="{ ready: modelConfigured }"></span>{{ modelConfigured ? 'DeepSeek 已连接' : '模型未连接' }}</span><button v-if="activePage === 'review'" class="new-session" :disabled="busy" @click="emit('new-conversation')"><Plus :size="15" /> 新复习</button></div>
  </header>
</template>
