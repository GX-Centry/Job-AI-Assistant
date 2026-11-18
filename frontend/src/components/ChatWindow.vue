
<script setup>
import { nextTick, ref, watch } from 'vue'
import MarkdownContent from './MarkdownContent.vue'
import { attachmentHint, attachmentIcon, formatSize } from '../utils/attachments'

const props = defineProps({
  messages: {
    type: Array,
    required: true,
  },
  generating: {
    type: Boolean,
    default: false,
  },
  historyLoading: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: '',
  },
})

const messageContainer = ref(null)

async function scrollToBottom() {
  await nextTick()

  if (messageContainer.value) {
    messageContainer.value.scrollTop =
      messageContainer.value.scrollHeight
  }
}

// 消息数量变化(发送、切换会话)与流式 token 追加都会触发滚动。
watch(
  () => [
    props.messages.length,
    props.messages[props.messages.length - 1]?.content?.length ?? 0,
  ],
  () => {
    scrollToBottom()
  },
)
</script>

<template>
  <header class="topbar">
    <div>
      <div class="eyebrow">AI WORKSPACE</div>
      <h1>Assistant</h1>
    </div>

    <!-- 状态文案只描述 composable 实际观测到的操作状态,
         不声称无法证实的“连接”状态。 -->
    <span class="connection-status">
      <span class="status-dot"></span>
      {{
        generating
          ? 'Generating...'
          : historyLoading
            ? 'Loading history...'
            : 'Ready'
      }}
    </span>
  </header>

  <section class="message-list" ref="messageContainer">
    <div v-if="messages.length === 0" class="welcome">
      <div class="welcome-icon">AI</div>
      <h2>How can I help you?</h2>
      <p>
        Start a conversation with your AI job assistant.
      </p>
    </div>

    <article
      v-for="(message, index) in messages"
      :key="index"
      class="message-row"
      :class="message.role"
    >
      <div class="avatar">
        {{ message.role === 'user' ? 'You' : 'AI' }}
      </div>

      <div class="message-content">
        <div class="message-author">
          {{ message.role === 'user' ? 'You' : 'Assistant' }}
        </div>

        <MarkdownContent
          :content="
            message.content ||
            (generating &&
            message.role === 'assistant' &&
            index === messages.length - 1
              ? 'Thinking...'
              : '')
          "
        />

        <!-- 附件:历史只有元数据;本会话图片带即时缩略图(previewUrl)。
             状态徽章与后端行为一致(M1-b):file+已解析→"已解析",
             转译失败→"解析失败",语音与未转译文件→"无法解析"。 -->
        <div
          v-if="message.attachments && message.attachments.length"
          class="attachment-list"
        >
          <div
            v-for="(att, attIndex) in message.attachments"
            :key="attIndex"
            class="attachment-chip"
          >
            <img
              v-if="att.previewUrl"
              :src="att.previewUrl"
              :alt="att.name"
              class="attachment-thumb"
            />
            <span>
              {{ attachmentIcon(att.kind) }} {{ att.name }} ·
              {{ formatSize(att.size) }}
            </span>
            <span v-if="attachmentHint(att)" class="attachment-hint">
              {{ attachmentHint(att) }}
            </span>
          </div>
        </div>
      </div>
    </article>
  </section>

  <div v-if="error" class="error-message">
    {{ error }}
  </div>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 36px;
  border-bottom: 1px solid #e2e8f0;
}

.eyebrow {
  color: #64748b;
  font-size: 10px;
  letter-spacing: 1.5px;
}

.topbar h1 {
  margin: 5px 0 0;
  font-size: 21px;
}

.connection-status {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #64748b;
  font-size: 12px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22c55e;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 32px max(24px, calc((100% - 800px) / 2));
}

.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
}

.welcome-icon {
  display: grid;
  place-items: center;
  width: 54px;
  height: 54px;
  border-radius: 12px;
  background: #2563eb;
  color: white;
  font-size: 20px;
  font-weight: 700;
}

.welcome h2 {
  margin: 20px 0 8px;
}

.welcome p {
  color: #64748b;
}

.message-row {
  display: flex;
  gap: 14px;
  margin-bottom: 30px;
}

.avatar {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: #e2e8f0;
  color: #334155;
  font-size: 11px;
  font-weight: 700;
}

.assistant .avatar {
  background: #dbeafe;
  color: #1d4ed8;
}

.message-content {
  min-width: 0;
}

.message-author {
  margin-bottom: 7px;
  font-size: 13px;
  font-weight: 700;
}

.attachment-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
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

.attachment-thumb {
  width: 42px;
  height: 42px;
  object-fit: cover;
  border-radius: 6px;
}

.attachment-hint {
  color: #94a3b8;
}

.error-message {
  padding: 10px 24px;
  color: #dc2626;
  text-align: center;
  font-size: 13px;
}

@media (max-width: 700px) {
  .topbar {
    padding: 16px;
  }
}
</style>
