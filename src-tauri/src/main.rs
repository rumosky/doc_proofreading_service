// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::env;
use dotenvy::dotenv;
use reqwest::{Client, multipart};
use tauri::{command, State};
use std::path::PathBuf;
use jsonwebtoken::{encode, Algorithm, EncodingKey, Header};
use std::time::{SystemTime, UNIX_EPOCH};
use uuid::Uuid;
 
 macro_rules! get_env_var {
     ($name:literal) => {
         option_env!($name).map(|s| s.to_string()).or_else(|| std::env::var($name).ok())
     };
     ($name:literal, $default:expr) => {
         option_env!($name).map(|s| s.to_string()).or_else(|| std::env::var($name).ok()).unwrap_or_else(|| $default.to_string())
     };
 }


#[derive(Serialize, Deserialize, Clone)]
struct ChatRequest {
    message: String,
    temperature: f32,
    max_tokens: u32,
    model_type: String,
}

#[derive(Serialize, Deserialize)]
struct ChatResponse {
    reply: String,
    thinking_process: Option<String>,
    token_usage: TokenUsage,
    response_time_ms: u128,
}

#[derive(Serialize, Deserialize, Default)]
struct TokenUsage {
    send_token_usage: u32,
    reply_token_usage: u32,
    total_token_usage: u32,
}

struct AppState {
    client: Client,
}

#[command]
async fn chat(
    request: ChatRequest,
    state: State<'_, AppState>,
) -> Result<ChatResponse, String> {
    let start_time = std::time::Instant::now();
    
    let (api_key, base_url, model_id) = match request.model_type.as_str() {
        "doubao" => (
            get_env_var!("ARK_API_KEY").ok_or("ARK_API_KEY not found")?,
            get_env_var!("BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            get_env_var!("BOT_ID").ok_or("BOT_ID not found")?,
        ),
        "grok" => (
            get_env_var!("GROK_API_KEY").ok_or("GROK_API_KEY not found")?,
            get_env_var!("GROK_BASE_URL", "https://api.x.ai/v1"),
            "grok-4-1-fast-reasoning".to_string(),
        ),
        "chatgpt" => (
            get_env_var!("CHATGPT_API_KEY").ok_or("CHATGPT_API_KEY not found")?,
            get_env_var!("CHATGPT_BASE_URL", "https://api.openai.com/v1"),
            "gpt-4-turbo".to_string(),
        ),
        _ => return Err("Unsupported model type".to_string()),
    };

    let payload = serde_json::json!({
        "model": model_id,
        "messages": [{"role": "user", "content": request.message}],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    });

    let resp = state.client
        .post(format!("{}/chat/completions", base_url.trim_end_matches('/')))
        .header("Authorization", format!("Bearer {}", api_key))
        .json(&payload)
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;

    let json_resp: serde_json::Value = resp.json().await.map_err(|e| format!("Failed to parse response: {}", e))?;
    
    let reply = json_resp["choices"][0]["message"]["content"]
        .as_str()
        .unwrap_or("回复内容解析失败")
        .to_string();
        
    let thinking_process = json_resp["choices"][0]["message"]["reasoning_content"]
        .as_str()
        .map(|s| s.to_string());

    let usage = &json_resp["usage"];
    let token_usage = TokenUsage {
        send_token_usage: usage["prompt_tokens"].as_u64()
            .or_else(|| usage["input_tokens"].as_u64())
            .unwrap_or(0) as u32,
        reply_token_usage: usage["completion_tokens"].as_u64()
            .or_else(|| usage["output_tokens"].as_u64())
            .unwrap_or(0) as u32,
        total_token_usage: usage["total_tokens"].as_u64().unwrap_or(0) as u32,
    };

    Ok(ChatResponse {
        reply,
        thinking_process,
        token_usage,
        response_time_ms: start_time.elapsed().as_millis(),
    })
}

#[command]
async fn test_connectivity(model_type: String, state: State<'_, AppState>) -> Result<bool, String> {
    let req = ChatRequest {
        message: "hi".to_string(),
        temperature: 0.7,
        max_tokens: 10,
        model_type,
    };
    match chat(req, state).await {
        Ok(_) => Ok(true),
        Err(e) => Err(e),
    }
}

async fn fetch_coze_access_token(state: &Client) -> Result<String, String> {
    let jwt = generate_coze_jwt()?;
    let resp = state
        .post("https://api.coze.cn/api/permission/oauth2/token")
        .header("Content-Type", "application/json")
        .header("Authorization", format!("Bearer {}", jwt))
        .json(&serde_json::json!({
            "duration_seconds": 86399,
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer"
        }))
        .send()
        .await
        .map_err(|e| format!("OAuth request failed: {}", e))?;

    let json: serde_json::Value = resp.json().await.map_err(|e| format!("Failed to parse OAuth response: {}", e))?;
    if let Some(token) = json["access_token"].as_str() {
        Ok(token.to_string())
    } else {
        Err(format!("OAuth failed: {}", json["msg"].as_str().unwrap_or("Unknown error")))
    }
}

#[command]
async fn upload_file(path: String, state: State<'_, AppState>) -> Result<String, String> {
    let access_token = fetch_coze_access_token(&state.client).await?;
    let path_buf = PathBuf::from(&path);
    let file_name = path_buf.file_name().unwrap().to_string_lossy().to_string();
    let file_bytes = std::fs::read(&path_buf).map_err(|e| format!("Read file error: {}", e))?;

    let form = multipart::Form::new()
        .part("file", multipart::Part::bytes(file_bytes).file_name(file_name));

    let resp = state.client
        .post("https://api.coze.cn/v1/files/upload")
        .header("Authorization", format!("Bearer {}", access_token))
        .multipart(form)
        .send()
        .await
        .map_err(|e| format!("Upload failed: {}", e))?;

    let json: serde_json::Value = resp.json().await.map_err(|e| format!("Parse upload error: {}", e))?;
    json["data"]["id"]
        .as_str()
        .map(|s| s.to_string())
        .ok_or_else(|| format!("Upload failed: {:?}", json))
}

#[command]
async fn coze_workflow(input: String, file_id: String, state: State<'_, AppState>) -> Result<ChatResponse, String> {
    let start_time = std::time::Instant::now();
    let access_token = fetch_coze_access_token(&state.client).await?;
    let workflow_id = get_env_var!("COZE_WORKFLOW_ID").ok_or("COZE_WORKFLOW_ID not found")?;

    // 回退到流式接口要求的字符串化参数
    let parameters = if !file_id.is_empty() {
        serde_json::json!({
            "file": serde_json::to_string(&serde_json::json!({ "file_id": file_id })).unwrap()
        })
    } else {
        serde_json::json!({ "input": input })
    };

    let payload = serde_json::json!({
        "workflow_id": workflow_id,
        "parameters": serde_json::to_string(&parameters).unwrap() // 这里必须是字符串
    });

    // 切换回 stream_run
    let resp = state.client
        .post("https://api.coze.cn/v1/workflow/stream_run")
        .header("Authorization", format!("Bearer {}", access_token))
        .json(&payload)
        .send()
        .await
        .map_err(|e| format!("Workflow request failed: {}", e))?;

    // 读取字节流以避免解码错误
    let bytes = resp.bytes().await.map_err(|e| format!("Read stream bytes error: {}", e))?;
    let response_content = String::from_utf8_lossy(&bytes);
    
    let mut final_reply = String::new();
    let mut current_event = String::new();

    // 模拟原始 Python 的 SSE 解析逻辑
    for line in response_content.lines() {
        let line = line.trim();
        if line.is_empty() {
            current_event.clear();
            continue;
        }
        
        if line.starts_with("event:") {
            current_event = line[6..].trim().to_string();
        } else if line.starts_with("data:") && current_event == "Message" {
            let data_str = line[5..].trim();
            if let Ok(data_json) = serde_json::from_str::<serde_json::Value>(data_str) {
                if let Some(content_str) = data_json["content"].as_str() {
                    // 尝试从 content JSON 中提取结果 (与 Python 978/984 行逻辑一致)
                    if let Ok(content_json) = serde_json::from_str::<serde_json::Value>(content_str) {
                        if let Some(res) = content_json["file_result"].as_str().or(content_json["result"].as_str()) {
                            final_reply = res.to_string();
                        }
                    } else if !content_str.is_empty() {
                        final_reply = content_str.to_string();
                    }
                }
            }
        }
    }

    if final_reply.is_empty() {
        final_reply = "未获取到校对结果，请检查工作流配置。".to_string();
    }

    let token_usage = TokenUsage::default();
    // 注意：流式接口的 Token 统计通常在结束事件中，目前先保持默认值以保证编译通过

    Ok(ChatResponse {
        reply: final_reply,
        thinking_process: None,
        token_usage,
        response_time_ms: start_time.elapsed().as_millis(),
    })
}

fn generate_coze_jwt() -> Result<String, String> {
    let client_id = get_env_var!("COZE_CLIENT_ID").ok_or("COZE_CLIENT_ID not found")?;
    let private_key_filename = get_env_var!("COZE_PRIVATE_KEY_PATH", "coze_private_key.pem");
    let pub_key_id = get_env_var!("COZE_PUBLIC_KEY_ID").ok_or("COZE_PUBLIC_KEY_ID not found")?;

    let pem_content = if let Some(b64) = option_env!("COZE_PRIVATE_KEY_B64") {
        use base64::{Engine as _, engine::general_purpose};
        let bytes = general_purpose::STANDARD.decode(b64).map_err(|e| format!("PEM decode error: {}", e))?;
        String::from_utf8(bytes).map_err(|e| format!("PEM utf8 error: {}", e))?
    } else if let Some(content) = get_env_var!("COZE_PRIVATE_KEY_CONTENT") {
        content
    } else {
        let mut pem_path = PathBuf::from(&private_key_filename);
        if !pem_path.exists() {
            if let Ok(exe_path) = env::current_exe() {
                if let Some(exe_dir) = exe_path.parent() {
                    let p = exe_dir.join(&private_key_filename);
                    if p.exists() { pem_path = p; }
                }
            }
        }
        if !pem_path.exists() {
            let parent_path = PathBuf::from("..").join(&private_key_filename);
            if parent_path.exists() { pem_path = parent_path; }
        }
        std::fs::read_to_string(pem_path).map_err(|e| format!("Read PEM error: {}", e))?
    };
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
    
    let claims = serde_json::json!({
        "iss": client_id,
        "aud": "api.coze.cn",
        "iat": now,
        "exp": now + 3600,
        "jti": Uuid::new_v4().to_string(),
    });

    let mut header = Header::new(Algorithm::RS256);
    header.kid = Some(pub_key_id);
    header.typ = Some("JWT".to_string());

    let key = EncodingKey::from_rsa_pem(pem_content.as_bytes()).map_err(|e| format!("PEM error: {}", e))?;
    encode(&header, &claims, &key).map_err(|e| format!("JWT encode error: {}", e))
}

fn main() {
    if dotenv().is_err() {
        let _ = dotenvy::from_path("../.env");
    }
    let client = Client::builder().danger_accept_invalid_certs(true).build().expect("Failed to create HTTP client");

    tauri::Builder::default()
        .manage(AppState { client })
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![chat, test_connectivity, upload_file, coze_workflow])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
