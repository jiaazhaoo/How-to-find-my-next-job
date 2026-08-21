# How do I find my next job

Align what you have done with the tide of the times.

把自己的代码库、报告、项目材料交给 AI，让它解构出你的能力画像，再反过来问你几个关键问题。
这个仓库目前实现了这条链路的**输入层**——也是最容易被做砸、也最决定后面一切可信度的两步：

1. **脱敏**：闸门 fail-closed，凭据销毁、身份假名化，别名全语料稳定。
2. **深读**：四步漏斗把万级文件收敛到百级，模型只在高信号材料上花 token，产出**带引用、可机器校验**的证据卡片。

设计理由与取舍写在 [`docs/input-layer.md`](docs/input-layer.md)。

## 快速开始

无依赖，Python 3.11+，全程本地运行。

```bash
python -m career init                 # 写出 config/sources.json 模板
$EDITOR config/sources.json           # 填 sources / authors / sensitive_terms
python -m career doctor               # 检查脱敏工具链和配置
python -m career scan                 # ① 扫描 → 清单
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
  01_manifest.jsonl       扫描清单：路径、语言、git ownership
  02_shortlist.jsonl      每个文件的分数、理由、是否入选
  03_redacted/            脱敏后的全文
  03_corpus.json          模型实际看到的摘录（引用校验的基准）
  04_packs/pack-NN.md     交给模型的 read pack
  05_cards.jsonl          证据卡片（模型写）
  06_themes.json          聚类结果 + 矛盾点
  redaction-report.json   脱敏统计（不含明文，可分享）
```

## 三条不可协商的规则

- **凭据明文永不落盘。** vault 里存的是 PII 的映射，凭据只留 fingerprint。
- **没有引用就没有主张。** `verify` 会把编造的卡片抓出来（fixture 里试过）。
- **不做人格推断。** claim 里出现"内向/性格/MBTI"直接判校验失败。LLM 从文本推断大五人格
  与真实量表的相关系数低于 r≈0.30；特质只能来自你亲自填的量表，不能来自模型读你的代码。

## 状态

| 层 | 状态 |
|---|---|
| ① 脱敏 | 已完成，35 个测试 |
| ② 深读 → 证据卡片 | 已完成 |
| ③ 画像（只用 pattern 写） | 未开始 |
| ④ 因人而异的提问（Savickas CCI / RIASEC / IPIP-NEO） | 未开始，原料已经在 `themes` 的 tensions 和卡片的 `open_question` 里 |

```bash
python -m career selftest
```
