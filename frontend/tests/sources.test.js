import assert from 'node:assert/strict'
import test from 'node:test'
import { sourceScoreLabel, webSourceUrl } from '../src/sources.js'

test('网页引用允许正常外链，拒绝脚本、文件链接及包含凭据的地址', () => {
  assert.equal(webSourceUrl({ type: 'web', url: 'https://docs.langchain.com/' }), 'https://docs.langchain.com/')
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'https://user:password@example.com', '无效地址']) {
    assert.equal(webSourceUrl({ type: 'web', url }), '')
  }
  assert.equal(webSourceUrl({ source: '笔记.md' }), '')
})

test('网页不显示向量相似度，旧资料缺少分数时不显示 NaN', () => {
  assert.equal(sourceScoreLabel({ type: 'web', score: 0.9 }), '')
  assert.equal(sourceScoreLabel({ source: '笔记.md', score: 0.92 }), '92%')
  for (const score of [undefined, null, NaN, '0.9', 1.1, -0.1]) assert.equal(sourceScoreLabel({ score }), '')
})
