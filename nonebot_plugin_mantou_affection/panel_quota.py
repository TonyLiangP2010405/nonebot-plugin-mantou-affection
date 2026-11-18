"""角色面板次数的按群账本。

与图像处理次数（``ImageQuotaStore``）共用同一套异步锁 + 原子替换写盘的实现，
单独落盘到 ``panel_quota.json``，结构为 ``{"version": 1, "groups": {group: {user: n}}}``。
"""
from __future__ import annotations

from .image_processing import ImageQuotaStore


class PanelQuotaStore(ImageQuotaStore):
    """按群、按群友记录角色面板次数。"""


class PanelRankQuotaStore(ImageQuotaStore):
    """按群、按群友记录群面板排名次数（与角色面板次数分开记账）。"""
