import os
import sys
import time
import logging
import json
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv


def get_base_path():
    if getattr(sys, 'frozen', False):  # 打包后
        return sys._MEIPASS  # 临时解压目录，.env也在这里
    else:
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_path()

# 这里用绝对路径加载打包进去的 .env 文件
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

# 配置日志

log_file = os.path.join(BASE_DIR, "app.log")  # 日志文件路径
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别
    format="%(asctime)s [%(levelname)s]: %(message)s",  # 日志格式
    handlers=[
        logging.FileHandler(log_file),  # 输出到文件
        logging.StreamHandler(),  # 输出到控制台
    ],
)


api_key = os.getenv("ARK_API_KEY")
base_url = os.getenv("BASE_URL")
bot_id = os.getenv("BOT_ID")
grok_api_key = os.getenv("GROK_API_KEY")
grok_base_url = os.getenv("GROK_BASE_URL")
chatgpt_api_key = os.getenv("CHATGPT_API_KEY")
chatgpt_base_url = os.getenv("CHATGPT_BASE_URL")

client = OpenAI(base_url=base_url, api_key=api_key)
grok_client = OpenAI(api_key=grok_api_key, base_url=grok_base_url)
chatgpt_client = OpenAI(api_key=chatgpt_api_key, base_url=chatgpt_base_url)

app = Flask(__name__)
CORS(app)  # 允许所有跨域请求，前端 localhost 调试可用


def load_grok_system_prompt():
    try:
        # 这里用 exe 所在目录，而不是 BASE_DIR（避免取到 sys._MEIPASS）
        exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(exe_dir, "prompt.json")
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("grok_system_prompt", "")
    except Exception as e:
        app.logger.error(f"读取 grok_system_prompt 失败: {e}")
        return ""

def load_chatgpt_system_prompt():
    try:
        exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(exe_dir, "prompt.json")
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("chatgpt_system_prompt", "")
    except Exception as e:
        app.logger.error(f"读取 prompt.json 失败: {e}")
        return ""


def parse_temperature(value):
    try:
        temp = float(value)
        if 0 <= temp <= 2:
            return temp
    except (TypeError, ValueError):
        pass
    return 1.0  # 默认值


def parse_max_tokens(value):
    try:
        tokens = int(value)
        if tokens > 0:
            return tokens
    except (TypeError, ValueError):
        pass
    return 4096  # 默认值


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
    system_prompt = load_grok_system_prompt()

    start_time = time.time()
    app.logger.info(f"处理 Grok 用户请求: {user_input}")

    try:
        # 构造消息格式：system + user
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        # 调用 Grok（与官方示例一致）
        resp = grok_client.chat.completions.create(
            model="grok-4",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        elapsed_time = (time.time() - start_time) * 1000  # 转毫秒

        choice = resp.choices[0]
        message = choice.message
        assistant_reply = getattr(message, "content", "")

        # Token 使用（Grok 兼容 OpenAI 格式）
        usage = getattr(resp, "usage", None)
        if usage:
            send_token = getattr(usage, "prompt_tokens", 0)
            reply_token = getattr(usage, "completion_tokens", 0)
            total_token = getattr(usage, "total_tokens", 0)
        else:
            send_token = reply_token = total_token = 0

        app.logger.info(f"Grok 响应内容: {assistant_reply}")

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
        app.logger.error(f"Grok 接口发生错误: {str(e)}")
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
            model="grok-4",
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
    system_prompt = load_chatgpt_system_prompt()

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
    

# 调试开发
# if __name__ == "__main__":
#     app.run(debug=False, host="0.0.0.0", port=5000)
