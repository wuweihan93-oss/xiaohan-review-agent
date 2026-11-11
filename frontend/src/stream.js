// fetch 支持 POST 和主动中断；按 SSE 行解析，兼容网络拆包与跨包的中文字符。
export async function readEventStream(response, onEvent) {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail || `请求失败（${response.status}）`)
  }
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('未收到流式回答，请检查后端和代理配置。')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let event = 'message'
  let data = []
  let ended = false
  function line(value) {
    if (!value) {
      if (data.length) onEvent(event, JSON.parse(data.join('\n')))
      event = 'message'
      data = []
    } else if (value.startsWith('event:')) {
      event = value.slice(6).trimStart()
    } else if (value.startsWith('data:')) {
      data.push(value.slice(5).replace(/^ /, ''))
    }
  }
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      let index
      while ((index = buffer.indexOf('\n')) >= 0) {
        line(buffer.slice(0, index).replace(/\r$/, ''))
        buffer = buffer.slice(index + 1)
      }
      if (done) {
        if (buffer) line(buffer.replace(/\r$/, ''))
        line('')
        ended = true
        break
      }
    }
  } finally {
    if (!ended) await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
