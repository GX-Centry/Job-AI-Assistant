import { describe, expect, it } from 'vitest'

import {
  AUDIO_RESOLVED,
  IMAGE_RESOLVED,
  MAX_ATTACHMENTS,
  MAX_AUDIO_BYTES,
  MAX_DOCUMENT_BYTES,
  MAX_IMAGE_BYTES,
  MAX_TEXT_BYTES,
  attachmentHint,
  attachmentIcon,
  classifyKind,
  formatSize,
  prepareAttachment,
  sniffFileFamily,
  sniffSelectionProblem,
  toApiAttachments,
  toBase64,
  toDisplayAttachments,
  validateSelection,
} from './attachments'

function fakeFile(name, type, bytes) {
  let reads = 0

  const bufferOf = (from, to) =>
    bytes.slice(from, to).buffer.slice(
      0,
      Math.min(to ?? bytes.length, bytes.length) - Math.max(from, 0),
    )

  return {
    name,
    type,
    size: bytes.length,
    get reads() {
      return reads
    },
    async arrayBuffer() {
      reads += 1

      return bufferOf(0)
    },
    slice: (start, end) => ({
      async arrayBuffer() {
        return bufferOf(start, end)
      },
    }),
  }
}

function entryOf(file, kind) {
  return {
    file,
    kind,
    name: file.name,
    mime: file.type,
    size: file.size,
  }
}

describe('classifyKind', () => {
  it('maps mime to the three contract kinds', () => {
    expect(classifyKind('image/png')).toBe('image')
    expect(classifyKind('audio/wav')).toBe('audio')
    expect(classifyKind('text/plain')).toBe('file')
    expect(classifyKind('')).toBe('file')
    expect(classifyKind(undefined)).toBe('file')
  })
})

describe('validateSelection', () => {
  const image = { name: 'a.png', type: 'image/png', size: 10 }
  const audio = { name: 'v.wav', type: 'audio/wav', size: MAX_IMAGE_BYTES * 4 }

  it('enforces the shared count cap', () => {
    const pending = Array.from({ length: MAX_ATTACHMENTS }, (_, i) => ({
      name: `f${i}`,
    }))

    expect(validateSelection(pending, image)).toContain(`${MAX_ATTACHMENTS} 个附件`)
  })

  it('caps image at 5MB and audio at 20MB (M1-c); file uncapped here', () => {
    expect(
      validateSelection([], { name: 'big.png', type: 'image/png', size: MAX_IMAGE_BYTES + 1 }),
    ).toContain('5MB')
    expect(validateSelection([], audio)).toBeNull()
    expect(
      validateSelection([], { name: 'big.wav', type: 'audio/wav', size: MAX_AUDIO_BYTES + 1 }),
    ).toContain('20MB')
  })

  it('rejects kind/picker mismatches and blank names', () => {
    expect(validateSelection([], image, 'audio')).toContain('音频')
    expect(validateSelection([], audio, 'image')).toContain('图片')
    expect(validateSelection([], { name: '  ', type: 'text/plain', size: 1 })).toContain(
      '文件名',
    )
  })
})

describe('toBase64 / prepareAttachment', () => {
  const bytes = (...values) => Uint8Array.from(values)
  const text = (value) => new TextEncoder().encode(value)

  it('encodes bytes to base64', () => {
    expect(toBase64(text('hi').buffer)).toBe('aGk=')
  })

  it('M1-c: reads image and audio content; recorded entry passes data through', async () => {
    const imageFile = fakeFile('a.png', 'image/png', text('hi'))
    const audioFile = fakeFile('v.wav', 'audio/wav', text('RIFFvoice'))

    const preparedImage = await prepareAttachment(entryOf(imageFile, 'image'))
    const preparedAudio = await prepareAttachment(entryOf(audioFile, 'audio'))

    expect(preparedImage.data).toBe('aGk=')
    expect(preparedAudio.data).toBe(toBase64(text('RIFFvoice').buffer))

    // 录音入口(ChatInput 已转 base64):直接透传,不再读文件。
    const recorded = await prepareAttachment({
      kind: 'audio',
      name: 'voice.webm',
      mime: 'audio/webm',
      size: 2,
      data: 'aGk=',
    })

    expect(recorded.data).toBe('aGk=')
  })

  it('M1-c: audio over 20MiB cap never reads the file', async () => {
    let reads = 0
    const hugeAudio = {
      name: 'huge.wav',
      type: 'audio/wav',
      size: MAX_AUDIO_BYTES + 1,
      async arrayBuffer() {
        reads += 1

        return text('x').buffer
      },
    }

    const prepared = await prepareAttachment(
      entryOf(hugeAudio, 'audio'),
    )

    expect('data' in prepared).toBe(false)
    expect(reads).toBe(0)
  })

  it('M1-c: reads parseable file families (incl. plain zip), ole2 stays metadata-only', async () => {
    const pdf = fakeFile('r.pdf', 'application/pdf', text('%PDF-1.4 body'))
    const plain = fakeFile('notes.txt', 'text/plain', text('纯文本笔记'))
    const docx = fakeFile('resume.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', bytes(0x50, 0x4b, 0x03, 0x04, 0x00, 0x00))
    const zip = fakeFile('pack.zip', 'application/zip', bytes(0x50, 0x4b, 0x03, 0x04, 0x00, 0x00))
    const legacy = fakeFile('old.doc', 'application/msword', bytes(0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1))

    const preparedPdf = await prepareAttachment(entryOf(pdf, 'file'))
    const preparedText = await prepareAttachment(entryOf(plain, 'file'))
    const preparedDocx = await prepareAttachment(entryOf(docx, 'file'))
    const preparedZip = await prepareAttachment(entryOf(zip, 'file'))
    const preparedLegacy = await prepareAttachment(entryOf(legacy, 'file'))

    expect(preparedPdf.data).toBe(toBase64(text('%PDF-1.4 body').buffer))
    expect(preparedText.data).toBe(toBase64(text('纯文本笔记').buffer))
    // M1-c:整个 zip 族(office 容器与普通压缩包)都发 data,
    // docx/zip 的区分与解包仲裁全部交给后端嗅探层。
    expect('data' in preparedDocx).toBe(true)
    expect('data' in preparedZip).toBe(true)
    expect(zip.reads).toBe(1)
    // 老式 Office(ole2)无执行器:不发 data,也绝不整文件读取。
    expect('data' in preparedLegacy).toBe(false)
    expect(legacy.reads).toBe(0)
  })

  it('contract cap literals pinned (backend attachment_types.py is the source)', async () => {
    // 契约限额字面值(后端 attachment_types.py 为权威出处,双侧测试各钉字面值)。
    expect(MAX_DOCUMENT_BYTES).toBe(20971520)
    expect(MAX_TEXT_BYTES).toBe(1048576)
    expect(MAX_AUDIO_BYTES).toBe(20971520)

    const hugeText = {
      name: 'huge.txt',
      type: 'text/plain',
      size: MAX_TEXT_BYTES + 1,
      async arrayBuffer() {
        return text('x').buffer
      },
      // 探针必须是 text 族('ABC'),否则会被判成 pdf 走 20MB 上限而非 1MB。
      slice: () => ({ async arrayBuffer() { return text('ABC').buffer } }),
    }

    const prepared = await prepareAttachment(entryOf(hugeText, 'file'))

    expect('data' in prepared).toBe(false)
  })
})

describe('display vs api projection', () => {
  const prepared = [
    { kind: 'image', name: 'a.png', mime: 'image/png', size: 2, data: 'aGk=' },
    { kind: 'audio', name: 'v.wav', mime: 'audio/wav', size: 1024 },
    { kind: 'file', name: 'r.pdf', mime: 'application/pdf', size: 7 },
  ]

  it('display form drops data but gives images an instant preview', () => {
    const display = toDisplayAttachments(prepared)

    expect('data' in display[0]).toBe(false)
    expect(display[0].previewUrl).toBe('data:image/png;base64,aGk=')
    expect(display[1].previewUrl).toBe('')
    expect(display[2].previewUrl).toBe('')
    expect(display[0].extractStatus).toBe('')
    expect(display[1].audioId).toBe('')
  })

  it('M1-c: api form keeps image/file/audio data (audio is now a content channel)', () => {
    const api = toApiAttachments([
      ...prepared,
      // file 通道可携带 data(prepareAttachment 判定后注入)。
      { kind: 'file', name: 'r2.pdf', mime: 'application/pdf', size: 2, data: 'aGk=' },
      // M1-c:语音 data 是被接收并持久化的内容通道 —— 必须保留。
      { kind: 'audio', name: 'voice.webm', mime: 'audio/webm', size: 2, data: 'aGk=' },
    ])

    expect(api[0].data).toBe('aGk=')
    expect('data' in api[1]).toBe(false)
    // 原 prepared 里的 r.pdf 没有 data(不可判定/未读取)→ 仍无 data。
    expect('data' in api[2]).toBe(false)
    expect(api[3].data).toBe('aGk=')
    expect(api[4].data).toBe('aGk=')
    expect('previewUrl' in api[0]).toBe(false)
  })
})

describe('attachmentHint (M1-b/M1-c 状态徽章)', () => {
  it('maps kind/extract status/audio id to the hint label', () => {
    expect(attachmentHint({ kind: 'image' })).toBe('')
    expect(attachmentHint({ kind: 'file', extract_status: 'parsed' })).toBe('已解析')
    expect(attachmentHint({ kind: 'file', extractStatus: 'parsed' })).toBe('已解析')
    expect(attachmentHint({ kind: 'file', extract_status: 'failed' })).toBe(
      '已接收 · 解析失败',
    )
    expect(attachmentHint({ kind: 'file' })).toBe('已接收 · 无法解析')
    // M1-c:有 audio_id = 已持久化可转写;旧语音(无 id)仍是无法解析。
    expect(attachmentHint({ kind: 'audio' })).toBe('已接收 · 无法解析')
    expect(attachmentHint({ kind: 'audio', audio_id: 'abc' })).toBe('已接收 · 可转写')
    expect(attachmentHint({ kind: 'audio', audioId: 'abc' })).toBe('已接收 · 可转写')
  })
})

describe('display helpers', () => {
  it('formats sizes and icons', () => {
    expect(formatSize(512)).toBe('512 B')
    expect(formatSize(2048)).toBe('2.0 KB')
    expect(formatSize(MAX_IMAGE_BYTES)).toBe('5.0 MB')
    expect(attachmentIcon('image')).toBe('🖼')
    expect(attachmentIcon('audio')).toBe('🎤')
    expect(attachmentIcon('file')).toBe('📎')
  })
})

describe('sniffFileFamily (M1-a magic mirror table)', () => {
  const bytes = (...values) => Uint8Array.from(values)
  const text = (value) => new TextEncoder().encode(value)

  it('pins the magic table', () => {
    expect(sniffFileFamily(bytes(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a))).toBe('png')
    expect(sniffFileFamily(bytes(0xff, 0xd8, 0xff, 0xe0))).toBe('jpeg')
    expect(sniffFileFamily(text('GIF89a'))).toBe('gif')
    expect(sniffFileFamily([...text('RIFF'), 0, 0, 0, 0, ...text('WEBP')])).toBe('webp')
    expect(sniffFileFamily([...text('RIFF'), 0, 0, 0, 0, ...text('WAVE')])).toBe('wav')
    expect(sniffFileFamily(bytes(0x42, 0x4d, 0x36, 0x00))).toBe('bmp')
    expect(sniffFileFamily(bytes(0x49, 0x49, 0x2a, 0x00))).toBe('tiff')
    expect(sniffFileFamily(bytes(0x00, 0x00, 0x01, 0x00))).toBe('ico')
    expect(sniffFileFamily(bytes(0x1a, 0x45, 0xdf, 0xa3))).toBe('webm')
    expect(sniffFileFamily(text('OggS'))).toBe('ogg')
    expect(sniffFileFamily(text('ID3'))).toBe('mp3')
    expect(sniffFileFamily(bytes(0xff, 0xfb, 0x90, 0x64))).toBe('mp3')
    expect(sniffFileFamily([...bytes(0, 0, 0, 0x18), ...text('ftyp'), ...text('M4A ')])).toBe('m4a')
    // HEIC 与 m4a 同为 ftyp 容器,品牌字段必须拉回图片族。
    expect(sniffFileFamily([...bytes(0, 0, 0, 0x18), ...text('ftyp'), ...text('heic')])).toBe('heif')
    expect(sniffFileFamily(text('%PDF-1.7'))).toBe('pdf')
    expect(sniffFileFamily(bytes(0x50, 0x4b, 0x03, 0x04))).toBe('zip')
    expect(sniffFileFamily(bytes(0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1))).toBe('ole2')
    expect(sniffFileFamily(new Uint8Array(0))).toBe('unknown')
    expect(sniffFileFamily(bytes(0xff, 0x00, 0xc0, 0xff))).toBe('unknown')
  })

  it('classifies utf-8 text, including probes cut mid multi-byte char', () => {
    expect(sniffFileFamily(text('你好,这是中文纯文本。'))).toBe('text')
    // 512 字节探针正好切在 3 字节汉字中间 —— 回退守卫必须仍判 text。
    expect(sniffFileFamily(text('中'.repeat(400)))).toBe('text')
  })

  it('family sets agree with the resolved strings the sniffer emits', () => {
    for (const family of ['png', 'jpeg', 'gif', 'webp', 'bmp', 'tiff', 'ico', 'heif']) {
      expect(IMAGE_RESOLVED).toContain(family)
    }

    for (const family of ['webm', 'ogg', 'wav', 'mp3', 'm4a']) {
      expect(AUDIO_RESOLVED).toContain(family)
    }
  })
})

describe('sniffSelectionProblem', () => {
  const wavFile = {
    name: 'fake.png',
    type: 'image/png',
    slice: (start, end) => ({
      arrayBuffer: async () =>
        Uint8Array.from([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x41, 0x56, 0x45]).slice(
          start,
          end,
        ).buffer,
    }),
  }

  it('flags an image picker given non-image bytes', async () => {
    expect(await sniffSelectionProblem(wavFile, 'image')).toContain('魔数与图片不符')
  })

  it('M1-c: audio picker enforces magic family and the 20MiB cap', async () => {
    // 内容是真 WAV 魔数:家族通过;大小决定放行/拦截。
    expect(await sniffSelectionProblem({ ...wavFile, size: 1024 }, 'audio')).toBeNull()

    const bigWav = { ...wavFile, size: MAX_AUDIO_BYTES + 1 }

    expect(await sniffSelectionProblem(bigWav, 'audio')).toContain('语音超过 20MB')
  })

  it('flags an audio picker given non-audio bytes', async () => {
    const pngAsAudio = {
      name: 'fake.wav',
      type: 'audio/wav',
      size: 10,
      slice: (start, end) => ({
        async arrayBuffer() {
          return Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]).slice(
            start,
            end,
          ).buffer
        },
      }),
    }

    expect(await sniffSelectionProblem(pngAsAudio, 'audio')).toContain('魔数与音频不符')
  })

  it('ignores file pickers and non-forced kinds', async () => {
    expect(await sniffSelectionProblem(wavFile, 'file')).toBeNull()
    expect(await sniffSelectionProblem(wavFile, null)).toBeNull()
  })

  it('tolerates objects without slice (jsdom literals)', async () => {
    expect(await sniffSelectionProblem({ name: 'x.png' }, 'image')).toBeNull()
  })

  describe('file kind (M1-b data-channel caps)', () => {
    const headFile = (name, type, headBytes, size) => ({
      name,
      type,
      size,
      slice: (start, end) => ({
        async arrayBuffer() {
          return Uint8Array.from(headBytes).slice(start, end).buffer
        },
      }),
    })

    it('caps documents/zip at 20MB and plain text at 1MB', async () => {
      const bigPdf = headFile('big.pdf', 'application/pdf', [0x25, 0x50, 0x44, 0x46], MAX_DOCUMENT_BYTES + 1)
      const bigTxt = headFile('big.txt', 'text/plain', [0x41, 0x42, 0x43], MAX_TEXT_BYTES + 1)
      const bigDocx = headFile('big.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', [0x50, 0x4b, 0x03, 0x04], MAX_DOCUMENT_BYTES + 1)
      const bigZip = headFile('big.zip', 'application/zip', [0x50, 0x4b, 0x03, 0x04], MAX_DOCUMENT_BYTES + 1)

      expect(await sniffSelectionProblem(bigPdf, 'file')).toContain('20MB')
      expect(await sniffSelectionProblem(bigTxt, 'file')).toContain('1MB')
      expect(await sniffSelectionProblem(bigDocx, 'file')).toContain('20MB')
      // M1-c:普通压缩包也在 data 通道 —— 限额文案独立点名"压缩包"。
      expect(await sniffSelectionProblem(bigZip, 'file')).toContain('压缩包超过 20MB')
    })

    it('in-limit parseable families and unsupported families pass (silent metadata fallback)', async () => {
      const okPdf = headFile('r.pdf', 'application/pdf', [0x25, 0x50, 0x44, 0x46], 10)
      const okTxt = headFile('n.txt', 'text/plain', [0x41, 0x42, 0x43], 10)
      const okDocx = headFile('r.docx', '', [0x50, 0x4b, 0x03, 0x04], 10)
      const plainZip = headFile('pack.zip', 'application/zip', [0x50, 0x4b, 0x03, 0x04], 10)
      const legacy = headFile('old.doc', 'application/msword', [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1], 10)

      expect(await sniffSelectionProblem(okPdf, 'file')).toBeNull()
      expect(await sniffSelectionProblem(okTxt, 'file')).toBeNull()
      expect(await sniffSelectionProblem(okDocx, 'file')).toBeNull()
      // M1-c:普通压缩包在限额内静默进入 data 通道(不再仅元数据)。
      expect(await sniffSelectionProblem(plainZip, 'file')).toBeNull()
      expect(await sniffSelectionProblem(legacy, 'file')).toBeNull()
    })
  })
})
