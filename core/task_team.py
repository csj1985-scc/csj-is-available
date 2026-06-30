# -*- coding: utf-8 -*-
"""
任务团队调度系统 v0.1
根据任务类型自动匹配专业 Agent 团队，由 Team Lead 执行任务。
"""

import os
import re
import json
import time
from typing import Optional, List, Dict, Any, Tuple

import yaml


_TEAMS_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "prompts", "task_teams.yaml",
)

# 类型分类关键词
_TYPE_KEYWORDS = {
    "dev": {
        "code", "开发", "编程", "实现", "功能", "bug", "修复", "重构",
        "优化", "算法", "写代码", "改代码", "建项目", "脚手架",
        "测试用例", "单元测试", "python", "javascript", "sql", "git",
    },
    "frontend": {
        "前端", "界面", "ui", "交互", "页面", "样式", "布局", "组件",
        "vue", "react", "html", "css", "javascript", "桌面端",
    },
    "backend": {
        "后端", "服务端", "接口", "路由", "api", "数据库", "模型",
        "中间件", "认证", "权限", "存储",
    },
    "fullstack": {
        "全栈", "前后端", "完整功能", "完整实现",
    },
    "research": {
        "调研", "分析", "研究", "调查", "对比", "评估", "选型", "方案",
        "报告", "调查", "查", "搜索", "了解", "学习", "趋势", "行情",
        "竞品", "市场", "技术选型",
    },
    "writing": {
        "文档", "readme", "说明", "手册", "教程", "博客", "文章",
        "写文档", "写说明", "报告", "ppt", "大纲", "文案",
        "内容", "创作", "编辑", "翻译",
    },
    "ops": {
        "部署", "发布", "上线", "运维", "配置", "环境", "安装",
        "docker", "nginx", "服务器", "监控", "日志", "排查",
        "重启", "启动", "停止", "迁移", "备份",
    },
}


def classify_task(title: str, description: str = "") -> str:
    """根据任务标题和描述判断任务类型"""
    text = (title + " " + description).lower()
    scores = {}
    for ttype, keywords in _TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[ttype] = score
    if scores:
        return max(scores, key=scores.get)
    return "general"


def get_team_config(task_type: str) -> Optional[dict]:
    """获取指定任务类型的团队配置"""
    if not os.path.exists(_TEAMS_CONFIG_PATH):
        return None
    with open(_TEAMS_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("teams", {}).get(task_type)


def build_lead_system_prompt(task_type: str, task: dict) -> str:
    """构建 Team Lead 的系统提示词"""
    config = get_team_config(task_type)
    if not config:
        config = get_team_config("general")

    role_names = config.get("roles", [])
    lead_prompt = config.get("lead_prompt", "完成任务。")

    task_title = task.get("title", "")
    task_desc = task.get("description", "")

    prompt = (
        f"你是悟道的「{config.get('name', '执行团队')}负责人」。\n\n"
        f"{lead_prompt}\n\n"
        f"==========\n"
        f"【当前任务】\n"
        f"标题：{task_title}\n"
    )
    if task_desc:
        prompt += f"描述：{task_desc}\n"
    prompt += (
        f"任务ID：{task.get('id', 'unknown')}\n"
        f"\n"
        f"请开始执行这个任务。完成后用 task_update 更新状态为 completed "
        f"并在描述中记录执行结果。"
    )
    return prompt


def get_allowed_tools(task_type: str) -> list:
    """获取任务类型允许的工具列表"""
    config = get_team_config(task_type)
    if not config:
        config = get_team_config("general")
    return config.get("allowed_tools", [])
