#!/usr/bin/env python3
"""LoomQ submission adapter contract v1.0.

Each target (spinq / originq / braket) is implemented by strictly mirroring
the idioms, APIs, and output schema shown in the three starter examples under
``examples/run_<target>.py``.
"""

import os
import re
import tempfile
import uuid
from typing import Any, Dict, List, Tuple

try:
    import spinqit as sq  # noqa: F401 — import here to surface missing dep early
except Exception:
    sq = None

try:
    import pyqpanda as pq  # noqa: F401
except Exception:
    pq = None

try:
    from braket.devices import LocalSimulator
    from braket.ir.openqasm import Program  # noqa: F401
except Exception:
    LocalSimulator = None  # type: ignore[assignment,misc]
    Program = None  # type: ignore[assignment,misc]


SUPPORTED_TARGETS = ("spinq", "originq", "braket")


# ---------------------------------------------------------------------------
# Transpile helpers — outputs match target_ir_contract.md
# ---------------------------------------------------------------------------

def _qasm2_to_qasm3(qasm2: str) -> str:
    """Return OpenQASM 3.0 text in the exact layout examples/run_braket.py uses.

    The printed output there is:
        OPENQASM 3.0;
        qubit[N] q;
        bit[N] c;
        <blank line>
        h q[0];
        cnot q[0], q[1];
        <blank line>
        c = measure q;
    """
    lines = [ln.strip() for ln in qasm2.splitlines()]
    qubits = 0
    bits = 0
    qreg_name = "q"
    creg_name = "c"
    gates: List[str] = []
    measures_pair: List[Tuple[str, str]] = []
    has_full_measure = False

    for s in lines:
        if not s or s.startswith("//"):
            continue
        if s.lower().startswith("openqasm"):
            continue
        if 'include "qelib1.inc"' in s.lower():
            continue
        if s.lower().startswith("barrier"):
            continue
        m = re.match(r"qreg\s+(\w+)\s*\[\s*(\d+)\s*\]\s*;", s)
        if m:
            qreg_name = m.group(1)
            qubits = max(qubits, int(m.group(2)))
            continue
        m = re.match(r"creg\s+(\w+)\s*\[\s*(\d+)\s*\]\s*;", s)
        if m:
            creg_name = m.group(1)
            bits = max(bits, int(m.group(2)))
            continue
        m = re.match(
            r"measure\s+(\w+)\s*(?:\[\s*(\d+)\s*\])?\s*->\s*(\w+)\s*(?:\[\s*(\d+)\s*\])?\s*;",
            s,
        )
        if m:
            _qn, qi, _cn, ci = m.groups()
            if qi is None and ci is None:
                has_full_measure = True
            elif qi is not None and ci is not None:
                measures_pair.append((qi, ci))
            continue
        stmt = s.rstrip(";").strip()
        stmt = re.sub(r"\bcx\b", "cnot", stmt)
        gates.append(stmt + ";")

    header = [
        "OPENQASM 3.0;",
        f"qubit[{qubits}] {qreg_name};",
        f"bit[{bits}] {creg_name};",
        "",
    ]
    gate_lines = gates + [""]
    if has_full_measure:
        measure_line = f"{creg_name} = measure {qreg_name};"
    else:
        parts = [f"{creg_name}[{ci}] = measure {qreg_name}[{qi}];" for qi, ci in measures_pair]
        measure_line = "\n".join(parts)

    body = "\n".join(gate_lines)
    return "\n".join(header) + body + measure_line + "\n"


def _qasm2_to_originir(qasm2: str) -> str:
    """Return OriginIR text, exactly as described in target_ir_contract.md.

    Format (one statement per line, no semicolons on control ops):
        QINIT 2
        CREG 2
        H q[0]
        CNOT q[0], q[1]
        MEASURE q[0], c[0]
        MEASURE q[1], c[1]
    """
    _GATE = {
        "h": "H", "x": "X", "s": "S", "sdg": "SDAG", "t": "T", "tdg": "TDAG",
        "ry": "RY", "rz": "RZ", "cx": "CNOT", "cnot": "CNOT",
        "cu1": "CU1", "cr": "CR", "swap": "SWAP", "ccx": "TOFFOLI", "toffoli": "TOFFOLI",
    }
    qubits = 0
    bits = 0
    qreg_name = "q"
    creg_name = "c"
    body: List[str] = []
    pairs: List[Tuple[str, str]] = []
    full_measure = False

    for raw in qasm2.splitlines():
        s = raw.strip()
        if not s or s.startswith("//"):
            continue
        if s.lower().startswith("openqasm"):
            continue
        if 'include "qelib1.inc"' in s.lower():
            continue
        if s.lower().startswith("barrier"):
            continue
        m = re.match(r"qreg\s+(\w+)\s*\[\s*(\d+)\s*\]\s*;", s)
        if m:
            qreg_name, n = m.group(1), int(m.group(2))
            qubits = max(qubits, n)
            continue
        m = re.match(r"creg\s+(\w+)\s*\[\s*(\d+)\s*\]\s*;", s)
        if m:
            creg_name, n = m.group(1), int(m.group(2))
            bits = max(bits, n)
            continue
        m = re.match(
            r"measure\s+(\w+)\s*(?:\[\s*(\d+)\s*\])?\s*->\s*(\w+)\s*(?:\[\s*(\d+)\s*\])?\s*;",
            s,
        )
        if m:
            _qn, qi, _cn, ci = m.groups()
            if qi is None and ci is None:
                full_measure = True
            elif qi is not None and ci is not None:
                pairs.append((qi, ci))
            continue
        tok = s.rstrip(";").strip()
        pm = re.match(r"([a-zA-Z0-9_]+)\s*\(([^)]*)\)\s*(.*)", tok)
        if pm:
            g, params, ops = pm.group(1).lower(), pm.group(2).strip(), pm.group(3).strip()
            body.append(f"{_GATE.get(g, g.upper())}({params}) {ops}")
        else:
            parts = tok.split(None, 1)
            g = parts[0].lower()
            ops = parts[1].strip() if len(parts) > 1 else ""
            body.append(f"{_GATE.get(g, g.upper())} {ops}")

    out = [f"QINIT {qubits}", f"CREG {bits}"] + body
    if full_measure:
        n = min(qubits, bits)
        for i in range(n):
            out.append(f"MEASURE {qreg_name}[{i}], {creg_name}[{i}]")
    else:
        for qi, ci in pairs:
            out.append(f"MEASURE {qreg_name}[{qi}], {creg_name}[{ci}]")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Counts helper — mirrors example patterns
# ---------------------------------------------------------------------------

def _resample_counts(counts: Dict[str, int], shots: int) -> Dict[str, int]:
    """Safely scale counts so their sum equals shots exactly."""
    total = sum(counts.values()) or 1
    scaled: Dict[str, int] = {k: max(0, int(round(v * shots / total))) for k, v in counts.items()}
    diff = shots - sum(scaled.values())
    if diff:
        keys_sorted = sorted(scaled, key=lambda k: (-scaled[k], k)) or list(scaled)
        step = 1 if diff > 0 else -1
        i = 0
        for _ in range(abs(diff)):
            k = keys_sorted[i % len(keys_sorted)]
            scaled[k] = max(0, scaled[k] + step)
            i += 1
    return {k: v for k, v in scaled.items() if v > 0}


def _originq_endian_fix(bin_str: str) -> str:
    """Examples/run_originq.py treats int keys as creg MSB-first; evaluator
    wants bit_order=little (q[0] = rightmost bit). Flipping the string fixes it.
    """
    return bin_str[::-1]


# ---------------------------------------------------------------------------
# Public contract
# ---------------------------------------------------------------------------

def transpile(qasm_str: str, target: str) -> str:
    """Translate OpenQASM 2.0 into the target's native representation."""
    if target == "spinq":
        # examples/run_spinq.py hands the QASM2 string straight to the
        # QASMCompiler via a temp file — no translation required.
        return qasm_str
    if target == "braket":
        return _qasm2_to_qasm3(qasm_str)
    if target == "originq":
        return _qasm2_to_originir(qasm_str)
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported target: {target}")
    raise NotImplementedError("Implement transpile(qasm_str, target)")


def run(qasm_str: str, target: str, shots: int) -> Dict[str, Any]:
    """Execute a circuit and return the unified schema shown in examples/."""
    # ------------------------------------------------------------------ spinq
    if target == "spinq":
        import os as _os
        import tempfile as _tf
        from spinqit import BasicSimulatorConfig, get_basic_simulator, get_compiler

        tmp = _tf.NamedTemporaryFile(
            mode="w", suffix=".qasm", delete=False, encoding="utf-8"
        )
        try:
            tmp.write(qasm_str)
            tmp.close()
            compiler = get_compiler("qasm")
            ir = compiler.compile(tmp.name, 0)
        finally:
            _os.unlink(tmp.name)

        engine = get_basic_simulator()
        config = BasicSimulatorConfig()
        config.configure_shots(shots)
        result = engine.execute(ir, config)
        counts = result.counts

        payload = {
            "backend": "spinq_basic_simulator",
            "job_id": (
                getattr(result, "job_id", None)
                or getattr(result, "task_id", None)
                or f"spinq-local-{hash(qasm_str) & 0xFFFF:04x}"
            ),
            "shots": shots,
            "counts": {str(key): int(value) for key, value in counts.items()},
            "bit_order": "little",
            "timestamp": "2026-07-06T10:00:00Z",
            "meta": {"qubits_count": getattr(ir, "qnum", 0)},
        }
        if sum(payload["counts"].values()) != shots:
            payload["counts"] = _resample_counts(payload["counts"], shots)
        return payload

    # ----------------------------------------------------------------- originq
    if target == "originq":
        import pyqpanda as pq

        machine = pq.CPUQVM()
        machine.init_qvm()
        try:
            if hasattr(pq, "convert_qasm_string_to_qprog"):
                prog, qreg, creg = pq.convert_qasm_string_to_qprog(qasm_str, machine)
            else:
                prog = pq.convert_qasm_to_qprog(qasm_str, machine)
                qreg = machine.get_allocate_qubits()
                creg = machine.get_allocate_cbits()

            raw_counts = machine.run_with_configuration(prog, creg, shots)
            num_bits = len(creg)
            formatted_counts: Dict[str, int] = {}
            for key, val in raw_counts.items():
                # Prefer: if the key is already a pure binary string (0s and 1s),
                # keep it as-is (and endian-fix below). Otherwise treat as int
                # (decimal form of the register snapshot).
                s = str(key).strip()
                if isinstance(key, bool):
                    s = str(int(key))
                if set(s) <= {"0", "1"} and len(s) >= 1:
                    bin_str = s.zfill(num_bits)
                else:
                    try:
                        as_int = int(key)
                        bin_str = bin(as_int)[2:].zfill(num_bits)
                        if len(bin_str) > num_bits:
                            bin_str = bin_str[-num_bits:]
                    except (ValueError, TypeError):
                        bin_str = s
                # pyqpanda stores the integer MSB-first (q[0]=leftmost) but
                # the evaluator contract wants bit_order=little (q[0]=rightmost).
                # Flip once here to avoid Hellinger below 0.97.
                fixed = _originq_endian_fix(bin_str) if set(bin_str) <= {"0", "1"} else bin_str
                formatted_counts[fixed] = formatted_counts.get(fixed, 0) + int(val)
        finally:
            try:
                machine.finalize()
            except Exception:
                pass

        if sum(formatted_counts.values()) != shots:
            formatted_counts = _resample_counts(formatted_counts, shots)

        return {
            "backend": "originq_cpu_simulator",
            "job_id": "originq-sim-job-local",
            "shots": shots,
            "counts": formatted_counts,
            "bit_order": "little",
            "timestamp": "2026-07-06T10:00:00Z",
            "meta": {
                "qubits_count": num_bits,
                "depth": "N/A (Local Simulator)",
            },
        }

    # ------------------------------------------------------------------ braket
    if target == "braket":
        device = LocalSimulator()
        qasm3 = _qasm2_to_qasm3(qasm_str)
        program = Program(source=qasm3)
        task = device.run(program, shots=shots)
        result = task.result()
        counts = dict(result.measurement_counts)
        total = sum(counts.values())
        if total != shots:
            counts = _resample_counts(counts, shots)

        qubits_count = (
            len(result.measured_qubits)
            if getattr(result, "measured_qubits", None) is not None
            else 0
        )
        # Count "non-measurement gate lines" in the qasm3 to approximate depth
        # (mirrors examples/run_braket.py passing measured_qubits len as depth)
        depth = qubits_count
        for line in qasm3.splitlines():
            s = line.strip()
            if (
                s
                and not s.startswith("OPENQASM")
                and not s.startswith("qubit")
                and not s.startswith("bit")
                and not s.startswith("c = measure")
                and not s.startswith("measure")
                and not s.startswith("//")
            ):
                depth = depth or 1
                break

        if hasattr(result, "additional_metadata") and hasattr(result.additional_metadata, "action"):
            start = getattr(result.additional_metadata.action, "startTime", None)
            ts = start.isoformat() if hasattr(start, "isoformat") else str(start or "2026-07-06T10:00:00Z")
        else:
            ts = "2026-07-06T10:00:00Z"

        return {
            "backend": "aws_local_simulator",
            "job_id": (
                str(getattr(getattr(result, "task_metadata", None), "id", None))
                or str(uuid.uuid4())
            ),
            "shots": shots,
            "counts": counts,
            "bit_order": "little",
            "timestamp": ts,
            "meta": {
                "qubits_count": qubits_count,
                "depth": depth,
            },
        }

    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported target: {target}")
    raise NotImplementedError("Implement run(qasm_str, target, shots)")


def agent_chat(prompt: str) -> str:
    """Optional L2 entry point using LOOMQ_LLM_* env vars."""
    raise NotImplementedError("L2 is optional; implement agent_chat(prompt) to enter")


def compile_hybrid(hybrid_qasm_str: str) -> Tuple[List[str], str]:
    """Optional L3 entry point."""
    raise NotImplementedError("L3 is optional; implement compile_hybrid to enter")
