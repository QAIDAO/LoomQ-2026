#!/usr/bin/env python3
"""
本源量子 pyqpanda 平台接入最小可跑示例
演示如何导入 OpenQASM 2.0 字符串，转译为本源量子程序，并运行在 CPU 模拟器上。
"""

import json

try:
    import pyqpanda as pq
except ImportError:
    # 允许没有安装 pyqpanda 时仅做语法和结构提示
    pq = None

def run_on_originq_simulator(qasm_str: str, shots: int = 1024) -> dict:
    if pq is None:
        print("[Warning] 未检测到 pyqpanda 模块，无法真正运行本源量子示例。将返回 Mock 数据。")
        return {
            "backend": "originq_cpu_simulator_mock",
            "job_id": "mock-job-123",
            "shots": shots,
            "counts": {"00": 510, "11": 514},
            "bit_order": "little",
            "timestamp": "2026-07-06T10:00:00Z",
            "meta": {"info": "Mock data since pyqpanda is not installed"}
        }

    # 1. 初始化量子虚拟机器 (QVM)
    machine = pq.CPUQVM()
    machine.init_qvm()

    # 2. 转换 QASM 2.0 字符串为 pyqpanda 内部的 QProg (量子程序)
    # pyqpanda 支持通过 convert_qasm_to_qprog 或 convert_qasm_string_to_qprog 导入 QASM
    try:
        # 兼容不同版本 pyqpanda 接口
        if hasattr(pq, 'convert_qasm_string_to_qprog'):
            prog, qreg, creg = pq.convert_qasm_string_to_qprog(qasm_str, machine)
        else:
            prog = pq.convert_qasm_to_qprog(qasm_str, machine)
            # 如果接口只返回 prog，需要从机器获取比特列表
            qreg = machine.get_allocate_qubits()
            creg = machine.get_allocate_cbits()
    except Exception as e:
        raise RuntimeError(f"QASM 转译失败，请检查语法兼容性: {e}")

    # 3. 运行线路
    # 使用 run_with_configuration 进行多次测量采样 (shots)
    result = machine.run_with_configuration(prog, creg, shots)
    
    # 4. 统计结果
    # pyqpanda 返回的 counts 是以十进制或二进制字符串作为 key
    # 我们确保将其标准化为二进制 key，如 "00", "11"
    raw_counts = result
    formatted_counts = {}
    
    # 获取比特总数，以便将十进制格式化为对应长度的二进制串
    num_bits = len(creg)
    for key, val in raw_counts.items():
        # 如果 key 本身是十进制整数，转为二进制字符串
        if isinstance(key, int) or key.isdigit():
            bin_str = bin(int(key))[2:].zfill(num_bits)
            formatted_counts[bin_str] = val
        else:
            formatted_counts[key] = val

    # 5. 释放量子虚拟机器资源
    machine.finalize()

    return {
        "backend": "originq_cpu_simulator",
        "job_id": "originq-sim-job-local",
        "shots": shots,
        "counts": formatted_counts,
        "bit_order": "little",
        "timestamp": "2026-07-06T10:00:00Z",
        "meta": {
            "qubits_count": num_bits,
            "depth": "N/A (Local Simulator)"
        }
    }

def main():
    qasm_str = """
    OPENQASM 2.0;
    include "qelib1.inc";
    qreg q[2];
    creg c[2];
    h q[0];
    cx q[0],q[1];
    measure q[0] -> c[0];
    measure q[1] -> c[1];
    """
    print("--- 待转译的 QASM 2.0 电路 ---")
    print(qasm_str.strip())
    print("----------------------------")

    res = run_on_originq_simulator(qasm_str, shots=1024)
    print("\n运行并标准化后的统一输出结果:")
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
