# -*- coding: utf-8 -*-
"""
AI 聊天前端（Streamlit，云端单程序版）
- 不再依赖本地 Flask，直接调用 Coze v3 接口
- 配置全部来自环境变量：COZE_API_TOKEN、COZE_BOT_ID
- answer 显示在聊天气泡里，sources 以灰色小字显示在下方
"""
import os
import time

import requests
import streamlit as st
from dotenv import load_dotenv

# 本地开发时从同目录 .env 读取；云端直接在平台设置环境变量即可
load_dotenv()

# ---------- 基本设置 ----------
st.set_page_config(page_title="AI 聊天助手", layout="centered")

# ---------- 配置（全部来自环境变量） ----------
COZE_API_TOKEN = os.getenv("COZE_API_TOKEN", "").strip().strip('"').strip("'")
COZE_BOT_ID = os.getenv("COZE_BOT_ID", "").strip().strip('"').strip("'")
COZE_USER_ID = os.getenv("COZE_USER_ID", "streamlit_user").strip()

COZE_BASE_URL = "https://api.coze.cn"
POLL_TIMEOUT_SECONDS = 60  # 等待回答的最长秒数
POLL_INTERVAL_SECONDS = 1  # 每次轮询间隔

# 聊天记录保存在 session_state 中，页面交互时不会丢失
# 每条记录：{"role": "user"/"assistant", "content": 文本, "sources": [...]}
if "messages" not in st.session_state:
    st.session_state.messages = []

def _coze_headers():
    return {
        "Authorization": f"Bearer {COZE_API_TOKEN}",
        "Content-Type": "application/json",
    }

def _extract_sources(response_meta):
    """
    从消息的 response_meta.reference_info 中提取引用来源。
    每条形如：{"document_name": "xxx.pdf", "section_name": "第1章", "url": "..."}
    """
    if not isinstance(response_meta, dict):
        return []

    sources = []
    for ref in response_meta.get("reference_info") or []:
        if isinstance(ref, str):
            sources.append(ref)
            continue
        if not isinstance(ref, dict):
            continue

        doc_name = ref.get("document_name") or ref.get("doc_name") or ""
        section = ref.get("section_name") or ""
        url = ref.get("url") or ""

        text = " - ".join(p for p in (doc_name, section) if p)
        if url:
            text = f"{text}（{url}）" if text else url
        if text:
            sources.append(text)

    # 去重并保持顺序
    seen = set()
    unique = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique

def call_coze_api(question):
    """直接调用 Coze v3，返回 (answer, sources)"""

    # 第 1 步：发起对话（非流式，需要后续轮询）
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
        raise RuntimeError(f"发起对话失败：{result.get('msg', result)}")

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
            raise TimeoutError("等待 Coze 回答超时，请稍后重试")
        time.sleep(POLL_INTERVAL_SECONDS)

        r = requests.get(retrieve_url, headers=_coze_headers(), params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"查询状态失败：{data.get('msg', data)}")
        status = data["data"].get("status")

    if status != "completed":
        raise RuntimeError(f"对话未正常完成，当前状态：{status}")

    # 第 3 步：拉取本轮消息列表，取 assistant 的 answer
    msg_url = f"{COZE_BASE_URL}/v3/chat/message/list"
    r = requests.get(msg_url, headers=_coze_headers(), params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取消息失败：{data.get('msg', data)}")

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

def show_sources(sources):
    """用灰色小字把引用来源展示在回答下方"""
    if not sources:
        return
    st.caption("引用来源：")
    for i, src in enumerate(sources, start=1):
        st.caption(f"{i}. {src}")

# ---------- 启动配置检查 ----------
if not COZE_API_TOKEN or not COZE_BOT_ID:
    st.error(
        "缺少环境变量 COZE_API_TOKEN 或 COZE_BOT_ID。\n\n"
        "- 本地运行：在同目录 .env 文件中写入这两个变量；\n"
        "- 云端部署：在平台的「环境变量 / Secrets」配置页添加。"
    )
    st.stop()

st.title("AI 聊天助手")

# ---------- 渲染历史聊天记录 ----------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            show_sources(msg.get("sources", []))

# ---------- 底部输入框（自带发送按钮） ----------
if prompt := st.chat_input("请输入你的问题，回车或点发送按钮..."):
    # 1. 立即显示用户自己的消息
    st.session_state.messages.append({"role": "user", "content": prompt, "sources": []})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 2. 直接调用 Coze，显示 AI 回答
    with st.chat_message("assistant"):
        try:
            with st.spinner("AI 正在思考..."):
                answer, sources = call_coze_api(prompt)

            st.markdown(answer)        # 回答放在聊天气泡里
            show_sources(sources)      # 引用来源：灰色小字在下方

            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "sources": sources}
            )
        except requests.exceptions.ConnectionError:
            st.error("网络连接失败，请检查部署环境是否能访问 api.coze.cn。")
        except requests.exceptions.Timeout:
            st.error("请求超时，请稍后再试。")
        except requests.exceptions.HTTPError as e:
            status_code = getattr(e.response, "status_code", "?")
            st.error(f"Coze 返回 HTTP {status_code}，请检查 Token 和网络配置。")
        except Exception as e:
            st.error(f"请求出错：{e}")