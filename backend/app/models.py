"""ResumeCraft 结构化数据模型。

md 经过解析后转换为这些 Pydantic 模型，作为「数据」与「样式」解耦的中间层。
前端 / 模板只消费这些模型，不直接解析 md。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Meta(BaseModel):
    """front matter 元信息：样式/模板/布局/头像等，不渲染进正文。"""
    template: str = "minimal"
    avatar: Optional[str] = None   # 照片/头像 URL 或相对路径
    theme: Dict[str, str] = Field(default_factory=dict)
    layout: str = "split"          # split 双栏 / full 单栏
    pdf: Dict[str, Any] = Field(default_factory=dict)
    title: Optional[str] = None


class Contact(BaseModel):
    """基本信息区字段，通用 kv 结构。"""
    name: Optional[str] = None
    role: Optional[str] = None
    fields: List[Dict[str, str]] = Field(default_factory=list)  # [{"label": "电话", "value": "138..."}]
    summary: Optional[str] = None


class Item(BaseModel):
    """一条经历/项目/教育条目。"""
    title: str = ""                # 主标题（公司/项目名/学校）
    subtitle: Optional[str] = None # 副标题（岗位/角色/专业）
    date: Optional[str] = None     # 时间
    tags: List[str] = Field(default_factory=list)  # 标签（如 负责人）
    points: List[str] = Field(default_factory=list) # 要点（问题→行动→结果）


class Section(BaseModel):
    """简历的一个区块。"""
    name: str = ""                 # 区块名，如 工作经历
    items: List[Item] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)  # 若为技能区
    # Markdown 表格块：每表为「行组」，每行为「单元格字符串列表」（首行为表头）
    tables: List[List[List[str]]] = Field(default_factory=list)


class Resume(BaseModel):
    """整份简历的完整结构化数据。"""
    meta: Meta = Field(default_factory=Meta)
    contact: Contact = Field(default_factory=Contact)
    sections: List[Section] = Field(default_factory=list)
