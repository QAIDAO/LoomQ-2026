"""Pure state-vector simulator and sampling, consuming :class:`Circuit`.

This is the originq ``run`` path (and a useful oracle for the other targets):
the same ``Circuit`` the emitters translate is simulated directly.
"""

from __future__ import annotations

import cmath
import math
import random
from typing import Dict, List, Optional

try:
    from .qasm.ir import Circuit, Gate, Measurement
except ImportError:
    from qasm.ir import Circuit, Gate, Measurement


def _bit_at(index: int, qubit: int, num_qubits: int) -> int:
    return (index >> (num_qubits - 1 - qubit)) & 1


def _bits_for_basis(basis_index: int, qubits: List[int], num_qubits: int) -> List[int]:
    return [_bit_at(basis_index, qubit, num_qubits) for qubit in qubits]


def _apply_single_qubit_gate(
    state: List[complex],
    qubit: int,
    matrix,
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


def _apply_cx(state: List[complex], control: int, target: int, num_qubits: int) -> List[complex]:
    new_state = list(state)
    for basis in range(len(state)):
        if _bit_at(basis, control, num_qubits) == 1 and _bit_at(basis, target, num_qubits) == 0:
            other = basis | (1 << (num_qubits - 1 - target))
            new_state[basis] = state[other]
            new_state[other] = state[basis]
    return new_state


def _apply_swap(state: List[complex], qubit0: int, qubit1: int, num_qubits: int) -> List[complex]:
    new_state = [0j] * len(state)
    for basis in range(len(state)):
        bit0 = _bit_at(basis, qubit0, num_qubits)
        bit1 = _bit_at(basis, qubit1, num_qubits)
        swapped_index = basis
        if bit0 != bit1:
            swapped_index = basis ^ (1 << (num_qubits - 1 - qubit0))
            swapped_index ^= 1 << (num_qubits - 1 - qubit1)
        new_state[basis] = state[swapped_index]
    return new_state


def _apply_controlled_phase(
    state: List[complex], control: int, target: int, theta: float, num_qubits: int
) -> List[complex]:
    new_state = list(state)
    phase = cmath.exp(1j * theta)
    for basis in range(len(state)):
        if _bit_at(basis, control, num_qubits) == 1 and _bit_at(basis, target, num_qubits) == 1:
            new_state[basis] = state[basis] * phase
    return new_state


def _apply_ccx(
    state: List[complex], control0: int, control1: int, target: int, num_qubits: int
) -> List[complex]:
    new_state = list(state)
    for basis in range(len(state)):
        if (
            _bit_at(basis, control0, num_qubits) == 1
            and _bit_at(basis, control1, num_qubits) == 1
            and _bit_at(basis, target, num_qubits) == 0
        ):
            other = basis | (1 << (num_qubits - 1 - target))
            new_state[basis] = state[other]
            new_state[other] = state[basis]
    return new_state


def _apply_gate(state: List[complex], gate: Gate, circuit: Circuit) -> List[complex]:
    num_qubits = circuit.qubit_count
    name = gate.name
    qubits = [ref.global_index for ref in gate.qubits]
    if name == "h":
        s = 1 / math.sqrt(2)
        return _apply_single_qubit_gate(state, qubits[0], (s, s, s, -s), num_qubits)
    if name == "x":
        return _apply_single_qubit_gate(state, qubits[0], (0, 1, 1, 0), num_qubits)
    if name == "s":
        return _apply_single_qubit_gate(state, qubits[0], (1, 0, 0, 1j), num_qubits)
    if name == "sdg":
        return _apply_single_qubit_gate(state, qubits[0], (1, 0, 0, -1j), num_qubits)
    if name == "t":
        return _apply_single_qubit_gate(
            state, qubits[0], (1, 0, 0, cmath.exp(1j * math.pi / 4)), num_qubits
        )
    if name == "tdg":
        return _apply_single_qubit_gate(
            state, qubits[0], (1, 0, 0, cmath.exp(-1j * math.pi / 4)), num_qubits
        )
    if name == "rz":
        theta = gate.params[0]
        return _apply_single_qubit_gate(
            state, qubits[0], (cmath.exp(-1j * theta / 2), 0, 0, cmath.exp(1j * theta / 2)), num_qubits
        )
    if name == "ry":
        theta = gate.params[0]
        c = math.cos(theta / 2)
        s = math.sin(theta / 2)
        return _apply_single_qubit_gate(state, qubits[0], (c, -s, s, c), num_qubits)
    if name == "cx":
        return _apply_cx(state, qubits[0], qubits[1], num_qubits)
    if name == "cu1":
        return _apply_controlled_phase(state, qubits[0], qubits[1], gate.params[0], num_qubits)
    if name == "swap":
        return _apply_swap(state, qubits[0], qubits[1], num_qubits)
    if name == "ccx":
        return _apply_ccx(state, qubits[0], qubits[1], qubits[2], num_qubits)
    raise ValueError(f"unsupported gate: {name}")


def simulate(circuit: Circuit) -> List[complex]:
    """Run all gates from the all-zeros state and return the state vector."""
    state = [0j] * (1 << circuit.qubit_count)
    state[0] = 1.0 + 0.0j
    for gate in circuit.gates:
        state = _apply_gate(state, gate, circuit)
    return state


def _normalize(state: List[complex]) -> List[complex]:
    norm = math.sqrt(sum(abs(amplitude) ** 2 for amplitude in state))
    if norm == 0:
        return state
    return [amplitude / norm for amplitude in state]


def _measure(state: List[complex], qubits: List[int], num_qubits: int, rng):
    """Sample one outcome on ``qubits`` and collapse the state in place.

    Returns the outcome bits in the same order as ``qubits``.
    """
    if not qubits:
        return []
    outcomes = []
    probabilities = []
    for bits in _iter_bit_patterns(len(qubits)):
        probability = sum(
            abs(amplitude) ** 2
            for basis_index, amplitude in enumerate(state)
            if _bits_for_basis(basis_index, qubits, num_qubits) == list(bits)
        )
        outcomes.append(list(bits))
        probabilities.append(probability)
    total = sum(probabilities)
    if total <= 0:
        chosen = [0] * len(qubits)
    else:
        chosen = rng.choices(outcomes, weights=probabilities, k=1)[0]

    collapsed = [0j] * len(state)
    for basis_index, amplitude in enumerate(state):
        if _bits_for_basis(basis_index, qubits, num_qubits) == list(chosen):
            collapsed[basis_index] = amplitude
    state[:] = _normalize(collapsed)
    return list(chosen)


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


def sample_counts(circuit: Circuit, shots: int, rng=None) -> Dict[str, int]:
    """Sample ``shots`` shots; keys follow the little-endian convention
    (rightmost character is ``c[0]``)."""
    if shots <= 0:
        raise ValueError("shots must be positive")
    if rng is None:
        rng = random
    if not circuit.measurements:
        return {"0" * circuit.cbit_count: shots}

    state = simulate(circuit)
    num_qubits = circuit.qubit_count
    counts: Dict[str, int] = {}
    for _ in range(shots):
        current = list(state)
        result: List[int] = [0] * circuit.cbit_count
        for measurement in circuit.measurements:
            qubits = [ref.global_index for ref in measurement.qubits]
            bits = _measure(current, qubits, num_qubits, rng)
            for bit, cbit in zip(bits, measurement.cbits):
                result[cbit.global_index] = bit
        key = "".join(str(bit) for bit in reversed(result))
        counts[key] = counts.get(key, 0) + 1
    return counts
