#!/usr/bin/env python3
"""
AWS Braket 平台接入最小可跑示例
演示如何在本地模拟器 (LocalSimulator) 上加载并运行 OpenQASM 3.0 线路。
AWS Braket 原生支持 OpenQASM 3.0，对 2.0 标准也可以通过语法调整进行支持。
"""

import json
from braket.devices import LocalSimulator
from braket.ir.openqasm import Program

def main():
    # 1. 准备 OpenQASM 3.0 代码 (Bell State)
    # AWS Braket 底层使用 OpenQASM 3.0 语法
    qasm_3_code = """
    OPENQASM 3.0;
    qubit[2] q;
    bit[2] c;

    h q[0];
    cnot q[0], q[1];

    c = measure q;
    """
    print("--- 待运行的 OpenQASM 3.0 线路 ---")
    print(qasm_3_code.strip())
    print("---------------------------------")

    # 2. 初始化 AWS Braket 本地模拟器（免费，无需云端凭证）
    print("正在初始化 AWS Braket 本地模拟器...")
    device = LocalSimulator()

    # 3. 创建 Braket Program 任务
    program = Program(source=qasm_3_code)

    # 4. 提交并运行
    shots = 1024
    print(f"提交计算任务中，shots={shots}...")
    task = device.run(program, shots=shots)
    
    # 5. 获取结果并解析为 counts
    result = task.result()
    counts = result.measurement_counts
    
    print("\n运行成功！原始 Counts 结果:")
    print(counts)
    
    # 6. 转化为大赛规定的统一 JSON 输出格式
    unified_result = {
        "backend": "aws_local_simulator",
        "job_id": result.task_metadata.id,
        "shots": shots,
        "counts": dict(counts),
        "bit_order": "little",
        "timestamp": result.additional_metadata.action.startTime if hasattr(result, 'additional_metadata') else "2026-07-06T10:00:00Z",
        "meta": {
            "qubits_count": 2,
            "depth": len(result.measured_qubits)
        }
    }
    
    print("\n符合大赛契约的统一 JSON 结构:")
    print(json.dumps(unified_result, indent=2))

if __name__ == "__main__":
    main()
