"""Mai Message 类型存根文件"""
from __future__ import annotations
from typing import Any, Optional

class UserInfo:
    """用户信息类"""
    def __init__(
        self,
        user_id: str,
        user_nickname: str = "",
        platform: str = "qq",
        **kwargs: Any
    ) -> None:
        self.user_id: str
        self.user_nickname: str
        self.platform: str

class GroupInfo:
    """群组信息类"""
    def __init__(
        self,
        group_id: str,
        group_name: str = "",
        platform: str = "qq",
        **kwargs: Any
    ) -> None:
        self.group_id: str
        self.group_name: str
        self.platform: str

class MessageBase:
    """消息基类"""
    def __init__(self, **kwargs: Any) -> None:
        self.message_info: MessageInfo
        self.user_info: UserInfo
        self.group_info: Optional[GroupInfo]
        self.raw_message: str

class MessageInfo:
    """消息信息类"""
    def __init__(self, **kwargs: Any) -> None:
        self.message_id: str
        self.time: float

class MessageSegment:
    """消息段类"""
    def __init__(self, type: str, data: dict[str, Any]) -> None:
        self.type: str
        self.data: dict[str, Any]

    @classmethod
    def text(cls, text: str) -> MessageSegment:
        ...

    @classmethod
    def image(cls, file: str, **kwargs: Any) -> MessageSegment:
        ...

class Seg:
    """消息段列表"""
    def __init__(self, segments: list[MessageSegment]) -> None:
        self.segments: list[MessageSegment]

    @classmethod
    def from_str(cls, text: str) -> Seg:
        ...

class Message:
    """消息类"""
    def __init__(self, **kwargs: Any) -> None:
        self.message_info: MessageInfo
        self.user_info: UserInfo
        self.group_info: Optional[GroupInfo]
        self.raw_message: str
        self.message_segment: Seg

class ChatStream:
    """聊天流类"""
    def __init__(self, **kwargs: Any) -> None:
        self.stream_id: str
        self.platform: str
        self.user_info: UserInfo
        self.group_info: Optional[GroupInfo]
