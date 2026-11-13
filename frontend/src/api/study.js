import { request } from './client.js'

export const getVisitor = () => request('/visitor')
export const getHealth = () => request('/health')
export const getMaterials = () => request('/materials')
export const importTutorialMaterials = () => request('/materials/import-tutorial', { method: 'POST' })
export const deleteMaterial = (id) => request(`/materials/${encodeURIComponent(id)}`, { method: 'DELETE' })
export const getConversations = ({ limit = 50, offset = 0 } = {}) =>
  request(`/conversations?limit=${limit}&offset=${offset}`)
export const getConversation = (id) => request(`/conversations/${encodeURIComponent(id)}`)
export const deleteConversation = (id) => request(`/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' })

export function uploadMaterial(file) {
  const body = new FormData()
  body.append('file', file)
  return request('/materials/upload', { method: 'POST', body })
}
