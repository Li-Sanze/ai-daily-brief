# 内容升级（从封面图方案 Phase 3 拆出）— Plan

> lifecycle_state: developed（代码已完成，待用户审计验收）
> 拆分自: `20260517_cover_image_and_content_upgrade` Phase 3（T6/T7/T8）
> 级别: light

## 背景

原封面图+内容升级方案搁置 2.5 个月，其中 Phase 3（内容升级）无需新增外部依赖，
只增加少量 Stage 2 token 开销，对 GitHub 和 Obsidian 输出都有收益，因此拆出独立实施。
图片部分（Phase 1/2）留在原方案继续待决策。

## 与原方案 T6/T7 的差异（经用户确认）

| 项 | 原方案 | 本方案 |
|----|--------|--------|
| 行业数据 industry_data | ✅ 最多 3 条 | ✅ 保留，表格渲染 |
| 技术趋势 tech_trends | ✅ 最多 2 条 | ✅ 保留（核心价值：跨条目归纳） |
| 风险与争议 risks | ✅ | ❌ 砍掉（与速览重复，只是换筐装） |
| 专家观点 expert_quotes | 优先真人引言 | 降级为"有则显示"，禁止创作引言 |
| 金句（今日洞察） | 被专家观点替代 | 保留，与专家观点共存 |
| archives 同步 | 未提及 | ✅ 新增（Obsidian 分发链路依赖 archives） |
| summary 透传 | 未提及 | ✅ 新增（防幻觉前置条件） |

## 改动清单

### summarizer.py
1. `STAGE2_PROMPT`: 新增要求 6/7/8（行业数据/技术趋势/专家观点，均可选、空则输出 `[]`）
   + JSON schema 示例新增 3 个字段
2. Stage 1 scored dict（成功+fallback 两条路径）: 透传 `summary`（截断 200 字）
3. Stage 2 `candidate_text`: 摘要非空时追加 `摘要:` 行（防止模型从纯标题编造数字/引言）
4. Stage 2 `max_tokens`: 2048 → 4096（防 JSON 截断导致整体 fallback）
5. Stage 2 exception fallback: 补 3 个空数组字段

### outputs.py
6. `format_daily_brief()`: 尾部重构为 tail_sections 列表 + `---` join
   （保持"最后一节不带分隔线"的原有约束），新增 3 个板块，
   顺序: 今日洞察 → 行业数据(表格) → 技术趋势 → 专家观点 → 延伸阅读
7. `write_archive()`: 速览与全部候选之间插入相同 3 个板块（`##` 级标题）
8. 所有新板块"有有效数据才渲染"；行业数据来源无效时显示 `—`，专家观点来源无效时不展示

### feishu_push.py
零改动。`###` 分节 + 表格→键值对转换（:67-91）均为通用逻辑，自动覆盖新板块。

## 防幻觉约束（写入 prompt）

- 行业数据: 数字必须来自标题/摘要原文，禁止推测编造
- 技术趋势: 仅当 ≥2 条新闻反映同一方向；跨条目归纳，不重复焦点/速览单条内容
- 专家观点: 仅当明确包含具名人物直接引言；禁止创作或改写

## 验收标准

1. mock 数据渲染: daily-brief 含 3 个新板块，位置在今日洞察后、延伸阅读前
2. mock 数据渲染: archive 含 3 个新板块，位置在速览后、全部候选前
3. 空数据渲染: 3 个板块完全不出现，输出与改动前逐字节一致（新字段为空时零回归）
4. feishu clean_markdown 对新表格转换为键值对文本，无残留 `|`
5. Stage 2 fallback 路径含全部新字段空数组
6. 线上首跑（Actions）后人工核对行业数据数字与原文一致（防幻觉抽查）

## 风险

- Stage 2 JSON 复杂度上升 → JSON 解析失败时 fallback 到旧格式；新字段形状异常时按空数据处理
- HN/GitHub Trending/HF 源无摘要 → 这些源只能依赖标题提取数字或引言
