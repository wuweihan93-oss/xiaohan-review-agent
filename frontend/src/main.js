import { createApp } from 'vue'
import './style.css'

// 后台独立加载，不增加普通聊天页面的登录流程。
const component = /^\/admin(?:\/|$)/.test(window.location.pathname)
  ? import('./AdminApp.vue')
  : import('./App.vue')

component.then(({ default: App }) => createApp(App).mount('#app'))
