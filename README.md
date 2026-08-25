# How do I find my next job

Align what you have done with the tide of the times.

把自己的代码库、报告、项目材料交给 AI，让它解构出你的能力画像，再反过来问你几个关键问题。
整条链路都在了：接入 → 脱敏 → 分诊 → 深读 → 归约 → 提问 → 画像，外加一层信度测量。
贯穿全程的一条规矩是**每句话都要能被反驳**：断言必须挂着逐字引用，引用由代码核对，
一处证据只算一次，你自己说的话不算能力证据。

设计理由写在 [`docs/input-layer.md`](docs/input-layer.md)（前半段）和
[`docs/career-evidence-profile-and-interview.md`](docs/career-evidence-profile-and-interview.md)（后半段）。

## 快速开始

无依赖，Python 3.9+（macOS 自带的就够），全程本地运行。

```bash
git clone -b claude/career-profile-assessment-t4xmll \
  https://github.com/jiaazhaoo/How-to-find-my-next-job.git
cd How-to-find-my-next-job
pip install -e .                            # career-evidence 命令进 PATH
./scripts/install-skills.sh                 # /career-evidence 在任意目录可用

career-evidence run                         # 在哪都能跑
```

就这一个命令。它会做完所有能自动做的事，停在第一个真正需要你的地方，
然后你再敲一次同样的命令，它接着往下走。

在 Claude Code 里更简单——直接 `/career-evidence`，它替你跑上面这个循环，
连深读和访谈也一并做了。

数据默认放在 `~/.career-evidence/`——**你的职业语料横跨所有仓库，本来就不属于某一个项目**。
如果当前目录有 `config/sources.json`（比如你在改这个工具本身），会优先用它。

**只有三处真的需要你**：填客户名和项目代号（没有扫描器能替你做）、
亲眼过一遍脱敏结果、回答访谈问题。其余都是自动的。

<details>
<summary>如果你想手动控制每一步</summary>

```bash
career-evidence init                 # 自动探测身份、你提交过的仓库、已下载的导出
career-evidence repos                # 看它凭什么把这些仓库算成你的
career-evidence connect claude-code  # 导入会话日志（codex / notion-export / x-archive / web 同理）
career-evidence scan                 # 建清单
career-evidence prep -v              # 脱敏 + 分诊 + 生成 read pack
# /career-evidence-redaction  → /career-evidence-read
career-evidence verify               # 每条引用必须真实存在
career-evidence themes               # 聚类，≥2 个独立来源才算 pattern
career-evidence questions --year 2026
# /career-evidence-interview
career-evidence answers --file answers.jsonl
career-evidence profile              # 骨架
# /career-evidence-profile
career-evidence profile --check workspace/09_profile.md
career-evidence reliability workspace/rel/run*.jsonl   # 这一切有多可复现
```

</details>

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
  07_questions.json       为你生成的问题 + 依据
  07_answers.jsonl        你的回答（会变成新卡片）
  08_skeleton.json        什么可以写进画像
  09_profile.md           画像正文（--check 校验）
  redaction-report.json   脱敏统计（不含明文，可分享）
```

## 输入源

连接器只做一件事：**把内容落成本地文件放进 staging**，然后交给同一条流水线。
绝不在分析时实时拉数据——脱敏必须只有一个入口，否则 fail-closed 就是摆设。

| 源 | 独有贡献 | 状态 |
|---|---|---|
| Claude Code 会话 | 仓库记录什么上线了，会话记录你**试过什么**；带 `cwd`/`gitBranch`，天然贴着提交 | ✅ |
| Codex 会话 | 同上。只导一个助手会让语料偏向你恰好在那个工具里做的事 | ✅ |
| Notion 导出 | 项目文档和会议记录，常常是一个项目唯一的书面记录 | ✅ 导出自动探测，或用 `career-evidence-notion` skill 走 MCP |
| X 归档 | 面向受众的写作 = 兴趣与定位；自我复述的长贴按线程重组 | ✅ 导出自动探测（X API 已改按次计费，导出更划算） |
| 个人博客 / 任何公开地址 | 无报酬写作 = 强兴趣信号 | ✅ `web` 连接器，填链接即可；页面声明了 feed 会自动跟过去 |
| LinkedIn 导出 | 别人写的推荐 + 职位时间脊柱（**不当能力证据**） | 待做 |
| 本地 git 仓库 | 哪些是"你的"由 `git shortlog` 测量，不由模型判断 | ✅ 自动发现 |
| GitHub MCP | 你写在**别人 PR 上的 review 评论**——本地 clone 一条都没有 | 待做 |
| 日历 | 你到底把时间花在哪了 | 待做 |

`career-evidence stage` 让 agent 通过 MCP 取到的内容走同一条流水线——取的方式可以变，
**落成文件再脱敏这一步不能变**，否则闸门就成了摆设。代价见 [`docs/input-layer.md`](docs/input-layer.md#关于-mcp能替代手动配置的和不能的)。

"啥都放"的源（Notion / X）另有一层 `relevance` 分诊：**它决定什么值得发，不决定什么安全发**——
后者永远是 `redact` 的事。导入时会打印相关度直方图，方便调 `min_relevance`。

## 三条不可协商的规则

- **凭据明文永不落盘。** vault 里存的是 PII 的映射，凭据只留 fingerprint。
- **没有引用就没有主张。** `verify` 会把编造的卡片抓出来（fixture 里试过）。
- **可追溯不等于可复现。** `career-evidence reliability` 把最大的未知变成一个数字；
  各次运行不能看见彼此，否则测的是记忆不是信度。
- **说过两遍不等于有佐证。** 一个主题要成为 pattern，至少要有一张**有工件支撑**的卡片——
  否则推文里说一遍、访谈里再说一遍，就能自我认证。
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
| ⑥ 因人而异的提问 | 已完成，10 类候选 + CCI 角度 + 三重配额 → [`docs/career-evidence-profile-and-interview.md`](docs/career-evidence-profile-and-interview.md) |
| ⑤ 画像 | 已完成，骨架 + 强制引用 + 四段结构，`--check` 机器校验 |
| 信度测量 | 已完成，`career-evidence reliability` + `/career-evidence-reliability`；5 个指标 + 受控对照 |
| 行业趋势对照 | 未开始，调研见对话记录（O*NET / Anthropic Economic Index / Lightcast） |
| 量表（RIASEC / IPIP-NEO） | 未开始，且只会用你亲自填的结果，不从材料推断 |

```bash
career-evidence selftest
```
