
<script setup>
import { onMounted, ref } from 'vue'
import Sidebar from './components/Sidebar.vue'
import ChatWindow from './components/ChatWindow.vue'
import ChatInput from './components/ChatInput.vue'
import { useChat } from './composables/useChat'

const {
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
} = useChat()

const draft = ref('')

onMounted(loadSessions)

// ChatInput 的 send 负载:{ text, attachments }(见 useChat 附件注释)。
function handleSend(payload) {
  draft.value = ''
  sendMessage(payload.text, payload.attachments)
}

function handleRemove(session) {
  if (window.confirm(`Delete conversation "${session.title}"?`)) {
    deleteSession(session)
  }
}
</script>

<template>
  <div class="app-shell">
    <Sidebar
      :sessions="sessions"
      :active-conversation-id="activeConversationId"
      :disabled="generating || sessionsBusy"
      @create="createSession"
      @select="selectSession"
      @remove="handleRemove"
    />

    <div class="main-panel">
      <ChatWindow
        :messages="messages"
        :generating="generating"
        :history-loading="historyLoading"
        :error="error"
      />

      <ChatInput
        v-model="draft"
        :generating="generating"
        :disabled="generating || sessionsBusy"
        @send="handleSend"
        @stop="stopGeneration"
      />
    </div>
  </div>
</template>

<style>
* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: Inter, Arial, sans-serif;
  background: #f8fafc;
  color: #1e293b;
}

#app {
  width: 100%;
  max-width: none;
  min-height: 100vh;
  margin: 0;
  padding: 0;
  text-align: left;
}

button,
textarea {
  font: inherit;
}

button {
  cursor: pointer;
}

.app-shell {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  height: 100vh;
}

.main-panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100vh;
  background: white;
}

@media (max-width: 700px) {
  .app-shell {
    grid-template-columns: 190px minmax(0, 1fr);
  }
}
</style>
