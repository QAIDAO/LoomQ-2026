"""Per-target execution backends.

``run_spinq`` and ``run_braket`` execute the transpiled native text with the
official SDKs; ``run_originq`` uses the built-in state-vector simulator on the
same :class:`Circuit` the emitters consume.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any, Dict, List

try:
    from .qasm.ir import Circuit
    from .simulator import sample_counts
except ImportError:
    from qasm.ir import Circuit
    from simulator import sample_counts


def _strip_comment(line: str) -> str:
    return line.split("//", 1)[0].strip()


def _little_endian(counts: Dict[str, int]) -> Dict[str, int]:
    """SDKs report keys with c[0] leftmost; the contract wants it rightmost."""
    return {key[::-1]: value for key, value in counts.items()}


def run_spinq(native_qasm: str, circuit: Circuit, shots: int) -> Dict[str, int]:
    """Execute native OpenQASM 2.0 on the SpinQit basic simulator."""
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
        return _little_endian({str(key): int(value) for key, value in result.counts.items()})
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def _parse_braket_text(native_qasm: str):
    """Parse the canonical OpenQASM 3 output produced by the translator."""
    qubit_names: Dict[str, int] = {}
    classical_names: Dict[str, int] = {}
    instructions: List[Dict[str, Any]] = []
    measurements: List[tuple] = []

    for raw_line in native_qasm.splitlines():
        line = _strip_comment(raw_line)
        if not line or line.startswith("OPENQASM") or line.startswith("include"):
            continue
        qubit_match = re.match(r"^qubit\[(\d+)\]\s+(\w+)\s*;\s*$", line)
        if qubit_match:
            reg_name = qubit_match.group(2)
            for idx in range(int(qubit_match.group(1))):
                qubit_names[f"{reg_name}[{idx}]"] = len(qubit_names)
            continue
        bit_match = re.match(r"^bit\[(\d+)\]\s+(\w+)\s*;\s*$", line)
        if bit_match:
            reg_name = bit_match.group(2)
            for idx in range(int(bit_match.group(1))):
                classical_names[f"{reg_name}[{idx}]"] = len(classical_names)
            continue
        measure_match = re.match(r"^(\w+)\s*=\s*measure\s+(.+)\s*;\s*$", line)
        if measure_match:
            dest_text = measure_match.group(1).strip()
            source_text = measure_match.group(2).strip()
            if source_text == "q":
                src_tokens = [
                    name for name in sorted(qubit_names, key=qubit_names.get) if name.startswith("q[")
                ]
            else:
                src_tokens = [token.strip() for token in source_text.split(",") if token.strip()]
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
        gate_match = re.match(r"^([A-Za-z0-9_]+)(?:\(([^)]+)\))?\s*(.+)\s*;\s*$", line)
        if gate_match:
            instructions.append(
                {
                    "name": gate_match.group(1).lower(),
                    "param": gate_match.group(2),
                    "targets": [
                        token.strip()
                        for token in gate_match.group(3).split(",")
                        if token.strip()
                    ],
                }
            )
            continue
        raise ValueError(f"unsupported OpenQASM 3 line: {line}")
    return qubit_names, classical_names, instructions, measurements


def run_braket(native_qasm: str, circuit: Circuit, shots: int) -> Dict[str, int]:
    """Execute native OpenQASM 3 on the AWS Braket local simulator."""
    from braket.circuits import Circuit as BraketCircuit
    from braket.devices import LocalSimulator

    qubit_names, classical_names, instructions, measurements = _parse_braket_text(native_qasm)
    braket = BraketCircuit()
    for instruction in instructions:
        name = instruction["name"]
        targets = instruction["targets"]
        if name == "h":
            braket.h(qubit_names[targets[0]])
        elif name == "x":
            braket.x(qubit_names[targets[0]])
        elif name == "s":
            braket.s(qubit_names[targets[0]])
        elif name == "sdg":
            braket.si(qubit_names[targets[0]])
        elif name == "t":
            braket.t(qubit_names[targets[0]])
        elif name == "tdg":
            braket.ti(qubit_names[targets[0]])
        elif name == "rz":
            braket.rz(qubit_names[targets[0]], angle=float(instruction["param"] or 0.0))
        elif name == "ry":
            braket.ry(qubit_names[targets[0]], angle=float(instruction["param"] or 0.0))
        elif name == "u1":
            braket.phaseshift(qubit_names[targets[0]], angle=float(instruction["param"] or 0.0))
        elif name in {"cx", "cnot"}:
            braket.cnot(qubit_names[targets[0]], qubit_names[targets[1]])
        elif name == "cu1":
            braket.cphaseshift(
                qubit_names[targets[0]],
                qubit_names[targets[1]],
                angle=float(instruction["param"] or 0.0),
            )
        elif name == "swap":
            braket.swap(qubit_names[targets[0]], qubit_names[targets[1]])
        elif name in {"ccx", "ccnot"}:
            braket.ccnot(qubit_names[targets[0]], qubit_names[targets[1]], qubit_names[targets[2]])
        else:
            raise ValueError(f"unsupported Braket gate: {name}")

    for src_tokens, dst_tokens in measurements:
        if len(src_tokens) != len(dst_tokens):
            raise ValueError("measurement arity mismatch")
        braket.measure([qubit_names[src] for src in src_tokens])

    simulator = LocalSimulator()
    result = simulator.run(braket, shots=shots).result()
    return _little_endian({str(key): int(value) for key, value in result.measurement_counts.items()})


def run_originq(native_qasm: str, circuit: Circuit, shots: int) -> Dict[str, int]:
    """Execute on the built-in state-vector simulator."""
    return sample_counts(circuit, shots)
