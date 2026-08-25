"""Propose sensitive terms instead of asking you to remember them.

The gate is right -- no scanner knows that a particular word is a client's
name -- but the question was wrong. Asking "which client and project names
appear in your material?" is a recall task, the hardest kind, and in the
common case the tool already has the evidence to answer it: a repository with
one author, no corporate email domains and no other committers has no
colleagues to protect.

So: scan the corpus, produce candidates, and let the answer be recognition
rather than recall. What cannot be automated is the last step -- deciding
which of the candidates is actually confidential -- and that stays with you.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

PUBLIC_MAIL = {"gmail.com", "outlook.com", "hotmail.com", "qq.com", "163.com", "126.com",
               "yahoo.com", "icloud.com", "foxmail.com", "protonmail.com", "example.com",
               "users.noreply.github.com", "noreply.github.com", "anthropic.com"}

# Capitalised phrases that are technology, not organisations.
TECH_STOP = {
    "Python", "JavaScript", "TypeScript", "Claude Code", "Read Pack", "Agent Skills",
    "GitHub", "Git", "Notion", "Open Skills", "Stack Overflow", "Big Five",
    "Career Construction", "Docker", "Kubernetes", "Node", "React", "MIT", "API",
    "CLI", "JSON",
    "YAML", "SQL", "HTTP", "HTTPS", "URL", "PDF", "Markdown", "Linux", "macOS", "Windows",
}

# Measure-word phrases rather than function characters: adding 一 to the
# character filter would also drop 统一 and 三一, which are real names.
CJK_STOP = {
    "一个", "一家", "一些", "一种", "一条", "一套", "整个", "各个", "这个", "那个",
    "两个", "几个", "多个", "单个", "本次", "该项", "此类",
    "个人", "公司", "项目", "系统", "平台", "工程", "计划", "内部", "外部",
    "证据", "画像", "技能", "脱敏", "卡片", "主题", "问题", "回答", "材料", "来源", "工作",
    "项目", "配置", "命令", "目录", "文件", "仓库", "模型", "数据", "测试", "代码", "设计",
    "职业", "访谈", "分诊", "深读", "语料", "信度", "引用", "断言", "结果", "方案", "迁移",
}

# Frequency alone cannot find a proper noun in Chinese without segmentation --
# the first attempt returned 所以 and 关于. Context can: organisations and
# projects are named by the word that follows them. Fewer candidates, and the
# ones that surface are worth reading.
# A greedy match swallows the sentence in front of the suffix ("客户名和项目"),
# so anything containing a high-frequency function character is thrown away.
# Precision matters more than recall here: a candidate list full of noise is
# worse than no list, because nobody reads the second screen of it.
FUNCTION_CHARS = set("的了是和在这那就而也都还把被对从与及或者我你他她它们因所但如"
                     "果没不很上下过来去说做让给要能会有由为将于其此以并且则等"
                     "某同整每另几两单各全该本此些")

# Zero-width on purpose. Filtering happens after matching, and a rejected
# match had already consumed its characters -- so a junk candidate starting
# two characters early hid the real name behind it. As a lookahead nothing is
# consumed and every position gets tried.
# Directional complements: common at the end of a verb phrase, vanishingly
# rare at the end of a company or project name.
VERB_TAIL = set("出到上下来去成过好完掉起住开走清做用查找看写读跑改")

ORG_SUFFIX = re.compile(
    r"(?=([\u4e00-\u9fff]{2,6}?)(?:公司|科技|集团|银行|保险|证券|医院|大学|研究院|"
    r"事业部|控股|实业|资本|基金))")
PROJECT_SUFFIX = re.compile(
    r"(?=([\u4e00-\u9fff]{2,6}?)(?:项目|计划|系统|平台|工程|产品线|业务线))")
CLIENT_PREFIX = re.compile(
    r"(?:客户|甲方|乙方|合作方|供应商)[：:\s]*([\u4e00-\u9fff]{2,8})")
ENGLISH_ORG = re.compile(
    r"\b([A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,3})\s+"
    r"(?:Corp|Corporation|Inc|Ltd|LLC|GmbH|Technologies|Holdings|Group|Bank|Labs)\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
INTERNAL_HOST = re.compile(
    r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:internal|intranet|corp|local|lan)\b", re.I)


@dataclass
class Candidate:
    term: str
    label: str
    hits: int
    why: str
    files: list[str] = field(default_factory=list)


@dataclass
class Suggestion:
    candidates: list[Candidate] = field(default_factory=list)
    signals: dict = field(default_factory=dict)

    @property
    def looks_personal(self) -> bool:
        """No colleagues, no corporate mail, no internal hosts, one author.

        When all four hold, "there is no client here" is a finding, not a
        guess -- and asking anyway wastes the one question the user has to
        think hard about.
        """
        s = self.signals
        # Having scanned nothing is not evidence of anything. Concluding "no
        # client here" from an empty scan is the exact false confidence this
        # project exists to avoid.
        if not s.get("files_scanned"):
            return False
        return (s.get("other_committers", 0) == 0
                and not s.get("corporate_domains")
                and s.get("internal_hosts", 0) == 0
                and s.get("repos_scanned", 0) <= 1)


def _read(path: Path, limit: int = 400_000) -> str:
    try:
        return path.read_text("utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def suggest(paths: list[Path], authors: list[str], repos: list = None,
            limit: int = 25) -> Suggestion:
    mine = {a.strip().lower() for a in authors if a.strip()}
    out = Suggestion()
    domains: Counter = Counter()
    phrases: Counter = Counter()
    cjk: Counter = Counter()
    hosts: Counter = Counter()
    where: dict = {}

    for path in paths:
        text = _read(path)
        if not text:
            continue
        name = path.name
        for domain in EMAIL.findall(text):
            domains[domain.lower()] += 1
            where.setdefault(domain.lower(), set()).add(name)
        for host in INTERNAL_HOST.findall(text):
            hosts[host.lower()] += 1
            where.setdefault(host.lower(), set()).add(name)
        for phrase in ENGLISH_ORG.findall(text):
            if phrase in TECH_STOP or phrase.lower() in mine:
                continue
            phrases[phrase] += 1
            where.setdefault(phrase, set()).add(name)
        # A verb phrase is not a name. My own documentation sentence
        # "扫材料找出公司名、项目代号" matched the organisation pattern and
        # surfaced 扫材料找出 as a candidate -- the detector caught its own prose.
        for pattern, bucket in ((ORG_SUFFIX, cjk), (PROJECT_SUFFIX, cjk),
                                (CLIENT_PREFIX, cjk)):
            for run in pattern.findall(text):
                if run in CJK_STOP or not 2 <= len(run) <= 6:
                    continue
                if FUNCTION_CHARS & set(run):
                    continue
                if run[-1] in VERB_TAIL:
                    continue
                bucket[run] += 1
                where.setdefault(run, set()).add(name)

    corporate = [d for d in domains if d not in PUBLIC_MAIL]
    other_committers = []
    for repo in (repos or []):
        for author_name, email, count in getattr(repo, "top_authors", []):
            if author_name.lower() in mine or email.lower() in mine:
                continue
            other_committers.append((author_name, email, count))

    out.signals = {
        "repos_scanned": len(repos or []),
        "other_committers": len(other_committers),
        "corporate_domains": corporate,
        "internal_hosts": len(hosts),
        "files_scanned": len(paths),
    }

    for author_name, email, count in other_committers:
        out.candidates.append(Candidate(
            term=author_name, label="PERSON", hits=count,
            why=f"git 里的另一个提交者 <{email}>"))
    for domain in corporate:
        out.candidates.append(Candidate(
            term=domain, label="ORG", hits=domains[domain],
            why="非公共邮箱域名，通常是公司", files=sorted(where.get(domain, []))[:3]))
    for host, count in hosts.most_common(8):
        out.candidates.append(Candidate(
            term=host, label="INTERNAL_HOST", hits=count,
            why="内网域名", files=sorted(where.get(host, []))[:3]))

    # Repeated proper nouns that are neither technology nor common words: the
    # shape a product or client name takes. Frequency is the only filter that
    # works without knowing the domain, so this list is meant to be skimmed
    # and mostly rejected.
    for phrase, count in phrases.most_common(limit):
        out.candidates.append(Candidate(
            term=phrase, label="ORG", hits=count,
            why=f"后面跟着 Corp/Inc/Ltd 之类（{count} 次）",
            files=sorted(where.get(phrase, []))[:3]))
    for run, count in cjk.most_common(limit):
        out.candidates.append(Candidate(
            term=run, label="PROJECT", hits=count,
            why=f"后面跟着 公司/项目/系统 之类（{count} 次）",
            files=sorted(where.get(run, []))[:3]))

    out.candidates = _dedupe_overlaps(out.candidates)
    out.candidates.sort(key=lambda c: (-{"PERSON": 3, "INTERNAL_HOST": 2}.get(c.label, 1),
                                       -c.hits))
    return out


def _dedupe_overlaps(candidates: list[Candidate]) -> list[Candidate]:
    """`远洋` and `远洋银行` are one candidate. Keep the longer form."""
    by_length = sorted(candidates, key=lambda c: -len(c.term))
    kept: list[Candidate] = []
    for cand in by_length:
        if any(cand.term in other.term and cand.label == other.label for other in kept):
            continue
        kept.append(cand)
    return kept
