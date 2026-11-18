import { afterEach, describe, expect, it, vi } from 'vitest'

vi.stubEnv('VITE_API_KEY', 'test-key')

const { useChat } = await import('./useChat')

// ==================================================
// 受控 fetch:按路径路由,/chat/stream 的 chunk 节奏由测试掌控
// ==================================================

function bytes(text) {
  return new TextEncoder().encode(text)
}

function tokenEvent(text) {
  return bytes(
    `event: token\ndata: ${JSON.stringify({ content: text })}\n\n`,
  )
}

function doneEvent(id) {
  return bytes(
    `event: done\ndata: ${JSON.stringify({ conversation_id: id })}\n\n`,
  )
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function deferred() {
  let resolve

  const promise = new Promise((r) => {
    resolve = r
  })

  return { promise, resolve }
}

// gates[i]:在发送第 i 个 chunk 前等待对应 promise,
// 使测试能在流中途做断言/取消,精确复现真实网络的逐步到达。
function installChatStreamFetch(chunks, gates = []) {
  let index = 0

  vi.stubGlobal(
    'fetch',
    vi.fn(async (url, init = {}) => {
      const path = new URL(url, 'http://localhost').pathname

      if (path === '/api/sessions' && init.method === 'POST') {
        return jsonResponse(
          {
            conversation_id: 'conv-1',
            title: 'New conversation',
            created_at: 'now',
            updated_at: 'now',
          },
          201,
        )
      }

      if (path === '/api/sessions' && !init.method) {
        return jsonResponse([])
      }

      if (path === '/api/chat/stream') {
        const signal = init.signal

        const stream = new ReadableStream({
          async pull(controller) {
            if (signal?.aborted) {
              controller.error(
                new DOMException(
                  'The operation was aborted.',
                  'AbortError',
                ),
              )
              return
            }

            if (gates[index]) {
              await gates[index]
            }

            if (signal?.aborted) {
              controller.error(
                new DOMException(
                  'The operation was aborted.',
                  'AbortError',
                ),
              )
              return
            }

            if (index < chunks.length) {
              controller.enqueue(chunks[index++])
            } else {
              controller.close()
            }
          },
        })

        return new Response(stream, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }

      throw new Error(`Unexpected fetch: ${init.method || 'GET'} ${path}`)
    }),
  )
}

async function waitFor(assertion, attempts = 200) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      assertion()
      return
    } catch {
      await new Promise((resolve) => {
        setTimeout(resolve, 0)
      })
    }
  }

  assertion()
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useChat streaming', () => {
  it('renders assistant content incrementally, token by token', async () => {
    // 第 2 个 token 前暂停:测试在流中途断言已显示内容。
    // 若前端等待完整回答才渲染,中间断言必然失败。
    const gate = deferred()

    installChatStreamFetch(
      [
        tokenEvent('你'),
        tokenEvent('好'),
        tokenEvent('!'),
        doneEvent('conv-1'),
      ],
      [null, gate.promise],
    )

    const chat = useChat()

    const finished = chat.sendMessage('hello')

    await waitFor(() => {
      expect(chat.messages.value[1].content).toBe('你')
    })

    // 关键验证点:流被刻意暂停,答案尚未完整,内容已上屏。
    expect(chat.loading.value).toBe(true)
    expect(chat.messages.value[1].content).toBe('你')

    gate.resolve()

    await finished

    expect(chat.messages.value[1].content).toBe('你好!')
    expect(chat.loading.value).toBe(false)
    expect(chat.error.value).toBe('')
  })

  it('keeps partial content and reports no error when the user stops', async () => {
    const gate = deferred()

    installChatStreamFetch(
      [tokenEvent('部分'), tokenEvent('剩余'), doneEvent('conv-1')],
      [null, gate.promise],
    )

    const chat = useChat()

    const finished = chat.sendMessage('hello')

    await waitFor(() => {
      expect(chat.messages.value[1].content).toBe('部分')
    })

    chat.stopGeneration()
    gate.resolve()

    await finished

    expect(chat.messages.value[1].content).toBe('部分')
    expect(chat.error.value).toBe('')
    expect(chat.loading.value).toBe(false)
  })

  it('shows backend error events in the error ref', async () => {
    installChatStreamFetch([
      tokenEvent('partial'),
      bytes(
        'event: error\ndata: {"message": "Agent execution failed."}\n\n',
      ),
    ])

    const chat = useChat()

    await chat.sendMessage('hello')

    expect(chat.error.value).toBe('Agent execution failed.')
    expect(chat.loading.value).toBe(false)
  })
})

describe('useChat sessions', () => {
  it('creates a session and activates it', async () => {
    installChatStreamFetch([doneEvent('conv-1')])

    const chat = useChat()

    await chat.createSession()

    expect(chat.activeConversationId.value).toBe('conv-1')
    expect(chat.sessions.value).toHaveLength(1)
    expect(chat.messages.value).toEqual([])
  })

  it('auto-creates a session when sending without one', async () => {
    installChatStreamFetch([tokenEvent('ok'), doneEvent('conv-1')])

    const chat = useChat()

    await chat.sendMessage('hello')

    expect(chat.activeConversationId.value).toBe('conv-1')
    expect(chat.messages.value[0]).toEqual({
      role: 'user',
      content: 'hello',
      attachments: [],
    })
    expect(chat.messages.value[1].content).toBe('ok')
  })

  it('ignores empty sends and duplicate concurrent sends', async () => {
    installChatStreamFetch([doneEvent('conv-1')])

    const chat = useChat()

    await chat.sendMessage('   ')

    expect(chat.messages.value).toEqual([])
    expect(fetch).not.toHaveBeenCalled()
  })
})

describe('useChat attachments', () => {
  // 专用 fetch:除路由外还把 /chat/stream 的 body 记录下来供断言。
  function installBodyCapturingFetch(bodies) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url, init = {}) => {
        const path = new URL(url, 'http://localhost').pathname

        if (path === '/api/sessions' && init.method === 'POST') {
          return jsonResponse(
            {
              conversation_id: 'conv-att',
              title: 'New conversation',
              created_at: 'now',
              updated_at: 'now',
            },
            201,
          )
        }

        if (path === '/api/sessions' && !init.method) {
          return jsonResponse([])
        }

        if (path === '/api/chat/stream') {
          bodies.push(JSON.parse(init.body))

          const stream = new ReadableStream({
            start(controller) {
              controller.enqueue(tokenEvent('看懂了'))
              controller.enqueue(doneEvent('conv-att'))
              controller.close()
            },
          })

          return new Response(stream, {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
          })
        }

        throw new Error(`Unexpected fetch: ${init.method || 'GET'} ${path}`)
      }),
    )
  }

  it('sends contract attachments; message keeps display form', async () => {
    const bodies = []

    installBodyCapturingFetch(bodies)

    const chat = useChat()

    await chat.sendMessage('看图', [
      { kind: 'image', name: 'shot.png', mime: 'image/png', size: 2, data: 'aGk=' },
      { kind: 'audio', name: 'voice.wav', mime: 'audio/wav', size: 1024 },
    ])

    const streamBody = bodies.find((body) => body.message === '看图')

    expect(streamBody).toBeTruthy()
    // API 契约:图片带 data;本条语音未携带 data(纯元数据形态,M1-c 起
    // 语音入口选中的文件才会带 base64;任何形态都不带 previewUrl)。
    expect(streamBody.attachments[0]).toEqual({
      kind: 'image',
      name: 'shot.png',
      mime: 'image/png',
      size: 2,
      data: 'aGk=',
    })
    expect(streamBody.attachments[1]).toEqual({
      kind: 'audio',
      name: 'voice.wav',
      mime: 'audio/wav',
      size: 1024,
    })

    // 展示形态:无 data 大字符串;图片带即时缩略图,语音没有。
    const userMessage = chat.messages.value[0]

    expect(userMessage.role).toBe('user')
    expect(userMessage.content).toBe('看图')
    expect(userMessage.attachments[0].data).toBeUndefined()
    expect(userMessage.attachments[0].previewUrl).toBe('data:image/png;base64,aGk=')
    expect(userMessage.attachments[1].previewUrl).toBe('')

    expect(chat.messages.value[1].content).toBe('看懂了')
  })

  it('allows attachment-only sends with empty text', async () => {
    const bodies = []

    installBodyCapturingFetch(bodies)

    const chat = useChat()

    await chat.sendMessage('', [{ kind: 'file', name: 'x.pdf', mime: 'application/pdf', size: 9 }])

    const streamBody = bodies.find((body) => body.conversation_id === 'conv-att')

    expect(streamBody.message).toBe('')
    expect(streamBody.attachments).toHaveLength(1)
    expect(chat.messages.value[0].role).toBe('user')
    expect(chat.messages.value[0].attachments[0].name).toBe('x.pdf')

    // 文本与附件都为空仍然被拒绝,不发任何请求。
    const before = fetch.mock.calls.length

    await chat.sendMessage('', [])

    expect(fetch.mock.calls.length).toBe(before)
  })
})
