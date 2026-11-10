import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import test from 'node:test'
import { createId } from '../src/id.js'

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

test('公网 HTTP 缺少 randomUUID 时，仍能为会话和消息生成有效且不同的 ID', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
  Object.defineProperty(globalThis, 'crypto', {
    configurable: true,
    value: { getRandomValues: webcrypto.getRandomValues.bind(webcrypto) },
  })
  try {
    const ids = Array.from({ length: 100 }, () => createId())
    for (const id of ids) assert.match(id, uuid)
    assert.equal(new Set(ids).size, ids.length)
  } finally {
    Object.defineProperty(globalThis, 'crypto', original)
  }
})

test('HTTPS 与 localhost 环境可以继续生成有效的 ID', () => {
  assert.match(createId(), uuid)
})
