<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ArrowLeft, ChevronLeft, ChevronRight, LockKeyhole, LogOut, RefreshCw, Search, ShieldCheck, X } from 'lucide-vue-next'
import AssistantAnswer from '../components/chat/AssistantAnswer.vue'
import { adminRequest } from '../api/admin.js'

const session = ref(null)
const username = ref('admin'), password = ref('')
const booting = ref(true), loggingIn = ref(false), loggingOut = ref(false), loading = ref(false)
const error = ref('')
const overview = ref({ conversations: 0, visitors: 0, messages: 0, legacy_conversations: 0 })
const rows = ref([]), total = ref(0), expandedId = ref('')
const query = ref(''), appliedQuery = ref(''), visitorFilter = ref(''), legacyOnly = ref(false)
const offset = ref(0), lastUpdated = ref(null)
const limit = 30
let timer, controller, generation = 0
const page = computed(() => Math.floor(offset.value / limit) + 1)
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / limit)))

function clearSession() {
  generation++
  controller?.abort()
  clearTimeout(timer)
  session.value = null
  rows.value = []
  expandedId.value = ''
  total.value = 0
  overview.value = { conversations: 0, visitors: 0, messages: 0, legacy_conversations: 0 }
}
async function api(path, options = {}) {
  try {
    return await adminRequest(path, options)
  } catch (error) {
    if (error.status === 401) clearSession()
    throw error
  }
}
async function login() {
  if (loggingIn.value) return
  loggingIn.value = true
  error.value = ''
  try {
    session.value = await api('/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Admin-Request': '1' },
      body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    })
    password.value = ''
    await refresh()
  } catch (caught) { error.value = caught.message; password.value = '' }
  finally { loggingIn.value = false }
}
async function logout() {
  loggingOut.value = true
  try {
    await api('/logout', { method: 'POST', headers: { 'X-CSRF-Token': session.value.csrf_token } })
    clearSession()
    error.value = ''
  } catch (caught) { error.value = caught.message }
  finally { loggingOut.value = false }
}
function schedule() {
  clearTimeout(timer)
  if (session.value) timer = setTimeout(() => {
    if (document.hidden) schedule()
    else refresh()
  }, 5000)
}
async function refresh() {
  if (!session.value) return
  clearTimeout(timer)
  controller?.abort()
  controller = new AbortController()
  const signal = controller.signal, current = ++generation
  loading.value = true
  try {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset.value), q: appliedQuery.value })
    if (visitorFilter.value) params.set('visitor_id', visitorFilter.value)
    if (legacyOnly.value) params.set('legacy', 'true')
    const [counts, listing] = await Promise.all([api('/overview', { signal }), api(`/turns?${params}`, { signal })])
    if (current !== generation) return
    overview.value = counts
    rows.value = listing.rows
    total.value = listing.total
    lastUpdated.value = new Date()
    error.value = ''
    if (offset.value && offset.value >= total.value) {
      offset.value = Math.max(0, (Math.ceil(total.value / limit) - 1) * limit)
      return refresh()
    }
  } catch (caught) {
    if (caught.name !== 'AbortError' && current === generation) error.value = caught.message
  } finally {
    if (current === generation) { loading.value = false; schedule() }
  }
}
function search() { appliedQuery.value = query.value.trim(); offset.value = 0; refresh() }
function filterVisitor(id) { visitorFilter.value = id || ''; legacyOnly.value = !id; offset.value = 0; refresh() }
function clearFilter() { visitorFilter.value = ''; legacyOnly.value = false; offset.value = 0; refresh() }
function toggleRow(id) { expandedId.value = expandedId.value === id ? '' : id }
function movePage(delta) { offset.value = Math.max(0, offset.value + delta * limit); refresh() }
function time(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function visitorLabel(id) { return id ? `访客 ${id.slice(0, 10)}` : '旧版无归属记录' }
function visibleAgain() { if (!document.hidden && session.value) refresh() }
onMounted(async () => {
  document.addEventListener('visibilitychange', visibleAgain)
  try { session.value = await api('/session'); await refresh() }
  catch (caught) { if (caught.status !== 401) error.value = caught.message }
  finally { booting.value = false }
})
onBeforeUnmount(() => { generation++; controller?.abort(); clearTimeout(timer); document.removeEventListener('visibilitychange', visibleAgain) })
</script>

<template>
  <main v-if="!session" class="admin-login">
    <form class="login-card" @submit.prevent="login">
      <div class="admin-mark"><ShieldCheck :size="26" /></div>
      <p class="eyebrow">小晗 · 管理员后台</p><h1>查看学习对话</h1>
      <p class="login-description">使用独立管理员账号登录，查看匿名访客的会话与已保存消息。</p>
      <p v-if="booting" role="status">正在检查登录状态…</p>
      <template v-else>
        <label>管理员账号<input v-model="username" name="username" autocomplete="username" required maxlength="64" /></label>
        <label>密码<input v-model="password" name="password" type="password" autocomplete="current-password" required maxlength="256" /></label>
        <p v-if="error" class="admin-error" role="alert">{{ error }}</p>
        <button class="admin-primary" :disabled="loggingIn || !username.trim() || !password"><LockKeyhole :size="15" />{{ loggingIn ? '正在登录…' : '登录后台' }}</button>
      </template>
      <a class="return-link" href="/"><ArrowLeft :size="14" />返回学习页面</a>
    </form>
  </main>
  <main v-else class="admin-shell">
    <header class="admin-header"><div><div class="eyebrow"><ShieldCheck :size="15" />小晗管理后台</div><h1>对话记录</h1></div><div class="admin-actions"><a href="/">学习页面 ↗</a><span>{{ session.username }}</span><button :disabled="loggingOut" @click="logout"><LogOut :size="15" />退出</button></div></header>
    <div class="table-toolbar">
      <form class="admin-search" @submit.prevent="search"><Search :size="16" /><input v-model="query" type="search" placeholder="搜索用户标识、IP、问题或回答" aria-label="搜索问答记录" maxlength="200" /><button>搜索</button></form>
      <div class="table-filters"><button v-if="visitorFilter || legacyOnly" class="visitor-filter" @click="clearFilter">{{ visitorLabel(visitorFilter) }}<X :size="12" /></button><label v-else><input v-model="legacyOnly" type="checkbox" @change="offset = 0; refresh()" />仅旧版记录</label><button :disabled="loading" @click="refresh"><RefreshCw :size="14" :class="{ spin: loading }" />刷新</button></div>
    </div>
    <div class="refresh-bar"><span>{{ overview.visitors }} 位匿名访客 · {{ overview.conversations }} 段会话 · 当前 {{ total }} 轮问答</span><span>每 5 秒刷新<span v-if="lastUpdated" class="updated-time"> · {{ time(lastUpdated) }}</span></span></div>
    <p v-if="error" class="admin-error dashboard-error" role="alert">{{ error }}</p>
    <div class="table-scroll" :aria-busy="loading">
      <table class="review-table">
        <caption>匿名访客问答记录，每行对应一轮提问与已保存的回答</caption>
        <colgroup><col class="user-col" /><col class="time-col" /><col class="question-col" /><col /><col class="ip-col" /><col class="action-col" /></colgroup>
        <thead><tr><th scope="col">用户</th><th scope="col">时间</th><th scope="col">问题</th><th scope="col">回答</th><th scope="col">IP 地址</th><th scope="col">详情</th></tr></thead>
        <tbody>
          <tr v-for="row in rows" :key="row.question_id" :class="{ expanded: expandedId === row.question_id }">
            <td><button class="visitor-link" :title="row.visitor_id || '旧记录未绑定访客'" @click="filterVisitor(row.visitor_id)">{{ visitorLabel(row.visitor_id) }}</button><small :title="row.conversation_title" class="conversation-label">{{ row.conversation_title }}</small></td>
            <td><time class="asked-time">{{ time(row.asked_at) }}</time><small v-if="expandedId === row.question_id && row.answered_at">回答于 {{ time(row.answered_at) }}</small></td>
            <td><p class="cell-text" :class="{ complete: expandedId === row.question_id }">{{ row.question }}</p></td>
            <td class="answer-cell"><span v-if="row.answer === null" class="pending-answer">等待回答保存…</span><template v-else><span v-if="row.failed" class="failed-badge">生成中断 / 失败</span><AssistantAnswer v-if="expandedId === row.question_id" :content="row.answer" :failed="row.failed" /><p v-else class="cell-text">{{ row.answer }}</p></template></td>
            <td><span class="ip-address" :class="{ unrecorded: !row.client_ip }">{{ row.client_ip || '未记录' }}</span></td>
            <td><button class="expand-row" :aria-expanded="expandedId === row.question_id" @click="toggleRow(row.question_id)">{{ expandedId === row.question_id ? '收起' : '展开' }}</button></td>
          </tr>
          <tr v-if="!rows.length"><td colspan="6" class="empty-table">{{ loading ? '正在读取记录…' : '没有符合条件的问答记录。' }}</td></tr>
        </tbody>
      </table>
    </div>
    <footer class="table-footer"><span>每行一轮问答，长内容可展开。旧记录未保存 IP，匿名编号不代表真实姓名。<span class="mobile-table-hint">手机可左右滑动查看全部列。</span></span><div class="admin-pagination"><button aria-label="上一页" :disabled="!offset || loading" @click="movePage(-1)"><ChevronLeft :size="17" /></button><span>{{ page }} / {{ pageCount }}</span><button aria-label="下一页" :disabled="offset + limit >= total || loading" @click="movePage(1)"><ChevronRight :size="17" /></button></div></footer>
  </main>
</template>

<style scoped>
.admin-login { height: 100dvh; display: grid; place-items: center; padding: 24px; background: #f7f7f6; overflow: auto; }
.login-card { width: min(100%, 420px); padding: 36px; border-radius: 28px; background: white; box-shadow: 0 16px 70px #00000006; }
.admin-mark { width: 54px; height: 54px; display: grid; place-items: center; border-radius: 18px; color: var(--accent-dark); background: var(--avatar-soft); }
.eyebrow { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--muted); }
h1 { margin: 10px 0; font-size: 25px; font-weight: 600; letter-spacing: -.5px; }
.login-description { color: var(--muted); line-height: 1.8; margin-bottom: 24px; }
.login-card label { display: grid; gap: 8px; margin: 17px 0; font-size: 13px; }
input { font: inherit; color: var(--ink); min-width: 0; }
.login-card input { width: 100%; border: 1px solid var(--line-strong); border-radius: 12px; padding: 12px; background: #fff; }
button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; border: 0; border-radius: 9px; padding: 8px 11px; background: var(--surface); cursor: pointer; }
button:disabled { opacity: .5; cursor: default; } a { color: var(--text); text-decoration: none; }
.admin-primary { width: 100%; margin-top: 9px; min-height: 42px; background: var(--ink); color: #fff; }
.return-link { display: flex; justify-content: center; align-items: center; gap: 6px; margin-top: 23px; color: var(--muted); font-size: 12px; }
.admin-error { color: #9d4545; line-height: 1.8; font-size: 13px; overflow-wrap: anywhere; }
.admin-shell { height: 100dvh; display: flex; flex-direction: column; padding: 24px 30px 16px; overflow: hidden; }
.admin-header { display: flex; justify-content: space-between; gap: 16px; align-items: center; }
.admin-header h1 { margin: 7px 0 0; font-size: 24px; }.admin-actions { display: flex; align-items: center; gap: 20px; font-size: 12px; color: var(--muted); }
.table-toolbar { display: flex; gap: 16px; justify-content: space-between; align-items: center; padding: 24px 0 13px; }
.admin-search { display: flex; gap: 9px; align-items: center; padding: 3px 5px 3px 13px; background: var(--surface); border-radius: 12px; width: min(480px, 55%); }
.admin-search input { border: 0; padding: 9px 0; width: 100%; background: transparent; font-size: 13px; }.admin-search button { flex-shrink: 0; font-size: 12px; }
.table-filters { display: flex; align-items: center; gap: 16px; color: var(--muted); font-size: 12px; }.table-filters label { display: flex; align-items: center; gap: 5px; }.visitor-filter { font-size: 11px; }
.refresh-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; color: var(--muted); font-size: 11px; padding-bottom: 13px; line-height: 1.8; }
.dashboard-error { margin: 0 0 10px; }.table-scroll { flex: 1; min-height: 0; overflow: auto; border: 1px solid var(--line); border-radius: 16px; }
.review-table { width: 100%; min-width: 1050px; border-spacing: 0; table-layout: fixed; font-size: 12px; line-height: 1.75; }
.review-table caption { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.user-col { width: 148px; }.time-col { width: 154px; }.question-col { width: 26%; }.ip-col { width: 142px; }.action-col { width: 68px; }
th { position: sticky; top: 0; z-index: 1; background: #f7f7f6; color: var(--text); font-size: 12px; font-weight: 500; text-align: left; padding: 13px 15px; border-bottom: 1px solid var(--line); }
td { padding: 16px 15px; vertical-align: top; border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }.review-table tbody tr:hover { background: #fcfcfb; }.review-table tr.expanded { background: #fcfcfb; }.review-table tbody tr:last-child td { border-bottom: 0; }
.visitor-link { padding: 0; background: transparent; color: var(--accent-dark); font-size: 12px; text-align: left; line-height: 1.7; }.conversation-label { display: block; color: var(--faint); font-size: 10px; margin-top: 7px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.asked-time { font-variant-numeric: tabular-nums; color: var(--muted); font-size: 11px; }td > small { display: block; color: var(--faint); font-size: 10px; margin-top: 8px; }
.cell-text { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }.cell-text.complete { display: block; overflow: visible; }
.ip-address { font: 11px/1.8 'SFMono-Regular', Consolas, monospace; overflow-wrap: anywhere; }.unrecorded, .pending-answer { color: var(--faint); }.failed-badge { display: block; color: var(--danger); font-size: 10px; margin-bottom: 5px; }.expand-row { padding: 3px 0; background: transparent; font-size: 11px; color: var(--muted); }
.empty-table { text-align: center; padding: 60px 20px; color: var(--muted); }
.table-footer { display: flex; justify-content: space-between; align-items: center; gap: 18px; padding-top: 12px; color: var(--faint); font-size: 10px; }.admin-pagination { display: flex; flex-shrink: 0; align-items: center; gap: 13px; font-size: 11px; color: var(--muted); }.admin-pagination button { padding: 5px 8px; }
.mobile-table-hint { display: none; }
@media (max-width: 700px) { .mobile-table-hint { display: block; }.admin-shell { padding: 18px 14px 12px; }.admin-header h1 { font-size: 21px; }.admin-actions { gap: 10px; }.admin-actions > span { display: none; }.admin-actions a { font-size: 11px; }.table-toolbar { flex-wrap: wrap; gap: 11px; padding-top: 20px; }.admin-search { width: 100%; }.table-filters { width: 100%; justify-content: space-between; font-size: 11px; }.updated-time { display: none; }.refresh-bar { font-size: 10px; gap: 7px; }.table-footer { align-items: flex-start; font-size: 9px; line-height: 1.8; }.admin-pagination { gap: 6px; }.table-scroll { border-radius: 12px; }.login-card { padding: 28px 24px; } }
</style>
