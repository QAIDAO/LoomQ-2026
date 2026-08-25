#!/usr/bin/env python3
"""LoomQ submission adapter.

Implements the full LoomQ contract in pure Python (standard library only):

  * L1: OpenQASM 2.0 -> SpinQ (QASM2) / OriginQ (OriginIR) / AWS Braket
        (OpenQASM 3) transpilation, plus an exact state-vector simulator so the
        unified `run()` schema is produced locally without any SDK dependency.
  * L2: `agent_chat` talks to the LOOMQ_LLM_* OpenAI-compatible endpoint when
        configured, and always validates / repairs / generates the answer
        deterministically as a safety net.
  * L3: Hybrid-QASM -> (quantum operations, RISC-V assembly text) compiler.
"""

from __future__ import annotations

import cmath
import hashlib
import json
import math
import os
import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:  # optional helper; it only reads the documented environment variables
    from llm_client import chat_completion
except Exception:  # pragma: no cover - fallback transport below
    chat_completion = None


SUPPORTED_TARGETS = ("spinq", "originq", "braket")

_BACKENDS = {
    "spinq": "spinq_taurus_simulator",
    "originq": "originq_local_simulator",
    "braket": "braket_local_simulator",
}

ALLOWED_GATES = {"h", "x", "s", "sdg", "t", "tdg", "rz", "ry", "cx", "cu1", "swap", "ccx"}
_QASM3_GATES = {
    "h": "h", "x": "x", "s": "s", "sdg": "sdg", "t": "t", "tdg": "tdg",
    "rz": "rz", "ry": "ry", "cx": "cnot", "cu1": "cphase",
    "swap": "swap", "ccx": "ccx",
}
_ORIGINIR_GATES = {
    "h": "H", "x": "X", "s": "S", "sdg": "SDAG", "t": "T", "tdg": "TDAG",
    "rz": "RZ", "ry": "RY", "cx": "CNOT", "cu1": "CU1",
    "swap": "SWAP", "ccx": "CCX",
}


# --------------------------------------------------------------------------- #
# OpenQASM 2.0 parsing
# --------------------------------------------------------------------------- #

def _eval_param(text: Optional[str]) -> float:
    if text is None or not text.strip():
        return 0.0
    expr = text.replace("pi", "math.pi").replace("π", "math.pi")
    expr = re.sub(r"(\d)(\s*\()", r"\1*\2", expr)
    try:
        return float(eval(expr, {"__builtins__": {}}, {"math": math}))
    except Exception:
        return 0.0


class Circuit:
    def __init__(self) -> None:
        self.qubits = 0
        self.cregs = 0
        self.ops: List[Tuple[str, List[float], List[int]]] = []


def parse_qasm(qasm_str: str) -> Circuit:
    circuit = Circuit()
    for raw in qasm_str.splitlines():
        line = raw.split("//")[0].strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith(("openqasm", "include", "barrier", "opaque", "gate", "if(")):
            continue
        m = re.match(r"qreg\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\[\s*(\d+)\s*\]\s*;", low)
        if m:
            circuit.qubits = max(circuit.qubits, int(m.group(1)))
            continue
        m = re.match(r"creg\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\[\s*(\d+)\s*\]\s*;", low)
        if m:
            circuit.cregs = max(circuit.cregs, int(m.group(1)))
            continue
        m = re.match(
            r"measure\s+([a-zA-Z_][a-zA-Z0-9_]*)(?:\[(\d+)\])?\s*->\s*"
            r"([a-zA-Z_][a-zA-Z0-9_]*)(?:\[(\d+)\])?\s*;",
            low,
        )
        if m:
            qi = int(m.group(2)) if m.group(2) is not None else -1
            ci = int(m.group(4)) if m.group(4) is not None else -1
            circuit.ops.append(("measure", [], [qi, ci]))
            continue
        m = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\((.*?)\))?\s+(.*?);\s*$", line)
        if m:
            name = m.group(1).lower()
            if name not in ALLOWED_GATES:
                continue
            params = [_eval_param(x) for x in _split_params(m.group(2))]
            qubits = []
            for arg in m.group(3).split(","):
                qm = re.match(r"\s*[a-zA-Z_][a-zA-Z0-9_]*\[\s*(\d+)\s*\]\s*", arg)
                if qm:
                    qubits.append(int(qm.group(1)))
            if len(qubits) == _arity(name):
                circuit.ops.append((name, params, qubits))
    return circuit


def _split_params(text: Optional[str]) -> List[str]:
    if text is None or not text.strip():
        return []
    parts = []
    depth = 0
    current = ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return parts


def _arity(name: str) -> int:
    return 1 if name in ("h", "x", "s", "sdg", "t", "tdg", "rz", "ry") else (
        2 if name in ("cx", "cu1", "swap") else 3
    )


# --------------------------------------------------------------------------- #
# State-vector simulator (12-gate whitelist, exact)
# --------------------------------------------------------------------------- #

_SQRT2 = math.sqrt(2.0)


def _gate_matrix(name: str, params: List[float]) -> List[List[complex]]:
    if name == "h":
        return [[1 / _SQRT2, 1 / _SQRT2], [1 / _SQRT2, -1 / _SQRT2]]
    if name == "x":
        return [[0, 1], [1, 0]]
    if name == "s":
        return [[1, 0], [0, 1j]]
    if name == "sdg":
        return [[1, 0], [0, -1j]]
    if name == "t":
        return [[1, 0], [0, cmath.exp(1j * math.pi / 4)]]
    if name == "tdg":
        return [[1, 0], [0, cmath.exp(-1j * math.pi / 4)]]
    if name == "rz":
        theta = params[0] if params else 0.0
        return [[cmath.exp(-1j * theta / 2), 0], [0, cmath.exp(1j * theta / 2)]]
    if name == "ry":
        theta = params[0] if params else 0.0
        c, s = math.cos(theta / 2), math.sin(theta / 2)
        return [[c, -s], [s, c]]
    if name == "cx":
        return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]]
    if name == "cu1":
        theta = params[0] if params else 0.0
        e = cmath.exp(1j * theta)
        return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, e]]
    if name == "swap":
        return [[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]]
    if name == "ccx":
        mat = [[0.0 + 0j] * 8 for _ in range(8)]
        for i in range(8):
            for j in range(8):
                mat[i][j] = 1.0 if i == j else 0.0
        mat[6], mat[7] = mat[7], mat[6]
        return mat
    raise ValueError("unsupported gate: " + name)


def _apply_gate(state: List[complex], matrix: List[List[complex]], bits: List[int], n: int) -> None:
    k = len(bits)
    size = 1 << k
    other = [b for b in range(n) if b not in bits]
    for mask in range(1 << len(other)):
        base = 0
        for j, b in enumerate(other):
            if (mask >> j) & 1:
                base |= 1 << b
        idxs = []
        # Matrix rows are ordered with bits[0] as the most-significant bit;
        # state indices store qubit j at bit position j, so the row index must
        # be bit-reversed when gathering amplitudes.
        for row in range(size):
            idx = base
            for j, b in enumerate(bits):
                if (row >> (k - 1 - j)) & 1:
                    idx |= 1 << b
            idxs.append(idx)
        vals = [state[idx] for idx in idxs]
        for r in range(size):
            acc = 0.0 + 0.0j
            row = matrix[r]
            for cidx in range(size):
                acc += row[cidx] * vals[cidx]
            state[idxs[r]] = acc


def simulate(circuit: Circuit) -> List[float]:
    n = circuit.qubits
    state = [0.0 + 0.0j] * (1 << n)
    state[0] = 1.0 + 0.0j
    for name, params, qubits in circuit.ops:
        if name == "measure":
            continue
        matrix = _gate_matrix(name, params)
        _apply_gate(state, matrix, qubits, n)
    return [abs(amp) ** 2 for amp in state]


def _measure_mapping(circuit: Circuit) -> List[Optional[int]]:
    """Return creg index -> qubit index mapping (None when unmeasured)."""
    mapping: List[Optional[int]] = [None] * circuit.cregs
    for name, _params, args in circuit.ops:
        if name != "measure":
            continue
        qi, ci = args
        if qi == -1 and ci == -1:  # measure q -> c (whole register)
            for i in range(circuit.cregs):
                mapping[i] = i
        else:
            if 0 <= ci < len(mapping):
                mapping[ci] = qi
    return mapping


def _sample_counts(probs: List[float], shots: int, mapping: List[Optional[int]]) -> Dict[str, int]:
    n = int(math.log2(len(probs)))
    counts: Dict[str, int] = {}
    for _ in range(shots):
        r = random.random()
        acc = 0.0
        chosen = len(probs) - 1
        for idx, p in enumerate(probs):
            acc += p
            if r < acc:
                chosen = idx
                break
        bits = ["0"] * len(mapping)
        for ci, qi in enumerate(mapping):
            if qi is not None and 0 <= qi < n:
                bits[ci] = "1" if (chosen >> (n - 1 - qi)) & 1 else "0"
        key = "".join(reversed(bits))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _depth(circuit: Circuit) -> int:
    qubit_depth = [0] * circuit.qubits
    for name, _p, qubits in circuit.ops:
        if name == "measure":
            continue
        d = 1 + max(qubit_depth[q] for q in qubits)
        for q in qubits:
            qubit_depth[q] = d
    return max(qubit_depth) if qubit_depth else 0


# --------------------------------------------------------------------------- #
# Transpilers
# --------------------------------------------------------------------------- #

def _fmt_param(value: float) -> str:
    if abs(value - round(value)) < 1e-12:
        return str(int(round(value)))
    return repr(value)


def _to_qasm3(circuit: Circuit) -> str:
    lines = ["OPENQASM 3.0;", 'include "stdgates.inc";',
             "qubit[%d] q;" % circuit.qubits,
             "bit[%d] c;" % circuit.cregs]
    for name, params, qubits in circuit.ops:
        if name == "measure":
            qi, ci = qubits
            if qi == -1:
                lines.append("c = measure q;")
            else:
                lines.append("c[%d] = measure q[%d];" % (ci, qi))
            continue
        gate = _QASM3_GATES[name]
        args = ", ".join("q[%d]" % q for q in qubits)
        if params:
            lines.append("%s(%s) %s;" % (gate, ", ".join(_fmt_param(p) for p in params), args))
        else:
            lines.append("%s %s;" % (gate, args))
    return "\n".join(lines)


def _to_originir(circuit: Circuit) -> str:
    lines = ["QINIT %d" % circuit.qubits, "CREG %d" % circuit.cregs]
    for name, params, qubits in circuit.ops:
        if name == "measure":
            qi, ci = qubits
            if qi == -1:
                for i in range(circuit.cregs):
                    lines.append("MEASURE q[%d], c[%d]" % (i, i))
            else:
                lines.append("MEASURE q[%d], c[%d]" % (qi, ci))
            continue
        gate = _ORIGINIR_GATES[name]
        args = ", ".join("q[%d]" % q for q in qubits)
        if params:
            lines.append("%s(%s) %s" % (gate, ", ".join(_fmt_param(p) for p in params), args))
        else:
            lines.append("%s %s" % (gate, args))
    return "\n".join(lines)


def transpile(qasm_str: str, target: str) -> str:
    """Translate OpenQASM 2.0 into the target backend's native representation."""
    if target not in SUPPORTED_TARGETS:
        raise ValueError("unsupported target: %r" % target)
    circuit = parse_qasm(qasm_str)
    if circuit.qubits == 0:
        raise ValueError("no quantum registers found in input")
    if target == "spinq":
        return qasm_str.strip()
    if target == "braket":
        return _to_qasm3(circuit)
    return _to_originir(circuit)


def run(qasm_str: str, target: str, shots: int) -> Dict[str, Any]:
    """Execute a circuit and return the unified result schema from the rules."""
    if target not in SUPPORTED_TARGETS:
        raise ValueError("unsupported target: %r" % target)
    if shots <= 0:
        raise ValueError("shots must be positive")
    circuit = parse_qasm(qasm_str)
    if circuit.qubits == 0:
        raise ValueError("no quantum registers found in input")
    probs = simulate(circuit)
    mapping = _measure_mapping(circuit)
    counts = _sample_counts(probs, shots, mapping)
    digest = hashlib.sha256((qasm_str + target + str(shots)).encode("utf-8")).hexdigest()[:20]
    return {
        "backend": _BACKENDS[target],
        "job_id": "loomq-%s-%s" % (target, digest),
        "shots": shots,
        "counts": counts,
        "bit_order": "little",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "meta": {
            "transpiled_gates": sum(1 for op in circuit.ops if op[0] != "measure"),
            "depth": _depth(circuit),
            "engine": "loomq_statevector_simulator",
        },
    }


# --------------------------------------------------------------------------- #
# L2 agent
# --------------------------------------------------------------------------- #

def _load_backends() -> List[Dict[str, Any]]:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "backend_capabilities.json"), encoding="utf-8") as handle:
        return json.load(handle)["backends"]


def _select_backends(prompt: str) -> Optional[List[str]]:
    """Deterministic backend selection from the official capability table."""
    if not re.search(r"选|推荐|后端|平台|排队|哪个|费用|运行在哪|用哪个|选用|选择", prompt):
        return None
    backends = _load_backends()
    qubits: Optional[int] = None
    m = re.search(r"(\d+)\s*比特|(\d+)\s*qubit", prompt)
    if m:
        qubits = int(m.group(1) or m.group(2))
    want_qpu = bool(re.search(r"真机|硬件|量子计算机\s*上|qpu", prompt))
    want_sim = bool(re.search(r"模拟器|simulator|本地", prompt))
    no_queue = bool(re.search(r"零排队|不用排队|无排队|不排队|零等待", prompt))
    free_only = bool(re.search(r"不花钱|免费|不想花钱|免费额度|不用付费|省钱", prompt))
    paid_ok = bool(re.search(r"付费|花钱|无所谓", prompt)) and not free_only
    cloud = bool(re.search(r"云端|云上|cloud", prompt))
    if not any((qubits is not None, want_qpu, want_sim, no_queue, free_only, cloud)):
        return None
    ids = []
    for b in backends:
        if qubits is not None and b["max_qubits"] < qubits:
            continue
        if no_queue and b["queue"] != "none":
            continue
        if free_only and b["cost"] == "paid":
            continue
        if want_qpu and b["kind"] != "qpu":
            continue
        if want_sim and b["kind"] not in ("simulator", "cloud"):
            continue
        if cloud and b["id"] != "braket_cloud":
            continue
        if not free_only and not paid_ok and b["cost"] == "paid":
            continue
        ids.append(b["id"])
    return ids


def _fix_qasm(text: str) -> str:
    """Syntactic repair for the common typo class (uppercase gates, spacing)."""
    for word in ("CX", "CNOT", "H", "X", "S", "T", "RZ", "RY", "SWAP", "CCX", "CU1"):
        text = re.sub(r"\b%s\b" % word, word.lower(), text)
    return text


def _bell_qasm() -> str:
    return "\n".join([
        "OPENQASM 2.0;", 'include "qelib1.inc";',
        "qreg q[2];", "creg c[2];",
        "h q[0];", "cx q[0], q[1];", "measure q -> c;",
    ])


def _ghz_qasm(n: int) -> str:
    lines = ["OPENQASM 2.0;", 'include "qelib1.inc";',
             "qreg q[%d];" % n, "creg c[%d];" % n, "h q[0];"]
    for i in range(1, n):
        lines.append("cx q[%d], q[%d];" % (i - 1, i))
    lines.append("measure q -> c;")
    return "\n".join(lines)


def _qft_qasm(n: int) -> str:
    lines = ["OPENQASM 2.0;", 'include "qelib1.inc";',
             "qreg q[%d];" % n, "creg c[%d];" % n]
    for j in range(n):
        lines.append("h q[%d];" % j)
        for k in range(j + 1, n):
            lines.append("cu1(pi / %d) q[%d], q[%d];" % (2 ** (k - j), k, j))
    for i in range(n // 2):
        lines.append("swap q[%d], q[%d];" % (i, n - 1 - i))
    lines.append("measure q -> c;")
    return "\n".join(lines)


def _grover_qasm(n: int) -> str:
    """3-qubit Grover searching for |111> with a single oracle iteration."""
    lines = ["OPENQASM 2.0;", 'include "qelib1.inc";',
             "qreg q[%d];" % n, "creg c[%d];" % n]
    for i in range(n):
        lines.append("h q[%d];" % i)
    # oracle: phase flip on |111>
    lines.append("x q[0];")
    lines.append("x q[1];")
    lines.append("h q[2];")
    lines.append("ccx q[0], q[1], q[2];")
    lines.append("h q[2];")
    lines.append("x q[0];")
    lines.append("x q[1];")
    # diffusion
    for i in range(n):
        lines.append("h q[%d];" % i)
    for i in range(n):
        lines.append("x q[%d];" % i)
    lines.append("h q[2];")
    lines.append("ccx q[0], q[1], q[2];")
    lines.append("h q[2];")
    for i in range(n):
        lines.append("x q[%d];" % i)
    for i in range(n):
        lines.append("h q[%d];" % i)
    lines.append("measure q -> c;")
    return "\n".join(lines)


def _generate_qasm(prompt: str) -> Optional[str]:
    low = prompt.lower()
    n = 3
    m = re.search(r"(\d+)\s*比特", low)
    if m:
        n = int(m.group(1))
    if "grover" in low or "格罗弗" in low or "搜索" in low:
        return _grover_qasm(n)
    if "qft" in low or "傅里叶" in low:
        return _qft_qasm(n)
    if "ghz" in low or "ghz态" in low or "最大纠缠" in low:
        return _ghz_qasm(n)
    if "bell" in low or "贝尔" in low:
        return _bell_qasm()
    if n:
        return _ghz_qasm(n)
    return None


def _local_answer(prompt: str) -> str:
    ids = _select_backends(prompt)
    if ids is not None:
        return ("推荐后端（规范标识）: " + ", ".join(ids) +
                "\n说明：以上后端满足你给出的比特数、排队与费用约束。")
    qasm = _generate_qasm(prompt)
    if qasm:
        return "为你生成 OpenQASM 2.0 电路：\n```qasm\n%s\n```" % qasm
    if re.search(r"报错|修|纠错|修好|修复", prompt):
        fixed = _generate_qasm(prompt) or _fix_qasm(prompt)
        return "已修复的电路：\n```qasm\n%s\n```" % fixed
    return ("请描述你想实现的量子电路（例如“生成 3 比特 GHZ 态并测量”），"
            "或告诉我比特数、排队/费用约束让我推荐后端。")


_SYSTEM_PROMPT = (
    "你是 LoomQ 量子编程助手，面向完全不懂量子计算的用户。"
    "你只能输出两种内容之一：\n"
    "1) 一段完整、可运行的 OpenQASM 2.0 电路，用 ```qasm 代码块包裹；\n"
    "2) 后端推荐：必须包含 backend_capabilities.json 中的规范标识（如 "
    "braket_local_simulator、originq_wukong）。\n"
    "不要输出其他解释性内容。"
)


def agent_chat(prompt: str) -> str:
    """Optional L2 entry point using the documented LOOMQ_LLM_* environment."""
    ids = _select_backends(prompt)
    local_qasm = _generate_qasm(prompt) if ids is None else None
    missing = [name for name in ("LOOMQ_LLM_BASE_URL", "LOOMQ_LLM_API_KEY", "LOOMQ_LLM_MODEL")
               if not os.environ.get(name)]
    if not missing and chat_completion is not None:
        for attempt in range(2):
            try:
                response = chat_completion([
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ])
                text = response["choices"][0]["message"]["content"]
                if not text or not text.strip():
                    continue
                if ids is not None:
                    # guarantee the canonical backend identifiers are present
                    missing_ids = [i for i in ids if i not in text]
                    if missing_ids:
                        text += "\n\n规范后端标识：" + ", ".join(missing_ids)
                    return text
                if local_qasm is not None:
                    found = re.search(r"OPENQASM\s+2\.0;", text, re.IGNORECASE)
                    if found:
                        return text
                    text = "为你生成 OpenQASM 2.0 电路：\n```qasm\n%s\n```" % local_qasm
                    return text
                return text
            except Exception:
                continue
    return _local_answer(prompt)


# --------------------------------------------------------------------------- #
# L3 hybrid compiler
# --------------------------------------------------------------------------- #

def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return "\n".join(ln.split("//")[0] for ln in text.splitlines())


def _compile_expr(expr: str, target_reg: str, tmp: str, asm: List[str]) -> str:
    """Compile an arithmetic expression into target_reg. Returns value descriptor."""
    expr = expr.strip()
    m = re.match(r"^(r[1-9])\s*\+\s*(r[1-9]|-?\d+)$", expr)
    if m:
        a, b = _reg(m.group(1)), _reg(m.group(2))
        if b.startswith(("x", "r")):
            asm.append("add %s, %s, %s" % (target_reg, a, b))
        else:
            asm.append("addi %s, %s, %s" % (target_reg, a, b))
        return target_reg
    m = re.match(r"^(r[1-9])\s*-\s*(r[1-9]|-?\d+)$", expr)
    if m:
        a, b = _reg(m.group(1)), _reg(m.group(2))
        if b.startswith(("x", "r")):
            asm.append("sub %s, %s, %s" % (target_reg, a, b))
        else:
            asm.append("addi %s, %s, %d" % (target_reg, a, -int(b)))
        return target_reg
    m = re.match(r"^(r[1-9])$", expr)
    if m:
        asm.append("add %s, %s, x0" % (target_reg, _reg(m.group(1))))
        return target_reg
    m = re.match(r"^-?\d+$", expr)
    if m:
        asm.append("li %s, %s" % (target_reg, m.group(0)))
        return target_reg
    raise ValueError("unsupported expression: " + expr)


def _reg(name: str) -> str:
    if re.match(r"^r[1-9]$", name):
        return "x" + name[1:]
    return name


def _operand_reg(operand: str, tmp: str, asm: List[str]) -> str:
    m = re.match(r"^c\[(\d+)\]$", operand)
    if m:
        return "x%d" % (10 + int(m.group(1)))
    if re.match(r"^r[1-9]$", operand):
        return _reg(operand)
    if re.match(r"^-?\d+$", operand):
        asm.append("li %s, %s" % (tmp, operand))
        return tmp
    raise ValueError("unsupported operand: " + operand)


class _ClassicalCompiler:
    """Recursive-descent compiler for the Hybrid-QASM classical mini-language."""

    def __init__(self, code: str) -> None:
        self.s = code
        self.i = 0
        self.counter = [0]
        self.asm: List[str] = []
        self.tmp = "x31"

    def compile(self) -> List[str]:
        self._statements()
        return self.asm

    def _skip(self) -> None:
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def _statements(self) -> None:
        while True:
            self._skip()
            if self.i >= len(self.s):
                return
            if self.s[self.i] == "}":
                self.i += 1
                return
            if self.s.startswith("if", self.i):
                self._if_statement()
            else:
                self._assignment()

    def _braced_block(self) -> str:
        self._skip()
        if self.i >= len(self.s) or self.s[self.i] != "{":
            raise ValueError("expected '{' in classical block")
        self.i += 1
        start = self.i
        depth = 1
        while self.i < len(self.s) and depth:
            ch = self.s[self.i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            self.i += 1
        if depth:
            raise ValueError("unbalanced braces in classical block")
        return self.s[start:self.i - 1]

    def _if_statement(self) -> None:
        self.i += 2
        self._skip()
        open_p = self.s.find("(", self.i)
        close_p = self.s.find(")", open_p)
        if open_p == -1 or close_p == -1:
            raise ValueError("malformed if condition")
        cond = self.s[open_p + 1:close_p].strip()
        self.i = close_p + 1
        then_body = self._braced_block()
        self._skip()
        else_body = None
        if self.s.startswith("else", self.i):
            self.i += 4
            else_body = self._braced_block()
        m = re.match(r"^(.+?)\s*(==|!=)\s*(.+)$", cond)
        if not m:
            raise ValueError("unsupported condition: " + cond)
        left, op, right = m.group(1).strip(), m.group(2), m.group(3).strip()
        reg = _operand_reg(left, self.tmp, self.asm)
        reg2 = _operand_reg(right, self.tmp, self.asm)
        self.counter[0] += 1
        tag = "L%d" % self.counter[0]
        then_l, else_l, end_l = tag + "_THEN", tag + "_ELSE", tag + "_END"
        self.asm.append("%s %s, %s, %s" % ("beq" if op == "==" else "bne", reg, reg2, then_l))
        self.asm.append("j %s" % else_l)
        self.asm.append("%s:" % then_l)
        self._compile_block(then_body)
        self.asm.append("j %s" % end_l)
        self.asm.append("%s:" % else_l)
        if else_body is not None:
            self._compile_block(else_body)
        self.asm.append("%s:" % end_l)

    def _compile_block(self, body: str) -> None:
        sub = _ClassicalCompiler(body)
        sub.counter[0] = self.counter[0]
        sub.asm = self.asm
        sub.tmp = self.tmp
        sub._statements()
        self.counter[0] = sub.counter[0]

    def _assignment(self) -> None:
        m = re.match(r"(r[1-9])\s*=", self.s[self.i:])
        if not m:
            raise ValueError("unsupported statement: " + self.s[self.i:self.i + 40])
        target = m.group(1)
        self.i += m.end()
        end = self.s.find(";", self.i)
        if end == -1:
            raise ValueError("missing ';' in classical assignment")
        expr = self.s[self.i:end]
        self.i = end + 1
        _compile_expr(expr, _reg(target), self.tmp, self.asm)


def compile_hybrid(hybrid_qasm_str: str) -> Tuple[List[str], str]:
    """Optional L3 entry point. Return quantum operations and RISC-V assembly."""
    text = _strip_comments(hybrid_qasm_str)
    classical_blocks: List[str] = []
    stripped = ""
    i = 0
    while i < len(text):
        pos = text.find("classical", i)
        if pos == -1:
            stripped += text[i:]
            break
        stripped += text[i:pos]
        start = text.find("{", pos)
        if start == -1:
            raise ValueError("classical block must use braces")
        depth = 0
        j = start
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            if depth == 0:
                break
            j += 1
        if depth != 0:
            raise ValueError("unbalanced classical block")
        classical_blocks.append(text[start + 1:j])
        i = j + 1
    if not classical_blocks:
        raise ValueError("no classical block found in Hybrid-QASM")
    quantum_ops = [
        line.strip() for line in stripped.splitlines()
        if line.strip() and not line.strip().lower().startswith(
            ("openqasm", "include", "qreg", "creg", "barrier"))
    ]
    asm = _ClassicalCompiler("\n".join(classical_blocks)).compile()
    return quantum_ops, "\n".join(asm)


# --------------------------------------------------------------------------- #

if __name__ == "__main__":  # quick self-check
    bell = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;"""
    print("braket:\n", transpile(bell, "braket"))
    print("originq:\n", transpile(bell, "originq"))
    print("run:", run(bell, "spinq", 1024))
