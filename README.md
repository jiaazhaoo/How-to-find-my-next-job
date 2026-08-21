# How do I find my next job

Align what you have done with the tide of the times.

把自己的代码库、报告、项目材料交给 AI，让它解构出你的能力画像，再反过来问你几个关键问题。
这个仓库目前实现了这条链路的**输入层**——也是最容易被做砸、也最决定后面一切可信度的两步：

1. **脱敏**：闸门 fail-closed，凭据销毁、身份假名化，别名全语料稳定。
2. **深读**：四步漏斗把万级文件收敛到百级，模型只在高信号材料上花 token，产出**带引用、可机器校验**的证据卡片。

设计理由与取舍写在 [`docs/input-layer.md`](docs/input-layer.md)。

## 快速开始

无依赖，Python 3.9+（macOS 自带的就够），全程本地运行。命令是 `python3`，不是 `python`。

```bash
python -m career init                 # 自动探测身份、会话日志、已下载的导出
$EDITOR config/sources.json           # 只需补两项：sources 和 sensitive_terms
python -m career doctor               # 检查脱敏工具链和配置

python -m career connectors           # 看有哪些可导入的源
python -m career connect claude-code  # ⓪ 导入本地 AI 会话日志
python -m career connect codex        #    另一个助手的会话（覆盖面）
python -m career connect notion-export
python -m career connect x-archive

python -m career scan                 # ① 扫描（staging 自动纳入）→ 清单
python -m career prep -v              # ②③ 脱敏 + 打分 + 选择 + 生成 read pack
```

然后在 Claude Code（或任何支持 skill 的 CLI）里：

```
/redaction-review                     # 亲眼过一遍第一个 pack，补 sensitive_terms
/deep-read workspace/04_packs/pack-01.md
```

```bash
python -m career verify               # ④ 每条引用必须真实存在于模型看过的摘录里
python -m career themes               # ⑤ 聚类：≥2 个独立来源才算 pattern；输出矛盾点
```

单文件临时用：

```bash
python -m career redact notes.md > safe.md      # 脱敏后再粘给任何 AI
python -m career restore report.md              # 本地把别名还原成真名
```

## 输出

```
workspace/
  vault/aliases.json      别名 → 真名（0600，已 gitignore，唯一不能外泄的文件）
  00_staging/<源>/        连接器导入的材料 + _provenance.jsonl
  01_manifest.jsonl       扫描清单：路径、语言、git ownership
  02_shortlist.jsonl      每个文件的分数、理由、是否入选
  03_redacted/            脱敏后的全文
  03_corpus.json          模型实际看到的摘录（引用校验的基准）
  04_packs/pack-NN.md     交给模型的 read pack
  05_cards.jsonl          证据卡片（模型写）
  06_themes.json          聚类结果 + 矛盾点
  redaction-report.json   脱敏统计（不含明文，可分享）
```

## 输入源

连接器只做一件事：**把内容落成本地文件放进 staging**，然后交给同一条流水线。
绝不在分析时实时拉数据——脱敏必须只有一个入口，否则 fail-closed 就是摆设。

| 源 | 独有贡献 | 状态 |
|---|---|---|
| Claude Code 会话 | 仓库记录什么上线了，会话记录你**试过什么**；带 `cwd`/`gitBranch`，天然贴着提交 | ✅ |
| Codex 会话 | 同上。只导一个助手会让语料偏向你恰好在那个工具里做的事 | ✅ |
| Notion 导出 | 项目文档和会议记录，常常是一个项目唯一的书面记录 | ✅ 导出自动探测，或用 `import-notion` skill 走 MCP |
| X 归档 | 面向受众的写作 = 兴趣与定位；自我复述的长贴按线程重组 | ✅ 导出自动探测（X API 已改按次计费，导出更划算） |
| 个人博客 | 无报酬写作 = 强兴趣信号 | 待做 |
| LinkedIn 导出 | 别人写的推荐 + 职位时间脊柱（**不当能力证据**） | 待做 |
| GitHub MCP | 你写在**别人 PR 上的 review 评论**——本地 clone 一条都没有 | 待做 |
| 日历 | 你到底把时间花在哪了 | 待做 |

`career stage` 让 agent 通过 MCP 取到的内容走同一条流水线——取的方式可以变，
**落成文件再脱敏这一步不能变**，否则闸门就成了摆设。代价见 [`docs/input-layer.md`](docs/input-layer.md#关于-mcp能替代手动配置的和不能的)。

"啥都放"的源（Notion / X）另有一层 `relevance` 分诊：**它决定什么值得发，不决定什么安全发**——
后者永远是 `redact` 的事。导入时会打印相关度直方图，方便调 `min_relevance`。

## 三条不可协商的规则

- **凭据明文永不落盘。** vault 里存的是 PII 的映射，凭据只留 fingerprint。
- **没有引用就没有主张。** `verify` 会把编造的卡片抓出来（fixture 里试过）。
- **不做人格推断。** claim 里出现"内向/性格/MBTI"直接判校验失败。LLM 从文本推断大五人格
  与真实量表的相关系数低于 r≈0.30；特质只能来自你亲自填的量表，不能来自模型读你的代码。
- **不让自我描述变成能力证据。** 也包括推文里你自己宣称的成果——没有工件支撑的结果是 `self_concept`，
  不是 `impact`。 你在聊天记录里对自己的说法是 `self_concept` 卡片，
  永远不能升级成 pattern——否则 AI 只是把你的自我认知加上引用还给你。
  它的正确用途是提问：**自述和作品之间的落差**。

## 状态

| 层 | 状态 |
|---|---|
| ⓪ 连接器 / 输入源 | 4 个已完成（见上表） |
| ① 脱敏 | 已完成（含聊天记录的主题切除策略） |
| ② 深读 → 证据卡片 | 已完成 |
| ③ 画像（只用 pattern 写） | 未开始 |
| ④ 因人而异的提问（Savickas CCI / RIASEC / IPIP-NEO） | 未开始，原料已经在 `themes` 的 tensions 和卡片的 `open_question` 里 |

```bash
python -m career selftest
```
