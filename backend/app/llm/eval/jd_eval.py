"""JD 解析评测（SPEC §5）：字段级准确率报告落盘。

验收线：degree/major/location 三字段 ≥85%，整体字段均值 ≥80%（SPEC §5）。
未达线的兜底：低置信/失败样本经 job_parses.needs_review 进人工复核队列（已实现）。

评分函数为纯函数（可单测，用录制样本）；跑真实模型另经 main()。
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from app.llm.parsers.jd import parse_jd
from app.llm.prompts.jd_parse import JD_PARSE_PROMPT_VER

GOLDEN_PATH = Path(__file__).parent / "golden" / "jd_golden.jsonl"
REPORT_DIR = Path(__file__).parent / "reports"

DEGREE_PASS = 0.85
MAJOR_PASS = 0.85
LOCATION_PASS = 0.85
MEAN_PASS = 0.80


def _norm(s: str | None) -> str:
    return (s or "").strip()


def degree_correct(pred: str | None, gold: str | None) -> bool:
    # None 视作"不限"
    p = _norm(pred) or "不限"
    g = _norm(gold) or "不限"
    return p == g


def _name_hit(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a) and bool(b) and (a in b or b in a)


def major_correct(pred_names: list[str], gold_names: list[str]) -> bool:
    if not gold_names:
        return not pred_names
    return any(_name_hit(p, g) for g in gold_names for p in (pred_names or []))


def location_correct(pred: str | None, gold: str | None) -> bool:
    p, g = _norm(pred), _norm(gold)
    if not g:
        return not p
    return bool(p) and (p in g or g in p)


def score_entry(pred: dict, gold: dict) -> dict:
    return {
        "degree": degree_correct(pred.get("degree_req"), gold.get("degree_req")),
        "major": major_correct(pred.get("major_names") or [], gold.get("major_names") or []),
        "location": location_correct(pred.get("location"), gold.get("location")),
    }


def aggregate(entry_scores: list[dict]) -> dict:
    n = len(entry_scores)
    if n == 0:
        return {"n": 0}
    deg = sum(s["degree"] for s in entry_scores) / n
    maj = sum(s["major"] for s in entry_scores) / n
    loc = sum(s["location"] for s in entry_scores) / n
    mean3 = round((deg + maj + loc) / 3, 4)
    return {
        "n": n,
        "degree_acc": round(deg, 4),
        "major_acc": round(maj, 4),
        "location_acc": round(loc, 4),
        "mean3": mean3,
        "passed": deg >= DEGREE_PASS and maj >= MAJOR_PASS and loc >= LOCATION_PASS and mean3 >= MEAN_PASS,
    }


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def run_eval(provider, golden: list[dict], *, limit: int | None = None,
             checkpoint: Path | None = None) -> dict:
    """对一个 provider 跑评测，返回指标 + 逐条明细。major 映射对评分无影响，用空索引。

    checkpoint 给定时，每条后写入部分明细（防长跑被中断丢结果）。
    """
    rows = golden[:limit] if limit else golden
    scores: list[dict] = []
    details: list[dict] = []
    for i, item in enumerate(rows, 1):
        result = parse_jd(provider, item["raw_text"], major_index={})
        pred = {
            "degree_req": result.degree_req,
            "major_names": result.major_names,
            "location": result.location,
        }
        s = score_entry(pred, item["gold"])
        scores.append(s)
        details.append({"id": item["id"], "difficult": item.get("difficult", False),
                        "pred": pred, "gold": {k: item["gold"].get(k) for k in ("degree_req", "major_names", "location")},
                        "score": s, "needs_review": result.needs_review})
        if checkpoint is not None:
            checkpoint.write_text(
                json.dumps({"done": i, "total": len(rows), "running_metrics": aggregate(scores)},
                           ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = aggregate(scores)
    # 困难样本单列
    diff_scores = [d["score"] for d in details if d["difficult"]]
    metrics["difficult"] = aggregate(diff_scores) if diff_scores else {"n": 0}
    return {"metrics": metrics, "details": details}


def main(argv: list[str] | None = None) -> int:
    from app.core.config import settings
    from app.llm.provider import ApiProvider, OllamaProvider

    parser = argparse.ArgumentParser(prog="app.llm.eval.jd_eval")
    parser.add_argument("--limit", type=int, default=None, help="只评测前 N 条（耐时控制）")
    args = parser.parse_args(argv)

    golden = load_golden()
    report: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "prompt_ver": JD_PARSE_PROMPT_VER,
        "golden_n": len(golden),
        "limit": args.limit,
        "thresholds": {"degree/major/location": DEGREE_PASS, "mean3": MEAN_PASS},
        "columns": {},
    }

    # 本地 7b 列（直接构造 OllamaProvider，绕开档位；评测本身不涉数据出校）
    local_key = f"local_{settings.ollama_model}"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ckpt = REPORT_DIR / "jd_eval_local_checkpoint.json"
    try:
        report["columns"][local_key] = run_eval(
            OllamaProvider(), golden, limit=args.limit, checkpoint=ckpt
        )["metrics"]
    except Exception as e:  # noqa: BLE001  评测环境问题如实记录，不伪造
        report["columns"][local_key] = {"status": "error", "error": f"{type(e).__name__}: {e}"}

    # API 列：无外部密钥则标记"待补"，禁止伪造
    if settings.llm_api_key.strip() and settings.allow_external_llm:
        try:
            report["columns"]["api"] = run_eval(ApiProvider(), golden, limit=args.limit)["metrics"]
        except Exception as e:  # noqa: BLE001
            report["columns"]["api"] = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    else:
        report["columns"]["api"] = {"status": "待补：本环境无外部 API 密钥（禁止伪造该列）"}

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = REPORT_DIR / f"jd_eval_{ts}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report["columns"].items()}, ensure_ascii=False, indent=2))
    print(f"[jd_eval] 报告落盘：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
