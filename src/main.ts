import { createApp } from 'vue'
import App from './App.vue'
import { 
  Upload, 
  Document, 
  Close, 
  Setting, 
  Delete, 
  Position, 
  QuestionFilled 
} from '@element-plus/icons-vue'

const app = createApp(App)

// 注册使用的图标
const icons = {
  Upload,
  Document,
  Close,
  Setting,
  Delete,
  Position,
  QuestionFilled
}

for (const [key, component] of Object.entries(icons)) {
  app.component(key, component)
}

app.mount('#app')
