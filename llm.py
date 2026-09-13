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
    """OpenAI 兼容的 LLM 客户端，同时支持 Claude Messages API."""

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 65536
    timeout: float = 120.0
    max_retries: int = 3
    api_format: str = ""  # "openai" or "claude", auto-detected if empty

    def __post_init__(self):
        if not self.base_url:
            self.base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        if not self.api_key:
            self.api_key = os.environ.get("LLM_API_KEY", "")
        if not self.model:
            self.model = os.environ.get("LLM_MODEL", "gpt-4o")
        if not self.api_format:
            self.api_format = os.environ.get("LLM_API_FORMAT", "")
        self.base_url = self.base_url.rstrip("/")
        # Auto-detect format from model name
        if not self.api_format:
            if "claude" in self.model.lower():
                self.api_format = "claude"
            else:
                self.api_format = "openai"

    def chat(self, messages: list[Message]) -> str:
        """
        发送多轮对话请求，返回助手回复文本。
        自动根据 api_format 选择 OpenAI 或 Claude Messages API。
        """
        if self.api_format == "claude":
            return self._chat_claude(messages)
        return self._chat_openai(messages)

    def _chat_claude(self, messages: list[Message]) -> str:
        """Claude Messages API (/v1/messages)."""
        url = f"{self.base_url}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

        # 提取 system message，剩余作为 messages
        system_text = ""
        conv_messages = []
        for m in messages:
            if m.role == "system":
                system_text += m.content + "\n"
            else:
                conv_messages.append({"role": m.role, "content": m.content})

        # Claude 要求 messages 非空且首条为 user
        if not conv_messages:
            conv_messages = [{"role": "user", "content": "hi"}]

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": conv_messages,
        }
        if system_text.strip():
            payload["system"] = system_text.strip()
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        try:
            for attempt in range(self.max_retries + 1):
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=self.timeout
                )
                resp.raise_for_status()
                data = resp.json()

                # Claude response: {"content": [{"type": "text", "text": "..."}], "stop_reason": "end_turn"}
                content_blocks = data.get("content", [])
                text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
                content = "\n".join(text_parts)
                stop_reason = data.get("stop_reason", "")

                if content.strip() and stop_reason in ("end_turn", "stop"):
                    return content

                if attempt < self.max_retries:
                    continue
                if content.strip():
                    return content
                raise RuntimeError(
                    f"Claude API returned empty content after {self.max_retries + 1} attempts "
                    f"(stop_reason={stop_reason})"
                )
            return content
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(
                f"Claude API error: {resp.status_code} — {resp.text[:500]}"
            ) from e
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            raise RuntimeError(f"Claude API connection failed: {e}") from e
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Claude API unexpected response format: {data}") from e

    def _chat_openai(self, messages: list[Message]) -> str:
        """OpenAI 兼容 API (/chat/completions)."""
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
            last_reason = ""
            for attempt in range(self.max_retries + 1):
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=self.timeout
                )
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                content = choice["message"].get("content") or ""
                finish = choice.get("finish_reason", "")

                if content.strip() and finish == "stop":
                    return content

                last_reason = f"finish_reason={finish}, content_len={len(content)}"
                if attempt < self.max_retries:
                    continue
                if content.strip():
                    return content
                raise RuntimeError(
                    f"LLM API returned empty content after {self.max_retries + 1} attempts "
                    f"({last_reason})"
                )
            return content
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