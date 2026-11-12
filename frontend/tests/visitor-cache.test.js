import assert from 'node:assert/strict'
import test from 'node:test'
import { prepareVisitorCache } from '../src/visitor-cache.js'

function storageWith(values) {
  const data = new Map(Object.entries(values))
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value),
    removeItem: (key) => data.delete(key),
  }
}

test('同一访客保留缓存，Cookie 对应身份变化后移除旧消息和会话配置', () => {
  const scope = 'a'.repeat(64)
  const storage = storageWith({ 'xiaohan-visitor-scope': scope, 'xiaohan-conversation': '旧会话', 'xiaohan-messages': '私有问答', 'xiaohan-prompt': '自定义提示词', unrelated: '其他应用数据' })
  prepareVisitorCache(storage, scope)
  assert.equal(storage.getItem('xiaohan-messages'), '私有问答')
  prepareVisitorCache(storage, 'b'.repeat(64))
  for (const key of ['xiaohan-conversation', 'xiaohan-messages', 'xiaohan-prompt']) assert.equal(storage.getItem(key), null)
  assert.equal(storage.getItem('unrelated'), '其他应用数据')
})

test('旧版本无归属缓存不分配给首位访客', () => {
  const storage = storageWith({ 'xiaohan-messages': '旧版公共记录' })
  prepareVisitorCache(storage, 'c'.repeat(64))
  assert.equal(storage.getItem('xiaohan-messages'), null)
})

test('身份初始化失败时不读取或覆盖旧访客缓存', () => {
  const storage = storageWith({ 'xiaohan-messages': '旧访客记录' })
  for (const scope of [undefined, '', 'invalid']) assert.throws(() => prepareVisitorCache(storage, scope), /匿名访客身份初始化失败/)
  assert.equal(storage.getItem('xiaohan-messages'), '旧访客记录')
})
