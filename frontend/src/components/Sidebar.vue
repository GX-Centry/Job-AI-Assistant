
<script setup>
defineProps({
  sessions: {
    type: Array,
    required: true,
  },
  activeConversationId: {
    type: String,
    default: '',
  },
  disabled: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['create', 'select', 'remove'])
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-icon">AI</div>
      <span>Job AI Assistant</span>
    </div>

    <button
      class="new-chat"
      :disabled="disabled"
      @click="emit('create')"
    >
      + New conversation
    </button>

    <div class="section-title">HISTORY</div>

    <div class="session-list">
      <div
        v-for="session in sessions"
        :key="session.conversation_id"
        class="session-row"
        :class="{
          active:
            activeConversationId === session.conversation_id,
        }"
      >
        <button
          class="session-item"
          :disabled="disabled"
          @click="emit('select', session)"
        >
          {{ session.title }}
        </button>

        <button
          class="session-remove"
          :disabled="disabled"
          title="Delete conversation"
          @click.stop="emit('remove', session)"
        >
          ×
        </button>
      </div>

      <p v-if="sessions.length === 0" class="empty-sidebar">
        No conversations yet
      </p>
    </div>

    <div class="sidebar-footer">
      Job AI Assistant · Development
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  padding: 24px 16px;
  background: #111827;
  color: white;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 17px;
  font-weight: 700;
  margin-bottom: 32px;
}

.brand-icon {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 12px;
  background: #2563eb;
  color: white;
  font-weight: 700;
}

.new-chat {
  padding: 12px;
  border: 1px solid #475569;
  border-radius: 9px;
  background: #1f2937;
  color: white;
  text-align: left;
}

.new-chat:hover {
  background: #374151;
}

.new-chat:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.section-title {
  margin: 30px 8px 12px;
  color: #94a3b8;
  font-size: 11px;
  letter-spacing: 1.5px;
}

.session-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  overflow-y: auto;
}

.session-row {
  display: flex;
  align-items: center;
  border-radius: 8px;
}

.session-row:hover,
.session-row.active {
  background: #374151;
}

.session-item {
  flex: 1;
  min-width: 0;
  padding: 11px;
  overflow: hidden;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #cbd5e1;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-row.active .session-item {
  color: white;
}

.session-item:disabled {
  cursor: not-allowed;
}

.session-remove {
  display: none;
  flex-shrink: 0;
  margin-right: 6px;
  padding: 2px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #94a3b8;
  font-size: 15px;
  line-height: 1;
}

.session-row:hover .session-remove,
.session-row.active .session-remove {
  display: block;
}

.session-remove:hover {
  background: #4b5563;
  color: white;
}

.empty-sidebar {
  color: #94a3b8;
  font-size: 13px;
}

.sidebar-footer {
  margin-top: auto;
  padding-top: 20px;
  color: #94a3b8;
  font-size: 12px;
}

@media (max-width: 700px) {
  .sidebar {
    padding: 16px 10px;
  }

  .brand {
    font-size: 13px;
  }
}
</style>
