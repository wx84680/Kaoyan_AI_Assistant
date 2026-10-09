# -*- coding: utf-8 -*-
"""
AI 聊天前端（Streamlit）
- 底部输入框 + 发送按钮
- 点击发送后 POST 到本地 Flask：http://127.0.0.1:5000/api/chat
- answer 显示在聊天气泡里，sources 以灰色小字显示在下方
"""
import requests
import streamlit as st

# ---------- 基本设置 ----------
st.set_page_config(page_title="AI 聊天助手", layout="centered")
st.title("AI 聊天助手")

API_URL = "http://127.0.0.1:5000/api/chat"

# 聊天记录保存在 session_state 中，页面交互时不会丢失
# 每条记录：{"role": "user"/"assistant", "content": 文本, "sources": [...]}
if "messages" not in st.session_state:
    st.session_state.messages = []

def call_flask_api(question):
    """把问题 POST 给 Flask，返回 (answer, sources)"""
    resp = requests.post(API_URL, json={"question": question}, timeout=60)
    resp.raise_for_status()  # HTTP 状态码不是 2xx 时抛异常
    data = resp.json()

    # 后端约定返回 {"answer": "...", "sources": [...]}
    answer = data.get("answer") or "（接口没有返回回答内容）"
    sources = data.get("sources") or []
    if isinstance(sources, str):  # 兼容来源只有一个字符串的情况
        sources = [sources]
    return answer, sources

def show_sources(sources):
    """用灰色小字把引用来源展示在回答下方"""
    if not sources:
        return
    st.caption("引用来源：")
    for i, src in enumerate(sources, start=1):
        st.caption(f"{i}. {src}")

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

    # 2. 调用后端，显示 AI 回答
    with st.chat_message("assistant"):
        try:
            with st.spinner("AI 正在思考..."):
                answer, sources = call_flask_api(prompt)

            st.markdown(answer)        # 回答放在聊天气泡里
            show_sources(sources)      # 引用来源：灰色小字在下方

            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "sources": sources}
            )
        except requests.exceptions.ConnectionError:
            st.error("连不上后端，请确认 Flask 已启动：python server.py（127.0.0.1:5000）")
        except requests.exceptions.Timeout:
            st.error("请求超时，请稍后再试。")
        except requests.exceptions.HTTPError as e:
            # 后端返回了错误信息（如 question 为空、Coze 调用失败）
            try:
                detail = e.response.json().get("error", str(e))
            except Exception:
                detail = str(e)
            st.error(f"后端返回错误：{detail}")
        except Exception as e:
            st.error(f"请求出错：{e}")