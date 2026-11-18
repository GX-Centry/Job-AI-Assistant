
<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps({
  content: {
    type: String,
    default: '',
  },
})

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

const defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, idx, options, env, self) =>
    self.renderToken(tokens, idx, options))

md.renderer.rules.link_open = (
  tokens,
  idx,
  options,
  env,
  self,
) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')

  return defaultLinkOpen(tokens, idx, options, env, self)
}

const renderedHtml = computed(() => {
  const html = md.render(props.content || '')

  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
  })
})
</script>

<template>
  <div class="markdown-content" v-html="renderedHtml"></div>
</template>

<style scoped>
.markdown-content {
  line-height: 1.8;
  overflow-wrap: anywhere;
}

.markdown-content :deep(p) {
  margin: 0 0 12px;
}

.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3) {
  margin: 18px 0 10px;
  line-height: 1.4;
}

.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  padding-left: 24px;
  margin: 10px 0;
}

.markdown-content :deep(pre) {
  overflow-x: auto;
  padding: 16px;
  border-radius: 9px;
  background: #0f172a;
  color: #e2e8f0;
}

.markdown-content :deep(code) {
  font-family: Consolas, monospace;
}

.markdown-content :deep(blockquote) {
  margin: 12px 0;
  padding-left: 14px;
  border-left: 3px solid #94a3b8;
  color: #64748b;
}

.markdown-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0;
}

.markdown-content :deep(th),
.markdown-content :deep(td) {
  padding: 9px;
  border: 1px solid #e2e8f0;
  text-align: left;
}

.markdown-content :deep(a) {
  color: #2563eb;
}
</style>
