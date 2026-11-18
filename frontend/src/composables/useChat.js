import { computed, ref } from 'vue'
import { api } from '../api/client'
import { toApiAttachments, toDisplayAttachments } from '../utils/attachments'

// 聊天业务状态统一管理:会话、消息、流式生成、取消。
// 组件只消费这里暴露的 ref 与方法,不直接触碰 API 层。
//
// 并发约束(M0-R2 行为约定):
// - historyToken:视图“归属”序号。切换会话/创建成功/删除/发送都会接管视图
//   并递增;历史回包(成功或失败)只有序号仍是最新时才允许写视图状态。
// - sendSeq:发送流程编号。stopGeneration 通过递增使当前流程立即失效;
//   旧流程的 token、自动创建结果(成功或失败)、错误与 finally 一律被拒绝,
//   不得写新流程状态。停止时由 stopGeneration 同步清除该流程的空占位气泡
//   (按对象身份核对下标,不依赖固定索引;已生成片段保留)。
// - 会话列表读取(后台刷新)是非阻塞的:不计入任何互斥状态;
//   listToken 保证“最后启动的刷新获胜”,mutSeq 记录已生效的创建/删除变更,
//   刷新启动后发生过变更,则该刷新的结果与失败都过期,不得覆盖更新状态。
//   新一轮发送也会递增 listToken:更早启动的刷新的结果与失败都归属上一轮,
//   不得显示为新生成的失败(R16)。
// - 会话列表按 conversation_id 唯一:后台刷新可能先带回服务端已创建的会话,
//   创建生效时必须先移除同 id 旧条目再置顶,不得出现重复(R14)。
// - 删除按实际影响范围作废请求:删除当前查看的会话才作废其在途历史
//   (迟到回包不得让内容重新出现);删除其他会话不得取消当前会话的
//   在途历史加载(R15)。
// - 互斥规则(业务函数与 UI 一致):生成中禁止 发送/切换/创建/删除;
//   创建或删除在途(sessionsBusy)禁止 发送/切换/创建/删除;
//   历史加载(historyLoading)与后台刷新不阻塞任何操作(由令牌保证正确性)。
//   被拒绝的冲突调用保持现状;被允许完成的以令牌取得唯一视图归属。
// - 状态区分:generating(生成中)/ historyLoading(历史加载)/
//   sessionsBusy(创建、删除等用户级写操作在途);loading 为三者并集。
//   自动建会话在途时停止:generating 立即恢复,但 sessionsBusy 仍为 true,
//   UI 必须继续锁定输入直到该请求结束。
export function useChat() {
  const sessions = ref([])
  const activeConversationId = ref('')
  const messages = ref([])

  const generating = ref(false)
  const historyLoading = ref(false)
  const sessionsInFlight = ref(0)

  const sessionsBusy = computed(() => sessionsInFlight.value > 0)

  const loading = computed(
    () => generating.value || historyLoading.value || sessionsBusy.value,
  )

  const error = ref('')

  let activeSend = null
  let historyToken = 0
  let sendSeq = 0
  let listToken = 0
  let mutSeq = 0

  // 后台列表刷新:非阻塞;结果与失败都要同时通过
  // “最新的刷新令牌”与“刷新期间无创建/删除变更”两道校验。
  async function loadSessions() {
    const myToken = ++listToken
    const mutAtStart = mutSeq

    try {
      const list = await api.listSessions()

      if (myToken !== listToken || mutAtStart !== mutSeq) return

      sessions.value = list
    } catch (err) {
      if (myToken === listToken && mutAtStart === mutSeq) {
        error.value = err.message
      }
    }
  }

  // 纯网络请求:不写任何状态。调用方(用户显式创建 / 发送时自动创建)
  // 根据各自的流程归属决定成功与失败如何落地。计入互斥计数器。
  async function requestCreateSession() {
    sessionsInFlight.value += 1

    try {
      const session = await api.createSession()

      return { status: 'ok', session }
    } catch (err) {
      return { status: 'error', error: err }
    } finally {
      sessionsInFlight.value -= 1
    }
  }

  // 创建成功并归属当前流程时:接管视图(作废在途历史请求与刷新),
  // 新会话置顶并激活,清空当前消息。
  // 列表按 conversation_id 唯一:后台刷新可能先带回服务端已创建的会话
  // (服务端可先完成创建、再延迟返回 POST),此时必须先移除同 id 旧条目
  // 再置顶,避免 [B, B] 重复,同时保留排序与激活语义。
  function applyCreatedSession(session) {
    historyToken += 1
    historyLoading.value = false
    mutSeq += 1

    sessions.value = [
      session,
      ...sessions.value.filter(
        (item) => item.conversation_id !== session.conversation_id,
      ),
    ]

    activeConversationId.value = session.conversation_id
    messages.value = []
    error.value = ''
  }

  async function createSession() {
    if (generating.value || sessionsBusy.value) return null

    const result = await requestCreateSession()

    if (result.status !== 'ok') {
      error.value = result.error.message
      return null
    }

    applyCreatedSession(result.session)

    return result.session
  }

  async function selectSession(session) {
    // 与 UI(Sidebar disabled)一致:生成中或创建/删除在途时拒绝,状态保持。
    if (generating.value || sessionsBusy.value) return

    const myHistory = ++historyToken

    activeConversationId.value = session.conversation_id
    messages.value = []
    error.value = ''
    historyLoading.value = true

    try {
      const history = await api.getMessages(session.conversation_id)

      // 过期成功:会话已被更新的视图归属取代,直接丢弃。
      if (myHistory !== historyToken) return

      messages.value = history
    } catch (err) {
      // 过期失败:同样丢弃,不得污染当前错误状态。
      if (myHistory !== historyToken) return

      error.value = err.message
    } finally {
      if (myHistory === historyToken) {
        historyLoading.value = false
      }
    }
  }

  async function deleteSession(session) {
    // 与 create/send 统一互斥:生成中或创建/删除在途时拒绝。
    if (generating.value || sessionsBusy.value) return

    const isActive = activeConversationId.value === session.conversation_id

    sessionsInFlight.value += 1

    // 按实际影响范围作废请求:只有删除“正在查看的会话”才作废在途历史,
    // 其迟到回包不得让已删内容重新出现;删除其他会话不得取消当前会话的
    // 在途历史加载。
    if (isActive) {
      historyToken += 1
      historyLoading.value = false
    }

    try {
      await api.deleteSession(session.conversation_id)

      mutSeq += 1

      sessions.value = sessions.value.filter(
        (item) => item.conversation_id !== session.conversation_id,
      )

      if (isActive) {
        activeConversationId.value = ''
        messages.value = []
      }
    } catch (err) {
      // 写操作按互斥保证同一时刻至多一个在途,失败属于当前状态。
      error.value = err.message
    } finally {
      sessionsInFlight.value -= 1
    }
  }

  // 停止时清理该流程的空占位 assistant 气泡:
  // 按对象身份核对下标(数组可能已变化),只删空内容;已生成片段保留。
  function removeEmptyPlaceholder(send) {
    const { assistantIndex, placeholder } = send

    if (!placeholder || assistantIndex < 0) return

    if (messages.value[assistantIndex] !== placeholder) return

    if (placeholder.content === '') {
      messages.value.splice(assistantIndex, 1)
    }
  }

  // 附件(prepared 形态:{kind,name,mime,size,data?}):
  // 消息流里存展示形态(无 data,图片带即时缩略图 URL),
  // API body 存契约形态(图片含 data,语音/文件仅元数据)。
  async function sendMessage(text, attachments = []) {
    const content = (text || '').trim()
    const prepared = Array.isArray(attachments) ? attachments : []

    if (!content && prepared.length === 0) return

    // 生成中不允许重复发送;创建/删除在途时同样互斥,
    // 避免向已删除会话发送、或重复自动建会话。
    // 后台列表刷新与历史加载不阻塞发送。
    if (generating.value || sessionsBusy.value) return

    const mySend = ++sendSeq

    // 发送接管视图:丢弃所有在途历史回包。
    historyToken += 1
    historyLoading.value = false

    // 新一轮发送同时作废在此之前启动的后台列表刷新:
    // 其结果与失败都归属上一轮,不得显示为新生成的失败。
    listToken += 1

    const controller = new AbortController()

    activeSend = {
      sendId: mySend,
      controller,
      assistantIndex: -1,
      placeholder: null,
    }

    generating.value = true
    error.value = ''

    try {
      if (!activeConversationId.value) {
        const result = await requestCreateSession()

        // 创建在途时已被停止/被新发送取代:结果无论成败一律丢弃。
        // 服务端已创建的会话保留(不自动删除),下次列表刷新自然带回。
        if (mySend !== sendSeq) return

        if (result.status !== 'ok') {
          error.value = result.error.message
          return
        }

        applyCreatedSession(result.session)
      }

      const conversationId = activeConversationId.value

      messages.value.push({
        role: 'user',
        content,
        attachments: toDisplayAttachments(prepared),
      })
      messages.value.push({ role: 'assistant', content: '' })

      const assistantIndex = messages.value.length - 1

      if (activeSend?.sendId === mySend) {
        // 记录占位(响应式代理)供 stopGeneration 按身份清理。
        activeSend.assistantIndex = assistantIndex
        activeSend.placeholder = messages.value[assistantIndex]
      }

      await api.streamChat(conversationId, content, {
        attachments: toApiAttachments(prepared),
        signal: controller.signal,
        onToken(token) {
          // 失效流程(已停止或被新发送取代)的 token 一律丢弃。
          if (mySend !== sendSeq) return

          const target = messages.value[assistantIndex]

          if (!target) return

          // 铁律:必须通过 messages.value 索引取到的响应式代理追加;
          // 修改 push 前持有的原始对象不会触发视图更新。
          target.content += token
        },
      })

      if (mySend !== sendSeq) return

      // 生成到此结束;列表刷新是后台操作,不再占用任何互斥状态,
      // 停止按钮与生成状态在同一时刻交还界面。
      generating.value = false

      await loadSessions()
    } catch (err) {
      // 旧流程的异常不得改写新流程的错误状态。
      // 用户主动停止由 stopGeneration 同步处理,不会到达这里。
      if (mySend !== sendSeq) return

      error.value = err.message
    } finally {
      // 只有仍属当前流程才允许清理状态;被停止/取代的流程直接跳过,
      // 避免旧 finally 破坏新流程。
      if (mySend === sendSeq) {
        if (activeSend?.sendId === mySend) {
          activeSend = null
        }

        generating.value = false
      }
    }
  }

  function stopGeneration() {
    // 仅生成中有效;流结束后的列表刷新是后台操作,不适用停止。
    if (!generating.value) return

    const send = activeSend

    // 使当前流程立即失效:其 token、自动创建结果、异常与 finally
    // 都不再写状态。
    sendSeq += 1

    send?.controller.abort()
    activeSend = null
    generating.value = false

    // 同步清理该流程仍为空的占位气泡(首 token 前停止的情况)。
    if (send) {
      removeEmptyPlaceholder(send)
    }
  }

  return {
    sessions,
    activeConversationId,
    messages,
    generating,
    historyLoading,
    sessionsBusy,
    loading,
    error,
    loadSessions,
    createSession,
    selectSession,
    deleteSession,
    sendMessage,
    stopGeneration,
  }
}
