# -*- coding: utf-8 -*-
"""
Flask 后端：接收前端问题 -> 调用 Coze 智能体 -> 返回 AI 回答和引用来源

接口：
    POST /api/chat
    请求体：{"question": "你的问题"}
    返回：  {"answer": "AI回答", "sources": ["来源1", "来源2"]}
"""
import os
import time

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

# 从同目录的 .env 文件加载环境变量（密钥放在 .env 里，不写进代码）
load_dotenv()

# ---------- 配置（全部来自环境变量） ----------
def _env(name, default=""):
    """读取环境变量，并去掉首尾空白、引号和误加的尾随逗号/分号"""
    value = os.getenv(name, default).strip().strip('"').strip("'")
    return value.rstrip(",;").strip()

COZE_API_TOKEN = _env("COZE_API_TOKEN")
COZE_BOT_ID = _env("COZE_BOT_ID", "7694629131199774783")
# user_id 是 Coze 要求的终端用户标识，自己起一个固定值即可
COZE_USER_ID = _env("COZE_USER_ID", "streamlit_user")

COZE_BASE_URL = "https://api.coze.cn"
# 轮询 Coze 结果的最长时间和间隔
POLL_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 1

app = Flask(__name__)

@app.after_request
def add_cors_headers(resp):
    """手动加跨域响应头，不依赖 flask-cors（前端浏览器直接调用时需要）"""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return resp

class CozeError(Exception):
    """调用 Coze 接口出错时抛出"""

def _coze_headers():
    return {
        "Authorization": f"Bearer {COZE_API_TOKEN}",
        "Content-Type": "application/json",
    }

def _extract_sources(response_meta):
    """
    从消息的 response_meta.reference_info 中提取引用来源。
    Coze 返回的每条形如：
        {"document_name": "xxx.pdf", "section_name": "第1章",
         "position": 2, "url": "https://...", "content": "..."}
    """
    if not isinstance(response_meta, dict):
        return []

    references = response_meta.get("reference_info") or []
    sources = []
    for ref in references:
        if isinstance(ref, str):
            sources.append(ref)
            continue
        if not isinstance(ref, dict):
            continue

        doc_name = ref.get("document_name") or ref.get("doc_name") or ""
        section = ref.get("section_name") or ""
        url = ref.get("url") or ""

        parts = " - ".join(p for p in (doc_name, section) if p)
        if url:
            parts = f"{parts}（{url}）" if parts else url
        if parts:
            sources.append(parts)

    # 去重并保持顺序
    seen = set()
    unique_sources = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            unique_sources.append(s)
    return unique_sources

def ask_coze(question):
    """向 Coze v3 接口发起对话，返回 (answer, sources)"""

    # 第 1 步：发起对话（非流式 stream=False，需要后续轮询）
    create_url = f"{COZE_BASE_URL}/v3/chat"
    payload = {
        "bot_id": COZE_BOT_ID,
        "user_id": COZE_USER_ID,
        "stream": False,
        "auto_save_history": True,
        "additional_messages": [
            {"role": "user", "content": question, "content_type": "text"}
        ],
    }

    resp = requests.post(create_url, headers=_coze_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise CozeError(f"发起对话失败：{result.get('msg', result)}")

    chat_data = result["data"]
    chat_id = chat_data["id"]
    conversation_id = chat_data["conversation_id"]

    # 第 2 步：轮询对话状态，直到 completed
    retrieve_url = f"{COZE_BASE_URL}/v3/chat/retrieve"
    params = {"chat_id": chat_id, "conversation_id": conversation_id}
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    status = chat_data.get("status", "in_progress")

    while status in ("in_progress", "created"):
        if time.time() > deadline:
            raise CozeError("等待 Coze 回答超时，请稍后重试")
        time.sleep(POLL_INTERVAL_SECONDS)

        r = requests.get(retrieve_url, headers=_coze_headers(), params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise CozeError(f"查询状态失败：{data.get('msg', data)}")
        status = data["data"].get("status")

    if status != "completed":
        raise CozeError(f"对话未正常完成，当前状态：{status}")

    # 第 3 步：拉取本轮消息列表，取 assistant 的 answer
    msg_url = f"{COZE_BASE_URL}/v3/chat/message/list"
    r = requests.get(msg_url, headers=_coze_headers(), params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise CozeError(f"获取消息失败：{data.get('msg', data)}")

    answer_parts = []
    sources = []
    for msg in data.get("data", []):
        if msg.get("role") == "assistant" and msg.get("type") == "answer":
            answer_parts.append(msg.get("content", ""))
            sources.extend(_extract_sources(msg.get("response_meta")))

    answer = "\n".join(p for p in answer_parts if p).strip()
    if not answer:
        answer = "（没有获取到 AI 回答）"
    return answer, sources

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question 字段不能为空"}), 400

    try:
        answer, sources = ask_coze(question)
        return jsonify({"answer": answer, "sources": sources})
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"调用 Coze 网络出错：{e}"}), 502
    except CozeError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:  # 兜底，保证前端一定收到 JSON
        return jsonify({"error": f"服务器内部错误：{e}"}), 500

@app.route("/", methods=["GET"])
def health():
    return "Flask 后端正在运行，聊天接口：POST /api/chat"

def main():
    # 启动时校验必填配置，缺了立刻给出明确提示
    if not COZE_API_TOKEN:
        print("=" * 60)
        print("缺少环境变量 COZE_API_TOKEN！")
        print("请在同目录 .env 文件中写入：COZE_API_TOKEN=pat_你的Token")
        print("=" * 60)
        raise SystemExit(1)

    print(f"配置检查通过，Bot ID：{COZE_BOT_ID}")
    print("启动 Flask：http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)

if __name__ == "__main__":
    main()