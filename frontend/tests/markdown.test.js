import assert from 'node:assert/strict'
import test from 'node:test'
import { renderMarkdown } from '../src/markdown.js'

test('回答支持标题、加粗、列表、引用和表格', () => {
  const html = renderMarkdown('## 复习 RAG\n\n**先检索**，再生成。\n\n1. 切分资料\n2. 写入向量库\n\n> 只使用相关片段\n\n|步骤|工具|\n|---|---|\n|检索|Milvus|')
  for (const tag of ['<h2>', '<strong>', '<ol>', '<blockquote>', '<table>', '<th>', '<td>']) assert.ok(html.includes(tag), tag)
  assert.ok(html.includes('class="markdown-table"'))
})

test('代码高亮保留原文，不执行 HTML；未知语言和未闭合围栏仍可渲染', () => {
  const html = renderMarkdown('```python\ndef review():\n    return "<script>alert(1)</script>"\n```')
  assert.ok(html.includes('hljs-keyword'))
  assert.ok(html.includes('&lt;script&gt;'))
  assert.ok(!html.includes('<script>'))
  const unknown = renderMarkdown('```unknown\n<img src=x onerror=alert(1)>\n```')
  assert.ok(unknown.includes('&lt;img'))
  const partial = renderMarkdown('先看例子：\n\n```python\nprint("生成中')
  assert.ok(partial.includes('生成中'))
  assert.ok(partial.includes('</code></pre>'))
})

test('模型和历史记录中的 HTML、脚本链接与嵌入图片不能执行', () => {
  const html = renderMarkdown('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[脚本](javascript:alert(1))\n\n[数据](data:text/html,test)\n\n[文件](file:///etc/passwd)\n\n[凭据](https://user:password@example.com/)\n\n![说明](https://example.com/tracker.png)')
  assert.ok(!/<script|<img|<iframe|<svg/i.test(html))
  assert.ok(!/href="(?:javascript|data|file):/i.test(html))
  assert.ok(!html.includes('href="https://user:password'))
  assert.ok(html.includes('&lt;script&gt;'))
})

test('正常链接可安全打开，不丢失中文和 URL 参数', () => {
  const html = renderMarkdown('[官方文档](https://docs.langchain.com/?a=1&b=2)\n\nhttps://example.com/')
  assert.ok(html.includes('target="_blank"'))
  assert.ok(html.includes('rel="noopener noreferrer"'))
  assert.ok(html.includes('href="https://docs.langchain.com/?a=1&amp;b=2"'))
  assert.ok(html.includes('官方文档'))
})
