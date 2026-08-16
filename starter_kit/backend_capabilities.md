# LoomQ 官方《后端能力表》

> **用途**：这是 L2「智能选后端」判定的**唯一基准数据**——评测系统按本表推导每个选型问题的正确答案集，
> 你的 Agent 回复中须包含正确的**规范后端标识**（`id` 列）才计分。
> 同一份数据的机读版是 [backend_capabilities.json](backend_capabilities.json)，
> **强烈建议你的 Agent 直接加载 JSON 作为选型知识库**，而不是把这张表塞进 prompt 里靠模型背诵。
>
> 数值为评测基准快照（2026-07）；各平台实时状态以其控制台公告为准，但**评测判定只认本表**。

| 规范标识 (`id`) | 后端 | 类型 | 比特上限 | 排队特性 | 费用 | 账号要求 |
|---|---|---|---:|---|---|---|
| `spinq_taurus_simulator` | 量旋 SpinQit Taurus 本地模拟器 | 模拟器 | 24 | 无排队 | 免费 | 无 |
| `spinq_cloud_qpu` | 量旋云真机（超导／核磁，2–8 比特） | 真机 | 8 | 分钟～小时级 | 免费额度 | 需注册 |
| `originq_local_simulator` | 本源 pyqpanda 本地模拟器（CPUQVM） | 模拟器 | 30 | 无排队 | 免费 | 无 |
| `originq_wukong` | 本源悟空超导真机（悟空芯 72 比特） | 真机 | 72 | 小时级 | 免费额度 | 需注册 + API Token |
| `braket_local_simulator` | AWS Braket LocalSimulator | 模拟器 | 25 | 无排队 | 免费 | 无 |
| `braket_cloud` | AWS Braket 云端（SV1 / 各厂商 QPU） | 云模拟器／真机 | 34 | 分钟～小时级 | **付费**（按任务 + shots） | 需 AWS 账号 |

## 选型逻辑示例（评测如何用这张表）

评测的选型 prompt 会给出若干**约束条件**，正确答案集 = 表中满足全部约束的后端：

- *"15 比特电路 + 零排队等待"* → 需要 `max_qubits ≥ 15` 且 `queue = none` → 正确答案集：`spinq_taurus_simulator`、`originq_local_simulator`、`braket_local_simulator`（回复包含其中任一规范标识即通过）。
- *"在真实量子硬件上跑一个 5 比特电路，不想花钱"* → `kind = qpu` 且 `max_qubits ≥ 5` 且 `cost ≠ paid` → 正确答案集：`spinq_cloud_qpu`、`originq_wukong`。
- *"50 比特电路"* → 无后端满足 → 正确回答是**如实说明超出所有可用后端能力**，并给出最接近的替代（如拆解电路或用 `originq_wukong` 的 72 比特真机——若约束允许排队）。

## 给 Agent 实现者的提示

1. 把 JSON 加载进你的工具函数（function calling / RAG 均可），让 LLM 按约束筛选，而不是自由发挥——评测 prompt 是未公开变体，背答案无效；
2. 回复中**必须出现规范标识原文**（如 `braket_local_simulator`），只写"AWS 本地模拟器"不计分；
3. 约束冲突或无解时如实说明，比给出错误答案得分更高（"智能"包含知道自己不知道）。
