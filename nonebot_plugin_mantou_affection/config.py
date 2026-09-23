from pydantic import BaseModel, Field


class Config(BaseModel):
    """馒头好感度配置，所有配置均有默认值。"""

    mantou_affection_bot_name: str = "馒头"
    mantou_affection_timezone: str = "Asia/Shanghai"
    mantou_affection_text_path: str = ""
    mantou_affection_interaction_limit: int = Field(default=5, ge=1, le=50)
    mantou_affection_interaction_cooldown: int = Field(default=7200, ge=0, le=86400)
    mantou_affection_max: int = Field(default=999, ge=1, le=999999)
    mantou_affection_daily_gain_limit: int = Field(default=3, ge=1, le=999)
    mantou_affection_ranking_size: int = Field(default=10, ge=3, le=50)
    mantou_affection_link_enabled: bool = True
    mantou_affection_link_notify: bool = True
    mantou_affection_link_daily_limit: int = Field(default=10, ge=0, le=1000)
    mantou_affection_link_cooldown: int = Field(default=300, ge=0, le=86400)
    mantou_affection_ambient_enabled: bool = True
    mantou_affection_ambient_probability: float = Field(default=0.01, ge=0.0, le=1.0)
    mantou_affection_ambient_event_ratio: float = Field(default=0.333, ge=0.0, le=1.0)
    mantou_affection_event_timeout: int = Field(default=20, ge=3, le=120)
    mantou_affection_event_timeout_penalty: int = Field(default=5, ge=1, le=100)
    mantou_affection_poke_event_chance: float = Field(default=0.01, ge=0.0, le=1.0)
    mantou_affection_link_rewards: dict[str, int] = Field(
        default_factory=lambda: {
            "nonebot_plugin_taozi": 2,
            "nonebot_plugin_taozi_music": 2,
            "nonebot_plugin_xianmei": 1,
            "nonebot_plugin_daily_attendance": 2,
            "nonebot_plugin_crystelf": 1,
            "nonebot_plugin_chat_learning": 1,
            "nonebot_plugin_msg_rank_card": 1,
            "nonebot_plugin_bili_dynamic": 1,
            "nonebot_plugin_miao": 1,
        }
    )
