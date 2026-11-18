
import { createSSEParser } from './sse'

const API_BASE = '/api'
const API_KEY = import.meta.env.VITE_API_KEY

function buildHeaders(extra = {}) {
  if (!API_KEY) {
    throw new Error('Missing VITE_API_KEY')
  }

  return {
    'X-API-Key': API_KEY,
    ...extra,
  }
}

async function request(path, options = {}) {
  const headers = buildHeaders({
    ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    ...options.headers,
  })

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  })

  if (!response.ok) {
    const text = await response.text()
    throw new Error(`HTTP ${response.status}: ${text}`)
  }

  if (response.status === 204) return null

  return response.json()
}

async function streamChat(
  conversationId,
  message,
  { onToken, signal, attachments = [] } = {},
) {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: buildHeaders({
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    }),
    body: JSON.stringify({
      conversation_id: conversationId,
      message,
      attachments,
    }),
    signal,
  })

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`)
  }

  if (!response.body) {
    throw new Error('Streaming response body is unavailable.')
  }

  const reader = response.body.getReader()

  // stream: true 保证跨网络 chunk 被截断的 UTF-8 字节序列正确续接。
  const decoder = new TextDecoder()

  let completed = false

  const parser = createSSEParser(({ event, data }) => {
    if (event === 'token') {
      if (typeof data.content === 'string') {
        onToken?.(data.content)
      }
    } else if (event === 'error') {
      throw new Error(data.message || 'Agent execution failed.')
    } else if (event === 'done') {
      completed = true
    }
  })

  try {
    while (true) {
      const { value, done } = await reader.read()

      if (done) break

      parser.feed(decoder.decode(value, { stream: true }))
    }

    parser.feed(decoder.decode())
    parser.flush()

    if (!completed) {
      throw new Error('Stream ended before completion event.')
    }
  } finally {
    reader.releaseLock()
  }
}

export const api = {
  listSessions() {
    return request('/sessions')
  },

  createSession(title = 'New conversation') {
    return request('/sessions', {
      method: 'POST',
      body: JSON.stringify({ title }),
    })
  },

  getMessages(conversationId) {
    return request(
      `/sessions/${encodeURIComponent(conversationId)}/messages`,
    )
  },

  deleteSession(conversationId) {
    return request(
      `/sessions/${encodeURIComponent(conversationId)}`,
      { method: 'DELETE' },
    )
  },

  streamChat,
}
