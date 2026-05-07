fn main() {
    // 加载 .env 文件（针对本地打包）
    dotenvy::dotenv().ok();
    let _ = dotenvy::from_path("../.env");

    // 需要注入到二进制中的变量列表
    let keys = [
        "ARK_API_KEY", "BASE_URL", "BOT_ID",
        "GROK_API_KEY", "GROK_BASE_URL",
        "CHATGPT_API_KEY", "CHATGPT_BASE_URL",
        "COZE_CLIENT_ID", "COZE_PUBLIC_KEY_ID", "COZE_WORKFLOW_ID",
        "COZE_PRIVATE_KEY_PATH"
    ];

    for key in keys {
        if let Ok(val) = std::env::var(key) {
            println!("cargo:rustc-env={}={}", key, val);
        }
    }

    // 特殊处理 PEM 内容注入
    let private_key_filename = std::env::var("COZE_PRIVATE_KEY_PATH").unwrap_or_else(|_| "coze_private_key.pem".to_string());
    let mut pem_path = std::path::PathBuf::from(&private_key_filename);
    if !pem_path.exists() {
        let parent = std::path::PathBuf::from("..").join(&private_key_filename);
        if parent.exists() { pem_path = parent; }
    }
    
    if let Ok(content) = std::fs::read_to_string(pem_path) {
        use base64::{Engine as _, engine::general_purpose};
        let b64 = general_purpose::STANDARD.encode(content);
        println!("cargo:rustc-env=COZE_PRIVATE_KEY_B64={}", b64);
    }

    tauri_build::build()
}
