const API_BASE = '/api'

export async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'same-origin',
    ...options,
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(payload.detail || `请求失败（${response.status}）`)
    error.status = response.status
    throw error
  }
  return payload
}
