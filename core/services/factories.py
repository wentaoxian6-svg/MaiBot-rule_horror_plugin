"""工厂协议 - 解耦 core 层与 commands 层。

通过 ``typing.Protocol`` 定义工厂接口，让 core 层不再反向依赖 commands 层的
具体实现。commands 层（``RuntimeSupportMixin``）实现这些 Protocol 的方法后，
core 层即可通过 Protocol 接口获取实例，避免对 commands 层私有工厂方法的反向
调用以及对应的类型忽略屏蔽。

设计要点：
- Protocol 为结构性子类型，commands 层无需显式继承，只要实现同名方法即可
- 单独定义 ``NPCSimulatorFactory`` / ``EventBusFactory`` / ``RuleMutationSystemFactory``
  便于按需注入；``RuntimeFactories`` 复合 Protocol 用于需要同时访问多种工厂的场景
  （如 ``GameState.start_npc_tick`` 需要 NPC 模拟器与事件总线）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    # 仅为类型检查器导入，避免运行时循环引用
    from ...systems.environment_evolution import EnvironmentEvolutionSystem
    from ...systems.rule_mutation_system import RuleMutationSystem
    from ..common.models import JsonObject
    from .event_bus import EventBus
    from .npc_simulator import NPCSimulator


class NPCSimulatorFactory(Protocol):
    """NPC 模拟器工厂协议。

    解耦 core 层对 commands 层 ``_get_or_create_npc_simulator`` 的反向依赖。
    实现方只需提供 ``get_or_create_npc_simulator`` 方法即可满足协议。
    """

    def get_or_create_npc_simulator(self) -> "NPCSimulator":
        """获取或创建 NPC 模拟器实例。"""
        ...


class EventBusFactory(Protocol):
    """事件总线工厂协议。

    解耦 core 层对 commands 层 ``_get_or_create_event_bus`` 的反向依赖。
    实现方只需提供 ``get_or_create_event_bus`` 方法即可满足协议。
    """

    def get_or_create_event_bus(self) -> "EventBus":
        """获取或创建事件总线实例。"""
        ...


class RuleMutationSystemFactory(Protocol):
    """规则变异系统工厂协议。

    解耦对 commands 层 ``_get_or_create_rule_mutation_system`` 的反向依赖。
    实现方只需提供 ``get_or_create_rule_mutation_system`` 方法即可满足协议。
    """

    def get_or_create_rule_mutation_system(self) -> "RuleMutationSystem":
        """获取或创建规则变异系统实例。"""
        ...


class EnvironmentSystemFactory(Protocol):
    """环境演化系统工厂协议。

    解耦对 commands 层 ``_get_or_create_environment_system`` 的反向依赖。
    实现方需提供 ``get_or_create_environment_system`` 方法即可满足协议。
    """

    def get_or_create_environment_system(
        self, game_states: dict[str, "JsonObject"]
    ) -> "EnvironmentEvolutionSystem":
        """获取或创建环境演化系统实例。

        Args:
            game_states: 按群组 ID 索引的游戏状态字典，用于环境演化的初始化与更新
        """
        ...


class RuntimeFactories(
    NPCSimulatorFactory,
    EventBusFactory,
    RuleMutationSystemFactory,
    EnvironmentSystemFactory,
    Protocol,
):
    """运行时工厂集合：同时提供 NPC 模拟器、事件总线、规则变异与环境演化系统。

    用于像 ``GameState.start_npc_tick`` 这类需要同时访问多种工厂的场景，
    调用方只需传入一个满足该复合 Protocol 的对象（如 ``RuleHorrorCommand`` 实例）。
    """
