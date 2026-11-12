// Cookie 更换后先移除旧访客缓存，再允许恢复消息或保存新状态。
const visitorKeys = [
  'xiaohan-conversation', 'xiaohan-messages', 'xiaohan-selected-materials',
  'xiaohan-mode', 'xiaohan-prompt', 'xiaohan-web-search',
]

export function prepareVisitorCache(storage, scope) {
  if (typeof scope !== 'string' || !/^[0-9a-f]{64}$/.test(scope)) {
    throw new Error('匿名访客身份初始化失败，请刷新页面重试。')
  }
  if (storage.getItem('xiaohan-visitor-scope') !== scope) {
    for (const key of visitorKeys) storage.removeItem(key)
    storage.setItem('xiaohan-visitor-scope', scope)
  }
}
