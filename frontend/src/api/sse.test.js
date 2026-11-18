import { afterEach, describe, expect, it, vi } from 'vitest'

vi.stubEnv('VITE_API_KEY', 'test-key')

const { createSSEParser } = await import('./sse')
const { api } = await import('./client')

// ==================================================
// createSSEParser:纯解析状态机
// ==================================================

function collectParser() {
  const events = []

  return {
    events,
    parser: createSSEParser((event) => {
      events.push(event)
    }),
  }
}

describe('createSSEParser', () => {
  it('parses multiple consecutive token events', () => {
    const { events, parser } = collectParser()

    parser.feed(
      'event: token\ndata: {"content": "你好"}\n\n' +
        'event: token\ndata: {"content": "世界"}\n\n',
    )
    parser.flush()

    expect(events).toEqual([
      { event: 'token', data: { content: '你好' } },
      { event: 'token', data: { content: '世界' } },
    ])
  })

  it('reassembles one event split across many chunks', () => {
    const { events, parser } = collectParser()

    parser.feed('eve')
    parser.feed('nt: tok')
    parser.feed('en\nda')
    parser.feed('ta: {"con')
    parser.feed('tent": "')
    parser.feed('流式"}')
    parser.feed('\n')
    parser.feed('\n')
    parser.flush()

    expect(events).toEqual([
      { event: 'token', data: { content: '流式' } },
    ])
  })

  it('handles CRLF and LF line endings', () => {
    const { events, parser } = collectParser()

    parser.feed('event: token\r\ndata: {"content": "crlf"}\r\n\r\n')
    parser.feed('event: token\ndata: {"content": "lf"}\n\n')
    parser.flush()

    expect(events).toEqual([
      { event: 'token', data: { content: 'crlf' } },
      { event: 'token', data: { content: 'lf' } },
    ])
  })

  it('handles CRLF split across chunks (\\r at end of one, \\n at start of next)', () => {
    const { events, parser } = collectParser()

    parser.feed('event: token\r')
    parser.feed('\ndata: {"content": "x"}\r')
    parser.feed('\n\r')
    parser.feed('\n')
    parser.flush()

    expect(events).toEqual([
      { event: 'token', data: { content: 'x' } },
    ])
  })

  it('flush dispatches a trailing event without final blank line', () => {
    const { events, parser } = collectParser()

    parser.feed('event: done\ndata: {"conversation_id": "c-1"}')
    parser.flush()

    expect(events).toEqual([
      { event: 'done', data: { conversation_id: 'c-1' } },
    ])
  })

  it('dispatches done and error events', () => {
    const { events, parser } = collectParser()

    parser.feed(
      'event: done\ndata: {"conversation_id": "abc"}\n\n' +
        'event: error\ndata: {"message": "Agent execution failed."}\n\n',
    )
    parser.flush()

    expect(events[0].event).toBe('done')
    expect(events[1].event).toBe('error')
    expect(events[1].data.message).toBe('Agent execution failed.')
  })

  it('ignores comment and heartbeat lines', () => {
    const { events, parser } = collectParser()

    parser.feed(': keep-alive\n\n')
    parser.feed('event: token\ndata: {"content": "ok"}\n\n')
    parser.flush()

    expect(events).toHaveLength(1)
    expect(events[0].data.content).toBe('ok')
  })

  it('strips only a single leading space of the data field', () => {
    const { events, parser } = collectParser()

    // 后端 sse_event 使用 "data: {json}",协议空格已被剥离,
    // JSON 中保留的内容空格不能丢失。
    parser.feed('event: token\ndata: {"content": " a "}\n\n')
    parser.flush()

    expect(events[0].data.content).toBe(' a ')
  })
})

// ==================================================
// api.streamChat:字节流 → TextDecoder → parser
// ==================================================

function bytes(text) {
  return new TextEncoder().encode(text)
}

function sseStream(chunks) {
  let index = 0

  return new ReadableStream({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(chunks[index++])
      } else {
        controller.close()
      }
    },
  })
}

function installFetch(chunks, options = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url, init) => {
      options.onRequest?.(url, init)

      return new Response(sseStream(chunks), {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('api.streamChat', () => {
  it('delivers tokens incrementally and completes on done', async () => {
    installFetch([
      bytes('event: token\ndata: {"content": "你好"}\n\n'),
      bytes('event: token\ndata: {"content": "世界"}\n\n'),
      bytes('event: done\ndata: {"conversation_id": "c1"}\n\n'),
    ])

    const tokens = []

    await api.streamChat('c1', 'hi', {
      onToken(token) {
        tokens.push(token)
      },
    })

    expect(tokens).toEqual(['你好', '世界'])
  })

  it('rejoins a Chinese character split across network chunks mid-byte', async () => {
    // “你” 的 UTF-8 编码为 E4 BD A0,拆成两个 chunk 各含部分字节。
    const full = bytes(
      'event: token\ndata: {"content": "中文流式"}\n\n' +
        'event: done\ndata: {"conversation_id": "c1"}\n\n',
    )

    const cut = full.indexOf(0xe4) + 1

    installFetch([full.slice(0, cut), full.slice(cut)])

    const tokens = []

    await api.streamChat('c1', 'hi', {
      onToken(token) {
        tokens.push(token)
      },
    })

    expect(tokens.join('')).toBe('中文流式')
    expect(tokens.join('')).not.toContain('�')
  })

  it('throws on error events', async () => {
    installFetch([
      bytes('event: token\ndata: {"content": "partial"}\n\n'),
      bytes(
        'event: error\ndata: {"message": "Agent execution failed."}\n\n',
      ),
    ])

    const tokens = []

    await expect(
      api.streamChat('c1', 'hi', {
        onToken(token) {
          tokens.push(token)
        },
      }),
    ).rejects.toThrow('Agent execution failed.')
  })

  it('rejects when the stream ends without a done event', async () => {
    installFetch([
      bytes('event: token\ndata: {"content": "truncated"}\n\n'),
    ])

    await expect(
      api.streamChat('c1', 'hi', { onToken() {} }),
    ).rejects.toThrow('Stream ended before completion event.')
  })

  it('propagates AbortError when the user cancels mid-stream', async () => {
    const controller = new AbortController()

    const chunks = [
      bytes('event: token\ndata: {"content": "partial"}\n\n'),
      bytes('event: done\ndata: {"conversation_id": "c1"}\n\n'),
    ]

    let index = 0

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        const stream = new ReadableStream({
          pull(readController) {
            if (controller.signal.aborted) {
              readController.error(
                new DOMException(
                  'The operation was aborted.',
                  'AbortError',
                ),
              )
              return
            }

            if (index < chunks.length) {
              readController.enqueue(chunks[index++])
            } else {
              readController.close()
            }
          },
        })

        return new Response(stream, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }),
    )

    const tokens = []

    const promise = api.streamChat('c1', 'hi', {
      signal: controller.signal,
      onToken(token) {
        tokens.push(token)
        controller.abort()
      },
    })

    await expect(promise).rejects.toMatchObject({
      name: 'AbortError',
    })

    expect(tokens).toEqual(['partial'])
  })
})
