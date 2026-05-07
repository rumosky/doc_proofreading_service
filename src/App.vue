<script setup lang="ts">
import { ref, onMounted, nextTick, computed, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { getVersion } from '@tauri-apps/api/app'
import { open } from '@tauri-apps/plugin-dialog'
import { marked } from 'marked'

interface Message {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  loading?: boolean
  displayedThinkingProcess?: string
  displayedContent?: string
  token_info?: {
    reply_token_usage: number
  }
  response_time_ms?: number
}

const messages = ref<Message[]>([
  {
    role: 'assistant',
    content: '欢迎使用文档校对助手！请在右侧输入需要校对的内容或上传文档进行校验。',
    timestamp: new Date().toLocaleTimeString()
  }
])
const userInput = ref('')
const isSending = ref(false)
const chatHistoryRef = ref<HTMLElement | null>(null)
const currentYear = new Date().getFullYear()
const externalModelType = ref('doubao')
const temperature = ref(1.0)
const maxTokens = ref(4096)
const showSettingsDialog = ref(false)

// 文件上传相关
const selectedFileName = ref('')
const uploadedFileId = ref('')
const isUploading = ref(false)
const appVersion = ref('')

onMounted(async () => {
  try {
    appVersion.value = await getVersion()
  } catch (err) {
    console.error('Failed to get version:', err)
  }
})

const modelTypeOptions = [
  { label: '豆包', value: 'doubao' },
  { label: 'ChatGPT', value: 'chatgpt' },
  // { label: 'Grok', value: 'grok' },
]

const scrollToBottom = async () => {
  await nextTick()
  if (chatHistoryRef.value) {
    chatHistoryRef.value.scrollTop = chatHistoryRef.value.scrollHeight
  }
}

const formatMessageContent = (content: string) => {
  return marked.parse(content, { gfm: true, breaks: true })
}

const formatResponseTime = (ms: number | undefined) => {
  if (!ms || ms <= 0) return '0秒'
  const totalSeconds = Math.round(ms / 1000)
  if (totalSeconds < 60) return `${totalSeconds}秒`
  return `${Math.floor(totalSeconds / 60)}分${totalSeconds % 60}秒`
}

// 选择并上传文件
const handleFileUpload = async () => {
  try {
    const selected = await open({
      multiple: false,
      filters: [{
        name: 'Word Documents',
        extensions: ['doc', 'docx']
      }]
    })

    if (selected && typeof selected === 'string') {
      isUploading.value = true
      selectedFileName.value = selected.split(/[\\/]/).pop() || ''
      
      // 调用 Rust 后端上传
      const fileId: string = await invoke('upload_file', { path: selected })
      uploadedFileId.value = fileId
      ElMessage.success('文件上传成功')
    }
  } catch (err) {
    ElMessage.error(`文件处理失败: ${err}`)
    selectedFileName.value = ''
  } finally {
    isUploading.value = false
  }
}

const removeFile = () => {
  selectedFileName.value = ''
  uploadedFileId.value = ''
}

const sendMessage = async () => {
  if (!userInput.value.trim() && !uploadedFileId.value) return
  if (isSending.value) return

  const userMsg = userInput.value.trim()
  const timestamp = new Date().toLocaleTimeString()
  
  // 逻辑：如果上传了文件，显示消息为文件提示，否则显示文本
  const displayContent = (externalModelType.value === 'doubao' && uploadedFileId.value) 
    ? `[文件校对申请: ${selectedFileName.value}]` 
    : userMsg;

  messages.value.push({
    role: 'user',
    content: displayContent,
    timestamp
  })
  
  userInput.value = ''
  isSending.value = true
  
  const assistantMsg: Message = {
    role: 'assistant',
    content: '',
    timestamp: new Date().toLocaleTimeString(),
    loading: true
  }
  messages.value.push(assistantMsg)
  await scrollToBottom()

  try {
    let response: any
    
    // 逻辑切换：如果有文件，走工作流（只传文件）；否则走普通对话
    if (externalModelType.value === 'doubao' && uploadedFileId.value) {
      console.log('检测到文件，调用工作流...');
      response = await invoke('coze_workflow', {
        input: '', // 有文件时按要求不传文本
        fileId: uploadedFileId.value
      })
    } else {
      console.log('普通文本对话...');
      response = await invoke('chat', {
        request: {
          message: userMsg,
          temperature: temperature.value,
          max_tokens: maxTokens.value,
          model_type: externalModelType.value
        }
      })
    }

    assistantMsg.loading = false
    assistantMsg.content = response.reply
    assistantMsg.displayedContent = response.reply
    assistantMsg.displayedThinkingProcess = response.thinking_process
    assistantMsg.token_info = {
      reply_token_usage: response.token_usage?.reply_token_usage || 0
    }
    assistantMsg.response_time_ms = response.response_time_ms
    
    // 发送成功后清除已选文件
    if (uploadedFileId.value) removeFile()

  } catch (err: any) {
    assistantMsg.loading = false
    assistantMsg.content = `错误: ${err}`
    assistantMsg.displayedContent = `错误: ${err}`
    ElMessage.error(`请求失败: ${err}`)
  } finally {
    isSending.value = false
    await scrollToBottom()
  }
}

const isTesting = ref(false)

const testConnectivity = async () => {
  if (isTesting.value) return
  isTesting.value = true
  
  try {
    const success = await invoke('test_connectivity', { modelType: externalModelType.value })
    if (success) {
      ElMessage.success({
        message: `${externalModelType.value} 模型连接成功！API 服务运行正常。`,
        duration: 3000
      })
    }
  } catch (err: any) {
    const errMsg = String(err)
    let userFriendlyMsg = '连接失败：未知错误'

    if (errMsg.includes('401') || errMsg.includes('key not found') || errMsg.includes('invalid')) {
      userFriendlyMsg = '鉴权失败：请检查 .env 文件中的 API Key 是否填写正确。'
    } else if (errMsg.includes('404') || errMsg.includes('not found')) {
      userFriendlyMsg = '连接失败：找不到接口地址，请检查 BASE_URL 或模型 ID。'
    } else if (errMsg.includes('timeout') || errMsg.includes('Request failed')) {
      userFriendlyMsg = '网络超时：无法连接到 AI 服务器，请检查您的网络连接。'
    } else if (errMsg.includes('429')) {
      userFriendlyMsg = '请求受限：由于请求过于频繁，API 已暂时限制访问。'
    } else {
      userFriendlyMsg = `服务异常：${errMsg}`
    }

    ElMessage.error({
      message: userFriendlyMsg,
      duration: 5000
    })
  } finally {
    isTesting.value = false
  }
}

const clearChat = () => {
  ElMessageBox.confirm('确定要清空聊天记录吗？', '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning'
  }).then(() => {
    // 保留第一条欢迎语
    if (messages.value.length > 1) {
      messages.value = [messages.value[0]]
    }
    ElMessage.success('已重置对话')
  })
}

const onTextareaKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

watch(externalModelType, () => {
  removeFile() // 切换模型时清除文件
})
</script>

<template>
  <div id="app-container">
    <header>
      <h1 class="logo-title">文档校对助手 <span class="badge">v{{ appVersion }}</span></h1>
    </header>
    <main>
      <section class="chat-history" ref="chatHistoryRef">
        <div v-for="(msg, idx) in messages" :key="idx" class="message-wrapper" :class="msg.role">
          <!-- 头像展示 -->
          <div class="avatar-container">
            <img :src="msg.role === 'assistant' ? '/ai.svg' : '/user.svg'" class="avatar" />
          </div>
          
          <div class="message" :class="msg.role + '-message'">
            <div class="message-header">{{ msg.role === 'user' ? '您' : '助手' }} - {{ msg.timestamp }}</div>
            <div v-if="msg.loading">
              <span class="loading-spinner"></span>
              <span class="loading-text">正在思考...</span>
            </div>
            <template v-else>
              <blockquote v-if="msg.displayedThinkingProcess" class="thinking-process">
                {{ msg.displayedThinkingProcess }}
              </blockquote>
              <div class="content" v-html="formatMessageContent(msg.displayedContent || msg.content)"></div>
            </template>
            
            <div v-if="msg.role === 'assistant' && !msg.loading" class="token-info">
              响应时间: {{ formatResponseTime(msg.response_time_ms) }}
            </div>
          </div>
        </div>
      </section>

      <section class="right-panel">
        <textarea
          v-model="userInput"
          placeholder="请输入您的内容... (Enter 发送, Shift+Enter 换行)"
          @keydown="onTextareaKeydown"
        ></textarea>
        
        <!-- 只有豆包模型显示上传按钮 -->
        <div v-if="externalModelType === 'doubao'" class="upload-section">
          <div v-if="!selectedFileName" class="upload-placeholder" @click="handleFileUpload">
            <el-icon><Upload /></el-icon>
            <span>{{ isUploading ? '正在上传...' : '点击上传文档 (.doc/.docx)' }}</span>
          </div>
          <div v-else class="file-card">
            <el-icon><Document /></el-icon>
            <span class="file-name">{{ selectedFileName }}</span>
            <el-button type="danger" link icon="Close" @click="removeFile" />
          </div>
        </div>

        <div class="control-group">
          <div class="left">
            <el-select v-model="externalModelType" placeholder="选择模型" style="width: 150px">
              <el-option v-for="item in modelTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <el-button 
              type="success" 
              link 
              :loading="isTesting" 
              @click="testConnectivity" 
              style="margin-left: 10px"
            >
              {{ isTesting ? '正在测试...' : '测试连接' }}
            </el-button>
          </div>
          <div class="right">
            <el-button @click="showSettingsDialog = true" circle icon="Setting" />
            <el-button type="warning" @click="clearChat" circle icon="Delete" />
            <el-button type="primary" :loading="isSending || isUploading" @click="sendMessage">
              发送 <el-icon class="el-icon--right"><Position /></el-icon>
            </el-button>
          </div>
        </div>
      </section>
    </main>

    <el-dialog v-model="showSettingsDialog" title="参数配置" width="400px" align-center>
      <div class="setting-item">
        <div class="label">
          温度值: {{ temperature.toFixed(2) }}
          <el-tooltip content="控制生成文本的随机程度，值越大越随机" placement="top">
            <el-icon style="margin-left: 4px; vertical-align: middle; cursor: help;"><QuestionFilled /></el-icon>
          </el-tooltip>
        </div>
        <el-slider v-model="temperature" :min="0" :max="2" :step="0.1" />
      </div>
      <div class="setting-item">
        <div class="label">
          最大回复长度: {{ maxTokens }}
          <el-tooltip content="控制模型单次回复的最大长度。一般不用动，输入文本太长的时候建议直接拉满" placement="top">
            <el-icon style="margin-left: 4px; vertical-align: middle; cursor: help;"><QuestionFilled /></el-icon>
          </el-tooltip>
        </div>
        <el-slider v-model="maxTokens" :min="1024" :max="32768" :step="1024" />
      </div>
      <template #footer>
        <el-button type="primary" @click="showSettingsDialog = false">确定</el-button>
      </template>
    </el-dialog>

    <footer>
      Copyright © {{ currentYear }} 文档校对助手 | Powered by 李斌斌
    </footer>
  </div>
</template>

<style>
/* 全局样式修复，去除滚动条 */
html, body {
  margin: 0;
  padding: 0;
  overflow: hidden;
  height: 100vh;
  width: 100vw;
}

#app-container {
  height: 100vh;
  width: 100vw;
  display: flex;
  flex-direction: column;
  padding: 10px 20px;
  box-sizing: border-box;
  background: #f5f7fa;
  overflow: hidden;
}

header {
  text-align: center;
  margin-bottom: 15px;
}

.logo-title {
  font-size: 2em;
  font-weight: 700;
  color: #2c3e50;
  margin: 0;
}

.badge {
  font-size: 0.4em;
  background: #646cff;
  color: white;
  padding: 2px 8px;
  border-radius: 10px;
  vertical-align: middle;
}

main {
  flex: 1;
  display: flex;
  gap: 20px;
  overflow: hidden;
}

.chat-history {
  flex: 1;
  background: #fdfdfd;
  border-radius: 16px;
  padding: 24px;
  overflow-y: auto;
  box-shadow: inset 0 2px 10px rgba(0,0,0,0.02);
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.message-wrapper {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  width: 100%;
}

.message-wrapper.user {
  flex-direction: row-reverse;
}

.avatar-container {
  flex-shrink: 0;
  width: 42px;
  height: 42px;
  border-radius: 50%;
  overflow: hidden;
  background: white;
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px solid #fff;
}

.avatar {
  width: 85%;
  height: 85%;
  object-fit: contain;
}

.message {
  max-width: 75%;
  padding: 14px 18px;
  border-radius: 18px;
  position: relative;
  box-shadow: 0 4px 15px rgba(0,0,0,0.03);
  font-size: 14.5px;
  line-height: 1.6;
}

.user-message {
  background: linear-gradient(135deg, #007aff, #005bb5);
  color: white;
  border-top-right-radius: 4px;
}

.assistant-message {
  background: white;
  color: #2c3e50;
  border-top-left-radius: 4px;
  border: 1px solid #f0f0f0;
}

.message-header {
  font-size: 11px;
  margin-bottom: 8px;
  opacity: 0.7;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.user-message .message-header {
  color: rgba(255, 255, 255, 0.9);
  text-align: right;
}

.thinking-process {
  margin-bottom: 12px;
  padding: 10px 14px;
  background: #f1f3f5;
  border-left: 3px solid #007aff;
  font-size: 13px;
  color: #495057;
  border-radius: 6px;
  font-style: italic;
}

.token-info {
  font-size: 11px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid rgba(0,0,0,0.05);
  color: #909399;
  display: flex;
  justify-content: space-between;
}

.user-message .token-info {
  border-top-color: rgba(255,255,255,0.1);
  color: rgba(255,255,255,0.8);
}

.right-panel {
  width: 35%;
  min-width: 350px;
  display: flex;
  flex-direction: column;
  gap: 15px;
}

textarea {
  flex: 1;
  padding: 15px;
  border: 1px solid #dcdfe6;
  border-radius: 12px;
  resize: none;
  font-family: inherit;
  font-size: 14px;
  outline: none;
  transition: border-color 0.3s;
}

textarea:focus {
  border-color: #646cff;
}

/* 新增上传样式 */
.upload-section {
  border: 2px dashed #dcdfe6;
  border-radius: 12px;
  padding: 10px;
  transition: all 0.3s;
}

.upload-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #909399;
  cursor: pointer;
  font-size: 14px;
}

.upload-placeholder:hover {
  color: #646cff;
  border-color: #646cff;
}

.file-card {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #ecf5ff;
  padding: 5px 12px;
  border-radius: 8px;
  font-size: 14px;
  color: #409eff;
}

.file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.control-group {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.setting-item {
  margin-bottom: 20px;
}

.setting-item .label {
  margin-bottom: 8px;
  font-size: 14px;
  color: #606266;
}

.loading-spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid rgba(0,0,0,0.1);
  border-radius: 50%;
  border-top-color: #646cff;
  animation: spin 1s linear infinite;
  margin-right: 8px;
}

@keyframes spin { to { transform: rotate(360deg); } }

footer {
  text-align: center;
  padding: 10px;
  font-size: 0.8em;
  color: #909399;
}
</style>
