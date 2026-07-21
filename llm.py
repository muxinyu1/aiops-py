"""
llm.py — LLM 接口封装

提供与 OpenAI 兼容 API 的交互能力，支持多轮对话。
可对接任意 OpenAI-compatible 接口（如 GPT-4、DeepSeek、Qwen 等）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

import requests


@dataclass
class Message:
    """单条对话消息."""
    role: str       # "system", "user", "assistant"
    content: str


@dataclass
class LLM:
    """OpenAI 兼容的 LLM 客户端."""

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: float = 120.0

    def __post_init__(self):
        # 支持从环境变量读取配置
        if not self.base_url:
            self.base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        if not self.api_key:
            self.api_key = os.environ.get("LLM_API_KEY", "")
        if not self.model:
            self.model = os.environ.get("LLM_MODEL", "gpt-4o")
        # 去掉末尾斜杠
        self.base_url = self.base_url.rstrip("/")

    def chat(self, messages: list[Message]) -> str:
        """
        发送多轮对话请求，返回助手回复文本。

        Args:
            messages: 对话消息列表 (system + user + assistant 交替)

        Returns:
            助手回复的文本内容

        Raises:
            RuntimeError: API 调用失败
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        try:
            resp = requests.post(
                url, headers=headers, json=payload, timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(
                f"LLM API error: {resp.status_code} — {resp.text[:500]}"
            ) from e
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            raise RuntimeError(f"LLM API connection failed: {e}") from e
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"LLM API unexpected response format: {data}") from e

    def make_response(self, system_prompt: str, user_prompt: str) -> str:
        """
        单轮对话的简化接口。

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户消息

        Returns:
            助手回复文本
        """
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        return self.chat(messages)
        return NotImplemented