import assert from 'node:assert/strict'
import test from 'node:test'
import { copyText } from '../src/clipboard.js'

function environment(context, navigator, document) {
  for (const [key, value] of Object.entries({ navigator, document })) {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, key)
    Object.defineProperty(globalThis, key, { configurable: true, value })
    context.after(() => {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    })
  }
}

function fallbackDocument(copied) {
  const state = { copied: '', removed: false, focusRestored: false, rangeRestored: false }
  const textarea = {
    style: {}, setAttribute() {}, focus() {}, select() {},
    setSelectionRange(start, end) { state.selected = [start, end] },
    remove() { state.removed = true },
  }
  const document = {
    activeElement: {
      selectionStart: 2, selectionEnd: 4, selectionDirection: 'forward',
      focus() { state.focusRestored = true },
      setSelectionRange(...range) { state.rangeRestored = range },
    },
    body: { appendChild() {} },
    createElement() { return textarea },
    getSelection() { return { rangeCount: 0, removeAllRanges() {} } },
    execCommand(command) { assert.equal(command, 'copy'); state.copied = textarea.value; return copied },
  }
  return { document, state }
}

test('优先使用 Clipboard API，完整保留 Markdown 和代码空格', async (context) => {
  let copied
  environment(context, { clipboard: { async writeText(text) { copied = text } } })
  const markdown = '## 学习笔记\n\n```python\n    print("你好")\n```\n'
  await copyText(markdown)
  assert.equal(copied, markdown)
})

test('普通 HTTP 无 Clipboard API 时回退复制，并恢复输入焦点与选区', async (context) => {
  const { document, state } = fallbackDocument(true)
  environment(context, {}, document)
  await copyText('**完整回答**\n第二行')
  assert.equal(state.copied, '**完整回答**\n第二行')
  assert.equal(state.removed, true)
  assert.equal(state.focusRestored, true)
  assert.deepEqual(state.rangeRestored, [2, 4, 'forward'])
})

test('Clipboard API 权限失败后继续尝试兼容复制', async (context) => {
  const { document, state } = fallbackDocument(true)
  environment(context, { clipboard: { async writeText() { throw new Error('权限不足') } } }, document)
  await copyText('回答内容')
  assert.equal(state.copied, '回答内容')
})

test('两种复制方式都失败时显示中文错误，并清理临时文本框', async (context) => {
  const { document, state } = fallbackDocument(false)
  environment(context, {}, document)
  await assert.rejects(copyText('回答内容'), /复制失败.*手动复制/)
  assert.equal(state.removed, true)
  await assert.rejects(copyText('  '), /没有可复制的内容/)
})
