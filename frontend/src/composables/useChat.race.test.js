// M0-R1 会话竞态与停止生命周期回归测试。
// 每个用例用受控 Promise 精确重现项目经理复现的乱序,不依赖随机延迟。
// 对应缺陷(修复前应当失败):
//   1. 过期历史回包覆盖当前会话内容(串位)。
//   2. 过期历史回包清空生成中的消息,追加 token 崩溃。
//   3. 已删除会话的历史回包让内容重新出现。
//   4. 删除在途时发送仍被放行(同类冲突)。
//   5. 创建在途时发送仍被放行(重复创建/发送冲突)。
//   6. 真实并发重复发送。
//   7. 自动建会话在途时停止:状态未及时恢复,且创建返回后仍继续发起生成。
//   8. 流已完成但列表刷新未结束的边界。
//   9. 过期历史请求的失败不得污染当前错误状态。
//
// M0-R2 补充验收(修复前应当失败):
//   R10. 首 token 前停止:空 assistant 占位气泡必须被清除。
//   R11. 已取消发送的迟到创建结果(成功/失败)不得接管视图或写入错误。
//   R12. 创建成功必须接管在途历史请求的视图归属。
//   R13. 后台列表刷新不得阻塞下一次发送与正常会话操作。
//
// M0-R3 边界收口(修复前应当失败):
//   R14. 列表刷新与创建回包交错(GET 先带回已创建的 B,POST 后返回 B):
//        列表按 conversation_id 唯一,不得出现 [B, B]。
//   R15. 删除其他会话不得作废当前会话的在途历史请求;
//        删除当前会话后拒绝迟到历史的保护必须保留(见缺陷 3)。
//   R16. 旧一轮后台列表刷新的失败不得显示为第二轮生成的失败,
//        第二轮成功收尾后也不得残留该错误。

import { afterEach, describe, expect, it, vi } from 'vitest'

vi.stubEnv('VITE_API_KEY', 'test-key')

const { useChat } = await import('./useChat')

// ==================================================
// 受控 fetch:按 "METHOD /path" 路由,每个 handler 自行决定何时返回
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

function sseResponse(chunks) {
  let index = 0

  return new Response(
    new ReadableStream({
      async pull(controller) {
        if (index < chunks.length) {
          // chunk 可以是 Promise:测试由此在流中途设置门控。
          controller.enqueue(await chunks[index++])
        } else {
          controller.close()
        }
      },
    }),
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
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
  let reject

  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })

  return { promise, resolve, reject }
}

// 让出宏任务,推进多层 Promise 链。
async function settle(times = 4) {
  for (let i = 0; i < times; i += 1) {
    await new Promise((resolve) => {
      setTimeout(resolve, 0)
    })
  }
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

function installRoutedFetch(routes) {
  const calls = []

  vi.stubGlobal(
    'fetch',
    vi.fn(async (url, init = {}) => {
      const path = new URL(url, 'http://localhost').pathname
      const method = (init.method || 'GET').toUpperCase()
      const key = `${method} ${path}`

      calls.push({ key, init })

      const handler = routes[key]

      if (!handler) {
        throw new Error(`Unexpected fetch: ${key}`)
      }

      return handler(url, init)
    }),
  )

  return calls
}

function sessionFixture(id) {
  return {
    conversation_id: id,
    title: `Session ${id}`,
    created_at: 'now',
    updated_at: 'now',
  }
}

function historyMessage(content) {
  return { role: 'user', content }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// ==================================================
// 缺陷 1:会话内容串位
// ==================================================

describe('useChat history races', () => {
  it('discards a stale history response when a newer selection completed', async () => {
    const gateA = deferred()

    const routes = {
      'GET /api/sessions/a/messages': async () => {
        await gateA.promise

        return jsonResponse([historyMessage('A-stale')])
      },
      'GET /api/sessions/b/messages': () =>
        Promise.resolve(jsonResponse([historyMessage('B-current')])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const pendingA = chat.selectSession(sessionFixture('a'))

    await settle()

    await chat.selectSession(sessionFixture('b'))

    expect(chat.messages.value.map((m) => m.content)).toEqual(['B-current'])

    // 过期回包稍后到达:不得覆盖当前会话内容。
    gateA.resolve()

    await pendingA
    await settle()

    expect(chat.messages.value.map((m) => m.content)).toEqual(['B-current'])
    expect(chat.activeConversationId.value).toBe('b')
    expect(chat.error.value).toBe('')
  })

  it('does not clear an in-flight send when a stale history resolves empty', async () => {
    const gateHistory = deferred()
    const gateStream = deferred()

    const streamCalls = []

    const routes = {
      'GET /api/sessions/a/messages': async () => {
        await gateHistory.promise

        return jsonResponse([])
      },
      'POST /api/chat/stream': (url, init) => {
        streamCalls.push(init)

        return gateStream.promise.then(() =>
          sseResponse([tokenEvent('ok'), doneEvent('a')]),
        )
      },
      'GET /api/sessions': () => Promise.resolve(jsonResponse([])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const pendingHistory = chat.selectSession(sessionFixture('a'))

    await settle()

    // 历史请求仍挂起时发送:发送接管视图,过期历史不得清空消息。
    const finished = chat.sendMessage('hello')

    await settle()

    gateHistory.resolve()

    await settle()

    gateStream.resolve()

    await pendingHistory
    await finished

    expect(chat.error.value).toBe('')
    expect(chat.messages.value.map((m) => m.content)).toEqual([
      'hello',
      'ok',
    ])
    expect(streamCalls).toHaveLength(1)
  })

  it('keeps a deleted conversation invisible when its history arrives late', async () => {
    const gateHistory = deferred()

    const routes = {
      'GET /api/sessions/a/messages': async () => {
        await gateHistory.promise

        return jsonResponse([historyMessage('A-ghost')])
      },
      'DELETE /api/sessions/a': () =>
        Promise.resolve(new Response(null, { status: 204 })),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const pendingHistory = chat.selectSession(sessionFixture('a'))

    await settle()

    await chat.deleteSession(sessionFixture('a'))

    expect(chat.activeConversationId.value).toBe('')
    expect(chat.messages.value).toEqual([])

    // 删除之后过期历史才返回:不得让内容重新出现。
    gateHistory.resolve()

    await pendingHistory
    await settle()

    expect(chat.activeConversationId.value).toBe('')
    expect(chat.messages.value).toEqual([])
    expect(chat.error.value).toBe('')
  })

  it('ignores a stale history failure after a newer selection', async () => {
    const gateA = deferred()

    const routes = {
      'GET /api/sessions/a/messages': async () => {
        await gateA.promise

        throw new Error('HTTP 500')
      },
      'GET /api/sessions/b/messages': () =>
        Promise.resolve(jsonResponse([historyMessage('B-current')])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const pendingA = chat.selectSession(sessionFixture('a'))

    await settle()

    await chat.selectSession(sessionFixture('b'))

    gateA.resolve()

    await pendingA
    await settle()

    // 过期失败不得污染当前错误状态。
    expect(chat.error.value).toBe('')
    expect(chat.messages.value.map((m) => m.content)).toEqual(['B-current'])
  })
})

// ==================================================
// 缺陷 4/5/6:创建、删除在途与发送冲突、并发重复发送
// ==================================================

describe('useChat operation-state conflicts', () => {
  it('blocks sending while a delete request is in flight', async () => {
    const gateDelete = deferred()

    const streamCalls = []

    const routes = {
      'GET /api/sessions/a/messages': () =>
        Promise.resolve(jsonResponse([historyMessage('A-old')])),
      'DELETE /api/sessions/a': async () => {
        await gateDelete.promise

        return new Response(null, { status: 204 })
      },
      'POST /api/chat/stream': (url, init) => {
        streamCalls.push(init)

        return Promise.resolve(
          sseResponse([tokenEvent('x'), doneEvent('a')]),
        )
      },
      'GET /api/sessions': () => Promise.resolve(jsonResponse([])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    await chat.selectSession(sessionFixture('a'))

    const deleting = chat.deleteSession(sessionFixture('a'))

    await settle()

    await chat.sendMessage('should be blocked')

    // 删除在途:发送必须被拒,不得向已删除会话发起流式请求。
    expect(streamCalls).toHaveLength(0)

    gateDelete.resolve()

    await deleting

    expect(chat.sessions.value).toEqual([])
    expect(chat.activeConversationId.value).toBe('')
    expect(chat.messages.value).toEqual([])
  })

  it('blocks a second send while session creation is in flight', async () => {
    const gateCreate = deferred()

    const createCalls = []
    const streamCalls = []

    const routes = {
      'POST /api/sessions': (url, init) => {
        createCalls.push(init)

        return gateCreate.promise.then(() =>
          jsonResponse(sessionFixture('created'), 201),
        )
      },
      'POST /api/chat/stream': (url, init) => {
        streamCalls.push(init)

        return Promise.resolve(
          sseResponse([tokenEvent('x'), doneEvent('created')]),
        )
      },
      'GET /api/sessions': () => Promise.resolve(jsonResponse([])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const creating = chat.createSession()

    await settle()

    await chat.sendMessage('should be blocked')

    expect(createCalls).toHaveLength(1)
    expect(streamCalls).toHaveLength(0)

    gateCreate.resolve()

    await creating

    expect(chat.activeConversationId.value).toBe('created')
    expect(chat.messages.value).toEqual([])
  })

  it('ignores a second concurrent send while generation is running', async () => {
    const streamCalls = []

    const routes = {
      'GET /api/sessions/a/messages': () =>
        Promise.resolve(jsonResponse([])),
      'POST /api/chat/stream': (url, init) => {
        streamCalls.push(init)

        return Promise.resolve(
          sseResponse([tokenEvent('x'), doneEvent('a')]),
        )
      },
      'GET /api/sessions': () => Promise.resolve(jsonResponse([])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    await chat.selectSession(sessionFixture('a'))

    const first = chat.sendMessage('one')
    const second = chat.sendMessage('two')

    await Promise.all([first, second])

    expect(streamCalls).toHaveLength(1)
    expect(chat.messages.value.map((m) => m.content)).toEqual(['one', 'x'])
  })
})

// ==================================================
// 缺陷 7/8:停止生命周期
// ==================================================

describe('useChat stop lifecycle', () => {
  it('exposes separated busy states', () => {
    const chat = useChat()

    expect(chat.generating).toBeDefined()
    expect(chat.historyLoading).toBeDefined()
    expect(chat.sessionsBusy).toBeDefined()
    expect(chat.generating.value).toBe(false)
    expect(chat.historyLoading.value).toBe(false)
    expect(chat.sessionsBusy.value).toBe(false)
  })

  it('restores operable state when stopped during automatic session creation', async () => {
    const gateCreate = deferred()

    const streamCalls = []

    const routes = {
      'POST /api/sessions': async () => {
        await gateCreate.promise

        return jsonResponse(sessionFixture('created'), 201)
      },
      'POST /api/chat/stream': (url, init) => {
        streamCalls.push(init)

        // 若已取消的发送仍走到流式接口,signal 已被 abort(浏览器 fetch 会立即拒绝)。
        if (init.signal?.aborted) {
          return Promise.reject(
            new DOMException('The operation was aborted.', 'AbortError'),
          )
        }

        return Promise.resolve(
          sseResponse([tokenEvent('x'), doneEvent('created')]),
        )
      },
      'GET /api/sessions': () => Promise.resolve(jsonResponse([])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const finished = chat.sendMessage('hello')

    await settle()

    expect(chat.generating.value).toBe(true)

    chat.stopGeneration()

    // 停止后必须及时恢复“生成”状态;建会话请求仍在途,
    // 它是真实的在途用户级操作,继续计入 sessionsBusy —— generating=false
    // 单独不代表全部交互恢复。
    expect(chat.generating.value).toBe(false)
    expect(chat.sessionsBusy.value).toBe(true)
    expect(chat.loading.value).toBe(true)

    gateCreate.resolve()

    await finished
    await settle()

    // 已取消的流程不得在建会话返回后继续插入消息、发起生成,
    // 也不得激活迟到的会话:服务端已建的会话保留,下次列表刷新自然带回。
    expect(streamCalls).toHaveLength(0)
    expect(chat.messages.value).toEqual([])
    expect(chat.activeConversationId.value).toBe('')
    expect(chat.sessions.value).toEqual([])
    expect(chat.error.value).toBe('')
    expect(chat.generating.value).toBe(false)
    expect(chat.sessionsBusy.value).toBe(false)
    expect(chat.loading.value).toBe(false)
  })

  it('treats stop as no-op when the stream completed but the list refresh is pending', async () => {
    const gateList = deferred()

    const routes = {
      'GET /api/sessions/a/messages': () =>
        Promise.resolve(jsonResponse([])),
      'POST /api/chat/stream': () =>
        Promise.resolve(sseResponse([tokenEvent('x'), doneEvent('a')])),
      'GET /api/sessions': async () => {
        await gateList.promise

        return jsonResponse([sessionFixture('a')])
      },
    }

    installRoutedFetch(routes)

    const chat = useChat()

    await chat.selectSession(sessionFixture('a'))

    const finished = chat.sendMessage('hello')

    // 流已完成,但会话列表刷新仍挂起。
    await waitFor(() => {
      expect(chat.messages.value[1].content).toBe('x')
    })

    // 生成已结束:停止操作为空操作,不得污染状态。
    expect(chat.generating.value).toBe(false)

    chat.stopGeneration()

    expect(chat.error.value).toBe('')
    expect(chat.messages.value[1].content).toBe('x')

    gateList.resolve()

    await finished

    expect(chat.sessions.value).toEqual([sessionFixture('a')])
    expect(chat.loading.value).toBe(false)
  })
})

// ==================================================
// M0-R2 缺口 1:首 token 前停止,空占位气泡必须被清除
// ==================================================

describe('useChat stop before first token', () => {
  it('removes the empty assistant placeholder when stopped before the first token', async () => {
    // 流永不返回首 token:复现“挂起首 token”的复现路径。
    const streamCalls = []

    const routes = {
      'GET /api/sessions/a/messages': () =>
        Promise.resolve(jsonResponse([])),
      'POST /api/chat/stream': (url, init) => {
        streamCalls.push(init)

        // 永远挂起:模拟首 token 迟迟不到达。
        return new Promise(() => {})
      },
    }

    installRoutedFetch(routes)

    const chat = useChat()

    await chat.selectSession(sessionFixture('a'))

    chat.sendMessage('hello')

    await settle()

    // 发送后、首 token 前:用户消息 + 空 assistant 占位。
    expect(chat.generating.value).toBe(true)
    expect(chat.messages.value).toHaveLength(2)
    expect(chat.messages.value[1].content).toBe('')

    chat.stopGeneration()

    // 停止必须同步清掉属于该流程的空占位;用户消息保留。
    expect(chat.generating.value).toBe(false)
    expect(chat.messages.value).toHaveLength(1)
    expect(chat.messages.value[0]).toEqual({
      role: 'user',
      content: 'hello',
      attachments: [],
    })
    expect(chat.error.value).toBe('')
  })

  it('keeps generated partial content when stopped after tokens arrived', async () => {
    const streamCalls = []
    const gateSecondToken = deferred()

    const routes = {
      'GET /api/sessions/a/messages': () =>
        Promise.resolve(jsonResponse([])),
      'POST /api/chat/stream': (url, init) => {
        streamCalls.push(init)

        // 第二个 token 被门控:停止发生在“部分”已生成、流未结束之时。
        return Promise.resolve(
          sseResponse([
            tokenEvent('部分'),
            gateSecondToken.promise.then(() => tokenEvent('继续')),
          ]),
        )
      },
      'GET /api/sessions': () => Promise.resolve(jsonResponse([])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    await chat.selectSession(sessionFixture('a'))

    const finished = chat.sendMessage('hello')

    await waitFor(() => {
      expect(chat.messages.value[1].content).toBe('部分')
    })

    chat.stopGeneration()

    // 已生成片段必须保留,只是不再继续追加。
    expect(chat.messages.value).toHaveLength(2)
    expect(chat.messages.value[1].content).toBe('部分')
    expect(chat.error.value).toBe('')

    // 放行后续 token:失效流程的 token 与异常都必须被丢弃。
    gateSecondToken.resolve()

    await finished
    await settle()

    expect(chat.messages.value[1].content).toBe('部分')
    expect(chat.error.value).toBe('')
  })

  it('placeholder cleanup must not touch the next send messages', async () => {
    let streamCount = 0

    const routes = {
      'GET /api/sessions/a/messages': () =>
        Promise.resolve(jsonResponse([])),
      'POST /api/chat/stream': () => {
        streamCount += 1

        if (streamCount === 1) {
          // 第一个流挂起(首 token 未到)。
          return new Promise(() => {})
        }

        return Promise.resolve(
          sseResponse([tokenEvent('y'), doneEvent('a')]),
        )
      },
      'GET /api/sessions': () => Promise.resolve(jsonResponse([])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    await chat.selectSession(sessionFixture('a'))

    chat.sendMessage('hello')

    await settle()

    expect(chat.messages.value).toHaveLength(2)

    chat.stopGeneration()

    // 停止后立刻再次发送:清理旧占位不得误删新流程的消息,
    // 也不得依赖固定下标。
    await chat.sendMessage('two')

    expect(chat.messages.value.map((m) => m.content)).toEqual([
      'hello',
      'two',
      'y',
    ])
    expect(chat.error.value).toBe('')
  })
})

// ==================================================
// M0-R2 缺口 2:已取消发送的迟到创建结果
// ==================================================

describe('useChat cancelled send late create', () => {
  it('keeps a failed create of a cancelled send out of the current error', async () => {
    const gateCreate = deferred()

    const createCalls = []
    const streamCalls = []

    const routes = {
      'POST /api/sessions': (url, init) => {
        createCalls.push(init)

        // 建会话最终失败(例如 HTTP 503)。
        return gateCreate.promise.then(() => {
          throw new Error('HTTP 503')
        })
      },
      'POST /api/chat/stream': (url, init) => {
        streamCalls.push(init)

        return Promise.resolve(
          sseResponse([tokenEvent('x'), doneEvent('created')]),
        )
      },
      'GET /api/sessions': () => Promise.resolve(jsonResponse([])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const finished = chat.sendMessage('hello')

    await settle()

    expect(chat.generating.value).toBe(true)

    chat.stopGeneration()

    // 已取消的流程:创建请求返回失败,也不得显示为当前错误。
    gateCreate.resolve()

    await finished
    await settle()

    expect(chat.error.value).toBe('')
    expect(chat.messages.value).toEqual([])
    expect(chat.activeConversationId.value).toBe('')
    expect(chat.sessions.value).toEqual([])
    expect(streamCalls).toHaveLength(0)
    expect(chat.generating.value).toBe(false)
  })

  it('a late create success after stop must not activate, clear, or take over', async () => {
    const gateCreate = deferred()

    let createCount = 0

    const routes = {
      'POST /api/sessions': () => {
        createCount += 1

        if (createCount === 1) {
          return gateCreate.promise.then(() =>
            jsonResponse(sessionFixture('late-created'), 201),
          )
        }

        return Promise.resolve(
          jsonResponse(sessionFixture('new-created'), 201),
        )
      },
      'GET /api/sessions/a/messages': () =>
        Promise.resolve(jsonResponse([historyMessage('A-history')])),
      'GET /api/sessions': () => Promise.resolve(jsonResponse([])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const finished = chat.sendMessage('hello')

    await settle()

    chat.stopGeneration()

    // 停止后、创建请求仍在途:新操作按统一互斥规则被拒,状态保持。
    await chat.selectSession(sessionFixture('a'))

    expect(chat.activeConversationId.value).toBe('')
    expect(chat.messages.value).toEqual([])

    gateCreate.resolve()

    await finished
    await settle()

    // 迟到成功:不得激活会话、不得清空当前消息、不得开始生成,
    // 也不得回填列表(服务端会话保留,下次刷新带回)。
    expect(chat.activeConversationId.value).toBe('')
    expect(chat.messages.value).toEqual([])
    expect(chat.sessions.value).toEqual([])
    expect(chat.error.value).toBe('')

    // 在途操作结束后,显式创建成为新的归属,正常接管视图。
    await chat.createSession()

    expect(chat.activeConversationId.value).toBe('new-created')
    expect(chat.messages.value).toEqual([])
    expect(chat.error.value).toBe('')
  })
})

// ==================================================
// M0-R2 缺口 3:创建成功必须接管历史视图归属
// ==================================================

describe('useChat create takes over history view', () => {
  it('a successful create wins view ownership over a pending history request', async () => {
    const gateA = deferred()

    const routes = {
      'GET /api/sessions/a/messages': async () => {
        await gateA.promise

        return jsonResponse([historyMessage('A-history')])
      },
      'POST /api/sessions': () =>
        Promise.resolve(jsonResponse(sessionFixture('b'), 201)),
      'GET /api/sessions': () => Promise.resolve(jsonResponse([])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    // 选择 A:历史请求挂起。
    const pendingA = chat.selectSession(sessionFixture('a'))

    await settle()

    // 期间创建成功:创建接管视图归属。
    await chat.createSession()

    expect(chat.activeConversationId.value).toBe('b')

    // A 的历史稍后返回:过期回包不得再写视图。
    gateA.resolve()

    await pendingA
    await settle()

    // 最终状态不得出现 active=b 而 messages=A(项目经理复现的串位)。
    expect(chat.activeConversationId.value).toBe('b')
    expect(chat.messages.value).toEqual([])
    expect(chat.error.value).toBe('')
  })

  it('a stale history failure after create must not surface as current error', async () => {
    const gateA = deferred()

    const routes = {
      'GET /api/sessions/a/messages': async () => {
        await gateA.promise

        throw new Error('HTTP 500')
      },
      'POST /api/sessions': () =>
        Promise.resolve(jsonResponse(sessionFixture('b'), 201)),
      'GET /api/sessions': () => Promise.resolve(jsonResponse([])),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const pendingA = chat.selectSession(sessionFixture('a'))

    await settle()

    await chat.createSession()

    // 过期历史失败迟到:不得显示为当前错误。
    gateA.resolve()

    await pendingA
    await settle()

    expect(chat.error.value).toBe('')
    expect(chat.activeConversationId.value).toBe('b')
    expect(chat.messages.value).toEqual([])
  })
})

// ==================================================
// M0-R2 缺口 4:后台列表刷新不得阻塞交互
// ==================================================

describe('useChat background refresh non-blocking', () => {
  it('lets the next send proceed while a post-reply list refresh is pending', async () => {
    const gateList = deferred()

    let streamCount = 0
    let listCount = 0

    const listV1 = [sessionFixture('a')]
    const listV2 = [sessionFixture('a'), sessionFixture('b')]

    const routes = {
      'GET /api/sessions/a/messages': () =>
        Promise.resolve(jsonResponse([])),
      'POST /api/chat/stream': () => {
        streamCount += 1

        const token = streamCount === 1 ? 'x' : 'y'

        return Promise.resolve(
          sseResponse([tokenEvent(token), doneEvent('a')]),
        )
      },
      'GET /api/sessions': () => {
        listCount += 1

        // 第一轮回复后的后台刷新挂起;第二轮的刷新立即返回。
        if (listCount === 1) {
          return gateList.promise.then(() => jsonResponse(listV1))
        }

        return Promise.resolve(jsonResponse(listV2))
      },
    }

    installRoutedFetch(routes)

    const chat = useChat()

    await chat.selectSession(sessionFixture('a'))

    const firstFinished = chat.sendMessage('one')

    await waitFor(() => {
      expect(chat.messages.value[1].content).toBe('x')
    })

    // 流已完成:后台刷新不得占用任何互斥状态。
    expect(chat.generating.value).toBe(false)
    expect(chat.sessionsBusy.value).toBe(false)

    // 后台刷新在途时允许下一轮发送与正常会话操作。
    const secondFinished = chat.sendMessage('two')

    await waitFor(() => {
      expect(chat.messages.value.map((m) => m.content)).toEqual([
        'one',
        'x',
        'two',
        'y',
      ])
    })

    await secondFinished

    // 旧刷新最后到达:过期结果不得覆盖更新的列表状态。
    gateList.resolve()

    await firstFinished
    await settle()

    expect(chat.sessions.value).toEqual(listV2)
    expect(chat.error.value).toBe('')
  })

  it('a failed stale background refresh leaves current state and error untouched', async () => {
    const gateList = deferred()

    let listCount = 0

    const routes = {
      'GET /api/sessions/a/messages': () =>
        Promise.resolve(jsonResponse([])),
      'POST /api/chat/stream': () =>
        Promise.resolve(
          sseResponse([tokenEvent('x'), doneEvent('a')]),
        ),
      'POST /api/sessions': () =>
        Promise.resolve(jsonResponse(sessionFixture('b'), 201)),
      'GET /api/sessions': () => {
        listCount += 1

        if (listCount === 1) {
          return gateList.promise.then(() => {
            throw new Error('HTTP 500')
          })
        }

        return Promise.resolve(jsonResponse([sessionFixture('b')]))
      },
    }

    installRoutedFetch(routes)

    const chat = useChat()

    await chat.selectSession(sessionFixture('a'))

    const firstFinished = chat.sendMessage('hello')

    await waitFor(() => {
      expect(chat.messages.value[1].content).toBe('x')
    })

    // 后台刷新失败期间,新的正常操作照常进行。
    await chat.createSession()

    expect(chat.activeConversationId.value).toBe('b')

    gateList.resolve()

    await firstFinished
    await settle()

    // 过期刷新的失败不得污染当前错误,也不得覆盖更新的操作结果。
    expect(chat.error.value).toBe('')
    expect(chat.sessions.value).toEqual([sessionFixture('b')])
    expect(chat.activeConversationId.value).toBe('b')
  })
})

// ==================================================
// M0-R3 R14:列表刷新与创建回包交错产生重复会话
// ==================================================

describe('useChat R14 list and create interleave', () => {
  it('keeps sessions unique when a pending refresh already includes the created session', async () => {
    const gateList = deferred()
    const gateCreate = deferred()

    const created = sessionFixture('b')

    const routes = {
      'GET /api/sessions': async () => {
        await gateList.promise

        // 服务端可以先完成创建再延迟返回 POST:列表先带回 B。
        return jsonResponse([created, sessionFixture('a')])
      },
      'POST /api/sessions': async () => {
        await gateCreate.promise

        return jsonResponse(created, 201)
      },
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const refreshing = chat.loadSessions()

    await settle()

    const creating = chat.createSession()

    await settle()

    // GET 先返回:列表已含 B。
    gateList.resolve()

    await refreshing
    await settle()

    expect(chat.sessions.value.map((s) => s.conversation_id)).toEqual([
      'b',
      'a',
    ])

    // POST 后返回:不得出现 [B, B];排序与激活行为保留。
    gateCreate.resolve()

    await creating
    await settle()

    expect(chat.sessions.value.map((s) => s.conversation_id)).toEqual([
      'b',
      'a',
    ])
    expect(chat.activeConversationId.value).toBe('b')
    expect(chat.messages.value).toEqual([])
    expect(chat.error.value).toBe('')
  })

  it('a refresh that starts before a create must not resurrect stale ordering over the created session', async () => {
    const gateList = deferred()

    const created = sessionFixture('b')

    const routes = {
      'GET /api/sessions': async () => {
        await gateList.promise

        // 刷新启动时创建尚未生效:回包虽含 B,也属过期状态。
        return jsonResponse([sessionFixture('a')])
      },
      'POST /api/sessions': () => Promise.resolve(jsonResponse(created, 201)),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const refreshing = chat.loadSessions()

    await settle()

    await chat.createSession()

    gateList.resolve()

    await refreshing
    await settle()

    // 创建生效后,更早启动的刷新结果作废:列表保持创建后的状态。
    expect(chat.sessions.value.map((s) => s.conversation_id)).toEqual(['b'])
    expect(chat.activeConversationId.value).toBe('b')
    expect(chat.error.value).toBe('')
  })
})

// ==================================================
// M0-R3 R15:删除其他会话取消当前历史加载
// ==================================================

describe('useChat R15 delete other session keeps current history', () => {
  it('deleting another session must not invalidate the active session history request', async () => {
    const gateHistory = deferred()

    const routes = {
      'GET /api/sessions/a/messages': async () => {
        await gateHistory.promise

        return jsonResponse([historyMessage('A-real')])
      },
      'DELETE /api/sessions/b': () =>
        Promise.resolve(new Response(null, { status: 204 })),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const pendingHistory = chat.selectSession(sessionFixture('a'))

    await settle()

    // 历史在途时删除的是其他会话 B:不得作废 A 的请求。
    await chat.deleteSession(sessionFixture('b'))

    gateHistory.resolve()

    await pendingHistory
    await settle()

    expect(chat.activeConversationId.value).toBe('a')
    expect(chat.messages.value.map((m) => m.content)).toEqual(['A-real'])
    expect(chat.historyLoading.value).toBe(false)
    expect(chat.error.value).toBe('')
  })

  it('deleting the active session still rejects its late history', async () => {
    // 与缺陷 3 同族:按影响范围失效 —— 只有删除当前会话才作废其历史。
    const gateHistory = deferred()

    const routes = {
      'GET /api/sessions/a/messages': async () => {
        await gateHistory.promise

        return jsonResponse([historyMessage('A-ghost')])
      },
      'DELETE /api/sessions/a': () =>
        Promise.resolve(new Response(null, { status: 204 })),
      'DELETE /api/sessions/b': () =>
        Promise.resolve(new Response(null, { status: 204 })),
    }

    installRoutedFetch(routes)

    const chat = useChat()

    const pendingHistory = chat.selectSession(sessionFixture('a'))

    await settle()

    await chat.deleteSession(sessionFixture('b'))
    await chat.deleteSession(sessionFixture('a'))

    gateHistory.resolve()

    await pendingHistory
    await settle()

    expect(chat.activeConversationId.value).toBe('')
    expect(chat.messages.value).toEqual([])
    expect(chat.error.value).toBe('')
  })
})

// ==================================================
// M0-R3 R16:旧列表刷新错误污染新一轮生成
// ==================================================

describe('useChat R16 stale refresh error vs new generation', () => {
  it('a failed stale list refresh is neither shown during round 2 nor left after it succeeds', async () => {
    const gateListOld = deferred()
    const gateStream2 = deferred()

    let listCount = 0
    let streamCount = 0

    const routes = {
      'GET /api/sessions/a/messages': () =>
        Promise.resolve(jsonResponse([])),
      'POST /api/chat/stream': () => {
        streamCount += 1

        if (streamCount === 1) {
          return Promise.resolve(
            sseResponse([tokenEvent('x'), doneEvent('a')]),
          )
        }

        return gateStream2.promise.then(() =>
          sseResponse([tokenEvent('y'), doneEvent('a')]),
        )
      },
      'GET /api/sessions': () => {
        listCount += 1

        if (listCount === 1) {
          // 第一轮回复后的后台刷新:失败迟到,落在第二轮生成期间。
          return gateListOld.promise.then(() => {
            throw new Error('HTTP 500')
          })
        }

        return Promise.resolve(jsonResponse([sessionFixture('a')]))
      },
    }

    installRoutedFetch(routes)

    const chat = useChat()

    await chat.selectSession(sessionFixture('a'))

    const firstFinished = chat.sendMessage('one')

    await waitFor(() => {
      expect(chat.generating.value).toBe(false)
    })

    // 第一轮后台刷新仍挂起时开启第二轮。
    const secondFinished = chat.sendMessage('two')

    await waitFor(() => {
      expect(streamCount).toBe(2)
    })

    // 旧列表请求此刻失败:不得显示为第二轮的错误。
    gateListOld.resolve()

    await settle()

    // 断言一:第二轮仍在生成 —— 状态与错误都不受旧刷新影响。
    expect(chat.generating.value).toBe(true)
    expect(chat.error.value).toBe('')

    gateStream2.resolve()

    await secondFinished
    await firstFinished
    await settle()

    // 断言二:第二轮成功收尾 —— 无残留错误,新列表生效,消息完整。
    expect(chat.generating.value).toBe(false)
    expect(chat.error.value).toBe('')
    expect(chat.sessions.value.map((s) => s.conversation_id)).toEqual(['a'])
    expect(chat.messages.value.map((m) => m.content)).toEqual([
      'one',
      'x',
      'two',
      'y',
    ])
  })
})
