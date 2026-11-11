import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import json from 'highlight.js/lib/languages/json'
import bash from 'highlight.js/lib/languages/bash'
import sql from 'highlight.js/lib/languages/sql'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import { webSourceUrl } from './sources.js'

for (const [name, language] of Object.entries({ python, javascript, typescript, json, bash, sql, xml, css })) {
  hljs.registerLanguage(name, language)
}
hljs.registerAliases('vue', { languageName: 'xml' })

// 模型回答与网页摘要均按不可信文本处理，禁止执行其中的 HTML。
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })
const escape = markdown.utils.escapeHtml
markdown.validateLink = (url) => Boolean(webSourceUrl({ type: 'web', url }))
markdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
  const token = tokens[index]
  token.attrSet('target', '_blank')
  token.attrSet('rel', 'noopener noreferrer')
  return renderer.renderToken(tokens, index, options)
}
// 图片显示说明文字，避免仅阅读回答就请求模型给出的图片地址。
markdown.renderer.rules.image = (tokens, index) => escape(tokens[index].content || '图片')
markdown.renderer.rules.fence = (tokens, index) => {
  const token = tokens[index]
  const language = token.info.trim().split(/\s+/)[0]
  const code = language && hljs.getLanguage(language)
    ? hljs.highlight(token.content, { language, ignoreIllegals: true }).value
    : escape(token.content)
  return `<div class="code-block"><div class="code-language">${escape(language || '代码')}</div><pre tabindex="0" aria-label="代码"><code class="hljs">${code}</code></pre></div>\n`
}
markdown.renderer.rules.table_open = () => '<div class="markdown-table" tabindex="0" role="region" aria-label="表格"><table>\n'
markdown.renderer.rules.table_close = () => '</table></div>\n'

export function renderMarkdown(content) {
  return markdown.render(typeof content === 'string' ? content : '')
}
