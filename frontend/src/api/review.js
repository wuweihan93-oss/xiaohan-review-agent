export async function streamReview(payload, signal, onEvent) {
  const response = await fetch('/api/review/stream', {
    credentials: 'same-origin',
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify(payload),
  })
  const { readEventStream } = await import('../stream.js')
  await readEventStream(response, onEvent)
}
