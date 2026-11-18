// 微信式长按录音封装:MediaRecorder -> Blob。
// M1-c(2026-11-18 用户拍板"录音发送转写"):Blob 字节由 ChatInput 转
// base64 随消息发送,服务端持久化后由 transcribe_audio 工具本机转写。
// 不变的红线:语音内容绝不送 LLM 网关(网关对 input_audio 静默丢弃),
// 客户端也不长期保留录音字节 —— 只存在于本次发送负载。

export function createVoiceRecorder() {
  let stream = null
  let recorder = null
  let cancelled = false
  const chunks = []

  async function start() {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true })

    recorder = new MediaRecorder(stream)
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        chunks.push(event.data)
      }
    }
    recorder.start()
  }

  function releaseTracks() {
    if (stream) {
      stream.getTracks().forEach((track) => track.stop())
      stream = null
    }
  }

  // resolve Blob;cancel() 之后 resolve null。
  function stop() {
    return new Promise((resolve, reject) => {
      if (!recorder) {
        reject(new Error('录音未开始'))
        return
      }

      recorder.onstop = () => {
        const type = recorder.mimeType || 'audio/webm'
        const blob = new Blob(chunks, { type })

        releaseTracks()
        recorder = null

        resolve(cancelled ? null : blob)
      }

      try {
        recorder.stop()
      } catch (err) {
        releaseTracks()
        recorder = null
        reject(err)
      }
    })
  }

  function cancel() {
    cancelled = true

    return stop().catch(() => null)
  }

  return { start, stop, cancel }
}

export function voiceFileName(mimeType, date = new Date()) {
  const subtype = String(mimeType || '').split('/')[1] || 'webm'
  const clean = subtype.split(';')[0]
  const pad = (n) => String(n).padStart(2, '0')
  const stamp =
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
    `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`

  return `voice-${stamp}.${clean}`
}
