"""生成 JD 解析金标集（SPEC §5：≥100 条真实风格中文 JD，含 ≥20 条模糊困难样本）。

金标由本脚本作者按真实招聘文案撰写/组合，gold 为构造真值。运行：
  python -m app.llm.eval.make_golden   → 写 llm/eval/golden/jd_golden.jsonl
清晰样本：学历/专业/地点/薪资显式；困难样本：学历间接表述、地点为地域、专业靠岗位内容推断。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

GOLDEN_PATH = Path(__file__).parent / "golden" / "jd_golden.jsonl"

# (岗位, 主专业名[对齐种子库], 方向词, 行业句)
ROLES: list[tuple[str, list[str], list[str], str]] = [
    ("后端开发工程师", ["计算机科学与技术", "软件工程"], ["后端", "分布式", "微服务"], "负责核心交易系统的服务端研发与性能优化"),
    ("算法工程师", ["人工智能", "计算机科学与技术"], ["机器学习", "深度学习"], "负责推荐与排序模型的训练、评估与上线"),
    ("大数据开发工程师", ["数据科学与大数据技术", "软件工程"], ["数据仓库", "实时计算"], "负责数据管道建设与离线/实时数仓开发"),
    ("前端开发工程师", ["软件工程", "计算机科学与技术"], ["前端", "可视化"], "负责管理后台与大屏可视化的前端开发"),
    ("嵌入式软件工程师", ["电子信息工程", "电子科学与技术"], ["嵌入式", "驱动开发"], "负责智能硬件的固件与驱动开发"),
    ("通信算法工程师", ["通信工程"], ["5G", "信号处理"], "负责物理层算法设计与仿真验证"),
    ("机械设计工程师", ["机械设计制造及其自动化", "机械工程"], ["结构设计", "CAD"], "负责整机结构设计与工艺改进"),
    ("工业设计师", ["工业设计"], ["外观设计", "人机工程"], "负责消费电子产品的外观与交互设计"),
    ("材料研发工程师", ["材料科学与工程", "无机非金属材料工程"], ["复合材料", "涂层"], "负责新型功能材料的配方与性能测试"),
    ("电池材料工程师", ["高分子材料与工程", "材料科学与工程"], ["锂电池", "电极", "储能"], "负责动力电池正负极材料研发"),
    ("生物制药研究员", ["生物工程", "药学"], ["发酵", "纯化", "抗体"], "负责生物药的工艺开发与放大"),
    ("分子生物研究员", ["生物科学"], ["基因编辑", "测序"], "负责分子机制研究与实验设计"),
    ("财务分析师", ["财务管理", "会计学"], ["财务分析", "预算"], "负责经营分析与预算管理"),
    ("运营专员", ["电子商务", "工商管理"], ["电商运营", "供应链"], "负责线上店铺运营与活动策划"),
    ("经济研究员", ["经济学"], ["产业研究", "量化"], "负责宏观与行业研究报告撰写"),
    ("英语翻译", ["英语", "商务英语"], ["翻译", "本地化"], "负责技术文档与商务材料的中英互译"),
    ("日语商务专员", ["日语"], ["商务谈判", "跨文化"], "负责对日客户对接与商务沟通"),
]

DEGREES = [("本科", ["本科及以上学历", "本科以上", "全日制本科"]),
           ("硕士", ["硕士及以上", "研究生（硕士）以上学历", "硕士研究生"]),
           ("博士", ["博士学历", "博士及以上", "具有博士学位"])]

CITIES = ["北京", "上海", "深圳", "杭州", "广州", "成都", "西安", "武汉", "南京", "苏州"]


def _clear_samples(rng: random.Random) -> list[dict]:
    out: list[dict] = []
    idx = 0
    for title, majors, dirs, duty in ROLES:
        # 每个岗位生成 5 条不同学历/地点/薪资组合
        for _ in range(5):
            idx += 1
            deg_code, deg_phrases = rng.choice(DEGREES)
            city = rng.choice(CITIES)
            smin = rng.choice([8, 10, 12, 15, 18, 20])
            smax = smin + rng.choice([4, 6, 8, 10])
            hc = rng.choice([1, 2, 3, 5, 8])
            deg_phrase = rng.choice(deg_phrases)
            major_hint = "、".join(majors[:2]) + "等相关专业"
            raw = (
                f"【{title}】\n岗位职责：{duty}。\n"
                f"任职要求：{deg_phrase}，{major_hint}；熟悉{('、'.join(dirs))}相关技术。\n"
                f"工作地点：{city}。薪资：{smin}-{smax}k/月。招聘人数：{hc}人。"
            )
            out.append({
                "id": f"clear-{idx:03d}",
                "raw_text": raw,
                "difficult": False,
                "gold": {
                    "degree_req": deg_code,
                    "major_names": majors,
                    "location": city,
                    "salary_min": smin,
                    "salary_max": smax,
                    "headcount": hc,
                },
            })
    return out


# 困难样本（≥20）：学历间接/地点为地域/专业靠内容推断/信息缺失
FUZZY: list[dict] = [
    ("本科以上，硕士优先，专业不限但计算机相关者优先，工作地点长三角，负责后端服务开发",
     {"degree_req": "本科", "major_names": ["计算机科学与技术"], "location": "长三角"}),
    ("要求全日制本科，机械或材料背景，坐标珠三角，从事整机结构设计",
     {"degree_req": "本科", "major_names": ["机械工程"], "location": "珠三角"}),
    ("硕士起步，做深度学习模型训练与调优，地点在成渝地区",
     {"degree_req": "硕士", "major_names": ["人工智能"], "location": "成渝"}),
    ("学历要求专科以上即可，负责电商店铺日常运营，base杭州",
     {"degree_req": "不限", "major_names": ["电子商务"], "location": "杭州"}),
    ("博士优先，方向为锂电池正极材料，工作地不限可远程",
     {"degree_req": "博士", "major_names": ["材料科学与工程"], "location": None}),
    ("负责对日客户沟通与合同翻译，要求日语N1，本科即可",
     {"degree_req": "本科", "major_names": ["日语"], "location": None}),
    ("研究生学历，从事5G物理层算法，京津冀地区办公",
     {"degree_req": "硕士", "major_names": ["通信工程"], "location": "京津冀"}),
    ("做财务预算与经营分析，本科起，地点上海浦东",
     {"degree_req": "本科", "major_names": ["财务管理"], "location": "上海"}),
    ("负责生物药发酵工艺放大，硕士及以上，长三角优先",
     {"degree_req": "硕士", "major_names": ["生物工程"], "location": "长三角"}),
    ("岗位内容为嵌入式固件开发，学历本科，工作城市深圳南山",
     {"degree_req": "本科", "major_names": ["电子信息工程"], "location": "深圳"}),
    ("招聘数据仓库开发若干名，本科以上，熟悉实时计算，地点武汉光谷",
     {"degree_req": "本科", "major_names": ["数据科学与大数据技术"], "location": "武汉"}),
    ("从事产品外观与人机交互设计，本科，成都",
     {"degree_req": "本科", "major_names": ["工业设计"], "location": "成都"}),
    ("负责宏观与行业研究，硕士优先，本科亦可，北京",
     {"degree_req": "本科", "major_names": ["经济学"], "location": "北京"}),
    ("要求具有博士学位，分子生物学方向，从事基因编辑研究，西安",
     {"degree_req": "博士", "major_names": ["生物科学"], "location": "西安"}),
    ("英语专业八级，负责技术文档本地化，本科，可远程办公",
     {"degree_req": "本科", "major_names": ["英语"], "location": None}),
    ("负责高分子涂层材料研发，硕士，工作地苏州工业园区",
     {"degree_req": "硕士", "major_names": ["高分子材料与工程"], "location": "苏州"}),
    ("做微服务后端，学历不限有能力即可，深圳",
     {"degree_req": "不限", "major_names": ["软件工程"], "location": "深圳"}),
    ("招聘工商管理相关运营岗，本科以上，华南地区",
     {"degree_req": "本科", "major_names": ["工商管理"], "location": "华南"}),
    ("负责药物制剂研究，本科及以上药学背景，南京",
     {"degree_req": "本科", "major_names": ["药学"], "location": "南京"}),
    ("机械设计制造方向，本科，负责自动化产线设计，无固定地点全国出差",
     {"degree_req": "本科", "major_names": ["机械设计制造及其自动化"], "location": None}),
    ("硕士，做无机非金属材料烧结工艺，地点唐山",
     {"degree_req": "硕士", "major_names": ["无机非金属材料工程"], "location": "唐山"}),
    ("负责电子科学与技术相关芯片测试，本科，上海",
     {"degree_req": "本科", "major_names": ["电子科学与技术"], "location": "上海"}),
    ("商务英语方向，负责海外市场对接，本科，广州",
     {"degree_req": "本科", "major_names": ["商务英语"], "location": "广州"}),
    ("负责推荐系统算法研发，博士优先硕士可，杭州",
     {"degree_req": "硕士", "major_names": ["人工智能"], "location": "杭州"}),
    ("岗位为财务管理培训生，应届本科，地点成都天府新区",
     {"degree_req": "本科", "major_names": ["财务管理"], "location": "成都"}),
]


def _fuzzy_samples() -> list[dict]:
    out = []
    for i, (raw, gold) in enumerate(FUZZY, 1):
        g = {"degree_req": None, "major_names": [], "location": None,
             "salary_min": None, "salary_max": None, "headcount": None}
        g.update(gold)
        out.append({"id": f"fuzzy-{i:03d}", "raw_text": raw, "difficult": True, "gold": g})
    return out


def build_golden() -> list[dict]:
    rng = random.Random(2025)
    return _clear_samples(rng) + _fuzzy_samples()


def main() -> int:
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = build_golden()
    with GOLDEN_PATH.open("w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    diff = sum(1 for d in data if d["difficult"])
    print(f"[make_golden] 写入 {len(data)} 条 → {GOLDEN_PATH}（困难样本 {diff} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
