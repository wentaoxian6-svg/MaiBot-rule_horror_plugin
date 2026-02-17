"""存档管理器 - 支持批量保存和自动清理"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import shutil
import threading
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from ...common.models import JsonObject
from .models import GameSession
from ..config import get_config

logger = logging.getLogger(__name__)


class SaveManager:
    """存档管理器 - 单例模式"""

    _instance: SaveManager | None = None
    _lock: threading.Lock = threading.Lock()  # 使用threading.Lock而非asyncio.Lock

    def __new__(cls, data_dir: str | None = None) -> SaveManager:
        """单例模式（线程安全）"""
        if cls._instance is None:
            with cls._lock:
                # 双重检查锁定
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, data_dir: str | None = None):
        if hasattr(self, "_initialized"):
            return

        self._initialized: bool = True

        if data_dir is None:
            # 默认数据目录
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            data_dir = os.path.join(base_dir, "data", "saves")

        self.data_dir: Path = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.save_dir: Path = self.data_dir  # 别名，兼容旧代码

        self._config = get_config().save
        # 改为使用deque存储多个版本，避免数据丢失
        self._pending_saves: dict[str, deque[tuple[datetime, GameSession]]] = {}
        self._save_lock: asyncio.Lock = asyncio.Lock()
        self._batch_task: asyncio.Task[None] | None = None
        self._running: bool = False

    async def start(self) -> None:
        """启动批量保存任务"""
        if not self._running:
            self._running = True
            self._batch_task = asyncio.create_task(self._batch_save_loop())
            logger.info("SaveManager 已启动")

    async def stop(self) -> None:
        """停止批量保存任务并刷新所有待保存数据"""
        if self._running:
            self._running = False
            if self._batch_task:
                self._batch_task.cancel()
                try:
                    await self._batch_task
                except asyncio.CancelledError:
                    pass
            # 刷新所有待保存数据
            await self._flush_all()
            logger.info("SaveManager 已停止")

    async def _batch_save_loop(self) -> None:
        """批量保存循环"""
        while self._running:
            try:
                await asyncio.sleep(self._config.batch_save_interval)
                await self._flush_pending()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"批量保存时出错: {e}")

    async def schedule_save(self, group_id: str, session: GameSession) -> None:
        """
        计划保存游戏会话（保留多个版本）

        Args:
            group_id: 群组ID
            session: 游戏会话
        """
        async with self._save_lock:
            if group_id not in self._pending_saves:
                self._pending_saves[group_id] = deque(maxlen=10)
            
            # 添加时间戳，保留最近10个版本
            self._pending_saves[group_id].append((datetime.now(), session))
            logger.debug(f"计划保存: {group_id} (队列长度: {len(self._pending_saves[group_id])})")

    async def save_immediately(self, group_id: str, session: GameSession) -> bool:
        """
        立即保存游戏会话

        Args:
            group_id: 群组ID
            session: 游戏会话

        Returns:
            是否保存成功
        """
        return await self._do_save(group_id, session)

    async def _do_save(self, group_id: str, session: GameSession) -> bool:
        """执行实际保存操作"""
        try:
            save_data = {
                "version": "2.1.0",
                "saved_at": datetime.now().isoformat(),
                "session": session.to_dict(),
            }

            # 构建文件路径
            save_path = self._get_save_path(group_id)
            temp_path = save_path.with_suffix(".tmp")

            # 写入临时文件
            json_str = json.dumps(save_data, ensure_ascii=False, indent=2)

            if self._config.compress_saves:
                # 压缩保存
                with gzip.open(temp_path, "wt", encoding="utf-8") as f:
                    f.write(json_str)
                final_path = save_path.with_suffix(".json.gz")
            else:
                # 普通保存
                with open(temp_path, "w", encoding="utf-8") as f:
                    f.write(json_str)
                final_path = save_path.with_suffix(".json")

            # 原子替换
            shutil.move(str(temp_path), str(final_path))

            logger.debug(f"保存成功: {group_id} -> {final_path}")
            return True

        except Exception as e:
            logger.error(f"保存失败 {group_id}: {e}")
            return False

    async def _flush_pending(self) -> None:
        """刷新待保存的数据（只保存最新版本）"""
        async with self._save_lock:
            pending = dict(self._pending_saves)
            self._pending_saves.clear()

        for group_id, sessions in pending.items():
            if sessions:
                # 只保存最新的版本
                _, latest_session = sessions[-1]
                await self._do_save(group_id, latest_session)
                logger.debug(f"批量保存: {group_id} (跳过了 {len(sessions)-1} 个中间版本)")

    async def _flush_all(self) -> None:
        """刷新所有待保存数据"""
        await self._flush_pending()

    def _get_save_path(self, group_id: str) -> Path:
        """获取存档文件路径"""
        # 对 group_id 进行安全处理
        safe_id = "".join(c for c in group_id if c.isalnum() or c in "-_")
        return self.data_dir / f"save_{safe_id}"

    async def load(self, group_id: str) -> GameSession | None:
        """
        加载游戏会话

        Args:
            group_id: 群组ID

        Returns:
            游戏会话或 None
        """
        save_path = self._get_save_path(group_id)

        # 尝试加载压缩存档
        gz_path = save_path.with_suffix(".json.gz")
        if gz_path.exists():
            try:
                with gzip.open(gz_path, "rt", encoding="utf-8") as f:
                    data = json.load(f)
                logger.debug(f"加载压缩存档: {group_id}")
                return GameSession.from_dict(data["session"])
            except Exception as e:
                logger.error(f"加载压缩存档失败 {group_id}: {e}")

        # 尝试加载普通存档
        json_path = save_path.with_suffix(".json")
        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.debug(f"加载普通存档: {group_id}")
                return GameSession.from_dict(data["session"])
            except Exception as e:
                logger.error(f"加载存档失败 {group_id}: {e}")

        return None

    async def delete(self, group_id: str) -> bool:
        """
        删除存档

        Args:
            group_id: 群组ID

        Returns:
            是否删除成功
        """
        save_path = self._get_save_path(group_id)
        deleted = False

        for ext in [".json.gz", ".json"]:
            file_path = save_path.with_suffix(ext)
            if file_path.exists():
                try:
                    file_path.unlink()
                    deleted = True
                    logger.info(f"删除存档: {group_id}")
                except Exception as e:
                    logger.error(f"删除存档失败 {group_id}: {e}")

        return deleted

    async def list_saves(self) -> list[JsonObject]:
        """列出所有存档（包含默认存档与命名存档）

        Returns:
            存档信息列表
        """
        saves: list[JsonObject] = []

        for file_path in self.data_dir.iterdir():
            if not file_path.is_file():
                continue
            if file_path.suffix not in [".json", ".gz"]:
                continue

            try:
                if file_path.suffix == ".gz":
                    with gzip.open(file_path, "rt", encoding="utf-8") as f:
                        data = json.load(f)
                else:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)

                session = data.get("session", {})
                name = data.get("name")

                # 未显式写入 name 的，按文件类型给一个友好的显示名
                if not name:
                    if file_path.name.startswith("save_"):
                        name = "默认存档"
                    elif file_path.name.startswith("named_"):
                        name = "未命名"

                players = session.get("players", {})
                player_names = [p.get("name", "未知") for p in players.values() if p.get("name")]
                player_ids = [p.get("player_id", "") for p in players.values() if p.get("player_id")]

                saves.append({
                    "group_id": session.get("group_id", "unknown"),
                    "scene_name": session.get("scene_name", "未知场景"),
                    "game_mode": session.get("game_mode", "未知"),
                    "status": session.get("status", "unknown"),
                    "saved_at": data.get("saved_at", "unknown"),
                    "player_count": len(players),
                    "player_names": player_names,
                    "player_ids": player_ids,
                    "name": name,
                    "file": file_path.name,
                    "is_named": file_path.name.startswith("named_"),
                })
            except Exception as e:
                logger.warning(f"读取存档信息失败 {file_path}: {e}")

        # saved_at 是 ISO 字符串，按字符串排序即可满足时间倒序
        return sorted(saves, key=lambda x: str(x.get("saved_at", "")), reverse=True)

    async def cleanup_old_saves(self, max_age_days: int = 30) -> int:
        """
        清理旧存档

        Args:
            max_age_days: 最大保留天数

        Returns:
            清理的存档数量
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        cleaned = 0

        for file_path in self.data_dir.iterdir():
            if file_path.suffix in [".json", ".gz"]:
                try:
                    mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if mtime < cutoff:
                        file_path.unlink()
                        cleaned += 1
                        logger.info(f"清理旧存档: {file_path.name}")
                except Exception as e:
                    logger.error(f"清理存档失败 {file_path}: {e}")

        return cleaned

    async def save_with_name(self, group_id: str, session: GameSession, name: str) -> bool:
        """使用指定名称保存存档（用于手动存档）"""
        try:
            save_data = {
                "version": "2.1.0",
                "saved_at": datetime.now().isoformat(),
                "name": name,
                "session": session.to_dict(),
            }

            # 构建文件路径（Windows/跨平台安全）
            safe_id = "".join(c for c in group_id if c.isalnum() or c in "-_")
            safe_name = "".join(c for c in name if c.isalnum() or c in "-_")
            save_path = self.data_dir / f"named_{safe_id}_{safe_name}.json"

            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)

            logger.info(f"手动存档成功: {group_id} -> {name}")
            return True

        except Exception as e:
            logger.error(f"手动存档失败 {group_id}: {e}")
            return False

    async def load_with_name(self, group_id: str, name: str) -> GameSession | None:
        """加载指定名称的存档"""
        safe_id = "".join(c for c in group_id if c.isalnum() or c in "-_")
        safe_name = "".join(c for c in name if c.isalnum() or c in "-_")
        save_path = self.data_dir / f"named_{safe_id}_{safe_name}.json"

        if not save_path.exists():
            return None

        try:
            with open(save_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return GameSession.from_dict(data["session"])
        except Exception as e:
            logger.error(f"加载命名存档失败 {group_id}/{name}: {e}")
            return None

    async def delete_with_name(self, group_id: str, name: str) -> bool:
        """删除指定名称的存档"""
        safe_id = "".join(c for c in group_id if c.isalnum() or c in "-_")
        safe_name = "".join(c for c in name if c.isalnum() or c in "-_")
        save_path = self.data_dir / f"named_{safe_id}_{safe_name}.json"

        if not save_path.exists():
            return False

        try:
            save_path.unlink()
            logger.info(f"删除命名存档: {group_id}/{name}")
            return True
        except Exception as e:
            logger.error(f"删除命名存档失败 {group_id}/{name}: {e}")
            return False

    async def cleanup_ended_saves(self, group_id: str | None = None) -> int:
        """清理已结束的存档

        Args:
            group_id: 仅清理该群组/用户的存档；None 表示清理全部

        Returns:
            清理的存档数量
        """
        cleaned = 0

        for file_path in list(self.data_dir.iterdir()):
            if not file_path.is_file():
                continue
            if file_path.suffix not in [".json", ".gz"]:
                continue

            try:
                if file_path.suffix == ".gz":
                    with gzip.open(file_path, "rt", encoding="utf-8") as f:
                        data = json.load(f)
                else:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)

                session = data.get("session", {})
                if group_id is not None and session.get("group_id") != group_id:
                    continue

                if session.get("status") != "ended":
                    continue

                file_path.unlink()
                cleaned += 1
                logger.info(f"清理已结束存档: {file_path.name}")

            except Exception as e:
                logger.warning(f"清理已结束存档失败 {file_path}: {e}")

        return cleaned
