<template>
  <div class="container">
    <div class="search-page">
      <!-- 页面标题 -->
      <header class="page-header">
        <h1 class="page-title">AI 智能检索</h1>
        <p class="page-desc">输入设备故障现象，AI 将输出结构化的诊断报告</p>
      </header>

      <!-- 快捷操作栏 -->
      <div class="chat-toolbar">
        <div class="toolbar-left">
          <button class="btn btn-outline btn-xs" @click="clearChat" :disabled="messages.length === 0">
            清空对话
          </button>
          <button class="btn btn-outline btn-xs" @click="clearHistoryConfirm" :disabled="!hasHistory">
            清除历史记录
          </button>
        </div>
        <div class="toolbar-right">
          <span class="history-hint" v-if="hasHistory">已保存 {{ savedSessions.length }} 轮对话历史</span>
        </div>
      </div>

      <!-- 对话区 -->
      <div class="chat-area" ref="chatArea">
        <!-- 欢迎消息（初始状态） -->
        <div v-if="messages.length === 0" class="welcome-card card">
          <div class="welcome-icon">◎</div>
          <h3>欢迎使用智能检索</h3>
          <p>描述设备异常现象，或点击下方示例快速开始：</p>
          <div class="quick-examples">
            <button
              v-for="(ex, i) in examples"
              :key="i"
              class="example-chip"
              @click="askWithExample(ex)"
            >
              <span class="chip-icon">◎</span>
              <span class="chip-text">{{ ex }}</span>
            </button>
          </div>
        </div>

        <!-- 消息列表 -->
        <div v-for="(msg, i) in messages" :key="i" class="msg-row" :class="msg.role">
          <div class="msg-bubble">
            <div v-if="msg.role === 'assistant'" class="msg-content structured" v-html="formatAnswer(msg.content)"></div>
            <div v-else class="msg-content">{{ msg.content }}</div>
            <div v-if="msg.role === 'assistant' && msg.citations && msg.citations.length" class="citation-list">
              <div class="citation-title">参考来源</div>
              <a
                v-for="source in msg.citations"
                :key="source.id"
                class="citation-card"
                :href="source.file_url"
                target="_blank"
                rel="noopener"
              >
                <span class="citation-id">[{{ source.id }}]</span>
                <span class="citation-main">
                  <strong>《{{ source.document_title }}》</strong>
                  <small>第 {{ source.page_label }} 页 · {{ source.section_title || '未识别章节' }}</small>
                </span>
              </a>
            </div>
            <div class="msg-time">{{ formatMsgTime(msg.time) }}</div>
          </div>
        </div>

        <!-- 加载状态 -->
        <div v-if="loading" class="msg-row assistant">
          <div class="msg-bubble">
            <div class="typing-indicator">
              <span></span><span></span><span></span>
            </div>
            <div class="msg-time">AI 分析中...</div>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <form class="input-area" @submit.prevent="askAI">
        <textarea
          v-model="question"
          class="input search-input"
          placeholder="输入设备故障描述，按 Ctrl+Enter 发送..."
          :disabled="loading"
          rows="2"
          @keydown="handleKeydown"
        />
        <div class="input-actions">
          <div class="input-tip">{{ question.length }} / 500</div>
          <button type="submit" class="btn btn-primary send-btn" :disabled="loading || !question.trim()">
            发送 ↑
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<script>
const STORAGE_KEY = 'equipai_search_history'
const MAX_HISTORY = 10

export default {
  name: 'Search',
  data() {
    return {
      question: '',
      messages: [],
      loading: false,
      savedSessions: [],
      examples: [
        '火花塞电极间隙的标准范围是多少？',
        '气缸压缩压力低于标准值时如何进一步判断？',
        '进气门和排气门的标准间隙分别是多少？',
        '安装水泵时水封动环方向和扭矩是什么？'
      ]
    }
  },
  computed: {
    hasHistory() {
      return this.savedSessions.length > 0
    }
  },
  created() {
    this.loadHistory()
  },
  mounted() {
    this.$nextTick(() => this.scrollToBottom())
  },
  methods: {
    handleKeydown(e) {
      if (e.ctrlKey && e.key === 'Enter') {
        e.preventDefault()
        this.askAI()
      }
    },
    askWithExample(text) {
      this.question = text
      this.askAI()
    },
    async askAI() {
      const text = this.question.trim()
      if (!text || this.loading) return
      if (text.length > 500) return

      this.messages.push({ role: 'user', content: text, time: Date.now() })
      this.question = ''
      this.loading = true
      this.$nextTick(() => this.scrollToBottom())

      try {
        const token = localStorage.getItem('equipai_token') || ''
        const res = await fetch('/api/rag/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
          body: JSON.stringify({ question: text, top_k: 5 })
        })
        const data = await res.json()

        if (res.ok) {
          this.messages.push({
            role: 'assistant',
            content: data.answer,
            citations: data.citations || [],
            answerable: data.answerable,
            time: Date.now()
          })
        } else {
          const detail = typeof data.detail === 'string' ? data.detail : '未知错误'
          this.messages.push({ role: 'assistant', content: '❌ 请求失败：' + detail, time: Date.now() })
        }
      } catch (err) {
        this.messages.push({ role: 'assistant', content: '❌ 无法连接知识库服务，请确认后端正在运行。', time: Date.now() })
      } finally {
        this.loading = false
        this.saveCurrentSession()
        this.$nextTick(() => this.scrollToBottom())
      }
    },
    formatAnswer(text) {
      if (!text) return ''
      let html = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;')
        .replace(/【(.*?)】/g, '<div class="tag">[$1]</div>')
        .replace(/\n\s*\n/g, '</p><p>')
        .replace(/\n([0-9]+)\.\s*/g, '<br><span class="num">$1.</span> ')
        .replace(/\n[-•]\s*/g, '<br><span class="bullet">›</span> ')
        .replace(/\n-/g, '<br>-')
        .replace(/\n/g, '<br>')
      return '<p>' + html + '</p>'
    },
    formatMsgTime(ts) {
      if (!ts) return ''
      const d = new Date(ts)
      return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    },
    scrollToBottom() {
      const el = this.$refs.chatArea
      if (el) {
        el.scrollTop = el.scrollHeight
      }
    },
    clearChat() {
      this.messages = []
    },
    clearHistoryConfirm() {
      if (confirm('确定要清除所有本地对话历史吗？')) {
        localStorage.removeItem(STORAGE_KEY)
        this.savedSessions = []
        this.messages = []
      }
    },
    saveCurrentSession() {
      if (this.messages.length === 0) return
      try {
        const raw = localStorage.getItem(STORAGE_KEY)
        const list = raw ? JSON.parse(raw) : []
        const latest = this.messages.slice(0, 50)
        list.unshift({ id: Date.now(), time: Date.now(), messages: latest })
        while (list.length > MAX_HISTORY) list.pop()
        localStorage.setItem(STORAGE_KEY, JSON.stringify(list))
        this.savedSessions = list
      } catch (e) {}
    },
    loadHistory() {
      try {
        const raw = localStorage.getItem(STORAGE_KEY)
        if (raw) {
          const list = JSON.parse(raw)
          this.savedSessions = list || []
          if (list && list.length > 0 && list[0].messages) {
            this.messages = list[0].messages
          }
        }
      } catch (e) {}
    }
  }
}
</script>

<style scoped>
.search-page {
  max-width: 860px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 200px);
}

.page-header {
  text-align: center;
  margin-bottom: 24px;
}

.page-title {
  font-size: 1.75rem;
  margin-bottom: 8px;
}

.page-desc {
  color: var(--text-secondary);
  font-size: 0.9375rem;
}

/* 工具条 */
.chat-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.toolbar-left {
  display: flex;
  gap: 8px;
}

.history-hint {
  font-size: 0.75rem;
  color: var(--text-muted);
}

.btn-xs {
  padding: 5px 12px;
  font-size: 0.75rem;
}

.citation-list {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border-color);
}

.citation-title {
  margin-bottom: 8px;
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 600;
}

.citation-card {
  display: flex;
  gap: 8px;
  margin-top: 6px;
  padding: 8px 10px;
  color: inherit;
  text-decoration: none;
  background: rgba(37, 99, 235, 0.06);
  border: 1px solid rgba(37, 99, 235, 0.16);
  border-radius: 8px;
}

.citation-card:hover {
  border-color: var(--primary-color);
}

.citation-id {
  color: var(--primary-color);
  font-weight: 700;
}

.citation-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.citation-main small {
  margin-top: 2px;
  color: var(--text-muted);
}

/* 对话区 */
.chat-area {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0 16px;
  max-height: 55vh;
}

/* 欢迎卡片 */
.welcome-card {
  text-align: center;
  padding: 32px 28px;
}

.welcome-icon {
  font-size: 2.5rem;
  color: var(--primary);
  margin-bottom: 12px;
}

.welcome-card h3 {
  margin-bottom: 8px;
}

.welcome-card p {
  color: var(--text-secondary);
  font-size: 0.875rem;
  margin-bottom: 18px;
}

.quick-examples {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  max-width: 680px;
  margin: 0 auto;
  text-align: left;
}

.example-chip {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 14px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 0.8125rem;
  line-height: 1.5;
  transition: all var(--duration) var(--ease);
  text-align: left;
  font-family: inherit;
}

.example-chip:hover {
  background: var(--primary-subtle);
  border-color: var(--border-active);
  color: var(--text-primary);
}

.chip-icon {
  color: var(--primary);
  flex-shrink: 0;
  margin-top: 1px;
}

/* 消息行 */
.msg-row {
  display: flex;
  margin-bottom: 16px;
}

.msg-row.user {
  justify-content: flex-end;
}

.msg-row.assistant {
  justify-content: flex-start;
}

.msg-bubble {
  max-width: 85%;
  padding: 14px 18px;
  border-radius: var(--radius-lg);
  line-height: 1.7;
  font-size: 0.9375rem;
  position: relative;
}

.user .msg-bubble {
  background: rgba(0, 212, 255, 0.1);
  border: 1px solid var(--border-active);
  border-bottom-right-radius: 2px;
}

.assistant .msg-bubble {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-bottom-left-radius: 2px;
}

.msg-time {
  margin-top: 8px;
  font-size: 0.6875rem;
  color: var(--text-muted);
  font-family: 'JetBrains Mono', monospace;
  text-align: right;
}

.assistant .msg-time {
  text-align: left;
}

/* 结构化回答 */
.msg-content.structured p {
  margin: 0;
}

.msg-content.structured :deep(.tag) {
  display: inline-block;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--primary);
  background: var(--primary-subtle);
  padding: 2px 10px;
  border-radius: 2px;
  margin: 10px 0 6px;
  letter-spacing: 1px;
}

.msg-content.structured :deep(.num) {
  color: var(--accent-green);
  font-weight: 600;
  font-family: 'JetBrains Mono', monospace;
  margin-right: 4px;
}

.msg-content.structured :deep(.bullet) {
  color: var(--primary);
  margin-right: 6px;
}

/* 加载动画 */
.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 4px 0 10px;
}

.typing-indicator span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--primary);
  opacity: 0.4;
  animation: typing 1.4s infinite;
}

.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }

@keyframes typing {
  0%, 60%, 100% { opacity: 0.3; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-4px); }
}

/* 输入区 */
.input-area {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 18px;
  border-top: 1px solid var(--border-subtle);
}

.search-input {
  font-size: 0.9375rem;
  padding: 12px 16px;
  resize: none;
  line-height: 1.6;
  font-family: inherit;
}

.input-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.input-tip {
  font-size: 0.6875rem;
  color: var(--text-muted);
  font-family: 'JetBrains Mono', monospace;
}

.send-btn {
  flex-shrink: 0;
  padding: 10px 22px;
}
</style>
