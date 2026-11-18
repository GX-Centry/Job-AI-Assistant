import { describe, expect, it, vi } from 'vitest'

import { createVoiceRecorder, voiceFileName } from './voiceRecorder'

function makeStream() {
  const track = { stop: vi.fn() }

  return { stream: { getTracks: () => [track] }, track }
}

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

function installBrowserStubs(getUserMedia) {
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })
}

describe('createVoiceRecorder', () => {
  it('resolves a Blob with mime/size and releases mic tracks on stop', async () => {
    const { stream, track } = makeStream()
    const getUserMedia = vi.fn(async () => stream)

    installBrowserStubs(getUserMedia)

    const recorder = createVoiceRecorder()

    await recorder.start()

    expect(getUserMedia).toHaveBeenCalledWith({ audio: true })

    const blob = await recorder.stop()

    expect(blob).toBeInstanceOf(Blob)
    expect(blob.size).toBe(10)
    expect(blob.type).toBe('audio/webm')
    expect(track.stop).toHaveBeenCalledTimes(1)
  })

  it('cancel() discards audio (resolves null) but still releases tracks', async () => {
    const { stream, track } = makeStream()

    installBrowserStubs(async () => stream)

    const recorder = createVoiceRecorder()

    await recorder.start()

    const blob = await recorder.cancel()

    expect(blob).toBeNull()
    expect(track.stop).toHaveBeenCalledTimes(1)
  })

  it('stop() before start() rejects instead of hanging', async () => {
    installBrowserStubs(async () => makeStream().stream)

    const recorder = createVoiceRecorder()

    await expect(recorder.stop()).rejects.toThrow('录音未开始')
  })

  it('a failed getUserMedia propagates and never constructs a recorder', async () => {
    installBrowserStubs(async () => {
      throw new DOMException('Permission denied', 'NotAllowedError')
    })

    const recorder = createVoiceRecorder()

    await expect(recorder.start()).rejects.toThrow('Permission denied')
  })
})

describe('voiceFileName', () => {
  it('stamps the subtype and call time', () => {
    const name = voiceFileName('audio/webm', new Date(2026, 10, 17, 9, 30, 5))

    expect(name).toBe('voice-20261117-093005.webm')
  })

  it('strips codec parameters and falls back to webm', () => {
    const date = new Date(2026, 10, 17, 9, 30, 5)

    expect(voiceFileName('audio/webm;codecs=opus', date)).toBe(
      'voice-20261117-093005.webm',
    )
    expect(voiceFileName('', date)).toBe('voice-20261117-093005.webm')
  })
})
