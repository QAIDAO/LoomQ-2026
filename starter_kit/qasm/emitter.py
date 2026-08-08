"""Target translator: one Circuit, one method per target platform.

Every target is rendered by the same :func:`Translator._emit` kernel; a target
only contributes a :class:`TargetSpec` (gate mapping, header, measure syntax,
bit token strategy).  Adding a platform means adding one ``to_<name>`` method
and one spec.
"""

from __future__ import annotations

try:
    from .gates import TargetSpec, format_param, originir_spec, qasm2_spec, qasm3_spec
    from .ir import Circuit, Gate
except ImportError:
    from gates import TargetSpec, format_param, originir_spec, qasm2_spec, qasm3_spec
    from ir import Circuit, Gate


class Translator:
    """Translate a parsed :class:`Circuit` into target-native text."""

    def __init__(self, circuit: Circuit) -> None:
        self.circuit = circuit

    def dispatch(self, target: str) -> str:
        method = getattr(self, f"to_{target}", None)
        if method is None:
            raise ValueError(f"unsupported target: {target}")
        return method()

    def to_spinq(self) -> str:
        """Canonical OpenQASM 2.0 (native SpinQ format)."""
        return self._emit(qasm2_spec())

    def to_braket(self) -> str:
        """OpenQASM 3.0 (native Braket format)."""
        return self._emit(qasm3_spec())

    def to_originq(self) -> str:
        """OriginIR text (flat q/c registers)."""
        return self._emit(originir_spec())

    def _emit(self, spec: TargetSpec) -> str:
        lines = list(spec.header(self.circuit))
        for gate in self.circuit.gates:
            handler = spec.gates[gate.name]
            if callable(handler):
                lines.extend(handler(gate))
            else:
                lines.append(self._render_direct(gate, handler, spec))
        for measurement in self.circuit.measurements:
            lines.extend(spec.measure(measurement))
        return "\n".join(lines) + "\n"

    def _render_direct(self, gate: Gate, emit_name: str, spec: TargetSpec) -> str:
        tokens = ", ".join(spec.qubit_token(ref) for ref in gate.qubits)
        if gate.params:
            params = ", ".join(format_param(value) for value in gate.params)
            return f"{emit_name}({params}) {tokens}{spec.terminator}"
        return f"{emit_name} {tokens}{spec.terminator}"
