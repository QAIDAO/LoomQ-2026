"""OpenQASM 2.0 text parser producing a :class:`Circuit`.

This is the single parse entry point for both transpile and run.  The
supported grammar is deliberately small (the 12-gate whitelist): version
line, ``include``, ``qreg``/``creg`` declarations, gate applications with
numeric/``pi`` parameters, and whole-register or bitwise ``measure``.
"""

from __future__ import annotations

import ast
import math
import re

try:
    from .gates import ARITY, PARAM_GATES, WHITELIST
    from .ir import CbitRef, Circuit, Gate, Measurement, QubitRef
except ImportError:
    from gates import ARITY, PARAM_GATES, WHITELIST
    from ir import CbitRef, Circuit, Gate, Measurement, QubitRef


def _strip_comment(line: str) -> str:
    return line.split("//", 1)[0].strip()


def _eval_number(expression: str) -> float:
    """Evaluate a small numeric expression (numbers, pi, + - * / **)."""
    try:
        return float(expression)
    except ValueError:
        pass
    tree = ast.parse(expression, mode="eval")
    return _eval_ast(tree.body)


def _eval_ast(node) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id == "pi":
            return math.pi
        raise ValueError(f"unsupported parameter constant: {node.id}")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_ast(node.operand)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _eval_ast(node.operand)
    if isinstance(node, ast.BinOp):
        left = _eval_ast(node.left)
        right = _eval_ast(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            return left ** right
    raise ValueError(f"unsupported parameter expression: {ast.dump(node)}")


def _name2global(name: str, order: list, index: int) -> int:
    offset = 0
    for reg_name, size in order:
        if reg_name == name:
            return offset + index
        offset += size
    raise ValueError(f"unknown register: {name}")


def _parse_measure(circuit: Circuit, source: str, destination: str) -> Measurement:
    """Resolve a measure statement into expanded qubit/cbit references."""
    whole = re.match(r"^(\w+)$", source)
    dst_reg_match = re.match(r"^(\w+)$", destination)
    dst_bit_match = re.match(r"^(\w+)\[(\d+)\]\s*$", destination)
    if whole and (dst_reg_match or dst_bit_match):
        src_reg = source.strip()
        qregs = [(name, size) for name, size in circuit.qregs if name == src_reg]
        if not qregs:
            raise ValueError(f"unsupported whole-register measure: {source} -> {destination}")
        size = qregs[0][1]
        if dst_reg_match:
            dst_reg = destination.strip()
            cregs = [(name, size) for name, size in circuit.cregs if name == dst_reg]
            if not cregs or cregs[0][1] != size:
                raise ValueError(f"unsupported whole-register measure: {source} -> {destination}")
            dst_reg_name = dst_reg
            dst_start = 0
        else:
            dst_reg_name = dst_bit_match.group(1)
            dst_start = int(dst_bit_match.group(2))
            dst_size = [s for name, s in circuit.cregs if name == dst_reg_name]
            if not dst_size or dst_start + size > dst_size[0]:
                raise ValueError(f"unsupported whole-register measure: {source} -> {destination}")
        qubits = []
        cbits = []
        for index in range(size):
            qubits.append(QubitRef(src_reg, index, _name2global(src_reg, circuit.qregs, index)))
            cbits.append(CbitRef(dst_reg_name, dst_start + index, _name2global(dst_reg_name, circuit.cregs, dst_start + index)))
        # Only a clean aligned pair (q -> c, equal sizes, offset 0) can be
        # rendered as a whole-register statement; everything else stays bitwise.
        aligned = dst_reg_match and dst_start == 0
        return Measurement(qubits=qubits, cbits=cbits, whole_register=aligned)

    src_match = re.match(r"^(\w+)\[(\d+)\]\s*$", source)
    dst_match = re.match(r"^(\w+)\[(\d+)\]\s*$", destination)
    if src_match and dst_match:
        qubit = QubitRef(
            src_match.group(1),
            int(src_match.group(2)),
            _name2global(src_match.group(1), circuit.qregs, int(src_match.group(2))),
        )
        cbit = CbitRef(
            dst_match.group(1),
            int(dst_match.group(2)),
            _name2global(dst_match.group(1), circuit.cregs, int(dst_match.group(2))),
        )
        return Measurement(qubits=[qubit], cbits=[cbit], whole_register=False)

    raise ValueError(f"unsupported measure: {source} -> {destination}")


def _resolve_qubit(circuit: Circuit, token: str) -> QubitRef:
    match = re.match(r"^(\w+)\[(\d+)\]\s*$", token)
    if not match:
        raise ValueError(f"unsupported qubit token: {token}")
    reg, index = match.group(1), int(match.group(2))
    offset = 0
    for name, size in circuit.qregs:
        if name == reg:
            if index < size:
                return QubitRef(reg, index, offset + index)
            raise ValueError(f"qubit index out of range: {token}")
        offset += size
    raise ValueError(f"unknown qubit register: {token}")


def _parse_gate(circuit: Circuit, name: str, params_text: str, targets_text: str) -> Gate:
    name = name.lower()
    if name not in WHITELIST:
        raise ValueError(f"unsupported gate: {name}")

    params = []
    if params_text:
        params = [_eval_number(token) for token in params_text.split(",") if token.strip()]
    if name in PARAM_GATES:
        if len(params) != 1:
            raise ValueError(f"{name} requires exactly one parameter")
    elif params:
        raise ValueError(f"gate {name} takes no parameters")

    tokens = [token.strip() for token in targets_text.split(",") if token.strip()]
    if len(tokens) != ARITY[name]:
        raise ValueError(f"{name} expects {ARITY[name]} qubits, got {tokens}")
    qubits = [_resolve_qubit(circuit, token) for token in tokens]
    return Gate(name=name, params=params, qubits=qubits)


def parse_qasm(qasm_str: str) -> Circuit:
    """Parse OpenQASM 2.0 text into a :class:`Circuit`."""
    circuit = Circuit()
    for raw_line in qasm_str.splitlines():
        line = _strip_comment(raw_line)
        if not line or line.startswith("OPENQASM") or line.startswith("include"):
            continue

        qreg_match = re.match(r"^qreg\s+(\w+)\[(\d+)\]\s*;\s*$", line)
        if qreg_match:
            circuit.qregs.append((qreg_match.group(1), int(qreg_match.group(2))))
            continue
        creg_match = re.match(r"^creg\s+(\w+)\[(\d+)\]\s*;\s*$", line)
        if creg_match:
            circuit.cregs.append((creg_match.group(1), int(creg_match.group(2))))
            continue

        measure_match = re.match(r"^measure\s+(.+?)\s*->\s*(.+?)\s*;\s*$", line)
        if measure_match:
            circuit.measurements.append(
                _parse_measure(circuit, measure_match.group(1).strip(), measure_match.group(2).strip())
            )
            continue

        gate_match = re.match(r"^([A-Za-z0-9_]+)(?:\(([^)]+)\))?\s*(.+?)\s*;\s*$", line)
        if gate_match:
            circuit.gates.append(
                _parse_gate(
                    circuit,
                    gate_match.group(1),
                    gate_match.group(2),
                    gate_match.group(3).strip(),
                )
            )
            continue

        raise ValueError(f"unsupported OpenQASM 2.0 line: {line}")
    return circuit
