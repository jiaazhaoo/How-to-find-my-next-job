"""Is this professional material at all?

Needed because two of the richest sources are ones people use for everything.
A Notion workspace holds design docs and grocery lists; a timeline holds
architecture threads and holiday photos. Neither problem is solved by the
redaction gate or the topic policy -- a grocery list contains no identifier to
redact and no sensitive topic to excise. It is simply not evidence.

**This is a triage aid, not a privacy control.** Do not conflate the two:
`career_evidence.redact` decides what is *safe* to send, this decides what is *worth*
sending. A false negative here costs a little coverage. A false negative in
the gate costs a leaked credential. They are tuned accordingly -- this one
errs toward keeping things.
"""

from __future__ import annotations

import re

WORK_VOCAB = re.compile(
    r"(?:架构|部署|接口|数据库|索引|缓存|并发|重构|性能|上线|回滚|灰度|排期|需求|"
    r"指标|模型|算法|测试|评审|故障|复盘|迁移|方案|设计|技术|团队|项目|客户|流程)"
    r"|\b(?:api|sdk|deploy|schema|latency|throughput|refactor|infra|pipeline|migration|"
    r"endpoint|framework|repo|commit|pull request|rollout|incident|postmortem|roadmap|"
    r"stakeholder|architecture|benchmark|regression|kubernetes|docker|sql|typescript)\b",
    re.I)

CODE_SHAPE = re.compile(
    r"```|`[^`\n]{3,}`|^\s{4,}\S|\b\w+\.(?:py|ts|tsx|go|rs|java|sql|yaml|json|sh)\b"
    r"|\b(?:def|class|function|import|SELECT|curl|git|npm|pip|docker)\s",
    re.M)

REASONING = re.compile(
    r"(?:因为|所以|权衡|取舍|决定|对比|评估|结论|建议|问题是|原因)"
    r"|\b(?:because|therefore|trade-?off|decided|conclusion|the problem is|approach)\b",
    re.I)

LIFESTYLE = re.compile(
    r"(?:菜谱|食谱|做饭|旅行|机票|酒店|健身|减肥|追剧|电影票|购物|装修|搬家|"
    r"宠物|考驾照|外卖|游戏攻略)"
    r"|\b(?:recipe|grocery|workout|vacation|flight booking|hotel|movie night|"
    r"shopping list|netflix)\b", re.I)

BARE_URL = re.compile(r"^\s*(?:[-*]\s*)?https?://\S+\s*$")

CJK_WEIGHT = 2.5   # a Chinese character carries far more than a latin one


def weighted_length(text: str) -> int:
    """Length in rough information units, so one threshold works for both
    languages: 200 units is ~200 English characters or ~80 Chinese ones.

    Every length cutoff in the pipeline goes through this. Raw `len()` quietly
    discriminates against Chinese material -- a substantial paragraph looks
    like a fragment -- and that bias is invisible until you notice good
    sessions and posts missing from the corpus.
    """
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return int(cjk * CJK_WEIGHT + (len(text) - cjk))


def work_score(text: str) -> tuple[float, dict]:
    """Return (0..1, signal breakdown). 0.5 is "no opinion"."""
    stripped = text.strip()
    if not stripped:
        return 0.0, {"empty": 1}

    lines = [l for l in stripped.splitlines() if l.strip()]
    n_lines = max(len(lines), 1)
    words = max(len(stripped) / 6, 1)

    vocab = len(WORK_VOCAB.findall(stripped))
    code = len(CODE_SHAPE.findall(stripped))
    reason = len(REASONING.findall(stripped))
    lifestyle = len(LIFESTYLE.findall(stripped))
    link_ratio = sum(1 for l in lines if BARE_URL.match(l)) / n_lines

    signals = {"vocab": vocab, "code": code, "reasoning": reason,
               "lifestyle": lifestyle, "link_ratio": round(link_ratio, 2),
               "lines": n_lines}

    score = 0.5
    score += min(0.30, vocab / words * 6)
    score += min(0.20, code * 0.05)
    score += min(0.15, reason * 0.05)
    score -= min(0.55, lifestyle * 0.18)
    if link_ratio > 0.5:
        score -= 0.25
        signals["link_dump"] = 1
    if n_lines <= 2 and len(stripped) < 120:
        score -= 0.20
        signals["stub"] = 1

    return round(max(0.0, min(1.0, score)), 3), signals


def histogram(scores: list[float], bins: int = 5) -> dict[str, int]:
    """So a user tuning `min_relevance` can see the distribution they are cutting."""
    out: dict[str, int] = {}
    for s in scores:
        i = min(bins - 1, int(s * bins))
        key = f"{i / bins:.1f}-{(i + 1) / bins:.1f}"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))
