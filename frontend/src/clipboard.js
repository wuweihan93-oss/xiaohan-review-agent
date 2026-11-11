// 公网 HTTP 通常没有 Clipboard API，保留旧复制接口作为回退。
export async function copyText(text) {
  if (typeof text !== 'string' || !text.trim()) throw new Error('这条回答暂时没有可复制的内容。')
  if (globalThis.navigator?.clipboard?.writeText) {
    try {
      await globalThis.navigator.clipboard.writeText(text)
      return
    } catch {
      // 浏览器权限受限时，继续尝试由当前点击触发的兼容复制。
    }
  }
  const document = globalThis.document
  if (!document?.body) throw new Error('当前环境不支持复制，请选中回答后手动复制。')
  const previousFocus = document.activeElement
  const selection = document.getSelection()
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange()) : []
  const inputSelection = previousFocus && typeof previousFocus.selectionStart === 'number'
    ? [previousFocus.selectionStart, previousFocus.selectionEnd, previousFocus.selectionDirection]
    : null
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.readOnly = true
  textarea.tabIndex = -1
  textarea.setAttribute('aria-hidden', 'true')
  textarea.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;font-size:16px;'
  document.body.appendChild(textarea)
  try {
    textarea.focus({ preventScroll: true })
    textarea.select()
    textarea.setSelectionRange(0, text.length)
    if (!document.execCommand?.('copy')) throw new Error('复制未成功')
  } catch {
    throw new Error('复制失败，请长按或选中回答后手动复制。')
  } finally {
    textarea.remove()
    previousFocus?.focus({ preventScroll: true })
    if (inputSelection) previousFocus.setSelectionRange(...inputSelection)
    if (selection) {
      selection.removeAllRanges()
      for (const range of ranges) selection.addRange(range)
    }
  }
}
