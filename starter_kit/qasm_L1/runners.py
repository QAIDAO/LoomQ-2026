"""Per-target execution backends.

``run_spinq`` and ``run_braket`` execute the transpiled native text with the
official SDKs; ``run_originq`` executes via pyqpanda's official CPUQVM,
feeding it an OriginIR re-render of the same :class:`Circuit`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict

try:
    from .ir import Circuit
except ImportError:
    from ir import Circuit


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

    with tempfile.TemporaryDirectory() as temp_dir:
        qasm_path = Path(temp_dir) / "circuit.qasm"
        qasm_path.write_text(native_qasm, encoding="utf-8")
        ir = QASMCompiler().compile(str(qasm_path), 0)

    config = BasicSimulatorConfig()
    config.configure_shots(shots)
    result = BasicSimulatorBackend().execute(ir, config)
    return _little_endian({str(key): int(value) for key, value in result.counts.items()})


def run_braket(native_qasm: str, circuit: Circuit, shots: int) -> Dict[str, int]:
    """Execute native OpenQASM 3 on the AWS Braket local simulator."""
    from braket.devices import LocalSimulator
    from braket.ir.openqasm import Program as OpenQASMProgram

    # Keep the standard include in the transpiled IR, but omit it when running
    # locally: this Braket version resolves includes as filesystem paths while
    # already providing the emitted native gates itself.
    braket3 = native_qasm.replace('include "stdgates.inc";', "")
    task = LocalSimulator().run(OpenQASMProgram(source=braket3), shots=shots)
    result = task.result()
    return _little_endian(
        {str(key): int(value) for key, value in result.measurement_counts.items()}
    )


def run_originq(native_qasm: str, circuit: Circuit, shots: int) -> Dict[str, int]:
    """Execute on the official pyqpanda CPUQVM, fed the transpiled OriginIR.

    ``_ORIGINIR_GATES`` renders every gate in a pyqpanda-native form: parameter
    gates as ``RZ q[0],(θ)``/``CR q[0], q[1],(θ)``, and the phase-gate daggers
    ``sdg``/``tdg`` as native U1 rotations.  The transpile output is therefore
    fed verbatim to ``convert_originir_str_to_qprog``.

    pyqpanda returns little-endian counts keys (``c[0]`` rightmost), matching
    the contract, so no bit reversal is applied here.
    """
    try:
        from pyqpanda import CPUQVM, convert_originir_str_to_qprog
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pyqpanda is required for the originq run path") from exc

    machine = CPUQVM()
    machine.init_qvm()
    try:
        program, qubits, cbits = convert_originir_str_to_qprog(native_qasm, machine)
        result = machine.run_with_configuration(program, cbits, shots)
        return {str(key): int(value) for key, value in result.items()}
    finally:
        machine.finalize()
