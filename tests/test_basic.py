"""基础功能测试"""
import pytest
import asyncio
from pathlib import Path
import sys

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.config.loader import load_config_from_file
from core.game.models import Player, GameSession, PlayerStatus, GameStatus
from core.llm.client import LLMClient


class TestConfig:
    """配置测试"""
    
    def test_load_config(self):
        """测试配置加载"""
        config_path = project_root / "config.toml"
        if config_path.exists():
            config = load_config_from_file(str(config_path))
            assert config is not None
            assert config.plugin.config_version == "2.1.0"
            assert config.llm.max_concurrent > 0


class TestModels:
    """数据模型测试"""
    
    def test_player_creation(self):
        """测试玩家创建"""
        player = Player(player_id="test_user", name="测试玩家")
        assert player.player_id == "test_user"
        assert player.name == "测试玩家"
        assert player.status == PlayerStatus.ALIVE
        assert player.sanity == 100
        assert player.health == 100
    
    def test_game_session_creation(self):
        """测试游戏会话创建"""
        session = GameSession(
            group_id="test_group",
            scene_name="测试场景",
            game_mode="单人",
        )
        assert session.group_id == "test_group"
        assert session.scene_name == "测试场景"
        assert session.status == GameStatus.WAITING
        assert len(session.players) == 0
    
    def test_add_player_to_session(self):
        """测试添加玩家到会话"""
        session = GameSession(group_id="test_group", game_mode="单人")
        player = Player(player_id="test_user", name="测试玩家")
        
        result = session.add_player(player)
        assert result is True
        assert len(session.players) == 1
        assert "test_user" in session.players
    
    def test_remove_player_from_session(self):
        """测试从会话移除玩家"""
        session = GameSession(group_id="test_group", game_mode="单人")
        player = Player(player_id="test_user", name="测试玩家")
        
        session.add_player(player)
        result = session.remove_player("test_user")
        
        assert result is True
        assert len(session.players) == 0


class TestStateManager:
    """状态管理器测试"""
    
    @pytest.mark.asyncio
    async def test_state_manager_singleton(self):
        """测试状态管理器单例"""
        from core.game.state_manager import GameStateManager
        
        manager1 = GameStateManager()
        manager2 = GameStateManager()
        
        assert manager1 is manager2
    
    @pytest.mark.asyncio
    async def test_get_or_create_state(self):
        """测试获取或创建状态"""
        from core.game.state_manager import GameStateManager
        
        manager = GameStateManager()
        await manager.start()
        
        try:
            state = await manager.get_or_create("test_group")
            assert state is not None
            assert state.group_id == "test_group"
            state.release()
        finally:
            await manager.stop()


class TestSaveManager:
    """存档管理器测试"""
    
    @pytest.mark.asyncio
    async def test_save_and_load(self):
        """测试保存和加载"""
        from core.game.save_manager import SaveManager
        import tempfile
        import shutil
        
        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        
        try:
            manager = SaveManager(temp_dir)
            await manager.start()
            
            # 创建测试会话
            session = GameSession(
                group_id="test_group",
                scene_name="测试场景",
                game_mode="单人",
            )
            
            # 保存
            await manager.save_immediately("test_group", session)
            
            # 加载
            loaded = await manager.load("test_group")
            
            assert loaded is not None
            assert loaded.group_id == "test_group"
            assert loaded.scene_name == "测试场景"
            
            await manager.stop()
        finally:
            # 清理临时目录
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
