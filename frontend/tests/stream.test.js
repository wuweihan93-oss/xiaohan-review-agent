import assert from 'node:assert/strict'
import test from 'node:test'
import { readEventStream } from '../src/stream.js'

function response(chunks) {
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk)
      controller.close()
    },
  }), { headers: { 'Content-Type': 'text/event-stream' } })
}

test('网络逐字节拆包、中文和 CRLF 仍能正确解析，心跳不显示为消息', async () => {
  const bytes = new TextEncoder().encode(': 心跳\r\n\r\nevent: token\r\ndata: {"text":"小晗你好"}\r\n\r\nevent: done\ndata: {"answer":"完成"}\n\n')
  const events = []
  await readEventStream(response([...bytes].map((byte) => new Uint8Array([byte]))), (event, data) => events.push([event, data]))
  assert.deepEqual(events, [['token', { text: '小晗你好' }], ['done', { answer: '完成' }]])
})

test('收到第一个片段就回调，无需等待整个回答结束', async () => {
  let controller
  const stream = new ReadableStream({ start(value) { controller = value } })
  let seen
  const first = new Promise((resolve) => { seen = resolve })
  let ended = false
  const reading = readEventStream(new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }), (event, data) => seen([event, data])).then(() => { ended = true })
  controller.enqueue(new TextEncoder().encode('event: token\ndata: {"text":"第一段"}\n\n'))
  assert.deepEqual(await first, ['token', { text: '第一段' }])
  assert.equal(ended, false)
  controller.close()
  await reading
})

test('兼容多行 data，HTTP 错误和错误代理响应显示中文提示', async () => {
  const events = []
  await readEventStream(response([new TextEncoder().encode('event: state\ndata: {"sources": [],\ndata: "trace": []}\n\n')]), (event, data) => events.push([event, data]))
  assert.deepEqual(events, [['state', { sources: [], trace: [] }]])
  await assert.rejects(readEventStream(new Response('{"detail":"请选择资料"}', { status: 400 }), () => {}), /请选择资料/)
  await assert.rejects(readEventStream(new Response('<html>代理错误</html>'), () => {}), /未收到流式回答/)
})
