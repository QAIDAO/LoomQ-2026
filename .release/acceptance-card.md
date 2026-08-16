# Acceptance Card

- Deliverable: LoomQ 线上比赛题面发布包
- Owner: QAIDAO/LoomQ-2026
- Canonical source: `problem_statement.md`
- Target release: 2026-08-12 线上赛规则修订

## Frozen brief

- Audience: 线上参赛选手、评委与赛事工作人员
- Purpose / decision this artifact supports: 明确比赛全程通过网络提交和评测，不要求选手同步陈述、在指定地点操作或口头解释
- Required facts, sections, or steps: 保留自动评测、变体复测、代码审查和可运行提交要求；明确交互体验由工作人员基于最终提交异步评测
- Forbidden content or claims: 任何要求选手在指定时间或地点同步出席、操作、演示、陈述或解释的内容
- Approved visual direction / tone: 保持现有正式赛题风格，仅做必要文字修订
- Formats and dimensions: Markdown、HTML、DOCX、PDF，以及发布说明和证据模板

## Verification matrix

| Criterion | Evidence required | Evidence location | Status (`pass/blocked/fail`) |
| --- | --- | --- | --- |
| Required content is complete | 修订段落与文本检索 | `problem_statement.md`、`README.md`、`starter_kit/evidence/README.md` | pass |
| Forbidden/stale content is absent | 仓库及发布文件全文检索 | `.release/verification.txt` | pass |
| Facts match the source of truth | 用户原始修订要求 | 本次对话（2026-08-12，Asia/Singapore） | pass |
| Layout is usable in target formats | DOCX 原生预览、PDF 全页渲染与 HTML 截图检查 | `.release/qa/` | pass |
| Canonical release is identified | 发布清单与 SHA-256 | `.release/release-manifest.yaml` | pass |

## Assumptions and approvals

- Assumptions: 用户要求指移除所有同步出席、同步陈述或指定地点演示前提；不删除“评委”“工作人员”“实际运行”等与在线异步评测兼容的角色或动作。
- Approved by / approval source: 用户本次明确要求
- Approval timestamp and timezone: 2026-08-12, Asia/Singapore

## Final status

- Overall status: pass
- Blocked or failed criteria: 无
- Next action: 提交并发布本次线上赛规则修订
