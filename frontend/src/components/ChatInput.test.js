// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import ChatInput from './ChatInput.vue'
import {
  MAX_ATTACHMENTS,
  MAX_IMAGE_BYTES,
  MAX_TEXT_BYTES,
  sniffFileFamily,
  toBase64,
} from '../utils/attachments'

// M1-a 起,图片/音频入口会嗅探内容魔数,测试文件必须带真实魔数头。
const PNG_MAGIC = Uint8Array.from([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
])
const WAVE_MAGIC = Uint8Array.from([
  0x52, 0x49, 0x46, 0x46, 0x00, 0x00, 0x00, 0x00, 0x57, 0x41, 0x56, 0x45,
])

function fakeFile(name, type, contentBytes = null) {
  const bytes = Uint8Array.from(
    contentBytes ??
      (type.startsWith('image/')
        ? PNG_MAGIC
        : type.startsWith('audio/')
          ? WAVE_MAGIC
          : [0x68, 0x69]),
  )

  return {
    name,
    type,
    size: bytes.length,
    arrayBuffer: async () => bytes.slice().buffer,
    slice: (start, end) => ({
      arrayBuffer: async () => bytes.slice(start, end).buffer,
    }),
  }
}

function mountInput(props = {}) {
  return mount(ChatInput, {
    props: { modelValue: '', generating: false, disabled: false, ...props },
    // 空格录音回退要看 document.activeElement,必须挂进文档。
    attachTo: document.body,
  })
}

async function chooseFiles(wrapper, picker, files) {
  const input = wrapper.find(`input[data-picker="${picker}"]`)

  Object.defineProperty(input.element, 'files', {
    value: files,
    configurable: true,
  })

  // onFiles 现在会 await 魔数嗅探(异步),trigger 不会等待它。
  await input.trigger('change')
  await flushPromises()
  await nextTick()
}

describe('ChatInput attachment pickers', () => {
  it('renders image/audio/file pickers with accept filters', () => {
    const wrapper = mountInput()

    expect(wrapper.find('input[data-picker="image"]').attributes('accept')).toBe('image/*')
    expect(wrapper.find('input[data-picker="audio"]').attributes('accept')).toBe('audio/*')
    expect(wrapper.find('input[data-picker="file"]').exists()).toBe(true)
  })

  it('selected files appear as removable chips', async () => {
    const wrapper = mountInput()

    await chooseFiles(wrapper, 'image', [fakeFile('cat.png', 'image/png')])
    await chooseFiles(wrapper, 'audio', [fakeFile('memo.webm', 'audio/webm')])

    const chips = wrapper.findAll('.attachment-chip')

    expect(chips).toHaveLength(2)
    expect(chips[0].text()).toContain('cat.png')
    expect(chips[1].text()).toContain('memo.webm')

    await chips[0].find('.chip-remove').trigger('click')

    expect(wrapper.findAll('.attachment-chip')).toHaveLength(1)
  })

  it('rejects over-count and oversized images with inline errors', async () => {
    const wrapper = mountInput()

    await chooseFiles(
      wrapper,
      'file',
      Array.from({ length: MAX_ATTACHMENTS + 1 }, (_, i) => ({
        name: `f${i}.bin`,
        type: '',
        size: 1,
        arrayBuffer: async () => new Uint8Array(1).buffer,
      })),
    )

    expect(wrapper.findAll('.attachment-chip')).toHaveLength(MAX_ATTACHMENTS)
    expect(wrapper.find('.attachment-error').text()).toContain(`${MAX_ATTACHMENTS} 个附件`)

    // 空列表下单独验证图片尺寸上限(满额时数量限制先命中,属组件契约)。
    const fresh = mountInput()

    await chooseFiles(fresh, 'image', [
      {
        name: 'huge.png',
        type: 'image/png',
        size: MAX_IMAGE_BYTES + 1,
        arrayBuffer: async () => new Uint8Array(1).buffer,
      },
    ])

    expect(fresh.find('.attachment-error').text()).toContain('5MB')
    expect(fresh.findAll('.attachment-chip')).toHaveLength(0)
  })

  it('emits send with text plus prepared attachments and clears the strip', async () => {
    const wrapper = mountInput()

    await chooseFiles(wrapper, 'image', [fakeFile('shot.png', 'image/png')])

    await wrapper.find('textarea').setValue('看图')
    wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const sent = wrapper.emitted('send')

    expect(sent).toHaveLength(1)

    const payload = sent[0][0]

    expect(payload.text).toBe('看图')
    expect(payload.attachments).toEqual([
      {
        kind: 'image',
        name: 'shot.png',
        mime: 'image/png',
        size: PNG_MAGIC.length,
        data: toBase64(PNG_MAGIC.buffer),
      },
    ])
    expect(wrapper.findAll('.attachment-chip')).toHaveLength(0)
  })

  it('attachment-only messages can be sent without text (audio now carries data)', async () => {
    const wrapper = mountInput()

    await chooseFiles(wrapper, 'audio', [fakeFile('voice.wav', 'audio/wav')])

    expect(wrapper.find('.send-button').attributes('disabled')).toBeUndefined()

    wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const payload = wrapper.emitted('send')[0][0]

    expect(payload.text).toBe('')
    // M1-c:语音入口选择的文件内容会被读取为 base64 随消息发送。
    expect(payload.attachments[0]).toEqual({
      kind: 'audio',
      name: 'voice.wav',
      mime: 'audio/wav',
      size: WAVE_MAGIC.length,
      data: toBase64(WAVE_MAGIC.buffer),
    })
  })

  it('image picker rejects files whose magic bytes are not images', async () => {
    const wrapper = mountInput()

    await chooseFiles(wrapper, 'image', [
      fakeFile('lie.png', 'image/png', WAVE_MAGIC),
    ])

    expect(wrapper.findAll('.attachment-chip')).toHaveLength(0)
    expect(wrapper.find('.attachment-error').text()).toContain('魔数与图片不符')
  })

  it('audio picker rejects files whose magic bytes are not audio', async () => {
    const wrapper = mountInput()

    await chooseFiles(wrapper, 'audio', [
      fakeFile('trick.webm', 'audio/webm', PNG_MAGIC),
    ])

    expect(wrapper.findAll('.attachment-chip')).toHaveLength(0)
    expect(wrapper.find('.attachment-error').text()).toContain('魔数与音频不符')
  })

  it('M1-b: file picker carries base64 data for real PDF content', async () => {
    const wrapper = mountInput()
    const pdfBytes = new TextEncoder().encode('%PDF-1.4 测试简历正文')

    await chooseFiles(wrapper, 'file', [
      fakeFile('resume.pdf', 'application/pdf', pdfBytes),
    ])

    wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const payload = wrapper.emitted('send')[0][0]

    expect(payload.attachments[0]).toEqual({
      kind: 'file',
      name: 'resume.pdf',
      mime: 'application/pdf',
      size: pdfBytes.length,
      data: toBase64(pdfBytes.buffer),
    })
  })

  it('M1-b: file picker blocks plain text above the 1MB cap', async () => {
    const wrapper = mountInput()

    await chooseFiles(wrapper, 'file', [
      {
        name: 'huge.txt',
        type: 'text/plain',
        size: MAX_TEXT_BYTES + 1,
        async arrayBuffer() {
          return new Uint8Array([0x41]).buffer
        },
        slice: () => ({
          async arrayBuffer() {
            return new Uint8Array([0x41, 0x42, 0x43]).buffer
          },
        }),
      },
    ])

    expect(wrapper.findAll('.attachment-chip')).toHaveLength(0)
    expect(wrapper.find('.attachment-error').text()).toContain('1MB')
  })

  it('hides pickers behind the stop state', () => {
    const wrapper = mountInput({ generating: true })

    expect(wrapper.find('.stop-button').exists()).toBe(true)

    for (const picker of ['image', 'audio', 'file']) {
      const button = wrapper
        .findAll('.picker-buttons button')
        .find((b) => b.text().includes(picker === 'image' ? '图片' : picker === 'audio' ? '语音' : '文件'))

      expect(button.attributes('disabled')).toBe('')
    }
  })
})

// 微信式长按录音:鼠标/空格长按开始,松开发送(录音转 base64 随消息上行,
// 服务端本机转写 —— M1-c 用户拍板),Esc 取消。
class FakeMediaRecorder {
  constructor(stream) {
    this.stream = stream
    this.mimeType = 'audio/webm'
    this.ondataavailable = null
    this.onstop = null
  }

  start() {}

  stop() {
    this.ondataavailable?.({ data: new Blob(['voice-data'], { type: 'audio/webm' }) })
    this.onstop?.()
  }
}

function installVoiceEnv() {
  const track = { stop: vi.fn() }
  const stream = { getTracks: () => [track] }

  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  vi.stubGlobal('navigator', {
    mediaDevices: { getUserMedia: vi.fn(async () => stream) },
  })

  return { track }
}

function pressSpace() {
  window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space' }))
}

function releaseSpace() {
  window.dispatchEvent(new KeyboardEvent('keyup', { code: 'Space' }))
}

function releaseMouse() {
  window.dispatchEvent(new Event('mouseup'))
}

describe('ChatInput hold-to-talk voice', () => {
  it('mouse hold records and release sends the recording as base64 (M1-c)', async () => {
    vi.useFakeTimers()
    const { track } = installVoiceEnv()
    const wrapper = mountInput()

    await wrapper.find('.hold-button').trigger('mousedown')
    await flushPromises()

    expect(wrapper.find('.recording-hint').exists()).toBe(true)
    expect(wrapper.find('.hold-button').classes()).toContain('recording')

    vi.advanceTimersByTime(650)
    releaseMouse()
    await flushPromises()

    expect(wrapper.find('.recording-hint').exists()).toBe(false)

    const payload = wrapper.emitted('send')[0][0]

    expect(payload.text).toBe('')

    const [audio] = payload.attachments

    expect(audio.kind).toBe('audio')
    expect(audio.mime).toBe('audio/webm')
    expect(audio.size).toBe(10)
    expect(audio.name).toMatch(/^voice-\d{8}-\d{6}\.webm$/)
    // M1-c:录音字节转 base64 随消息上行(服务端持久化并转写)。
    expect(audio.data).toBe(toBase64(new TextEncoder().encode('voice-data').buffer))
    expect(track.stop).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('long-pressing space records, releasing space sends', async () => {
    vi.useFakeTimers()
    installVoiceEnv()
    const wrapper = mountInput()

    wrapper.find('textarea').element.focus()
    pressSpace()

    vi.advanceTimersByTime(350)
    await flushPromises()

    expect(wrapper.find('.recording-hint').exists()).toBe(true)

    vi.advanceTimersByTime(650)
    releaseSpace()
    await flushPromises()

    const [audio] = wrapper.emitted('send')[0][0].attachments

    expect(audio.kind).toBe('audio')
    // M1-c:空格长按与鼠标长按同一链路,录音必须带 data。
    expect(audio.data).toBe(toBase64(new TextEncoder().encode('voice-data').buffer))

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('short space press falls back to inserting a plain space', async () => {
    vi.useFakeTimers()
    const wrapper = mountInput()

    wrapper.find('textarea').element.focus()
    pressSpace()
    releaseSpace()
    await flushPromises()

    const updates = wrapper.emitted('update:modelValue')

    expect(updates.at(-1)).toEqual([' '])

    // 越过长按阈值也不应开始录音/发送。
    vi.advanceTimersByTime(1000)
    await flushPromises()

    expect(wrapper.emitted('send')).toBeUndefined()
    expect(wrapper.find('.recording-hint').exists()).toBe(false)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('releasing before the minimum duration reports instead of sending', async () => {
    vi.useFakeTimers()
    installVoiceEnv()
    const wrapper = mountInput()

    await wrapper.find('.hold-button').trigger('mousedown')
    await flushPromises()

    vi.advanceTimersByTime(300)
    releaseMouse()
    await flushPromises()

    expect(wrapper.emitted('send')).toBeUndefined()
    expect(wrapper.find('.attachment-error').text()).toContain('录音时间太短')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('Escape discards the take: no send, mic released', async () => {
    vi.useFakeTimers()
    const { track } = installVoiceEnv()
    const wrapper = mountInput()

    await wrapper.find('.hold-button').trigger('mousedown')
    await flushPromises()

    vi.advanceTimersByTime(650)
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Escape' }))
    await flushPromises()

    expect(wrapper.emitted('send')).toBeUndefined()
    expect(wrapper.find('.recording-hint').exists()).toBe(false)
    expect(track.stop).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    vi.useRealTimers()
  })
})
