"""JD 解析编排 + 评测计分单测（SPEC §5）。LLM 输出用固定样本，评测另跑。"""

from __future__ import annotations

from app.llm.eval.jd_eval import (
    aggregate,
    degree_correct,
    location_correct,
    major_correct,
)
from app.llm.parsers.jd import map_major_names, parse_jd
from app.llm.provider import LLMParseError, Provider

# ---------------- 评分纯函数 ----------------


def test_degree_correct_none_is_buxian():
    assert degree_correct(None, "不限")
    assert degree_correct("本科", "本科")
    assert not degree_correct("硕士", "本科")


def test_major_correct_substring_hit():
    assert major_correct(["计算机"], ["计算机科学与技术"])
    assert major_correct(["软件工程"], ["软件工程"])
    assert not major_correct(["英语"], ["计算机科学与技术"])


def test_location_correct_region_and_city():
    assert location_correct("上海市", "上海")
    assert location_correct("长三角", "长三角")
    assert location_correct(None, None)
    assert not location_correct(None, "上海")


def test_aggregate_computes_accuracy_and_pass():
    scores = [
        {"degree": True, "major": True, "location": True},
        {"degree": True, "major": True, "location": False},
    ]
    m = aggregate(scores)
    assert m["n"] == 2
    assert m["degree_acc"] == 1.0
    assert m["location_acc"] == 0.5
    assert m["passed"] is False  # location 0.5 < 0.85


# ---------------- 规则映射 ----------------


def test_map_major_names_exact_and_substring():
    index = {"计算机科学与技术": "080901", "软件工程": "080902"}
    codes, matched = map_major_names(["计算机", "软件工程"], index)
    assert set(codes) == {"080901", "080902"}
    assert matched == 2


def test_map_major_names_no_hit():
    codes, matched = map_major_names(["不存在的专业"], {"英语": "050201"})
    assert codes == [] and matched == 0


# ---------------- parse_jd 编排（mock provider）----------------


class FixedProvider(Provider):
    name = "fixed"

    def __init__(self, payload: dict | None = None, raise_parse: bool = False) -> None:
        self.payload = payload
        self.raise_parse = raise_parse

    def _chat(self, system, user):  # noqa: ARG002
        raise NotImplementedError

    def complete_json(self, schema, system, user, retries=2):  # noqa: ARG002
        if self.raise_parse:
            raise LLMParseError("mock 解析失败")
        return self.payload

    def embed(self, texts):  # noqa: ARG002
        return []


def test_parse_jd_maps_and_scores_high_confidence():
    provider = FixedProvider({
        "degree_req": "本科", "major_names": ["计算机科学与技术"],
        "direction_keywords": ["后端"], "location": "上海",
        "salary_min": 12, "salary_max": 20, "headcount": 3, "deadline": None,
    })
    r = parse_jd(provider, "……", {"计算机科学与技术": "080901"})
    assert r.major_codes == ["080901"]
    assert r.degree_req == "本科" and r.location == "上海"
    assert r.parse_confidence >= 0.6
    assert r.needs_review is False


def test_parse_jd_low_confidence_needs_review():
    # 无学历、无地点、专业无法映射 → 低置信 → 进复核队列
    provider = FixedProvider({
        "degree_req": None, "major_names": ["未知专业"], "direction_keywords": [],
        "location": None, "salary_min": None, "salary_max": None,
        "headcount": None, "deadline": None,
    })
    r = parse_jd(provider, "……", {"计算机科学与技术": "080901"})
    assert r.needs_review is True
    assert r.review_reason is not None


def test_parse_jd_empty_major_forces_review():
    # 裁决#1：LLM 未给出专业名 → 必须进复核队列，理由"未识别专业口径"
    provider = FixedProvider({
        "degree_req": "本科", "major_names": [], "direction_keywords": [],
        "location": "上海", "salary_min": 12, "salary_max": 18,
        "headcount": 2, "deadline": None,
    })
    r = parse_jd(provider, "……", {"计算机科学与技术": "080901"})
    assert r.needs_review is True
    assert r.review_reason == "未识别专业口径"


def test_parse_jd_llm_failure_routed_to_review():
    r = parse_jd(FixedProvider(raise_parse=True), "……", {})
    assert r.needs_review is True
    assert r.parse_confidence == 0.0
    assert "解析失败" in r.review_reason
