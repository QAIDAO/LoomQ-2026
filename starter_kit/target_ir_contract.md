# Target IR Contract v1.0

正式 L1 评测不仅检查 `run()` 返回的 counts，也会解析并模拟 `transpile()` 的返回值。为保证不同实现可公平自动判定，请输出以下规范子集。

## `spinq`

返回完整、可执行的 OpenQASM 2.0。允许门集与题面 12 门白名单一致，必须包含寄存器声明和测量语句。

## `braket`

返回完整 OpenQASM 3：

```qasm
OPENQASM 3.0;
include "stdgates.inc";
qubit[2] q;
bit[2] c;
h q[0];
cnot q[0], q[1];
c = measure q;
```

评测器接受 `cx` 或 `cnot`，以及整寄存器或逐位测量赋值。

## `originq`

返回 OriginIR 文本，使用以下规范写法：

```text
QINIT 2
CREG 2
H q[0]
CNOT q[0], q[1]
MEASURE q[0], c[0]
MEASURE q[1], c[1]
```

允许门名：`H X S SDAG T TDAG RY RZ CNOT CU1/CR SWAP TOFFOLI/CCX`。参数门同时接受 `RY(θ) q[0]` 与 `RY q[0],(θ)`。

## 判定

组织方将目标 IR 转换为参考语义并进行无噪声模拟。目标 IR 与输入 QASM 的分布必须一致；返回任意占位字符串、注释或与输入无关的固定电路均计失败。
