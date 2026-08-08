#!/usr/bin/env python3
"""LoomQ public self-checker.

This suite verifies the public contract only. Formal scoring uses an organizer-
owned runner, randomized cases, isolated processes, and private expected values.
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from . import adapter
except ImportError:
    try:
        import adapter
    except ImportError:
        adapter = None

try:
    from .riscv_emulator import TinyRISCVEmulator
except ImportError:
    try:
        from riscv_emulator import TinyRISCVEmulator
    except ImportError:
        TinyRISCVEmulator = None


CONTRACT_VERSION = "1.0"
PUBLIC_DISTRIBUTIONS = {
    "bell.qasm": {"00": 0.5, "11": 0.5},
    "ghz3.qasm": {"000": 0.5, "111": 0.5},
}


def calculate_hellinger_fidelity(
    observed: Dict[str, float], expected: Dict[str, float]
) -> float:
    states = set(observed) | set(expected)
    distance = math.sqrt(
        sum(
            (math.sqrt(observed.get(state, 0.0)) - math.sqrt(expected.get(state, 0.0)))
            ** 2
            for state in states
        )
    ) / math.sqrt(2.0)
    return max(0.0, min(1.0, 1.0 - distance))


def validate_schema(result: Any) -> Tuple[bool, str]:
    if not isinstance(result, dict):
        return False, "result must be an object"
    required = ("backend", "job_id", "shots", "counts", "bit_order", "timestamp")
    missing = [field for field in required if field not in result]
    if missing:
        return False, "missing fields: " + ", ".join(missing)
    if not isinstance(result["shots"], int) or isinstance(result["shots"], bool) or result["shots"] <= 0:
        return False, "shots must be a positive integer"
    if not isinstance(result["counts"], dict) or not result["counts"]:
        return False, "counts must be a non-empty object"
    for key, value in result["counts"].items():
        if not isinstance(key, str) or not key or set(key) - {"0", "1"}:
            return False, "counts keys must be non-empty binary strings"
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return False, "counts values must be non-negative integers"
    if sum(result["counts"].values()) != result["shots"]:
        return False, "counts total must equal shots exactly"
    if result["bit_order"] != "little":
        return False, "bit_order must be little"
    if result.get("meta", {}).get("is_mock"):
        return False, "mock results never pass evaluation"
    return True, "schema valid"


def result(label: str, status: str, reason: str, **details: Any) -> Dict[str, Any]:
    item = {"case_id": label, "status": status, "reason": reason}
    item.update(details)
    return item


def evaluate_l1(targets: List[str], shots: int) -> List[Dict[str, Any]]:
    cases = []
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "circuits")
    for name, expected in sorted(PUBLIC_DISTRIBUTIONS.items()):
        qasm = open(os.path.join(base, name), encoding="utf-8").read()
        for target in targets:
            label = "l1:%s:%s" % (name, target)
            try:
                native = adapter.transpile(qasm, target)
                if not isinstance(native, str) or not native.strip():
                    cases.append(result(label, "FAIL", "transpile returned no artifact"))
                    continue
                payload = adapter.run(qasm, target, shots)
                valid, reason = validate_schema(payload)
                if not valid:
                    cases.append(result(label, "FAIL", reason))
                    continue
                observed = {key: value / shots for key, value in payload["counts"].items()}
                fidelity = calculate_hellinger_fidelity(observed, expected)
                status = "PASS" if fidelity >= 0.97 else "FAIL"
                cases.append(
                    result(
                        label,
                        status,
                        "fidelity threshold met" if status == "PASS" else "fidelity below 0.97",
                        fidelity=round(fidelity, 6),
                    )
                )
            except Exception as exc:
                cases.append(result(label, "FAIL", "%s: %s" % (type(exc).__name__, exc)))
    return cases


def extract_qasm(text: str) -> Optional[str]:
    if not isinstance(text, str):
        return None
    match = re.search(r"OPENQASM\s+2\.0;.*?(?=^\s*```|\Z)", text, re.DOTALL | re.MULTILINE)
    return match.group(0).strip() if match else None


def evaluate_l2() -> List[Dict[str, Any]]:
    label = "l2:public-ghz"
    try:
        reply = adapter.agent_chat("生成一个 3 比特 GHZ 态并进行全测量")
        qasm = extract_qasm(reply)
        if not qasm:
            return [result(label, "FAIL", "response contains no OpenQASM 2.0 program")]
        return [result(label, "PASS", "response contains parseable QASM")]
    except Exception as exc:
        return [result(label, "FAIL", "%s: %s" % (type(exc).__name__, exc))]


def evaluate_l3() -> List[Dict[str, Any]]:
    label = "l3:public-branch"
    if TinyRISCVEmulator is None:
        return [result(label, "FAIL", "riscv_emulator.py unavailable")]
    source = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[1];
creg c[1];
measure q[0] -> c[0];
classical { if (c[0] == 1) { r1 = 7; } else { r1 = 3; } }
"""
    try:
        quantum_ops, assembly = adapter.compile_hybrid(source)
        if not isinstance(quantum_ops, list) or not isinstance(assembly, str) or not assembly.strip():
            return [result(label, "FAIL", "invalid compile_hybrid return value")]
        for measured, expected in ((0, 3), (1, 7)):
            emulator = TinyRISCVEmulator()
            emulator.load_program(assembly)
            emulator.set_register("x10", measured)
            if emulator.execute().get("x1", 0) != expected:
                return [result(label, "FAIL", "compiled branch semantics are incorrect")]
        return [result(label, "PASS", "public branch semantics passed")]
    except Exception as exc:
        return [result(label, "FAIL", "%s: %s" % (type(exc).__name__, exc))]


def build_report(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    passed = sum(case["status"] == "PASS" for case in cases)
    failed = sum(case["status"] == "FAIL" for case in cases)
    return {
        "contract_version": CONTRACT_VERSION,
        "suite": "public",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"passed": passed, "failed": failed, "total": len(cases)},
        "cases": cases,
        "notice": "Public self-check only; this report is not an official score.",
    }


def declared_levels() -> List[str]:
    manifest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "submission.yaml")
    try:
        with open(manifest, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return ["l1"]
    return [
        name
        for name in ("l1", "l2", "l3")
        if re.search(r"^\s*%s:\s*true\s*$" % name, text, re.MULTILINE | re.IGNORECASE)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="LoomQ public contract self-check")
    parser.add_argument("--target", default="spinq,originq")
    parser.add_argument("--shots", type=int, default=8192)
    parser.add_argument("--level", choices=("declared", "all", "l1", "l2", "l3"), default="declared")
    parser.add_argument("--json-out", help="write a machine-readable report")
    args = parser.parse_args()

    if adapter is None:
        cases = [result("contract:adapter", "FAIL", "adapter.py not found")]
    else:
        cases = []
        targets = [target.strip() for target in args.target.split(",") if target.strip()]
        enabled = declared_levels() if args.level == "declared" else (["l1", "l2", "l3"] if args.level == "all" else [args.level])
        if "l1" in enabled:
            cases.extend(evaluate_l1(targets, args.shots))
        if "l2" in enabled:
            cases.extend(evaluate_l2())
        if "l3" in enabled:
            cases.extend(evaluate_l3())

    report = build_report(cases)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

    for case in cases:
        print("[{status}] {case_id}: {reason}".format(**case))
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["failed"] == 0 and report["summary"]["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
