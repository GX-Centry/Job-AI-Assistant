// SSE 解析状态机:把任意切分的文本块还原为完整事件。
// UTF-8 多字节字符的字节级切分由调用方 TextDecoder(stream: true) 处理。
// 事件名与数据结构遵循后端约定(token / done / error),见 job_agent/api/app.py。

export function createSSEParser(onEvent) {
  let buffer = ''
  let eventName = 'message'
  let dataLines = []

  function dispatchEvent() {
    const name = eventName
    const raw = dataLines.join('\n')

    eventName = 'message'
    dataLines = []

    if (raw === '') {
      return
    }

    let data

    try {
      data = JSON.parse(raw)
    } catch {
      throw new Error(`Invalid SSE data for event "${name}": ${raw}`)
    }

    onEvent({ event: name, data })
  }

  function processLine(rawLine) {
    let line = rawLine

    // 兼容 CRLF:CRLF 中 \r 位于行尾。\n 处切分后残留的 \r
    if (line.endsWith('\r')) {
      line = line.slice(0, -1)
    }

    if (line === '') {
      dispatchEvent()
    } else if (line.startsWith(':')) {
      // SSE 注释 / 心跳行,忽略。
    } else if (line.startsWith('event:')) {
      eventName = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      let value = line.slice('data:'.length)

      // SSE 规范:data: 后的单个前导空格属于协议,不属于内容。
      if (value.startsWith(' ')) {
        value = value.slice(1)
      }

      dataLines.push(value)
    }
    // 其他字段(id、retry)当前协议未使用,忽略。
  }

  return {
    // 输入一段已解码文本,内部按行缓冲,遇空行派发完整事件。
    feed(chunk) {
      buffer += chunk

      let newlineIndex

      while ((newlineIndex = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, newlineIndex)

        buffer = buffer.slice(newlineIndex + 1)
        processLine(line)
      }
    },

    // 流结束后调用:处理无结尾换行的尾部数据与未派发的残留事件。
    flush() {
      if (buffer !== '') {
        const line = buffer

        buffer = ''
        processLine(line)
      }

      if (dataLines.length > 0) {
        dispatchEvent()
      }
    },
  }
}
