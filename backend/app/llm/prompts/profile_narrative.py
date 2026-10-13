"""学生画像研判 prompt（SPEC §5 LLM 任务 2）。"""

from __future__ import annotations

import json

PROFILE_NARRATIVE_PROMPT_VER = "profile_narrative_v2"

PROFILE_NARRATIVE_SYSTEM = """你是高校就业辅导画像研判助手，当前 prompt 版本 profile_narrative_v2。只输出 JSON，不要解释。

硬性规则：
- narrative_text 必须是 2–3 句研判，每句都要有结论并标注证据编号，如 [E1]。
- suggestions 必须正好 3 条，每条建议必须以证据编号开头，如 [E7]，并与 evidence_facts 中的事实呼应。
- 建议必须具体到动作、数量或对象，例如“近30天投递偏低→本周补投3个本专业相关岗位”。
- 总体文字控制在 120–200 个中文字符；避免空泛口号。
- 无证据不得下结论，不得编造未给出的成绩、竞赛、家庭、健康或隐私信息。
- 禁止只复述预警等级；必须结合测评、求职行为、活动出席、专业与去向状态中的至少两类事实。
- 证据编号必须与 evidence 中的 type 对应：投递/面试/求职行为只能引用 job_action 或 job_action_summary；活动/出席/签到只能引用 activity 或 activity_summary；测评只能引用 assessment。
- 实际引用编号必须以用户 payload 中 citation_rules 与 citation_plan_hint 给出的真实编号为准，禁止编造或照抄无关编号。

输出 JSON 形状：
{
  "narrative_text": "[真实E编号]2-3句研判，每句必须带当前学生证据编号。",
  "suggestions": [
    "[真实E编号]具体动作1",
    "[真实E编号]具体动作2",
    "[真实E编号]具体动作3"
  ]
}
"""


def build_profile_narrative_user_prompt(
    *,
    student: dict,
    six_dims: dict[str, float],
    evidence_sources: list[dict],
    evidence_facts: dict,
) -> str:
    def ids_for(*source_types: str) -> list[str]:
        allowed = set(source_types)
        return [
            str(source["id"])
            for source in evidence_sources
            if str(source.get("type", "")) in allowed
        ]

    job_ids = ids_for("job_action_summary", "job_action")
    activity_ids = ids_for("activity_summary", "activity")
    assessment_ids = ids_for("assessment")
    student_ids = ids_for("student")
    degree_ids = ids_for("student_degree")
    direction_ids = ids_for("direction")

    def ref(ids: list[str]) -> str:
        return f"[{ids[0]}]" if ids else "[无可用编号]"

    payload = {
        "student": student,
        "six_dims": six_dims,
        "evidence_facts": evidence_facts,
        "evidence": evidence_sources,
        "citation_rules": {
            "投递/补投/面试/求职主动性": {
                "must_use_types": ["job_action", "job_action_summary"],
                "preferred_ids": job_ids,
            },
            "活动/出席/签到/就业指导": {
                "must_use_types": ["activity", "activity_summary"],
                "preferred_ids": activity_ids,
            },
            "测评/MBTI/霍兰德/自评": {
                "must_use_types": ["assessment"],
                "preferred_ids": assessment_ids,
            },
            "专业/去向/预警": {
                "must_use_types": ["student"],
                "preferred_ids": student_ids,
            },
            "学历": {
                "must_use_types": ["student_degree"],
                "preferred_ids": degree_ids,
            },
            "研究方向/专业方向（不用于测评结果）": {
                "must_use_types": ["direction"],
                "preferred_ids": direction_ids,
            },
        },
        "citation_plan_hint": {
            "研判_专业去向": f"{ref(student_ids)} 引用专业、去向、预警等学生基本事实",
            "研判_求职行为": f"{ref(job_ids)} 引用近30天投递、面试、补投、求职主动性",
            "研判_活动出席": f"{ref(activity_ids)} 引用活动出席、签到、就业指导活动",
            "建议_投递面试": f"{ref(job_ids)} 用于补投岗位、面试反馈等建议",
            "建议_活动": f"{ref(activity_ids)} 用于参加就业指导活动、签到等建议",
            "建议_测评": f"{ref(assessment_ids)} 用于测评、MBTI、霍兰德、自评相关建议",
            "建议_学历": f"{ref(degree_ids)} 用于学历层次相关判断",
            "建议_研究方向": f"{ref(direction_ids)} 用于研究方向或专业方向信息",
        },
    }
    return (
        "请基于以下画像分、结构化事实和证据编号输出 JSON：\n"
        f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    )
