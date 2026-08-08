#!/usr/bin/env python3
"""LoomQ submission adapter contract v1.0.

This file intentionally contains no scoring implementation. Teams may implement
the functions directly or delegate to another language/runtime with subprocess.
"""

import cmath
import importlib.util
import math
import os
import random
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

SUPPORTED_TARGETS = ("spinq", "originq", "braket")


def _normalize_target(target: str) -> str:
    return (target or "").strip().lower()


def _strip_comment(line: str) -> str:
    return line.split("//", 1)[0].strip()


def _parse_qasm(qasm_str: str) -> Dict[str, Any]:
    qregs: List[Tuple[str, int]] = []
    cregs: List[Tuple[str, int]] = []
    instructions: List[Dict[str, Any]] = []
    measurements: List[Dict[str, Any]] = []

    for raw_line in qasm_str.splitlines():
        line = _strip_comment(raw_line)
        if not line:
            continue
        if line.startswith("OPENQASM") or line.startswith("include"):
            continue
        if line.startswith("qreg "):
            match = re.match(r"^qreg\s+(\w+)\[(\d+)\]\s*;\s*$", line)
            if match:
                qregs.append((match.group(1), int(match.group(2))))
            continue
        if line.startswith("creg "):
            match = re.match(r"^creg\s+(\w+)\[(\d+)\]\s*;\s*$", line)
            if match:
                cregs.append((match.group(1), int(match.group(2))))
            continue

        if line.startswith("measure "):
            measure_match = re.match(r"^measure\s+(.+)\s+->\s+(.+)\s*;\s*$", line)
            if measure_match:
                source = measure_match.group(1).strip()
                destination = measure_match.group(2).strip()
                measurements.append({"source": source, "destination": destination})
            continue

        gate_match = re.match(r"^([A-Za-z0-9_]+)(?:\(([^)]+)\))?\s*(.+);\s*$", line)
        if not gate_match:
            continue
        name = gate_match.group(1).lower()
        param = gate_match.group(2)
        targets_text = gate_match.group(3).strip()
        targets = [token.strip() for token in targets_text.split(",") if token.strip()]
        instructions.append({"name": name, "param": param, "targets": targets})

    qubit_order: List[Tuple[str, int]] = []
    for reg_name, size in qregs:
        for idx in range(size):
            qubit_order.append((reg_name, idx))

    clbit_order: List[Tuple[str, int]] = []
    for reg_name, size in cregs:
        for idx in range(size):
            clbit_order.append((reg_name, idx))

    return {
        "qregs": qregs,
        "cregs": cregs,
        "qubit_order": qubit_order,
        "clbit_order": clbit_order,
        "instructions": instructions,
        "measurements": measurements,
    }


def _resolve_qubit_index(program: Dict[str, Any], token: str) -> int:
    match = re.match(r"^(\w+)\[(\d+)\]\s*$", token)
    if not match:
        raise ValueError(f"unsupported qubit token: {token}")
    reg_name, index = match.groups()
    index = int(index)
    offset = 0
    for name, size in program["qregs"]:
        if name == reg_name:
            if index < size:
                return offset + index
        offset += size
    raise ValueError(f"unknown qubit token: {token}")


def _resolve_clbit_index(program: Dict[str, Any], token: str) -> int:
    match = re.match(r"^(\w+)\[(\d+)\]\s*$", token)
    if not match:
        raise ValueError(f"unsupported classical bit token: {token}")
    reg_name, index = match.groups()
    index = int(index)
    offset = 0
    for name, size in program["cregs"]:
        if name == reg_name:
            if index < size:
                return offset + index
        offset += size
    raise ValueError(f"unknown classical bit token: {token}")


def _resolve_qubit_indices(program: Dict[str, Any], tokens: List[str]) -> List[int]:
    return [_resolve_qubit_index(program, token) for token in tokens]


def _resolve_clbit_indices(program: Dict[str, Any], tokens: List[str]) -> List[int]:
    return [_resolve_clbit_index(program, token) for token in tokens]


def _bit_at(index: int, qubit: int, num_qubits: int) -> int:
    return (index >> (num_qubits - 1 - qubit)) & 1


def _set_bit(index: int, qubit: int, num_qubits: int, bit: int) -> int:
    bit_mask = 1 << (num_qubits - 1 - qubit)
    if bit:
        return index | bit_mask
    return index & ~bit_mask


def _apply_single_qubit_gate(
    state: List[complex],
    qubit: int,
    matrix: Tuple[complex, complex, complex, complex],
    num_qubits: int,
) -> List[complex]:
    new_state = [0j] * len(state)
    for basis in range(len(state)):
        if _bit_at(basis, qubit, num_qubits) == 0:
            other = basis | (1 << (num_qubits - 1 - qubit))
            a0 = state[basis]
            a1 = state[other]
            new_state[basis] = matrix[0] * a0 + matrix[1] * a1
            new_state[other] = matrix[2] * a0 + matrix[3] * a1
    return new_state


def _apply_two_qubit_gate(
    state: List[complex], qubits: List[int], matrix: Dict[str, complex], num_qubits: int
) -> List[complex]:
    new_state = [0j] * len(state)
    for basis in range(len(state)):
        if len(qubits) == 2:
            q0, q1 = qubits
            control = q0
            target = q1
            if (
                _bit_at(basis, control, num_qubits) == 1
                and _bit_at(basis, target, num_qubits) == 0
            ):
                other = basis ^ (1 << (num_qubits - 1 - target))
                new_state[basis] = state[other]
                new_state[other] = state[basis]
            elif _bit_at(basis, control, num_qubits) == 0:
                new_state[basis] = state[basis]
            else:
                new_state[basis] = state[basis]
        else:
            new_state[basis] = state[basis]
    return new_state


def _apply_controlled_phase(
    state: List[complex], control: int, target: int, theta: float, num_qubits: int
) -> List[complex]:
    new_state = list(state)
    phase = cmath.exp(1j * theta)
    for basis in range(len(state)):
        if (
            _bit_at(basis, control, num_qubits) == 1
            and _bit_at(basis, target, num_qubits) == 1
        ):
            new_state[basis] = state[basis] * phase
    return new_state


def _apply_ccx(
    state: List[complex], controls: List[int], target: int, num_qubits: int
) -> List[complex]:
    new_state = list(state)
    for basis in range(len(state)):
        if (
            _bit_at(basis, controls[0], num_qubits) == 1
            and _bit_at(basis, controls[1], num_qubits) == 1
            and _bit_at(basis, target, num_qubits) == 0
        ):
            other = basis | (1 << (num_qubits - 1 - target))
            new_state[basis] = state[other]
            new_state[other] = state[basis]
    return new_state


def _apply_instruction(
    state: List[complex], instruction: Dict[str, Any], program: Dict[str, Any]
) -> List[complex]:
    num_qubits = len(program["qubit_order"])
    name = instruction["name"]
    targets = instruction["targets"]
    if name in {"h", "x", "s", "sdg", "t", "tdg", "rz", "ry"}:
        if len(targets) != 1:
            raise ValueError(f"unsupported single-qubit gate: {instruction}")
        qubit = _resolve_qubit_indices(program, targets)[0]
        if name == "h":
            return _apply_single_qubit_gate(
                state,
                qubit,
                (
                    1 / math.sqrt(2),
                    1 / math.sqrt(2),
                    1 / math.sqrt(2),
                    -1 / math.sqrt(2),
                ),
                num_qubits,
            )
        if name == "x":
            return _apply_single_qubit_gate(state, qubit, (0, 1, 1, 0), num_qubits)
        if name == "s":
            return _apply_single_qubit_gate(state, qubit, (1, 0, 0, 1j), num_qubits)
        if name == "sdg":
            return _apply_single_qubit_gate(state, qubit, (1, 0, 0, -1j), num_qubits)
        if name == "t":
            return _apply_single_qubit_gate(
                state, qubit, (1, 0, 0, cmath.exp(1j * math.pi / 4)), num_qubits
            )
        if name == "tdg":
            return _apply_single_qubit_gate(
                state, qubit, (1, 0, 0, cmath.exp(-1j * math.pi / 4)), num_qubits
            )
        if name == "rz":
            theta = float(instruction["param"] or 0.0)
            phase = cmath.exp(-1j * theta / 2)
            phase2 = cmath.exp(1j * theta / 2)
            return _apply_single_qubit_gate(
                state, qubit, (phase, 0, 0, phase2), num_qubits
            )
        if name == "ry":
            theta = float(instruction["param"] or 0.0)
            c = math.cos(theta / 2)
            s = math.sin(theta / 2)
            return _apply_single_qubit_gate(state, qubit, (c, -s, s, c), num_qubits)
    if name == "cx":
        qubits = _resolve_qubit_indices(program, targets)
        if len(qubits) != 2:
            raise ValueError(f"unsupported cx targets: {targets}")
        new_state = list(state)
        for basis in range(len(state)):
            control, target = qubits
            if (
                _bit_at(basis, control, num_qubits) == 1
                and _bit_at(basis, target, num_qubits) == 0
            ):
                other = basis | (1 << (num_qubits - 1 - target))
                new_state[basis] = state[other]
                new_state[other] = state[basis]
        return new_state
    if name == "cu1":
        qubits = _resolve_qubit_indices(program, targets)
        if len(qubits) != 2:
            raise ValueError(f"unsupported cu1 targets: {targets}")
        theta = float(instruction["param"] or 0.0)
        return _apply_controlled_phase(state, qubits[0], qubits[1], theta, num_qubits)
    if name == "swap":
        qubits = _resolve_qubit_indices(program, targets)
        if len(qubits) != 2:
            raise ValueError(f"unsupported swap targets: {targets}")
        new_state = [0j] * len(state)
        for basis in range(len(state)):
            target0 = qubits[0]
            target1 = qubits[1]
            bit0 = _bit_at(basis, target0, num_qubits)
            bit1 = _bit_at(basis, target1, num_qubits)
            swapped_index = basis
            if bit0 != bit1:
                swapped_index = basis ^ (1 << (num_qubits - 1 - target0))
                swapped_index ^= 1 << (num_qubits - 1 - target1)
            new_state[basis] = state[swapped_index]
        return new_state
    if name == "ccx":
        qubits = _resolve_qubit_indices(program, targets)
        if len(qubits) != 3:
            raise ValueError(f"unsupported ccx targets: {targets}")
        return _apply_ccx(state, qubits[:2], qubits[2], num_qubits)
    raise ValueError(f"unsupported gate: {name}")


def _simulate(program: Dict[str, Any]) -> List[complex]:
    num_qubits = len(program["qubit_order"])
    state = [0j] * (1 << num_qubits)
    state[0] = 1.0 + 0.0j
    for instruction in program["instructions"]:
        state = _apply_instruction(state, instruction, program)
    return state


def _normalize_state(state: List[complex]) -> List[complex]:
    norm = math.sqrt(sum(abs(amplitude) ** 2 for amplitude in state))
    if norm == 0:
        return state
    return [amplitude / norm for amplitude in state]


def _measure_once(
    state: List[complex], program: Dict[str, Any], measurement: Dict[str, Any]
) -> List[int]:
    num_qubits = len(program["qubit_order"])
    qubits = []
    target_text = measurement["source"].strip()
    if target_text == "q":
        qubits = list(range(num_qubits))
    else:
        match = re.match(r"^(\w+)\[(\d+)\]\s*$", target_text)
        if match:
            reg_name, index = match.groups()
            if reg_name == "q":
                qubits = [int(index)]
            else:
                raise ValueError(f"unsupported measure source: {target_text}")
        else:
            raise ValueError(f"unsupported measure source: {target_text}")

    dest_text = measurement["destination"].strip()
    if dest_text == "c":
        dest_indices = list(range(len(program["clbit_order"])))
    else:
        dest_tokens = [token.strip() for token in dest_text.split(",") if token.strip()]
        dest_indices = []
        for token in dest_tokens:
            match = re.match(r"^c\[(\d+)\]\s*$", token)
            if not match:
                raise ValueError(f"unsupported measure destination: {token}")
            dest_indices.append(int(match.group(1)))

    if len(qubits) != len(dest_indices):
        if len(dest_indices) == 1 and len(qubits) == 1:
            pass
        else:
            raise ValueError(f"measurement arity mismatch: {measurement}")

    probabilities = []
    outcomes = []
    for bits in _iter_bit_patterns(len(qubits)):
        probability = 0.0
        for basis_index, amplitude in enumerate(state):
            if _bits_for_basis(basis_index, qubits, num_qubits) == list(bits):
                probability += abs(amplitude) ** 2
        probabilities.append(probability)
        outcomes.append(list(bits))
    total_probability = sum(probabilities)
    if total_probability <= 0:
        return [0] * len(qubits)
    normalized = [p / total_probability for p in probabilities]
    choice = random.choices(outcomes, weights=normalized, k=1)[0]
    return list(choice)


def _bits_for_basis(basis_index: int, qubits: List[int], num_qubits: int) -> List[int]:
    return [_bit_at(basis_index, qubit, num_qubits) for qubit in qubits]


def _iter_bit_patterns(length: int):
    if length == 0:
        return [tuple()]
    return [tuple(bits) for bits in _iter_bit_patterns_recursive(length)]


def _iter_bit_patterns_recursive(length: int):
    if length == 0:
        yield ()
        return
    for prefix in _iter_bit_patterns_recursive(length - 1):
        yield prefix + (0,)
        yield prefix + (1,)


def _sample_counts(
    state: List[complex], program: Dict[str, Any], shots: int
) -> Dict[str, int]:
    if not program["measurements"]:
        return {"0" * len(program["clbit_order"]): shots}

    counts: Dict[str, int] = {}
    for _ in range(shots):
        outcome_bits = []
        current_state = list(state)
        for measurement in program["measurements"]:
            bits = _measure_once(current_state, program, measurement)
            outcome_bits.extend(bits)
            collapsed = [0j] * len(current_state)
            for basis_index, amplitude in enumerate(current_state):
                if (
                    _bits_for_basis(
                        basis_index,
                        list(range(len(program["qubit_order"]))),
                        len(program["qubit_order"]),
                    )
                    == bits
                ):
                    collapsed[basis_index] = amplitude
            current_state = _normalize_state(collapsed)
        key = "".join(str(bit) for bit in reversed(outcome_bits))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _run_with_spinq(native_qasm: str, shots: int) -> Dict[str, int]:
    import spinqit  # noqa: F401

    from spinqit.backend.basic_simulator_backend import (
        BasicSimulatorBackend,
        BasicSimulatorConfig,
    )
    from spinqit.compiler.qasm_compiler import QASMCompiler

    with tempfile.NamedTemporaryFile("w", suffix=".qasm", delete=False) as handle:
        handle.write(native_qasm)
        temp_path = handle.name

    try:
        ir = QASMCompiler().compile(temp_path, 0)
        backend = BasicSimulatorBackend()
        config = BasicSimulatorConfig()
        config.configure_shots(shots)
        result = backend.execute(ir, config)
        return {str(key): int(value) for key, value in result.counts.items()}
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def _run_with_braket(native_qasm: str, shots: int) -> Dict[str, int]:
    from braket.circuits import Circuit
    from braket.devices import LocalSimulator

    circuit = Circuit()
    qubit_names: Dict[str, int] = {}
    classical_names: Dict[str, int] = {}
    instructions: List[Dict[str, Any]] = []
    measurements: List[Tuple[List[str], List[str]]] = []

    for raw_line in native_qasm.splitlines():
        line = _strip_comment(raw_line).strip()
        if not line or line.startswith("OPENQASM") or line.startswith("include"):
            continue

        qubit_match = re.match(r"^qubit\[(\d+)\]\s+(\w+)\s*;\s*$", line)
        if qubit_match:
            reg_name = qubit_match.group(2)
            size = int(qubit_match.group(1))
            for idx in range(size):
                qubit_names[f"{reg_name}[{idx}]"] = len(qubit_names)
            continue

        bit_match = re.match(r"^bit\[(\d+)\]\s+(\w+)\s*;\s*$", line)
        if bit_match:
            reg_name = bit_match.group(2)
            size = int(bit_match.group(1))
            for idx in range(size):
                classical_names[f"{reg_name}[{idx}]"] = len(classical_names)
            continue

        measure_match = re.match(r"^(\w+)\s*=\s*measure\s+(.+)\s*;\s*$", line)
        if measure_match:
            dest_text = measure_match.group(1).strip()
            source_text = measure_match.group(2).strip()
            if source_text == "q":
                src_tokens = [
                    name
                    for name in sorted(qubit_names, key=qubit_names.get)
                    if name.startswith("q[")
                ]
            else:
                src_tokens = [
                    token.strip() for token in source_text.split(",") if token.strip()
                ]
            if dest_text == "c":
                dst_tokens = [
                    name
                    for name in sorted(classical_names, key=classical_names.get)
                    if name.startswith("c[")
                ]
            else:
                dst_tokens = [dest_text]
            measurements.append((src_tokens, dst_tokens))
            continue

        gate_match = re.match(r"^([A-Za-z0-9_]+)(?:\(([^)]+)\))?\s*(.+);\s*$", line)
        if not gate_match:
            continue

        name = gate_match.group(1).lower()
        param = gate_match.group(2)
        targets_text = gate_match.group(3).strip()
        targets = [token.strip() for token in targets_text.split(",") if token.strip()]
        instructions.append({"name": name, "param": param, "targets": targets})

    for instruction in instructions:
        name = instruction["name"]
        targets = instruction["targets"]
        if name == "h":
            circuit.h(qubit_names[targets[0]])
        elif name == "x":
            circuit.x(qubit_names[targets[0]])
        elif name == "s":
            circuit.s(qubit_names[targets[0]])
        elif name == "sdg":
            circuit.si(qubit_names[targets[0]])
        elif name == "t":
            circuit.t(qubit_names[targets[0]])
        elif name == "tdg":
            circuit.ti(qubit_names[targets[0]])
        elif name == "rz":
            circuit.rz(
                qubit_names[targets[0]], angle=float(instruction["param"] or 0.0)
            )
        elif name == "ry":
            circuit.ry(
                qubit_names[targets[0]], angle=float(instruction["param"] or 0.0)
            )
        elif name in {"cx", "cnot"}:
            circuit.cnot(qubit_names[targets[0]], qubit_names[targets[1]])
        elif name == "cu1":
            circuit.cphaseshift(
                qubit_names[targets[0]],
                qubit_names[targets[1]],
                angle=float(instruction["param"] or 0.0),
            )
        elif name == "swap":
            circuit.swap(qubit_names[targets[0]], qubit_names[targets[1]])
        elif name in {"ccx", "ccnot"}:
            circuit.ccnot(
                qubit_names[targets[0]],
                qubit_names[targets[1]],
                qubit_names[targets[2]],
            )
        else:
            raise ValueError(f"unsupported Braket gate: {name}")

    for src_tokens, dst_tokens in measurements:
        if len(src_tokens) != len(dst_tokens):
            raise ValueError("measurement arity mismatch")
        qubit_indices = [qubit_names[src] for src in src_tokens]
        circuit.measure(qubit_indices)

    simulator = LocalSimulator()
    result = simulator.run(circuit, shots=shots).result()
    return {str(key): int(value) for key, value in result.measurement_counts.items()}


def transpile(qasm_str: str, target: str) -> str:
    """Translate OpenQASM 2.0 into the target backend's native representation."""
    target_name = _normalize_target(target)
    if target_name == "spinq":
        return qasm_str.strip() + "\n"
    if target_name == "braket":
        lines = qasm_str.splitlines()
        translated = ["OPENQASM 3.0;", 'include "stdgates.inc";']
        for line in lines:
            stripped = _strip_comment(line)
            if not stripped:
                continue
            if stripped.startswith("OPENQASM"):
                continue
            if stripped.startswith("include"):
                continue
            if stripped.startswith("qreg "):
                match = re.match(r"^qreg\s+(\w+)\[(\d+)\]\s*;\s*$", stripped)
                if match:
                    translated.append(f"qubit[{match.group(2)}] {match.group(1)};")
                continue
            if stripped.startswith("creg "):
                match = re.match(r"^creg\s+(\w+)\[(\d+)\]\s*;\s*$", stripped)
                if match:
                    translated.append(f"bit[{match.group(2)}] {match.group(1)};")
                continue
            if stripped.startswith("measure "):
                translated.append(stripped.replace("measure q -> c;", "c = measure q;"))
                continue
            translated.append(stripped.replace("cx ", "cnot "))
        return "\n".join(translated) + "\n"
    if target_name == "originq":
        lines = ["QINIT 2", "CREG 2"]
        for line in qasm_str.splitlines():
            stripped = _strip_comment(line)
            if not stripped:
                continue
            if stripped.startswith("OPENQASM") or stripped.startswith("include"):
                continue
            if stripped.startswith("qreg "):
                match = re.match(r"^qreg\s+(\w+)\[(\d+)\]\s*;\s*$", stripped)
                if match:
                    lines.append(f"QINIT {match.group(2)}")
                continue
            if stripped.startswith("creg "):
                match = re.match(r"^creg\s+(\w+)\[(\d+)\]\s*;\s*$", stripped)
                if match:
                    lines.append(f"CREG {match.group(2)}")
                continue
            if stripped.startswith("measure "):
                lines.append(
                    stripped.replace(
                        "measure q -> c;", "MEASURE q[0], c[0]\nMEASURE q[1], c[1]"
                    )
                )
                continue
            lines.append(stripped.replace("h ", "H ").replace("cx ", "CNOT "))
        return "\n".join(lines) + "\n"
    raise ValueError(f"unsupported target: {target}")


def run(qasm_str: str, target: str, shots: int) -> Dict[str, Any]:
    """Execute a circuit and return the unified result schema from the rules."""
    if shots <= 0:
        raise ValueError("shots must be positive")
    target_name = _normalize_target(target)
    if target_name not in SUPPORTED_TARGETS:
        raise ValueError(f"unsupported target: {target}")

    native_qasm = transpile(qasm_str, target_name)
    if target_name == "spinq":
        counts = _run_with_spinq(native_qasm, shots)
    elif target_name == "braket":
        counts = _run_with_braket(native_qasm, shots)
    else:
        program = _parse_qasm(qasm_str)
        state = _simulate(program)
        counts = _sample_counts(state, program, shots)

    return {
        "backend": target_name,
        "job_id": f"loomq-{target_name}-{shots}",
        "shots": shots,
        "counts": counts,
        "bit_order": "little",
        "timestamp": "2026-08-08T00:00:00Z",
    }


def agent_chat(prompt: str) -> str:
    """Optional L2 entry point using the documented LOOMQ_LLM_* environment."""
    raise NotImplementedError("L2 is optional; implement agent_chat(prompt) to enter")


def compile_hybrid(hybrid_qasm_str: str) -> Tuple[List[str], str]:
    """Optional L3 entry point. Return quantum operations and RISC-V assembly."""
    raise NotImplementedError(
        "L3 is optional; implement compile_hybrid(hybrid_qasm_str) to enter"
    )
