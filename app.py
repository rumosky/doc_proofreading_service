# 先打猴子补丁，必须放最顶部，最先执行
from gevent import monkey

monkey.patch_all()

import os
from flask import Flask, request, jsonify
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


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    user_input = data.get("message", "").strip()
    if not user_input:
        return jsonify({"error": "消息不能为空"}), 400

    messages = [{"role": "user", "content": user_input}]

    try:
        # 同步调用，不用stream，简化示例
        resp = client.chat.completions.create(
            model=bot_id,
            messages=messages,
            temperature=0.6,
            max_tokens=32768,
        )
        assistant_reply = resp.choices[0].message.content
        return jsonify({"reply": assistant_reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
