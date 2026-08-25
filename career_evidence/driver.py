"""One command instead of seventeen.

The pipeline has three places where a human is genuinely required: reviewing
what redaction could not know to hide, reading the packs (a model pass), and
answering the interview. Everything else was only sequencing -- remembering
which of seventeen commands comes next -- and that is complexity I added, not
complexity the problem has.

This is a state machine over the workspace. It looks at what exists, runs
every automatic step it can, and stops at the first thing that actually needs
a person, saying exactly what that is. You always type the same thing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import Config

def cli() -> str:
    """How to invoke ourselves, in order of what is most likely to exist.

    Installed as a plugin, the code sits next to a launcher that needs nothing
    installed at all -- which is the only form that cannot break, and every
    deployment failure this project has had came from assuming otherwise.
    """
    import os
    import shutil

    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        launcher = Path(plugin_root) / "scripts" / "career-evidence"
        if launcher.exists():
            return f'python3 "{launcher}"'
    if shutil.which("career-evidence"):
        return "career-evidence"
    bundled = Path(__file__).resolve().parent.parent / "scripts" / "career-evidence"
    if bundled.exists():
        return f'python3 "{bundled}"'
    return "python3 -m career_evidence"


AUTO = "auto"        # the driver can do this itself
HUMAN = "human"      # needs you, or needs a model pass in your CLI
DONE = "done"


@dataclass
class Step:
    key: str
    kind: str
    title: str
    detail: str = ""
    command: str = ""

    @property
    def is_auto(self) -> bool:
        return self.kind == AUTO


def _terms_untouched(cfg: Config) -> bool:
    """The template ships with example terms, and they satisfied the check.

    A gate that a placeholder can walk through is not a gate -- this is the
    one field no scanner can fill in, so it has to notice when it is still
    holding the sample values.
    """
    from .config import TEMPLATE

    if not cfg.sensitive_terms:
        return True
    sample = {t["term"] for t in TEMPLATE["sensitive_terms"]}
    return {t.get("term") for t in cfg.sensitive_terms} <= sample


def _has(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _count_lines(path: Path) -> int:
    if not _has(path):
        return 0
    return sum(1 for line in path.read_text("utf-8").splitlines() if line.strip())


def next_step(cfg: Config, config_path: Path) -> Step:
    """The single next thing to do. Order matters; first match wins."""
    if not config_path.exists():
        return Step("init", AUTO, "建立配置", "自动探测身份、你提交过的仓库、已下载的导出",
                    f"{cli()} init")

    if not cfg.authors:
        return Step("authors", HUMAN, "填 authors",
                    "git 身份和实际提交者常常不一致。跑 `career-evidence repos` 看仓库里的提交者是谁，"
                    "把你的那一行填进 config 的 authors。")

    staged = (cfg.staging_root.exists() and any(cfg.staging_root.glob("*/*.md"))) \
        or _has(cfg.ws / ".nochat")
    if not staged and not cfg.sources:
        return Step("sources", HUMAN, "指定材料",
                    "没有仓库也没有导入的材料。`career-evidence repos --write` 可以自动填仓库，"
                    "或手工把目录加进 config 的 sources。")

    if _terms_untouched(cfg):
        return Step("terms", HUMAN, "确认敏感词",
                    "机器会先扫一遍材料给出候选，你只需要勾选哪些是真保密的——"
                    "它认得出模式（XX公司、XX项目、内网域名、git 里的其他提交者），"
                    "认不出哪个是机密。没有候选且没有公司痕迹时会自动判定为个人项目。",
                    f"{cli()} terms --auto")

    if not staged:
        # Only offer connectors that are actually usable here. A missing Codex
        # means "you do not use Codex", not a failure, and the driver should
        # not stop on it.
        from .connectors import REGISTRY

        usable = [name for name, c in sorted(REGISTRY.items())
                  if c.available(cfg.connectors.get(name, {}))[0]]
        if not usable:
            return Step("connect", HUMAN, "没有可导入的会话日志",
                        "找不到任何本地 AI 会话。没有也能继续——"
                        f"直接 `touch {cfg.ws}/.nochat` 跳过这一步，"
                        "或者用 `career-evidence connectors` 看怎么接。")
        return Step("connect", AUTO, "导入会话日志",
                    f"可用的来源：{', '.join(usable)}",
                    "; ".join(f"{cli()} connect {n}" for n in usable))

    if not _has(cfg.manifest_path):
        return Step("scan", AUTO, "扫描", "建立文件清单", f"{cli()} scan")

    if not (cfg.packs_dir.exists() and any(cfg.packs_dir.glob("pack-*.md"))):
        return Step("prep", AUTO, "脱敏 + 分诊 + 生成 read pack",
                    "这一步之后才有东西可以给模型看", f"{cli()} prep -v")

    if not _has(cfg.ws / ".reviewed"):
        packs = sorted(cfg.packs_dir.glob("pack-*.md"))
        return Step("review", HUMAN, "亲眼过一遍脱敏结果",
                    f"打开 {packs[0]} 找机器不可能知道的东西：正文里的同事名、"
                    f"项目代号、内部链接。找到就加进 sensitive_terms 重跑 prep。"
                    f"确认没问题后 `touch {cfg.ws}/.reviewed`。",
                    "/career-evidence-redaction")

    if _count_lines(cfg.cards_path) == 0:
        packs = sorted(cfg.packs_dir.glob("pack-*.md"))
        return Step("deep-read", HUMAN, "深读，产出证据卡片",
                    f"{len(packs)} 个 pack 要读。这一步必须模型来做。",
                    f"/career-evidence-read {packs[0]}")

    if not _has(cfg.themes_path):
        return Step("themes", AUTO, "核对引用 + 聚类",
                    "确认每条引用真实存在，然后归并成主题",
                    f"{cli()} verify; {cli()} themes")

    if not _has(cfg.questions_path):
        return Step("questions", AUTO, "生成该问你的问题",
                    "从卡片图里挖矛盾点和空白", f"{cli()} questions")

    if _count_lines(cfg.answers_path) == 0:
        return Step("interview", HUMAN, "回答问题",
                    "这是整条链上唯一产生新信息的一步——作品能证明你擅长什么，"
                    "永远证明不了你还想不想做。", "/career-evidence-interview")

    if not _has(cfg.skeleton_path):
        return Step("skeleton", AUTO, "算出什么能写进画像",
                    "只有 ≥2 个独立来源且有工件支撑的主题够格",
                    f"{cli()} profile")

    if not _has(cfg.profile_path):
        return Step("write", HUMAN, "写画像",
                    "从骨架写正文，每句断言挂卡片 id", "/career-evidence-profile")

    return Step("check", AUTO, "校验画像", "每条引用必须存在、合法、不是自述",
                f"{cli()} profile --check {cfg.profile_path}")


def progress(cfg: Config, config_path: Path) -> list[tuple[str, str]]:
    """A checklist of where things stand, for showing the user."""
    staged = cfg.staging_root.exists() and any(cfg.staging_root.glob("*/*.md"))
    packs = len(list(cfg.packs_dir.glob("pack-*.md"))) if cfg.packs_dir.exists() else 0
    rows = [
        ("配置", "ok" if config_path.exists() and cfg.authors else "缺"),
        ("材料", f"{len(cfg.sources)} 个来源" + (" + 会话日志" if staged else "")
         if cfg.sources or staged else "缺"),
        ("read pack", f"{packs} 个" if packs else "-"),
        ("脱敏复核", "ok" if _has(cfg.ws / ".reviewed") else "未做"),
        ("证据卡片", f"{_count_lines(cfg.cards_path)} 张" if _count_lines(cfg.cards_path) else "-"),
        ("主题", "ok" if _has(cfg.themes_path) else "-"),
        ("问题", "ok" if _has(cfg.questions_path) else "-"),
        ("回答", f"{_count_lines(cfg.answers_path)} 条" if _count_lines(cfg.answers_path) else "-"),
        ("画像", "ok" if _has(cfg.profile_path) else "-"),
    ]
    return rows


def summary(cfg: Config) -> str:
    """One line of what came out, when there is something to report."""
    bits = []
    if _has(cfg.ws / "prep-summary.json"):
        data = json.loads((cfg.ws / "prep-summary.json").read_text("utf-8"))
        bits.append(f"{data.get('selected', 0)}/{data.get('candidates', 0)} 份材料入选")
        totals = data.get("redaction_totals") or {}
        if totals:
            bits.append(f"脱敏 {sum(totals.values())} 处")
    cards = _count_lines(cfg.cards_path)
    if cards:
        bits.append(f"{cards} 张卡片")
    return " · ".join(bits)
