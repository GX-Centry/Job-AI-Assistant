// 验证浏览器实际使用的链路:FastAPI → Vite dev proxy(/api)→ fetch/ReadableStream。
// 使用与前端相同的 TextDecoder(stream: true) 解码,按到达时间打印 token 事件,
// 证明 Vite 代理不会缓冲 SSE。纯 Node 脚本,无第三方依赖。
//
// 用法: node scripts/check_vite_proxy_stream.mjs [proxyBaseURL]
//   proxyBaseURL 默认 http://127.0.0.1:5173

import { readFileSync } from 'node:fs'

const PROXY_BASE = process.argv[2] ?? 'http://127.0.0.1:5173'

const envText = readFileSync(
  new URL('../frontend/.env.local', import.meta.url),
  'utf-8',
)

const match = envText.match(/^VITE_API_KEY\s*=\s*(.+)$/m)

if (!match) {
  console.error('VITE_API_KEY not found in frontend/.env.local')
  process.exit(1)
}

const apiKey = match[1].trim()

async function main() {
  const sessionResponse = await fetch(`${PROXY_BASE}/api/sessions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': apiKey,
    },
    body: JSON.stringify({ title: 'Proxy streaming check' }),
  })

  if (!sessionResponse.ok) {
    console.error(
      `Create session failed: HTTP ${sessionResponse.status}`,
      await sessionResponse.text(),
    )
    process.exit(1)
  }

  const { conversation_id: conversationId } =
    await sessionResponse.json()

  const response = await fetch(`${PROXY_BASE}/api/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      'X-API-Key': apiKey,
    },
    body: JSON.stringify({
      conversation_id: conversationId,
      message: '请用中文写一段约150字的话,介绍FastAPI的两个优点。',
    }),
  })

  if (!response.ok) {
    console.error(
      `Stream failed: HTTP ${response.status}`,
      await response.text(),
    )
    process.exit(1)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()

  const start = Date.now()

  let buffer = ''
  let eventName = ''
  let tokenEvents = 0
  let tokenChars = 0
  let firstTokenAt = null

  function processLine(rawLine) {
    let line = rawLine

    if (line.endsWith('\r')) line = line.slice(0, -1)

    if (line === '') {
      eventName = ''
    } else if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith('data:') && eventName === 'token') {
      const elapsed = (Date.now() - start) / 1000

      if (firstTokenAt === null) firstTokenAt = elapsed

      const payload = JSON.parse(line.slice(5).trim())

      tokenEvents += 1
      tokenChars += (payload.content ?? '').length

      if (tokenEvents <= 3 || tokenEvents % 10 === 0) {
        console.log(
          `t+${elapsed.toFixed(2)}s token #${tokenEvents}: ` +
            JSON.stringify((payload.content ?? '').slice(0, 30)),
        )
      }
    }
  }

  while (true) {
    const { value, done } = await reader.read()

    if (done) break

    buffer += decoder.decode(value, { stream: true })

    let index

    while ((index = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, index)

      buffer = buffer.slice(index + 1)
      processLine(line)
    }
  }

  const total = ((Date.now() - start) / 1000).toFixed(2)

  console.log()
  console.log(
    `proxy=${PROXY_BASE} token_events=${tokenEvents} ` +
      `chars=${tokenChars} first_token_at=${firstTokenAt?.toFixed(2)}s ` +
      `total=${total}s`,
  )

  if (tokenEvents > 1) {
    console.log('VERDICT: Vite proxy streams SSE incrementally (no buffering)')
  } else {
    console.log('VERDICT: proxy buffered or single-token response')
    process.exit(1)
  }
}

main()
