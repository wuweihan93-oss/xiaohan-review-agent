// 引用来自接口与历史记录，网页链接只允许 HTTP / HTTPS。
export function webSourceUrl(source) {
  if (source?.type !== 'web' || typeof source.url !== 'string') return ''
  try {
    const url = new URL(source.url)
    if (!['https:', 'http:'].includes(url.protocol) || !url.hostname || url.username || url.password) return ''
    return url.href
  } catch {
    return ''
  }
}

export function sourceScoreLabel(source) {
  const score = source?.score
  if (source?.type === 'web' || typeof score !== 'number' || !Number.isFinite(score) || score < 0 || score > 1) return ''
  return `${Math.round(score * 100)}%`
}
