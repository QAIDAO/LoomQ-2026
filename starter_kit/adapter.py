#!/usr/bin/env python3
"""LoomQ submission adapter contract v1.0.

Thin facade over the translation pipeline:

    qasm/parser.py   OpenQASM 2.0 text -> Circuit IR
    qasm/emitter.py  Translator -> target-native text
    simulator.py     state-vector simulator (originq run path)
    runners.py       SDK execution backends (spinq / braket)

The contract functions below never re-parse or re-implement platform logic;
they only parse once and dispatch to a target.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

try:
    from . import runners
    from .qasm.emitter import Translator
    from .qasm.parser import parse_qasm
except ImportError:
    import runners
    from qasm.emitter import Translator
    from qasm.parser import parse_qasm

SUPPORTED_TARGETS = ("spinq", "originq", "braket")

RUNNERS = {
    "spinq": runners.run_spinq,
    "braket": runners.run_braket,
    "originq": runners.run_originq,
}


def _normalize_target(target: str) -> str:
    return (target or "").strip().lower()


def transpile(qasm_str: str, target: str) -> str:
    """Translate OpenQASM 2.0 into the target backend's native representation."""
    target_name = _normalize_target(target)
    if target_name not in SUPPORTED_TARGETS:
        raise ValueError(f"unsupported target: {target}")
    circuit = parse_qasm(qasm_str)
    return Translator(circuit).dispatch(target_name)


def run(qasm_str: str, target: str, shots: int) -> Dict[str, Any]:
    """Execute a circuit and return the unified result schema from the rules."""
    if shots <= 0:
        raise ValueError("shots must be positive")
    target_name = _normalize_target(target)
    if target_name not in SUPPORTED_TARGETS:
        raise ValueError(f"unsupported target: {target}")

    circuit = parse_qasm(qasm_str)
    native_qasm = transpile(qasm_str, target_name)
    counts = RUNNERS[target_name](native_qasm, circuit, shots)

    return {
        "backend": target_name,
        "job_id": f"loomq-{target_name}-{shots}",
        "shots": shots,
        "counts": counts,
        "bit_order": "little",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def agent_chat(prompt: str) -> str:
    """Optional L2 entry point using the documented LOOMQ_LLM_* environment."""
    raise NotImplementedError("L2 is optional; implement agent_chat(prompt) to enter")


def compile_hybrid(hybrid_qasm_str: str) -> Tuple[List[str], str]:
    """Optional L3 entry point. Return quantum operations and RISC-V assembly."""
    raise NotImplementedError(
        "L3 is optional; implement compile_hybrid(hybrid_qasm_str) to enter"
    )
