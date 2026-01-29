"""LLM 客户端模块 - 带连接池、限流和重试机制"""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import logging

from ..config import get_config

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 调用错误"""
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


@dataclass
class LLMResponse:
    """LLM 响应数据类"""
    content: str
    model: str
    usage: dict[str, Any]
    raw_response: dict[str, Any]

    def parse_json(self) -> dict[str, Any]:
        """尝试将内容解析为 JSON"""
        try:
            return json.loads(self.content)
        except json.JSONDecodeError as e:
            # 尝试清理响应内容
            cleaned = self._clean_json_response(self.content)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                raise LLMError(f"无法解析 JSON 响应: {e}", response=self.content)

    @staticmethod
    def _clean_json_response(response: str) -> str:
        """清理 LLM 返回的 JSON 响应"""
        # 移除 markdown 代码块标记
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        elif response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        return response.strip()


class LLMClient:
    """LLM 客户端 - 支持连接池复用和并发控制"""

    _instance: Optional[LLMClient] = None
    _lock = threading.Lock()  # 使用threading.Lock而非asyncio.Lock

    def __new__(cls) -> LLMClient:
        """单例模式（线程安全）"""
        if cls._instance is None:
            with cls._lock:
                # 双重检查锁定
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self._initialized = True
        self._config = get_config().llm
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(self._config.max_concurrent)
        self._model_index = self._config.current_model_index

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建会话"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._config.timeout)
            connector = aiohttp.TCPConnector(
                limit=20,  # 连接池大小
                limit_per_host=10,  # 每主机连接数
                enable_cleanup_closed=True,
                force_close=False,
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._config.api_key}",
                },
            )
        return self._session

    async def close(self) -> None:
        """关闭客户端会话"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _get_current_model(self) -> str:
        """获取当前使用的模型"""
        if self._model_index < len(self._config.model_list):
            return self._config.model_list[self._model_index]
        self._model_index = 0
        return self._config.model_list[0]

    def _switch_to_next_model(self) -> None:
        """切换到下一个模型（故障转移）"""
        self._model_index = (self._model_index + 1) % len(self._config.model_list)
        logger.warning(f"切换到下一个模型: {self._get_current_model()}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def call(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        调用 LLM API

        Args:
            prompt: 用户提示词
            system_prompt: 系统提示词
            temperature: 温度参数（覆盖默认配置）
            max_tokens: 最大生成token数

        Returns:
            LLMResponse 对象

        Raises:
            LLMError: 调用失败时抛出
        """
        async with self._semaphore:  # 并发控制
            session = await self._get_session()

            model = self._get_current_model()
            temp = temperature if temperature is not None else self._config.temperature

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": model,
                "messages": messages,
                "temperature": temp,
            }
            if max_tokens:
                payload["max_tokens"] = max_tokens

            try:
                async with session.post(
                    self._config.api_url,
                    json=payload,
                ) as response:
                    if response.status == 429:  # Rate limit
                        raise LLMError(
                            "API 速率限制",
                            status_code=response.status,
                        )

                    response.raise_for_status()
                    data = await response.json()

                    if "choices" not in data or not data["choices"]:
                        raise LLMError(
                            "API 响应格式错误",
                            response=json.dumps(data),
                        )

                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})

                    return LLMResponse(
                        content=content,
                        model=model,
                        usage=usage,
                        raw_response=data,
                    )

            except aiohttp.ClientResponseError as e:
                if e.status in [500, 502, 503, 504]:
                    # 服务器错误，尝试切换模型
                    self._switch_to_next_model()
                raise LLMError(
                    f"HTTP 错误: {e.status}",
                    status_code=e.status,
                )
            except asyncio.TimeoutError:
                raise LLMError("请求超时")
            except Exception as e:
                raise LLMError(f"调用失败: {e}")

    async def call_with_fallback(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        调用 LLM，支持模型故障转移

        尝试所有配置的模型直到成功
        """
        last_error = None
        initial_model_index = self._model_index

        for _ in range(len(self._config.model_list)):
            try:
                return await self.call(prompt, system_prompt, temperature, max_tokens)
            except LLMError as e:
                last_error = e
                if e.status_code in [429, 500, 502, 503, 504]:
                    self._switch_to_next_model()
                else:
                    raise

        # 恢复初始模型索引
        self._model_index = initial_model_index
        raise last_error or LLMError("所有模型都调用失败")

    async def __aenter__(self) -> LLMClient:
        """异步上下文管理器入口"""
        await self._get_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """异步上下文管理器出口"""
        await self.close()
