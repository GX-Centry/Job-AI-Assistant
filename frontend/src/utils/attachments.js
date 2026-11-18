// 附件工具(M1 首个增量 + M1-a 识别层 + M1-b 文档/文本 + M1-c 语音/zip):
// 图片可被模型直接读取;file 内容魔数可解析(pdf/纯文本/zip 族,含 office
// 容器)时携带 base64,由后端就地转译(zip 后端内存解包重路由);audio 在
// M1-c 起携带 base64(≤20MiB),由后端持久化后模型按需调用转写工具 ——
// 语音内容绝不进模型本身。不可解析 file 仍只带元数据。
// 上限与后端 job_agent/api/attachment_types.py 的 MAX_* 常量保持一致
// (前端测试钉死字面值,后端测试钉死跨模块一致性)。

export const MAX_ATTACHMENTS = 3
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024
export const MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
export const MAX_TEXT_BYTES = 1024 * 1024
export const MAX_AUDIO_BYTES = 20 * 1024 * 1024

// M1-c:zip 族(含 office 容器)整体进入 data 通道,由后端解包并按成员
// 魔数重路由;此前按扩展名区分 office/普通压缩包的前端闸口已取消 ——
// 仲裁在嗅探层,前端不再按扩展名猜内容。

export function classifyKind(mime) {
  const type = mime || ''

  if (type.startsWith('image/')) return 'image'
  if (type.startsWith('audio/')) return 'audio'
  return 'file'
}

// ==================================================
// M1-a 魔数嗅探 —— 与 job_agent/api/attachment_types.py 同源镜像表。
// ZIP 家族内部(docx/xlsx/pptx)需解包判定,前端一律记 'zip' 归档族;
// 权威仲裁在后端校验层,前端只做图片/音频入口的体验级强校验。
// ==================================================

export const IMAGE_RESOLVED = [
  'png',
  'jpeg',
  'gif',
  'webp',
  'bmp',
  'tiff',
  'ico',
  'heif',
]

export const AUDIO_RESOLVED = ['webm', 'ogg', 'wav', 'mp3', 'm4a', 'aac', 'flac']

const HEIF_BRANDS = ['heic', 'heix', 'heim', 'heif', 'mif1', 'msf1', 'hevx']

export function sniffFileFamily(input) {
  const data =
    input instanceof Uint8Array ? input : new Uint8Array(input || new ArrayBuffer(0))

  const eq = (offset, bytes) =>
    bytes.every((byte, index) => data[offset + index] === byte)

  const ascii = (offset, length) =>
    Array.from(data.slice(offset, offset + length), (byte) =>
      String.fromCharCode(byte),
    ).join('')

  if (data.length === 0) return 'unknown'
  if (eq(0, [0x89, 0x50, 0x4e, 0x47])) return 'png'
  if (eq(0, [0xff, 0xd8, 0xff])) return 'jpeg'
  if (ascii(0, 6) === 'GIF87a' || ascii(0, 6) === 'GIF89a') return 'gif'

  if (ascii(0, 4) === 'RIFF') {
    if (ascii(8, 4) === 'WEBP') return 'webp'
    if (ascii(8, 4) === 'WAVE') return 'wav'
    return 'riff'
  }

  if (ascii(0, 2) === 'BM') return 'bmp'
  if (eq(0, [0x49, 0x49, 0x2a, 0x00]) || eq(0, [0x4d, 0x4d, 0x00, 0x2a])) return 'tiff'
  if (eq(0, [0x00, 0x00, 0x01, 0x00])) return 'ico'
  if (eq(0, [0x1a, 0x45, 0xdf, 0xa3])) return 'webm'
  if (ascii(0, 4) === 'OggS') return 'ogg'
  if (ascii(0, 3) === 'ID3') return 'mp3'

  if (data.length >= 2 && data[0] === 0xff && (data[1] & 0xe0) === 0xe0) return 'mp3'

  if (data.length >= 12 && ascii(4, 4) === 'ftyp') {
    return HEIF_BRANDS.includes(ascii(8, 4)) ? 'heif' : 'm4a'
  }

  if (ascii(0, 4) === '%PDF') return 'pdf'

  if (
    eq(0, [0x50, 0x4b, 0x03, 0x04]) ||
    eq(0, [0x50, 0x4b, 0x05, 0x06]) ||
    eq(0, [0x50, 0x4b, 0x07, 0x08])
  ) {
    return 'zip'
  }

  if (eq(0, [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1])) return 'ole2'

  const probe = data.slice(0, 512)

  if (probe.length === 0) return 'unknown'

  if (eq(0, [0xef, 0xbb, 0xbf]) || eq(0, [0xff, 0xfe]) || eq(0, [0xfe, 0xff])) {
    return 'text'
  }

  // 截断探针可能切在多字节字符中间:与后端同法,回退 1~3 字节再判;
  // 守卫回退不得把探针削空(否则 1~2 字节坏二进制会误判为 text)。
  for (let trim = 0; trim <= 3; trim += 1) {
    if (trim >= probe.length) break

    const body = trim === 0 ? probe : probe.slice(0, probe.length - trim)

    try {
      new TextDecoder('utf-8', { fatal: true }).decode(body)

      return 'text'
    } catch {
      // 继续回退。
    }
  }

  return 'unknown'
}

// 入口校验:图片/音频入口内容魔数族必须匹配(强拒绝);file 入口按族限额
// (超限强拒绝),其余族静默回落"仅元数据"与旧契约一致。返回错误文案或 null。
export async function sniffSelectionProblem(file, forcedKind) {
  if (!['image', 'audio', 'file'].includes(forcedKind)) return null

  if (typeof file?.slice !== 'function') return null

  // 512 字节探针:text 族判定需要更长样本(与后端嗅探探针同宽)。
  const head = await file.slice(0, 512).arrayBuffer()
  const family = sniffFileFamily(new Uint8Array(head))

  if (forcedKind === 'image' && !IMAGE_RESOLVED.includes(family)) {
    return `内容魔数与图片不符:${file.name}`
  }

  if (forcedKind === 'audio') {
    if (!AUDIO_RESOLVED.includes(family)) {
      return `内容魔数与音频不符:${file.name}`
    }

    // M1-c:语音 data 会被接收并持久化,入口先挡 20MiB(与后端限额同源)。
    return file.size > MAX_AUDIO_BYTES ? `语音超过 20MB:${file.name}` : null
  }

  if (forcedKind === 'file') {
    if (family === 'pdf') {
      return file.size > MAX_DOCUMENT_BYTES ? `文档超过 20MB:${file.name}` : null
    }

    if (family === 'text') {
      return file.size > MAX_TEXT_BYTES ? `纯文本超过 1MB:${file.name}` : null
    }

    if (family === 'zip') {
      return file.size > MAX_DOCUMENT_BYTES ? `压缩包超过 20MB:${file.name}` : null
    }

    // ole2/音频族/图片族/unknown:不发 data,静默仅元数据(后端权威仲裁)。
    return null
  }

  return null
}

// 选择时校验:返回错误文案或 null。
export function validateSelection(pending, file, forcedKind = null) {
  if (pending.length >= MAX_ATTACHMENTS) {
    return `最多 ${MAX_ATTACHMENTS} 个附件`
  }

  if (!String(file.name || '').trim()) {
    return '文件名不能为空'
  }

  const kind = forcedKind || classifyKind(file.type)

  if (forcedKind === 'image' && !String(file.type || '').startsWith('image/')) {
    return `图片入口只能选图片:${file.name}`
  }

  if (forcedKind === 'audio' && !String(file.type || '').startsWith('audio/')) {
    return `语音入口只能选音频:${file.name}`
  }

  if (kind === 'image' && file.size > MAX_IMAGE_BYTES) {
    return `图片不能超过 5MB:${file.name}`
  }

  if (kind === 'audio' && file.size > MAX_AUDIO_BYTES) {
    return `语音不能超过 20MB:${file.name}`
  }

  return null
}

export function toBase64(buffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunkSize = 0x8000

  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize))
  }

  return btoa(binary)
}

// entry: { file, kind, name, mime, size } 或语音录音 { kind, name, mime, size, data }
// -> API 附件对象。图片读取内容;file 先嗅探魔数族,仅 pdf/纯文本/zip 族
// (M1-c 起含普通压缩包)且在限额内才读取 base64 —— 其余(ole2、音频伪装
// 等)不发 data;audio(M1-c)读取内容 base64(录音入口已带 data 则直接透传),
// 限额 20MiB,由后端持久化后经转写工具消费。
export async function prepareAttachment(entry) {
  const meta = {
    kind: entry.kind,
    name: entry.name,
    mime: entry.mime || 'application/octet-stream',
    size: entry.size,
  }

  if (entry.kind === 'image') {
    const buffer = await entry.file.arrayBuffer()

    meta.data = toBase64(buffer)

    return meta
  }

  if (entry.kind === 'audio') {
    // 录音入口:ChatInput 已把 blob 转 base64(无需再读文件)。
    if (entry.data) {
      meta.data = entry.data

      return meta
    }

    if (
      entry.file &&
      typeof entry.file.arrayBuffer === 'function' &&
      entry.size <= MAX_AUDIO_BYTES
    ) {
      meta.data = toBase64(await entry.file.arrayBuffer())
    }

    return meta
  }

  if (entry.kind === 'file' && typeof entry.file?.slice === 'function') {
    const head = new Uint8Array(await entry.file.slice(0, 512).arrayBuffer())
    const family = sniffFileFamily(head)

    const allowed = family === 'pdf' || family === 'text' || family === 'zip'

    const cap = family === 'text' ? MAX_TEXT_BYTES : MAX_DOCUMENT_BYTES

    if (allowed && entry.size <= cap) {
      meta.data = toBase64(await entry.file.arrayBuffer())
    }
  }

  return meta
}

// 消息展示用:剥离 data(避免大字符串进入响应式状态),
// 图片用 base64 生成即时缩略图(历史回读没有 data,则不显示缩略图)。
// extractStatus / audioId 透传(本会话发送时后端尚未处理,历史回读后才有值)。
export function toDisplayAttachments(prepared) {
  return prepared.map((attachment) => ({
    kind: attachment.kind,
    name: attachment.name,
    mime: attachment.mime,
    size: attachment.size,
    extractStatus: attachment.extractStatus || attachment.extract_status || '',
    audioId: attachment.audioId || attachment.audio_id || '',
    previewUrl:
      attachment.kind === 'image' && attachment.data
        ? `data:${attachment.mime};base64,${attachment.data}`
        : '',
  }))
}

// API body 用:图片含 data;file 仅当 prepareAttachment 判定可解析并已读取
// 时才含 data;audio(M1-c)含 data(服务端持久化+转写消费)。
export function toApiAttachments(prepared) {
  return prepared.map((attachment) => {
    const body = {
      kind: attachment.kind,
      name: attachment.name,
      mime: attachment.mime,
      size: attachment.size,
    }

    const channelCarriesData =
      attachment.kind === 'image' ||
      attachment.kind === 'file' ||
      attachment.kind === 'audio'

    if (channelCarriesData && attachment.data) {
      body.data = attachment.data
    }

    return body
  })
}

// 附件状态徽章(历史回读才有状态):图片无需提示;file+parsed 显示
// "已解析";failed 显示"解析失败";audio 有 audio_id(M1-c:已持久化、
// 模型可按 id 转写)显示"可转写",无 id 的旧语音显示"无法解析"。
export function attachmentHint(attachment) {
  if (!attachment || attachment.kind === 'image') return ''

  const status = attachment.extractStatus ?? attachment.extract_status ?? ''

  if (attachment.kind === 'audio') {
    const audioId = attachment.audioId ?? attachment.audio_id ?? ''

    return audioId ? '已接收 · 可转写' : '已接收 · 无法解析'
  }

  if (attachment.kind === 'file' && status === 'parsed') return '已解析'
  if (status === 'failed') return '已接收 · 解析失败'

  return '已接收 · 无法解析'
}

export function attachmentIcon(kind) {
  if (kind === 'image') return '🖼'
  if (kind === 'audio') return '🎤'
  return '📎'
}

export function formatSize(bytes) {
  if (!Number.isFinite(bytes)) return '?'

  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
