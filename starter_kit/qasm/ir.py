"""Intermediate representation shared by the parser, emitters, and runners.

A :class:`Circuit` is the single object that every OpenQASM 2.0 program is
parsed into.  Translators render it into target-native text; runners simulate
it.  All gate names are normalized to lowercase canonical names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass(frozen=True)
class QubitRef:
    """A reference to one qubit in a declared register."""

    reg: str
    index: int
    global_index: int

    @property
    def token(self) -> str:
        return f"{self.reg}[{self.index}]"


@dataclass(frozen=True)
class CbitRef:
    """A reference to one classical bit in a declared register."""

    reg: str
    index: int
    global_index: int

    @property
    def token(self) -> str:
        return f"{self.reg}[{self.index}]"


@dataclass
class Gate:
    """A single quantum gate application."""

    name: str
    params: List[float] = field(default_factory=list)
    qubits: List[QubitRef] = field(default_factory=list)


@dataclass
class Measurement:
    """A ``measure`` statement (whole-register or bitwise)."""

    qubits: List[QubitRef] = field(default_factory=list)
    cbits: List[CbitRef] = field(default_factory=list)
    whole_register: bool = False


@dataclass
class Circuit:
    """The full parsed program."""

    qregs: List[Tuple[str, int]] = field(default_factory=list)
    cregs: List[Tuple[str, int]] = field(default_factory=list)
    gates: List[Gate] = field(default_factory=list)
    measurements: List[Measurement] = field(default_factory=list)

    @property
    def qubit_count(self) -> int:
        return sum(size for _, size in self.qregs)

    @property
    def cbit_count(self) -> int:
        return sum(size for _, size in self.cregs)
