
<script setup>
import { onBeforeUnmount, ref } from 'vue'
import {
  MAX_AUDIO_BYTES,
  attachmentIcon,
  classifyKind,
  formatSize,
  prepareAttachment,
  sniffSelectionProblem,
  toBase64,
  validateSelection,
} from '../utils/attachments'
import { createVoiceRecorder, voiceFileName } from '../utils/voiceRecorder'

const draft = defineModel({
  type: String,
  default: '',
})

const props = defineProps({
  // 流式生成中:显示停止按钮并锁定输入。
  generating: {
    type: Boolean,
    default: false,
  },
  // 会话创建/删除/列表刷新在途:禁止发送,避免与在途操作冲突。
  disabled: {
    type: Boolean,
    default: false,
  },
})

// send 事件负载:{ text, attachments },attachments 为 prepareAttachment
// 产物({kind,name,mime,size,data?};M1-c 起语音也携带 base64 —— 服务端
// 持久化后由转写工具消费,语音内容绝不进模型)。
const emit = defineEmits(['send', 'stop'])

const pending = ref([])
const attachError = ref('')

const imageInput = ref(null)
const audioInput = ref(null)
const fileInput = ref(null)
const textarea = ref(null)
const holdButton = ref(null)

async function onFiles(event, forcedKind) {
  const files = event.target.files || []

  for (const file of files) {
    // 声明 mime 可以撒谎,内容魔数不能 —— 与后端嗅探强校验同源(M1-a);
    // file 入口(M1-b)按族做 data 通道限额校验,不可解析族静默回落仅元数据。
    const kind = forcedKind || classifyKind(file.type)
    const problem =
      validateSelection(pending.value, file, forcedKind) ||
      (await sniffSelectionProblem(file, forcedKind || (kind === 'file' ? 'file' : null)))

    if (problem) {
      attachError.value = problem
      continue
    }

    pending.value.push({
      file,
      kind,
      name: file.name,
      mime: file.type || 'application/octet-stream',
      size: file.size,
    })

    attachError.value = ''
  }

  // 允许再次选择同一文件。
  event.target.value = ''
}

function removePending(index) {
  pending.value.splice(index, 1)
}

function submit() {
  if (props.generating || props.disabled) return

  const text = (draft.value || '').trim()

  if (!text && pending.value.length === 0) return

  const entries = [...pending.value]

  // prepareAttachment 是异步的(图片读 base64);失败只显示错误,不清空
  // 已选附件,用户可自行调整后重试。
  Promise.all(entries.map(prepareAttachment))
    .then((attachments) => {
      pending.value = []
      attachError.value = ''

      emit('send', { text, attachments })
    })
    .catch((err) => {
      attachError.value = err?.message || '附件读取失败'
    })
}

// Enter 发送,Shift + Enter 换行(浏览器默认行为)。
function onKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    submit()
  }
}

// ==================================================
// 微信式语音:长按(按钮/空格)开始录音,松开发送,Esc 取消。
// M1-c(用户拍板"录音发送转写"):webm 字节转 base64 随消息发送,服务端
// 持久化后由 transcribe_audio 工具本机转写 —— 内容仍绝不进模型本身,
// 客户端也不保留语音字节(仅本次发送负载)。
// ==================================================

const HOLD_SPACE_MS = 350
const MIN_VOICE_MS = 600

const recording = ref(false)
const recordingSeconds = ref(0)

let voiceRecorder = null
let voiceStartedAt = 0
let elapsedTimer = null
let spaceHoldTimer = null
let spacePressedAt = null

function locked() {
  return props.generating || props.disabled
}

async function startVoice() {
  if (recording.value || locked() || voiceRecorder) return

  try {
    voiceRecorder = createVoiceRecorder()
    await voiceRecorder.start()

    recording.value = true
    voiceStartedAt = Date.now()
    recordingSeconds.value = 0
    elapsedTimer = setInterval(() => {
      recordingSeconds.value += 1
    }, 1000)
  } catch (err) {
    voiceRecorder = null
    attachError.value = `无法访问麦克风:${err?.message || err?.name || '未知错误'}`
  }
}

function clearElapsed() {
  if (elapsedTimer !== null) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

async function finishVoice(sendIt) {
  const recorder = voiceRecorder

  voiceRecorder = null
  clearElapsed()

  if (!recorder || !recording.value) {
    recording.value = false
    return
  }

  recording.value = false

  const durationMs = Date.now() - voiceStartedAt

  if (!sendIt) {
    await recorder.cancel()
    return
  }

  if (durationMs < MIN_VOICE_MS) {
    await recorder.cancel()
    attachError.value = '录音时间太短'
    return
  }

  const blob = await recorder.stop()

  if (!blob || blob.size === 0) {
    attachError.value = '录音失败,未收到音频数据'
    return
  }

  if (blob.size > MAX_AUDIO_BYTES) {
    attachError.value = '录音过大(超过 20MB),未发送'
    return
  }

  const mime = blob.type || 'audio/webm'
  const data = toBase64(await blob.arrayBuffer())

  // 语音进入待发送列表后立即发送(与已暂存文件/文本一起成为同一条消息)。
  // M1-c:携带 base64 —— 服务端持久化并转写;超过 20MiB 已在上方拦截。
  pending.value.push({
    kind: 'audio',
    name: voiceFileName(mime),
    mime,
    size: blob.size,
    data,
  })

  submit()
}

function onVoicePointerUp() {
  finishVoice(true)
}

function onVoiceTouchEnd() {
  finishVoice(true)
}

function attachVoiceReleaseListenersOnce() {
  window.addEventListener('mouseup', onVoicePointerUp, { once: true })
  window.addEventListener('touchend', onVoiceTouchEnd, { once: true })
}

function startVoiceWithPointer(event) {
  event.preventDefault()
  startVoice().then(attachVoiceReleaseListenersOnce)
}

// 空格长按:350ms 阈值。短按恢复普通空格输入(手动插入选材文本框);
// keydown 总是 preventDefault,否则长按会先插入空格/滚动页面。
function onWindowKeydown(event) {
  if (event.code === 'Escape' && recording.value) {
    finishVoice(false)
    return
  }

  if (
    event.code !== 'Space' ||
    event.repeat ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey
  ) {
    return
  }

  if (recording.value) {
    // 空格松开前再按空格:忽略,避免打断当前录音状态机。
    return
  }

  event.preventDefault()
  spacePressedAt = {
    at: Date.now(),
    target: document.activeElement,
  }
  spaceHoldTimer = setTimeout(() => {
    spaceHoldTimer = null

    if (spacePressedAt) {
      spacePressedAt.long = true
      startVoice().then(attachVoiceReleaseListenersOnce)
    }
  }, HOLD_SPACE_MS)
}

function onWindowKeyup(event) {
  if (event.code !== 'Space') return

  if (spaceHoldTimer !== null) {
    // 短按:取消长按,把手动 preventDefault 掉的空格补回输入框。
    clearTimeout(spaceHoldTimer)
    spaceHoldTimer = null

    const press = spacePressedAt
    spacePressedAt = null

    if (
      press &&
      press.target &&
      press.target === textarea.value &&
      !locked()
    ) {
      insertSpaceAtCaret(press.target)
    }

    return
  }

  if (spacePressedAt?.long) {
    spacePressedAt = null
    finishVoice(true)
  }
}

function insertSpaceAtCaret(el) {
  const start = el.selectionStart ?? draft.value.length
  const end = el.selectionEnd ?? start

  draft.value = draft.value.slice(0, start) + ' ' + draft.value.slice(end)

  requestAnimationFrame(() => {
    try {
      el.focus()
      el.setSelectionRange(start + 1, start + 1)
    } catch {
      // 元素已失焦/卸载:忽略。
    }
  })
}

window.addEventListener('keydown', onWindowKeydown)
window.addEventListener('keyup', onWindowKeyup)

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onWindowKeydown)
  window.removeEventListener('keyup', onWindowKeyup)
  window.removeEventListener('mouseup', onVoicePointerUp)
  window.removeEventListener('touchend', onVoiceTouchEnd)
  clearElapsed()

  if (spaceHoldTimer !== null) {
    clearTimeout(spaceHoldTimer)
  }

  if (voiceRecorder) {
    voiceRecorder.cancel()
    voiceRecorder = null
  }
})
</script>

<template>
  <div class="composer-shell">
    <div v-if="recording" class="recording-hint">
      🔴 录音中 {{ recordingSeconds }}s · 松开发送,Esc 取消(发送后由服务端本机转写)
    </div>

    <div v-if="pending.length || attachError" class="attachment-strip">
      <span
        v-for="(att, index) in pending"
        :key="`${att.name}-${index}`"
        class="attachment-chip"
      >
        {{ attachmentIcon(att.kind) }} {{ att.name }} · {{ formatSize(att.size) }}
        <button
          type="button"
          class="chip-remove"
          :disabled="generating"
          @click="removePending(index)"
        >
          ✕
        </button>
      </span>

      <span v-if="attachError" class="attachment-error">
        {{ attachError }}
      </span>
    </div>

    <form class="composer" @submit.prevent="submit">
      <div class="picker-buttons">
        <button
          type="button"
          title="添加图片(模型可读)"
          :disabled="generating || disabled"
          @click="imageInput.click()"
        >
          🖼 图片
        </button>
        <button
          type="button"
          title="添加语音文件(≤20MB,服务端本机转写)"
          :disabled="generating || disabled"
          @click="audioInput.click()"
        >
          🎤 语音文件
        </button>
        <button
          type="button"
          title="添加文件(pdf/docx/xlsx/pptx/纯文本/压缩包自动解析,其余仅接收)"
          :disabled="generating || disabled"
          @click="fileInput.click()"
        >
          📎 文件
        </button>
      </div>

      <textarea
        ref="textarea"
        v-model="draft"
        placeholder="Message your assistant... 长按空格可录音"
        rows="2"
        :disabled="generating"
        @keydown="onKeydown"
      ></textarea>

      <button
        ref="holdButton"
        type="button"
        class="hold-button"
        :class="{ recording }"
        :disabled="generating || disabled"
        title="按住说话(松开立即发送;服务端本机转写)"
        @mousedown.prevent="startVoiceWithPointer"
        @touchstart.prevent="startVoiceWithPointer"
      >
        {{ recording ? '松开 发送' : '🎙 按住说话' }}
      </button>

      <button
        v-if="generating"
        type="button"
        class="stop-button"
        @click="emit('stop')"
      >
        Stop
      </button>

      <button
        v-else
        type="submit"
        class="send-button"
        :disabled="disabled || (!draft.trim() && pending.length === 0)"
      >
        Send
      </button>
    </form>

    <input
      ref="imageInput"
      type="file"
      accept="image/*"
      multiple
      class="file-input"
      data-picker="image"
      @change="onFiles($event, 'image')"
    />
    <input
      ref="audioInput"
      type="file"
      accept="audio/*"
      multiple
      class="file-input"
      data-picker="audio"
      @change="onFiles($event, 'audio')"
    />
    <input
      ref="fileInput"
      type="file"
      multiple
      class="file-input"
      data-picker="file"
      @change="onFiles($event, null)"
    />
  </div>

  <div class="disclaimer">
    AI-generated content may contain errors.
    Images are readable by the model; documents, plain text and zip archives
    are auto-extracted; voice is sent for on-server local transcription.
  </div>
</template>

<style scoped>
.composer-shell {
  max-width: 850px;
  width: calc(100% - 48px);
  margin: 0 auto;
}

.recording-hint {
  padding: 8px 12px 0;
  color: #dc2626;
  font-size: 13px;
}

.attachment-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px 12px 0;
}

.attachment-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 999px;
  background: #f1f5f9;
  font-size: 12px;
}

.chip-remove {
  border: 0;
  background: transparent;
  color: #64748b;
  font-size: 12px;
  padding: 0 2px;
}

.attachment-error {
  color: #dc2626;
  font-size: 12px;
}

.composer {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  padding: 12px;
  border: 1px solid #cbd5e1;
  border-radius: 14px;
  box-shadow: 0 3px 12px #00000008;
}

.picker-buttons {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.picker-buttons button {
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
  padding: 5px 8px;
  font-size: 12px;
  white-space: nowrap;
}

.picker-buttons button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.hold-button {
  border: 1px solid #cbd5e1;
  border-radius: 9px;
  background: #f1f5f9;
  padding: 11px 14px;
  font-size: 13px;
  white-space: nowrap;
  user-select: none;
}

.hold-button.recording {
  background: #fee2e2;
  border-color: #dc2626;
  color: #b91c1c;
}

.hold-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.file-input {
  display: none;
}

.composer textarea {
  flex: 1;
  resize: none;
  min-height: 44px;
  max-height: 150px;
  padding: 12px;
  border: 0;
  outline: none;
}

.send-button {
  padding: 11px 20px;
  border: 0;
  border-radius: 9px;
  background: #2563eb;
  color: white;
}

.send-button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.stop-button {
  padding: 11px 20px;
  border: 0;
  border-radius: 9px;
  background: #dc2626;
  color: white;
}

.disclaimer {
  padding: 12px;
  color: #94a3b8;
  font-size: 11px;
  text-align: center;
}
</style>
