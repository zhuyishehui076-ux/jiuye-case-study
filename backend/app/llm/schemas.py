"""LLM 输出 JSON Schema（SPEC §5：complete_json(schema, ...) 校验用）。

JD 解析输出 schema 对齐 job_parses 字段（§4），但**专业只让 LLM 抽取名称/关键词**，
专业代码由规则映射生成（§5 三段式，禁止 LLM 直接输出代码 —— 幻觉源）。
"""

from __future__ import annotations

# LLM 直接产出的结构（raw parse）。major_names/direction_keywords 供后续规则映射。
JD_PARSE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "degree_req",
        "major_names",
        "direction_keywords",
        "location",
        "salary_min",
        "salary_max",
        "headcount",
        "deadline",
    ],
    "properties": {
        # 本科/硕士/博士/不限 —— 取最低门槛（"本科以上/硕士优先"→本科）
        "degree_req": {"type": ["string", "null"], "enum": ["本科", "硕士", "博士", "不限", None]},
        "major_names": {"type": "array", "items": {"type": "string"}},
        "direction_keywords": {"type": "array", "items": {"type": "string"}},
        "location": {"type": ["string", "null"]},
        "salary_min": {"type": ["integer", "null"]},  # 单位：k/月
        "salary_max": {"type": ["integer", "null"]},
        "headcount": {"type": ["integer", "null"]},
        "deadline": {"type": ["string", "null"]},  # YYYY-MM-DD 或 null
    },
}


PROFILE_NARRATIVE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["narrative_text", "suggestions"],
    "properties": {
        "narrative_text": {"type": "string", "maxLength": 200},
        "suggestions": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"type": "string"},
        },
    },
}
