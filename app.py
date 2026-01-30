import os
import sys
import time
import logging
import json
import requests
import threading
import webview  # 引入 pywebview
import socket
from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime
import urllib3

# 禁用 verify=False 产生的安全警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 核心路径处理函数 (关键) ---
def get_base_path():
    """获取运行时的基础路径，兼容 IDE 运行和 PyInstaller 打包后的 EXE"""
    if getattr(sys, 'frozen', False):
        # 如果是打包后的 EXE，资源在临时目录 sys._MEIPASS 中
        return sys._MEIPASS
    else:
        # 如果是脚本运行，资源在当前脚本所在目录
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_path()

# 定义前端资源目录 (打包时会把 web 文件夹放进去)
WEB_DIR = os.path.join(BASE_DIR, "web")

# 加载环境变量
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

# --- 修复控制台编码 ---
if sys.platform.startswith("win") and sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# --- 配置日志 ---
# 日志文件保存在 EXE 同级目录下，而不是临时目录，方便查看
exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else BASE_DIR
log_file = os.path.join(exe_dir, "app.log")

handlers_list = [logging.FileHandler(log_file, encoding="utf-8")]
if sys.stdout is not None:
    handlers_list.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
    handlers=handlers_list,
)

# --- 初始化配置和客户端 ---
api_key = os.getenv("ARK_API_KEY")
base_url = os.getenv("BASE_URL")
bot_id = os.getenv("BOT_ID")
grok_api_key = os.getenv("GROK_API_KEY")
grok_base_url = os.getenv("GROK_BASE_URL")
chatgpt_api_key = os.getenv("CHATGPT_API_KEY")
chatgpt_base_url = os.getenv("CHATGPT_BASE_URL")
chatgpt_proxy_base_url = os.getenv("CHATGPT_PROXY_BASE_URL")

client = OpenAI(base_url=base_url, api_key=api_key)
grok_client = OpenAI(api_key=grok_api_key, base_url=grok_base_url)
chatgpt_client = OpenAI(api_key=chatgpt_api_key, base_url=chatgpt_base_url)

# --- Flask 初始化 (修改部分) ---
# static_folder 指向 web 目录，static_url_path 设置为空，
# 这样前端请求 ./assets/xxx 就能直接映射到 web/assets/xxx
app = Flask(__name__, static_folder=WEB_DIR, static_url_path='')
CORS(app)

# --- 扣子配置 ---
COZE_WORKFLOW_ID = os.getenv("COZE_WORKFLOW_ID")
COZE_BASE_URL = os.getenv("COZE_BASE_URL")
COZE_CLIENT_TYPE = os.getenv("COZE_CLIENT_TYPE")
COZE_CLIENT_ID = os.getenv("COZE_CLIENT_ID")
COZE_COZE_WWW_BASE = os.getenv("COZE_COZE_WWW_BASE")
COZE_COZE_API_BASE = os.getenv("COZE_COZE_API_BASE")
COZE_PRIVATE_KEY_PATH = os.getenv("COZE_PRIVATE_KEY_PATH")
COZE_PUBLIC_KEY_ID = os.getenv("COZE_PUBLIC_KEY_ID")

# 临时文件目录 (使用系统临时目录或 EXE 同级目录，避免权限问题)
TEMP_DIR = os.path.join(exe_dir, "temp_files")
os.makedirs(TEMP_DIR, exist_ok=True)

# 加载扣子 JWT
from cozepy import load_oauth_app_from_config
coze_oauth_app = None

if all([COZE_CLIENT_TYPE, COZE_CLIENT_ID, COZE_COZE_WWW_BASE, COZE_COZE_API_BASE, COZE_PRIVATE_KEY_PATH, COZE_PUBLIC_KEY_ID]):
    try:
        # 修改：使用 BASE_DIR 确保打包后能找到 pem 文件
        private_key_path = os.path.join(BASE_DIR, COZE_PRIVATE_KEY_PATH)
        app.logger.info(f"正在加载密钥文件: {private_key_path}")
        
        with open(private_key_path, "r", encoding="utf-8") as f:
            COZE_PRIVATE_KEY = f.read()
        
        config = {
            "client_type": COZE_CLIENT_TYPE,
            "client_id": COZE_CLIENT_ID,
            "coze_www_base": COZE_COZE_WWW_BASE,
            "coze_api_base": COZE_COZE_API_BASE,
            "private_key": COZE_PRIVATE_KEY,
            "public_key_id": COZE_PUBLIC_KEY_ID
        }
        coze_oauth_app = load_oauth_app_from_config(config)
        app.logger.info("成功加载扣子JWT配置")
    except Exception as e:
        app.logger.error(f"加载扣子JWT配置失败: {str(e)}")

NO_PROXY = {"http": None, "https": None}
LOG_API_URL = "https://test.yuqing.cn/api/konne-ai-report/grok-log"
LOG_HEADERS = {
    "Host": "test.yuqing.cn",
    "referer": "http://10.1.0.41:8086/",
    "origin": "http://10.1.0.41:8086",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Python/3.12 Flask/3.0"
}

# --- 辅助函数 ---
def save_grok_log(model, req_content):
    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {"model": model, "req": req_content, "createdAt": current_time}
        resp = requests.post(LOG_API_URL, json=payload, headers=LOG_HEADERS, timeout=30, proxies=NO_PROXY, verify=False)
        return resp.json().get("data")
    except Exception as e:
        app.logger.error(f"创建日志失败: {e}")
        return None

def update_grok_log(log_id, response_content, usage=None, error_msg=None, is_success=True):
    if not log_id: return
    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {"completedAt": current_time}
        if is_success:
            payload["resp"] = response_content
            if usage:
                payload["promptTokens"] = usage.get("send_token_usage", 0)
                payload["completionTokens"] = usage.get("reply_token_usage", 0)
                payload["totalTokens"] = usage.get("total_tokens", 0)
        else:
            payload["resp"] = error_msg or "Unknown Error"
        requests.put(f"{LOG_API_URL}/{log_id}", json=payload, headers=LOG_HEADERS, timeout=30, proxies=NO_PROXY, verify=False)
    except Exception as e:
        app.logger.error(f"更新日志失败: {e}")

def load_common_system_prompt():
    try:
        # 修改：优先从 BASE_DIR (打包内部) 找，如果需要允许用户修改，可以改为 exe_dir
        # 这里假设 prompt.txt 是打包在 exe 内部的资源
        txt_path = os.path.join(exe_dir, "prompt.txt")
        if not os.path.exists(txt_path):
             # 备用：尝试从 exe 外部目录找
            txt_path = os.path.join(BASE_DIR, "prompt.txt")
            
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return "You are a helpful assistant." 
    except Exception as e:
        app.logger.error(f"读取 prompt.txt 失败: {e}")
        return "You are a helpful assistant."

def parse_temperature(value):
    try: return float(value) if 0 <= float(value) <= 2 else 1.0
    except: return 1.0

def parse_max_tokens(value):
    try: return int(value) if int(value) > 0 else 4096
    except: return 4096

# --- 路由定义 ---

# 新增：首页路由，返回 index.html
@app.route('/')
def index():
    return send_from_directory(WEB_DIR, 'index.html')

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}
    user_input = data.get("message", "").strip()
    if not user_input:
        app.logger.warning("收到空消息请求")  # 记录警告日志
        return jsonify({"error": "消息不能为空"}), 400

    temperature = parse_temperature(data.get("temperature", None))
    max_tokens = parse_max_tokens(data.get("max_tokens", None))

    messages = [{"role": "user", "content": user_input}]
    start_time = time.time()

    try:
        app.logger.info(f"处理用户请求: {user_input}")  # 记录信息日志
        resp = client.chat.completions.create(
            model=bot_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        elapsed_time = (time.time() - start_time) * 1000  # 转毫秒
        choice = resp.choices[0]
        message = choice.message
        assistant_reply = getattr(message, "content", "")
        thinking_process = getattr(message, "reasoning_content", "")
        send_token_usage = reply_token_usage = total_token_usage = 0
        if hasattr(resp, "bot_usage") and isinstance(resp.bot_usage, dict):
            model_usage_list = resp.bot_usage.get("model_usage", [])
            if isinstance(model_usage_list, list) and len(model_usage_list) > 0:
                first_usage = model_usage_list[0]
                if isinstance(first_usage, dict):
                    send_token_usage = first_usage.get("prompt_tokens", 0)
                    reply_token_usage = first_usage.get("completion_tokens", 0)
                    total_token_usage = first_usage.get("total_tokens", 0)

        app.logger.info(f"响应内容: {assistant_reply}")  # 记录响应内容
        return jsonify(
            {
                "reply": assistant_reply,
                "thinking_process": thinking_process,
                "token_usage": {
                    "send_token_usage": send_token_usage,
                    "reply_token_usage": reply_token_usage,
                    "total_token_usage": total_token_usage,
                },
                "response_time_ms": round(elapsed_time, 2),
            }
        )
    except Exception as e:
        app.logger.error(f"发生错误: {str(e)}")  # 记录错误日志
        return jsonify({"error": "服务器内部错误"}), 500

@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    data = request.json or {}
    user_input = data.get("message", "").strip()
    if not user_input:
        app.logger.warning("stream 接口收到空消息请求")
        return jsonify({"error": "消息不能为空"}), 400

    temperature = parse_temperature(data.get("temperature", None))
    max_tokens = parse_max_tokens(data.get("max_tokens", None))

    messages = [{"role": "user", "content": user_input}]
    stream_options = {"include_usage": True}
    model = bot_id

    app.logger.info(f"处理流式用户请求: {user_input}")

    def generate():
        start_time = time.time()
        try:
            resps = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options=stream_options,
            )
            for resp in resps:
                yield json.dumps(resp.model_dump()) + "\n"
            elapsed = round((time.time() - start_time) * 1000, 2)
            app.logger.info(f"流式请求处理完成，耗时: {elapsed} ms")
            yield json.dumps({"done": True, "response_time_ms": elapsed}) + "\n"
        except Exception as e:
            app.logger.error(f"流式接口发生错误: {str(e)}")
            yield json.dumps({"error": "服务器内部错误"}) + "\n"

    return Response(stream_with_context(generate()), mimetype="text/plain")


@app.route("/chat/grok", methods=["POST"])
def chat_grok():
    data = request.json or {}
    user_input = data.get("message", "").strip()
    if not user_input:
        app.logger.warning("Grok 接口收到空消息请求")
        return jsonify({"error": "消息不能为空"}), 400

    temperature = parse_temperature(data.get("temperature", None))
    max_tokens = parse_max_tokens(data.get("max_tokens", None))
    system_prompt = load_common_system_prompt()
    
    # 定义模型名称，方便日志记录
    model_name = "grok-4-1-fast-reasoning"

    start_time = time.time()
    app.logger.info(f"处理 Grok 用户请求: {user_input}")

    # 【新增 1】调用 API 前，先记录请求日志
    log_id = save_grok_log(model_name, user_input)
    app.logger.info(f"记录当前请求日志ID: {log_id}")

    try:
        # 构造消息格式：system + user
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        # 调用 Grok
        resp = grok_client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        elapsed_time = (time.time() - start_time) * 1000  # 转毫秒

        choice = resp.choices[0]
        message = choice.message
        assistant_reply = getattr(message, "content", "")

        # Token 使用
        usage = getattr(resp, "usage", None)
        if usage:
            send_token = getattr(usage, "prompt_tokens", 0)
            reply_token = getattr(usage, "completion_tokens", 0)
            total_token = getattr(usage, "total_tokens", 0)
        else:
            send_token = reply_token = total_token = 0
            
        token_usage_dict = {
            "send_token_usage": send_token,
            "reply_token_usage": reply_token,
            "total_tokens": total_token # 注意：这里为了方便传给日志函数，Key稍微做了适配
        }

        app.logger.info(f"Grok 响应内容: {assistant_reply}")

        # 【新增 2】调用成功，更新日志 (记录 Token 和回复)
        update_grok_log(
            log_id=log_id,
            response_content=assistant_reply,
            usage=token_usage_dict,
            is_success=True
        )
        app.logger.info(f"更新当前请求日志: {log_id}")

        return jsonify(
            {
                "reply": assistant_reply,
                "token_usage": {
                    "send_token_usage": send_token,
                    "reply_token_usage": reply_token,
                    "total_token_usage": total_token,
                },
                "response_time_ms": round(elapsed_time, 2),
            }
        )
    except Exception as e:
        error_msg = str(e)
        app.logger.error(f"Grok 接口发生错误: {error_msg}")
        
        # 【新增 3】发生异常，更新日志 (记录错误信息)
        update_grok_log(
            log_id=log_id,
            response_content=None,
            error_msg=error_msg,
            is_success=False
        )
        
        return jsonify({"error": "服务器内部错误"}), 500


@app.route("/chat/grok/test", methods=["GET"])
def test_grok():
    """前端用来测试 Grok 是否可用、网络是否连通的接口"""
    test_question = "hello"
    system_prompt = "You are Grok connectivity test assistant."

    if not grok_api_key:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "未检测到 XAI_API_KEY 环境变量，请检查后端配置。",
                }
            ),
            500,
        )

    start_time = time.time()
    app.logger.info("正在测试 Grok API 连通性...")

    try:
        resp = grok_client.chat.completions.create(
            model="grok-4-1-fast-reasoning",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": test_question},
            ],
        )

        elapsed_ms = round((time.time() - start_time) * 1000, 2)

        message = resp.choices[0].message
        assistant_reply = getattr(message, "content", "")

        usage = getattr(resp, "usage", None)
        if usage:
            send_token = getattr(usage, "prompt_tokens", 0)
            reply_token = getattr(usage, "completion_tokens", 0)
            total_token = getattr(usage, "total_tokens", 0)
        else:
            send_token = reply_token = total_token = 0

        app.logger.info(f"Grok 测试成功，延迟 {elapsed_ms} ms")

        return jsonify(
            {
                "success": True,
                "reply": assistant_reply,
                "latency_ms": elapsed_ms,
                "token_usage": {
                    "send_token_usage": send_token,
                    "reply_token_usage": reply_token,
                    "total_token_usage": total_token,
                },
            }
        )

    except Exception as e:
        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        error_msg = str(e)

        app.logger.error(f"Grok 测试失败（耗时 {elapsed_ms} ms）: {error_msg}")

        return (
            jsonify(
                {
                    "success": False,
                    "latency_ms": elapsed_ms,
                    "error": error_msg,
                }
            ),
            500,
        )

@app.route("/chat/chatgpt", methods=["POST"])
def chat_chatgpt():
    data = request.json or {}
    user_input = data.get("message", "").strip()
    if not user_input:
        app.logger.warning("ChatGPT 接口收到空消息请求")
        return jsonify({"error": "消息不能为空"}), 400

    temperature = parse_temperature(data.get("temperature", None))
    max_tokens = parse_max_tokens(data.get("max_tokens", None))
    system_prompt = load_common_system_prompt()

    # 构造系统+用户消息
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]

    start_time = time.time()
    app.logger.info(f"处理 ChatGPT 用户请求: {user_input}")

    try:
        # 这里改成你想调用的 ChatGPT 模型名称
        chatgpt_model = "gpt-4-turbo"

        resp = chatgpt_client.chat.completions.create(
            model=chatgpt_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        elapsed_time = (time.time() - start_time) * 1000  # 毫秒

        choice = resp.choices[0]
        message = choice.message
        assistant_reply = getattr(message, "content", "")

        usage = getattr(resp, "usage", None)
        if usage:
            send_token = getattr(usage, "prompt_tokens", 0)
            reply_token = getattr(usage, "completion_tokens", 0)
            total_token = getattr(usage, "total_tokens", 0)
        else:
            send_token = reply_token = total_token = 0

        app.logger.info(f"ChatGPT 响应内容: {assistant_reply}")

        return jsonify(
            {
                "reply": assistant_reply,
                "token_usage": {
                    "send_token_usage": send_token,
                    "reply_token_usage": reply_token,
                    "total_token_usage": total_token,
                },
                "response_time_ms": round(elapsed_time, 2),
            }
        )
    except Exception as e:
        app.logger.error(f"ChatGPT 接口发生错误: {str(e)}")
        return jsonify({"error": "服务器内部错误"}), 500


@app.route("/chat/chatgpt/test", methods=["GET"])
def test_chatgpt():
    """前端用来测试 ChatGPT 是否可用、网络是否连通的接口"""
    test_question = "hello"
    system_prompt = "You are ChatGPT connectivity test assistant."

    if not api_key:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "未检测到 CHATGPT_API_KEY 环境变量，请检查后端配置。",
                }
            ),
            500,
        )

    start_time = time.time()
    app.logger.info("正在测试 ChatGPT API 连通性...")

    try:
        chatgpt_model = "gpt-4-turbo"
        resp = chatgpt_client.chat.completions.create(
            model=chatgpt_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": test_question},
            ],
        )

        elapsed_ms = round((time.time() - start_time) * 1000, 2)

        message = resp.choices[0].message
        assistant_reply = getattr(message, "content", "")

        usage = getattr(resp, "usage", None)
        if usage:
            send_token = getattr(usage, "prompt_tokens", 0)
            reply_token = getattr(usage, "completion_tokens", 0)
            total_token = getattr(usage, "total_tokens", 0)
        else:
            send_token = reply_token = total_token = 0

        app.logger.info(f"ChatGPT 测试成功，延迟 {elapsed_ms} ms")

        return jsonify(
            {
                "success": True,
                "reply": assistant_reply,
                "latency_ms": elapsed_ms,
                "token_usage": {
                    "send_token_usage": send_token,
                    "reply_token_usage": reply_token,
                    "total_token_usage": total_token,
                },
            }
        )

    except Exception as e:
        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        error_msg = str(e)

        app.logger.error(f"ChatGPT 测试失败（耗时 {elapsed_ms} ms）: {error_msg}")

        return (
            jsonify(
                {
                    "success": False,
                    "latency_ms": elapsed_ms,
                    "error": error_msg,
                }
            ),
            500,
        )


import requests

@app.route("/chat/chatgpt/proxy", methods=["POST"])
def chat_chatgpt_proxy():
    data = request.json or {}
    user_input = data.get("message", "").strip()
    if not user_input:
        app.logger.warning("ChatGPT 代理接口收到空消息请求")
        return jsonify({"error": "消息不能为空"}), 400

    temperature = parse_temperature(data.get("temperature", None))
    max_tokens = parse_max_tokens(data.get("max_tokens", None))
    system_prompt = load_common_system_prompt()

    # 构造系统+用户消息
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]

    start_time = time.time()
    app.logger.info(f"处理 ChatGPT 代理用户请求: {user_input}")

    proxy_url = chatgpt_proxy_base_url.rstrip("/") + "/v1/chat/completions"

    try:
        # 构造请求体，根据你测试代码示例
        payload = {
            "model": "gpt-4-turbo",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # 如果需要开启流式，可加 "stream": True
        }

        headers = {
            "Content-Type": "application/json",
            # 如果代理接口需要API KEY，放这里，例如:
            # "Authorization": f"Bearer {your_proxy_api_key}"
        }

        resp = requests.post(proxy_url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        resp_json = resp.json()

        elapsed_time = (time.time() - start_time) * 1000  # 毫秒

        # 从返回结构中提取回答
        choices = resp_json.get("choices", [])
        if not choices:
            raise ValueError("代理接口返回无choices字段或为空")

        message = choices[0].get("message", {})
        assistant_reply = message.get("content", "")

        # token使用情况兼容处理
        usage = resp_json.get("usage", {})
        send_token = usage.get("prompt_tokens", 0)
        reply_token = usage.get("completion_tokens", 0)
        total_token = usage.get("total_tokens", 0)

        app.logger.info(f"ChatGPT 代理响应内容: {assistant_reply}")

        return jsonify(
            {
                "reply": assistant_reply,
                "token_usage": {
                    "send_token_usage": send_token,
                    "reply_token_usage": reply_token,
                    "total_token_usage": total_token,
                },
                "response_time_ms": round(elapsed_time, 2),
            }
        )

    except requests.exceptions.RequestException as e:
        app.logger.error(f"ChatGPT 代理接口请求错误: {str(e)}")
        return jsonify({"error": "代理服务器请求失败"}), 500
    except Exception as e:
        app.logger.error(f"ChatGPT 代理接口处理错误: {str(e)}")
        return jsonify({"error": "服务器内部错误"}), 500



@app.route("/chat/chatgpt/proxy/test", methods=["GET"])
def test_chatgpt_proxy():
    """前端用来测试 ChatGPT 代理是否可用、网络是否连通的接口"""
    test_question = "hello"
    system_prompt = "You are ChatGPT connectivity test assistant."

    if not chatgpt_proxy_base_url:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "未检测到 CHATGPT_PROXY_BASE_URL 环境变量，请检查后端配置。",
                }
            ),
            500,
        )

    start_time = time.time()
    app.logger.info("正在测试 ChatGPT 代理 API 连通性...")

    proxy_url = chatgpt_proxy_base_url.rstrip("/") + "/v1/chat/completions"

    try:
        payload = {
            "model": "gpt-4-turbo",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": test_question},
            ],
        }
        headers = {
            "Content-Type": "application/json",
        }

        resp = requests.post(proxy_url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        resp_json = resp.json()

        elapsed_ms = round((time.time() - start_time) * 1000, 2)

        choices = resp_json.get("choices", [])
        if not choices:
            raise ValueError("代理接口返回无choices字段或为空")

        message = choices[0].get("message", {})
        assistant_reply = message.get("content", "")

        usage = resp_json.get("usage", {})
        send_token = usage.get("prompt_tokens", 0)
        reply_token = usage.get("completion_tokens", 0)
        total_token = usage.get("total_tokens", 0)

        app.logger.info(f"ChatGPT 代理测试成功，延迟 {elapsed_ms} ms")

        return jsonify(
            {
                "success": True,
                "reply": assistant_reply,
                "latency_ms": elapsed_ms,
                "token_usage": {
                    "send_token_usage": send_token,
                    "reply_token_usage": reply_token,
                    "total_token_usage": total_token,
                },
            }
        )

    except requests.exceptions.RequestException as e:
        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        error_msg = str(e)
        app.logger.error(f"ChatGPT 代理测试请求失败（耗时 {elapsed_ms} ms）: {error_msg}")
        return (
            jsonify(
                {
                    "success": False,
                    "latency_ms": elapsed_ms,
                    "error": "代理服务器请求失败",
                }
            ),
            500,
        )
    except Exception as e:
        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        error_msg = str(e)
        app.logger.error(f"ChatGPT 代理测试处理失败（耗时 {elapsed_ms} ms）: {error_msg}")
        return (
            jsonify(
                {
                    "success": False,
                    "latency_ms": elapsed_ms,
                    "error": "服务器内部错误",
                }
            ),
            500,
        )


# --- 扣子工作流接口 ---
@app.route("/chat/upload", methods=["POST"])
def coze_upload():
    """
    调用扣子文件上传接口，获取file ID
    """
    try:
        # 获取上传的文件
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "未提供文件"}), 400
        
        # 检查文件类型
        if not (file.filename.endswith(".doc") or file.filename.endswith(".docx")):
            app.logger.warning("收到不支持的文件类型: %s", file.filename)
            return jsonify({"error": "仅支持 .doc 和 .docx 格式的文件"}), 400
        
        # 获取access_token
        access_token = None
        if coze_oauth_app:
            # 使用JWT获取access_token
            oauth_token = coze_oauth_app.get_access_token()
            access_token = oauth_token.access_token
            app.logger.info("使用JWT获取到access_token")
        else:
            # 只使用JWT认证，不使用API_KEY
            app.logger.error("未配置扣子JWT配置")
            return jsonify({"error": "未配置扣子JWT配置"}), 500
        
        # 确保COZE_BASE_URL没有任何不可见字符
        base_url = COZE_BASE_URL.strip()
        # 移除可能存在的尾部斜杠
        base_url = base_url.rstrip("/")
        
        app.logger.info("开始上传文件到扣子: %s", file.filename)
        
        # 调用扣子文件上传接口
        upload_url = f"{base_url}/files/upload"
        app.logger.info(f"扣子文件上传API URL: {upload_url}")
        
        # 准备文件上传请求
        upload_files = {
            "file": (file.filename, file.stream, file.mimetype)
        }
        
        upload_headers = {
            "Authorization": f"Bearer {access_token}"
        }
        
        # 发送文件上传请求
        upload_response = requests.post(
            upload_url,
            headers=upload_headers,
            files=upload_files,
            timeout=60
        )
        
        upload_response.raise_for_status()
        upload_result = upload_response.json()
        app.logger.info(f"文件上传响应: {upload_result}")
        
        # 提取file_id
        file_id = upload_result.get("data", {}).get("id")
        if not file_id:
            app.logger.error("文件上传失败，未返回file_id")
            return jsonify({"error": "文件上传失败，未返回file_id"}), 500
        
        app.logger.info(f"文件上传成功，获取到file_id: {file_id}")
        return jsonify({"file_id": file_id})
    except requests.exceptions.RequestException as e:
        # 捕获并记录更详细的错误信息
        error_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                # 尝试获取响应内容
                response_content = e.response.content.decode('utf-8')
                error_msg = f"{error_msg}\n响应内容: {response_content}"
            except Exception:
                # 如果无法解码响应内容，至少记录状态码
                error_msg = f"{error_msg}\n状态码: {e.response.status_code}"
        app.logger.error("扣子文件上传API请求失败: %s", error_msg)
        return jsonify({"error": f"文件上传失败: {str(e)}"}), 500
    except Exception as e:
        app.logger.error("处理文件上传请求时发生错误: %s", str(e))
        return jsonify({"error": f"服务器内部错误: {str(e)}"}), 500


@app.route("/chat/workflow", methods=["POST"])
def coze_workflow():
    """
    调用扣子工作流接口
    支持两种调用方式：
    1. 使用file_id和提供文本输入
    2. 直接提供文本输入
    """
    try:
        # 获取请求数据，支持JSON格式
        data = request.get_json() or {}
        
        # 获取file参数（已上传的文件ID）
        file_id = data.get("file")
        
        # 获取文本输入
        input_text = data.get("input", "").strip()
        
        # 获取access_token
        access_token = None
        if coze_oauth_app:
            # 使用JWT获取access_token
            oauth_token = coze_oauth_app.get_access_token()
            access_token = oauth_token.access_token
            app.logger.info("使用JWT获取到access_token")
        else:
            # 只使用JWT认证，不使用API_KEY
            app.logger.error("未配置扣子JWT配置")
            return jsonify({"error": "未配置扣子JWT配置"}), 500
        
        # 确保COZE_BASE_URL没有任何不可见字符
        base_url = COZE_BASE_URL.strip()
        # 移除可能存在的尾部斜杠
        base_url = base_url.rstrip("/")
        
        # 构建扣子工作流请求 - 使用流式接口
        workflow_url = f"{base_url}/workflow/stream_run"
        app.logger.info(f"扣子工作流API URL: {workflow_url}")
        
        # 准备请求数据
        # 如果有file_id，将其作为parameters的一部分
        if file_id:
            # 正确的格式：将file_id作为parameters的一个属性，值为包含file_id的JSON字符串
            parameters = {
                "file": json.dumps({"file_id": file_id}, ensure_ascii=False)
            }
        else:
            parameters = {
                "input": input_text
            }
        
        # 构造请求数据
        data = {
            "workflow_id": str(COZE_WORKFLOW_ID),  # 确保是字符串
            "parameters": json.dumps(parameters, ensure_ascii=False)
        }
        
        # 添加调试日志
        app.logger.info(f"请求参数: workflow_id={COZE_WORKFLOW_ID}, parameters={parameters}, file_id={file_id}")
        
        # 调用扣子工作流API - 始终使用application/json格式
        app.logger.info("调用扣子工作流流式API，输入: %s, 是否包含file_id: %s", input_text, bool(file_id))
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            workflow_url,
            headers=headers,
            json=data,  # 使用json参数，自动处理Content-Type
            stream=True,  # 启用流式响应
            timeout=60
        )
        
        # 处理响应状态
        response.raise_for_status()
        
        # 处理响应，解析返回的结果
        try:
            # 读取所有响应内容
            response_content = response.content.decode('utf-8')
            app.logger.info(f"响应内容: {response_content}")
            
            # 解析SSE格式的响应
            events = []
            current_event = {}
            
            for line in response_content.split('\n'):
                line = line.strip()
                if not line:
                    # 空行表示事件结束
                    if current_event:
                        events.append(current_event)
                        current_event = {}
                elif line.startswith('event: '):
                    current_event['event'] = line[7:]
                elif line.startswith('data: '):
                    if 'data' not in current_event:
                        current_event['data'] = []
                    current_event['data'].append(line[6:])
            
            # 处理所有事件
            for event in events:
                if event.get('event') == 'Message' and event.get('data'):
                    data_str = ''.join(event['data'])
                    try:
                        # 解析JSON
                        result = json.loads(data_str)
                        # 提取content字段
                        content_str = result.get('content', '')
                        app.logger.info(f"Content字段: {content_str}")
                        if content_str:
                            try:
                                # 解析content字段中的JSON
                                content = json.loads(content_str)
                                # 根据是否有file_id，返回不同的内容
                                if file_id:
                                    # 有文件上传，返回file_result
                                    file_result = content.get('file_result', '')
                                    if file_result:
                                        return jsonify({"result": file_result})
                                else:
                                    # 只有文本输入，返回result
                                    text_result = content.get('result', '')
                                    if text_result:
                                        return jsonify({"result": text_result})
                            except json.JSONDecodeError as e:
                                # 如果content不是JSON格式，直接返回
                                app.logger.error(f"解析content字段失败: {e}")
                                return jsonify({"result": content_str})
                    except json.JSONDecodeError as e:
                        app.logger.error(f"解析响应JSON失败: {e}")
                        return jsonify({"error": f"解析响应失败: {e}"}), 500
            
            # 如果没有找到有用的结果，返回空结果
            return jsonify({"result": ""})
        except Exception as e:
            app.logger.error("处理响应时发生错误: %s", str(e))
            return jsonify({"error": f"处理响应时发生错误: {str(e)}"}), 500
        finally:
            # 确保响应被关闭
            response.close()
    except requests.exceptions.RequestException as e:
        # 捕获并记录更详细的错误信息
        error_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                # 尝试获取响应内容
                response_content = e.response.content.decode('utf-8')
                error_msg = f"{error_msg}\n响应内容: {response_content}"
            except Exception:
                # 如果无法解码响应内容，至少记录状态码
                error_msg = f"{error_msg}\n状态码: {e.response.status_code}"
        app.logger.error("扣子工作流API请求失败: %s", error_msg)
        return jsonify({"error": f"调用扣子工作流失败: {str(e)}"}), 500
    except Exception as e:
        app.logger.error("处理扣子工作流请求时发生错误: %s", str(e))
        return jsonify({"error": f"服务器内部错误: {str(e)}"}), 500


# --- 这一块是用来填充上面省略的函数的，实际使用时，请把你的原函数体直接放回去 ---
# 下面我把你的原逻辑简单封装一下，确保你能直接运行
# *请务必将你原本的函数体完整粘贴回对应的路由下，不要使用下面的伪代码*

def original_chat_logic(req):
    # 这里放你原来的 chat 函数的代码
    data = req.json or {}
    user_input = data.get("message", "").strip()
    if not user_input: return jsonify({"error": "消息不能为空"}), 400
    temperature = parse_temperature(data.get("temperature", None))
    max_tokens = parse_max_tokens(data.get("max_tokens", None))
    messages = [{"role": "user", "content": user_input}]
    start_time = time.time()
    try:
        resp = client.chat.completions.create(model=bot_id, messages=messages, temperature=temperature, max_tokens=max_tokens)
        elapsed_time = (time.time() - start_time) * 1000
        choice = resp.choices[0]
        message = choice.message
        assistant_reply = getattr(message, "content", "")
        thinking_process = getattr(message, "reasoning_content", "")
        # ... token usage logic ...
        usage = getattr(resp, "bot_usage", {})
        # ... 简化处理 ...
        return jsonify({"reply": assistant_reply, "thinking_process": thinking_process, "response_time_ms": elapsed_time})
    except Exception as e:
        app.logger.error(str(e))
        return jsonify({"error": str(e)}), 500

# (请确保所有原来的路由函数逻辑都完整保留)
# ... 这里为了不重复刷屏，假设你已经把原来的函数逻辑都填好了 ...


# --- 启动逻辑 (修改部分) ---

# --- 辅助函数：获取空闲端口 ---
def get_free_port():
    """
    寻找一个未被占用的随机端口
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # 绑定到端口 0，系统会自动分配一个空闲端口
        s.bind(('127.0.0.1', 0))
        #以此获取分配的端口号
        return s.getsockname()[1]

# --- 启动逻辑 ---
def start_flask(port):
    """
    启动 Flask 服务，接收动态端口
    """
    # use_reloader=False 是必须的，否则打包后会报错
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    # 1. 获取一个随机空闲端口
    free_port = get_free_port()
    # print(f"正在启动服务，端口: {free_port}") # 调试用

    # 2. 启动 Flask 后台线程
    t = threading.Thread(target=start_flask, args=(free_port,))
    t.daemon = True
    t.start()

    # 3. 启动 PyWebview 窗口
    webview.create_window(
        title="文档校对助手",
        url=f"http://127.0.0.1:{free_port}",  # <--- 使用动态端口
        width=1800,
        height=800,
        resizable=True,
        min_size=(1280, 600)
    )
    
    # 5. 开始 GUI 循环
    webview.start()