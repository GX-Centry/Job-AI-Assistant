// @vitest-environment jsdom
// 组件层验收:在真实 DOM 中,流式 token 必须在 done 事件到达之前就已渲染。
// 只读取 ref 值无法防止“绕过响应式代理”的回归 —— 原始对象被修改时
// 不会触发重渲染,DOM 断言在这里必然失败。
//
// jsdom 不实现 fetch/Response;这里用 Node 内置 ReadableStream 与
// 鸭子类型响应对象(只实现 client.js 实际读取的 ok/body/status)模拟网络。

import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReadableStream } from 'node:stream/web'
import { mount } from '@vue/test-utils'

vi.stubEnv('VITE_API_KEY', 'test-key')

// 必须在 stubEnv 之后动态导入,client.js 在模块加载时捕获 VITE_API_KEY;
// 静态 import 会被提升到 stubEnv 之前执行。
const { default: App } = await import('./App.vue')

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

function jsonMockResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return body
    },
    async text() {
      return JSON.stringify(body)
    },
  }
}

function streamMockResponse(chunks) {
  let index = 0

  return {
    ok: true,
    status: 200,
    body: new ReadableStream({
      async pull(controller) {
        if (index < chunks.length) {
          controller.enqueue(await chunks[index++])
        } else {
          controller.close()
        }
      },
    }),
  }
}

function deferred() {
  let resolve

  const promise = new Promise((res) => {
    resolve = res
  })

  return { promise, resolve }
}

async function settle(times = 4) {
  for (let i = 0; i < times; i += 1) {
    await new Promise((resolve) => {
      setTimeout(resolve, 0)
    })
  }
}

async function waitFor(assertion, attempts = 500) {
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

function sessionCreatedBody() {
  return {
    conversation_id: 'created',
    title: 'New conversation',
    created_at: 'now',
    updated_at: 'now',
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('App streaming render (jsdom)', () => {
  it('renders streamed text into the DOM before the done event arrives', async () => {
    const gateSecondToken = deferred()
    const gateThirdToken = deferred()

    const chunks = [
      tokenEvent('你'),
      gateSecondToken.promise.then(() => tokenEvent('好')),
      gateThirdToken.promise.then(() => tokenEvent('!')),
      doneEvent('created'),
    ]

    vi.stubGlobal(
      'fetch',
      vi.fn(async (url, init = {}) => {
        const path = new URL(url, 'http://localhost').pathname
        const method = (init.method || 'GET').toUpperCase()

        if (method === 'GET' && path === '/api/sessions') {
          return jsonMockResponse([])
        }

        if (method === 'POST' && path === '/api/sessions') {
          return jsonMockResponse(sessionCreatedBody(), 201)
        }

        if (method === 'POST' && path === '/api/chat/stream') {
          return streamMockResponse(chunks)
        }

        throw new Error(`Unexpected fetch: ${method} ${path}`)
      }),
    )

    const wrapper = mount(App)

    // 等待挂载时的初始会话列表加载完成(在途时禁止发送是既定互斥行为)。
    await settle()

    // 空闲状态不得声称任何未经证实的连接状态。
    expect(wrapper.get('.connection-status').text()).toContain('Ready')

    await wrapper.find('textarea').setValue('hello')
    await wrapper.find('form.composer').trigger('submit')

    // 第一个 token 到达后:DOM 中必须立刻出现该文本,
    // 而后两个 token 被门控、done 尚未发送,流仍在工作。
    await waitFor(() => {
      expect(wrapper.find('.message-list').text()).toContain('你')
    })

    const partialText = wrapper.find('.message-list').text()

    expect(partialText).not.toContain('好')
    expect(wrapper.find('.connection-status').text()).toContain('Generating')

    // 生成中:停止按钮必须出现在组件层。
    expect(wrapper.find('.stop-button').exists()).toBe(true)

    gateSecondToken.resolve()
    gateThirdToken.resolve()

    await waitFor(() => {
      expect(wrapper.find('.message-list').text()).toContain('你好!')
    })

    // done 之后:回到空闲状态,停止按钮被发送按钮替换。
    await settle()

    expect(wrapper.find('.connection-status').text()).toContain('Ready')
    expect(wrapper.find('.stop-button').exists()).toBe(false)
    expect(wrapper.find('.send-button').exists()).toBe(true)

    // 用户消息也在 DOM 中。
    expect(wrapper.find('.message-list').text()).toContain('hello')

    wrapper.unmount()
  })

  it('keeps partial text in the DOM when the user clicks stop', async () => {
    const gateStream = deferred()

    let streamRequested = false

    vi.stubGlobal(
      'fetch',
      vi.fn(async (url, init = {}) => {
        const path = new URL(url, 'http://localhost').pathname
        const method = (init.method || 'GET').toUpperCase()

        if (method === 'GET' && path === '/api/sessions') {
          return jsonMockResponse([])
        }

        if (method === 'POST' && path === '/api/sessions') {
          return jsonMockResponse(sessionCreatedBody(), 201)
        }

        if (method === 'POST' && path === '/api/chat/stream') {
          streamRequested = true

          let index = 0

          const stages = [
            () => {
              const signal = init.signal

              if (signal?.aborted) {
                throw new DOMException(
                  'The operation was aborted.',
                  'AbortError',
                )
              }

              return tokenEvent('部分')
            },
            async () => {
              await gateStream.promise

              const signal = init.signal

              if (signal?.aborted) {
                throw new DOMException(
                  'The operation was aborted.',
                  'AbortError',
                )
              }

              return doneEvent('created')
            },
          ]

          return {
            ok: true,
            status: 200,
            body: new ReadableStream({
              async pull(controller) {
                if (index < stages.length) {
                  controller.enqueue(await stages[index++]())
                } else {
                  controller.close()
                }
              },
            }),
          }
        }

        throw new Error(`Unexpected fetch: ${method} ${path}`)
      }),
    )

    const wrapper = mount(App)

    // 等待挂载时的初始会话列表加载完成(在途时禁止发送是既定互斥行为)。
    await settle()

    await wrapper.find('textarea').setValue('hello')
    await wrapper.find('form.composer').trigger('submit')

    await waitFor(() => {
      expect(wrapper.find('.message-list').text()).toContain('部分')
    })

    expect(streamRequested).toBe(true)

    await wrapper.get('.stop-button').trigger('click')

    gateStream.resolve()

    await settle(8)

    // 保留已生成片段,停止不算错误,回到可操作状态。
    expect(wrapper.find('.message-list').text()).toContain('部分')
    expect(wrapper.find('.error-message').exists()).toBe(false)
    expect(wrapper.find('.send-button').exists()).toBe(true)
    expect(wrapper.find('.connection-status').text()).toContain('Ready')

    wrapper.unmount()
  })

  it('removes the empty assistant bubble when stopped before the first token', async () => {
    const gateFirstToken = deferred()

    vi.stubGlobal(
      'fetch',
      vi.fn(async (url, init = {}) => {
        const path = new URL(url, 'http://localhost').pathname
        const method = (init.method || 'GET').toUpperCase()

        if (method === 'GET' && path === '/api/sessions') {
          return jsonMockResponse([])
        }

        if (method === 'POST' && path === '/api/sessions') {
          return jsonMockResponse(sessionCreatedBody(), 201)
        }

        if (method === 'POST' && path === '/api/chat/stream') {
          let index = 0

          const stages = [
            async () => {
              // 首 token 挂起;停止后 signal 已 abort,流按取消失败。
              await gateFirstToken.promise

              const signal = init.signal

              if (signal?.aborted) {
                throw new DOMException(
                  'The operation was aborted.',
                  'AbortError',
                )
              }

              return tokenEvent('迟到')
            },
            () => doneEvent('created'),
          ]

          return {
            ok: true,
            status: 200,
            body: new ReadableStream({
              async pull(controller) {
                if (index < stages.length) {
                  controller.enqueue(await stages[index++]())
                } else {
                  controller.close()
                }
              },
            }),
          }
        }

        throw new Error(`Unexpected fetch: ${method} ${path}`)
      }),
    )

    const wrapper = mount(App)

    await settle()

    await wrapper.find('textarea').setValue('hello')
    await wrapper.find('form.composer').trigger('submit')

    // 发送后、首 token 前:用户消息 + 空 assistant 占位(Thinking...)。
    await waitFor(() => {
      expect(wrapper.findAll('.message-row')).toHaveLength(2)
    })

    expect(wrapper.find('.message-list').text()).toContain('Thinking')
    expect(wrapper.find('.stop-button').exists()).toBe(true)

    await wrapper.get('.stop-button').trigger('click')

    await settle()

    // 停止必须清掉空占位气泡:DOM 中只剩用户消息,无 Thinking、无错误。
    const rows = wrapper.findAll('.message-row')

    expect(rows).toHaveLength(1)
    expect(rows[0].classes()).toContain('user')
    expect(wrapper.find('.message-list').text()).not.toContain('Thinking')
    expect(wrapper.find('.error-message').exists()).toBe(false)
    expect(wrapper.find('.send-button').exists()).toBe(true)

    gateFirstToken.resolve()

    await settle(8)

    // 迟到的流失败不得让占位气泡或错误重新出现。
    expect(wrapper.findAll('.message-row')).toHaveLength(1)
    expect(wrapper.find('.error-message').exists()).toBe(false)

    wrapper.unmount()
  })

  it('keeps the composer locked when stopped during automatic session creation', async () => {
    const gateCreate = deferred()

    vi.stubGlobal(
      'fetch',
      vi.fn(async (url, init = {}) => {
        const path = new URL(url, 'http://localhost').pathname
        const method = (init.method || 'GET').toUpperCase()

        if (method === 'GET' && path === '/api/sessions') {
          return jsonMockResponse([])
        }

        if (method === 'POST' && path === '/api/sessions') {
          await gateCreate.promise

          return jsonMockResponse(sessionCreatedBody(), 201)
        }

        if (method === 'POST' && path === '/api/chat/stream') {
          throw new Error('stream must not start after stop')
        }

        throw new Error(`Unexpected fetch: ${method} ${path}`)
      }),
    )

    const wrapper = mount(App)

    await settle()

    await wrapper.find('textarea').setValue('hello')
    await wrapper.find('form.composer').trigger('submit')

    await settle()

    expect(wrapper.find('.connection-status').text()).toContain('Generating')

    await wrapper.get('.stop-button').trigger('click')

    await settle()

    // 生成状态已恢复,但建会话请求仍在途:发送按钮必须保持锁定,
    // generating=false 单独不代表全部交互恢复。
    expect(wrapper.find('.connection-status').text()).toContain('Ready')
    expect(wrapper.find('.stop-button').exists()).toBe(false)
    expect(wrapper.find('.send-button').exists()).toBe(true)
    expect(wrapper.find('.send-button').attributes('disabled')).toBeDefined()

    gateCreate.resolve()

    await settle(8)

    // 请求结束后交互恢复;迟到结果不得激活会话或插入消息。
    await wrapper.find('textarea').setValue('again')

    expect(wrapper.find('.send-button').attributes('disabled')).toBeUndefined()
    expect(wrapper.findAll('.message-row')).toHaveLength(0)
    expect(wrapper.find('.error-message').exists()).toBe(false)

    wrapper.unmount()
  })
})
