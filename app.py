import os
import time
import json
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("ARK_API_KEY")
base_url = os.getenv("BASE_URL")
bot_id = os.getenv("BOT_ID")

client = OpenAI(base_url=base_url, api_key=api_key)

app = Flask(__name__)
CORS(app)  # 允许所有跨域请求，前端 localhost 调试可用


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
        return jsonify({"error": "消息不能为空"}), 400

    temperature = parse_temperature(data.get("temperature", None))
    max_tokens = parse_max_tokens(data.get("max_tokens", None))

    messages = [{"role": "user", "content": user_input}]
    start_time = time.time()

    try:
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
        return jsonify({"error": "服务器内部错误"}), 500


@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    data = request.json or {}
    user_input = data.get("message", "").strip()
    if not user_input:
        return jsonify({"error": "消息不能为空"}), 400

    temperature = parse_temperature(data.get("temperature", None))
    max_tokens = parse_max_tokens(data.get("max_tokens", None))

    messages = [{"role": "user", "content": user_input}]
    stream_options = {"include_usage": True}
    model = bot_id

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
            elapsed_time = (time.time() - start_time) * 1000
            yield json.dumps(
                {"done": True, "response_time_ms": round(elapsed_time, 2)}
            ) + "\n"
        except Exception as e:
            yield json.dumps({"error": "服务器内部错误"}) + "\n"

    return Response(stream_with_context(generate()), mimetype="text/plain")


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
