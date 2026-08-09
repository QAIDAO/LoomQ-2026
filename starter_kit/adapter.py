#!/usr/bin/env python3
"""Dependency-free LoomQ L1 adapter.

The adapter deliberately keeps one parser/simulator as the semantic source of
truth; target-specific functions only serialize the parsed circuit.
"""
from typing import Any, Dict, List, Tuple
import cmath, math, re
from datetime import datetime, timezone

SUPPORTED_TARGETS = ("spinq", "originq", "braket")
_GATES = {"h", "x", "s", "sdg", "t", "tdg", "rz", "ry", "cx", "cu1", "swap", "ccx"}

def _expr(s: str) -> float:
    s = s.strip().replace("π", "pi")
    # QASM expressions in the challenge are numeric/pi arithmetic only.
    if not re.fullmatch(r"[0-9eE+\-*/(). pi]+", s):
        raise ValueError("unsupported angle expression")
    return float(eval(s, {"__builtins__": {}}, {"pi": math.pi}))

def _parse(qasm: str):
    clean = re.sub(r"//.*", "", qasm)
    nq = int(re.search(r"qreg\s+\w+\s*\[(\d+)\]", clean, re.I).group(1))
    nc_m = re.search(r"creg\s+\w+\s*\[(\d+)\]", clean, re.I)
    nc = int(nc_m.group(1)) if nc_m else nq
    ops, measurements = [], []
    for stmt in clean.split(";"):
        t = stmt.strip()
        if not t or t.upper().startswith(("OPENQASM", "INCLUDE", "QREG", "CREG")):
            continue
        m = re.match(r"measure\s+(.+?)\s*->\s*(.+)$", t, re.I)
        if m:
            a, b = m.group(1).strip(), m.group(2).strip()
            if re.match(r"\w+\s*$", a):
                measurements.extend((i, i) for i in range(min(nq, nc)))
            else:
                qi = int(re.search(r"\[(\d+)\]", a).group(1)); ci = int(re.search(r"\[(\d+)\]", b).group(1)); measurements.append((qi, ci))
            continue
        m = re.match(r"(\w+)(?:\s*\(([^)]*)\))?\s+(.+)$", t, re.I)
        if not m or m.group(1).lower() not in _GATES: continue
        name, arg, qtxt = m.group(1).lower(), m.group(2), m.group(3)
        qs = [int(x) for x in re.findall(r"\[(\d+)\]", qtxt)]
        ops.append((name, _expr(arg) if arg is not None else None, qs))
    return nq, nc, ops, measurements

def _one(name, theta=None):
    if name == "h": return [[1/math.sqrt(2),1/math.sqrt(2)],[1/math.sqrt(2),-1/math.sqrt(2)]]
    if name == "x": return [[0,1],[1,0]]
    if name in ("s","sdg","t","tdg"):
        k = {"s":math.pi/2,"sdg":-math.pi/2,"t":math.pi/4,"tdg":-math.pi/4}[name]; return [[1,0],[0,cmath.exp(1j*k)]]
    if name == "rz": return [[cmath.exp(-1j*theta/2),0],[0,cmath.exp(1j*theta/2)]]
    if name == "ry": return [[math.cos(theta/2),-math.sin(theta/2)],[math.sin(theta/2),math.cos(theta/2)]]

def _simulate(n, ops):
    state=[0j]*(1<<n); state[0]=1+0j
    def apply1(q, mat):
        nonlocal state
        for i in range(1<<n):
            if (i>>q)&1==0:
                j=i|(1<<q); a,b=state[i],state[j]; state[i]=mat[0][0]*a+mat[0][1]*b; state[j]=mat[1][0]*a+mat[1][1]*b
    def cx(c,t):
        nonlocal state
        for i in range(1<<n):
            if ((i>>c)&1) and not ((i>>t)&1): j=i|(1<<t); state[i],state[j]=state[j],state[i]
    for name,theta,qs in ops:
        if name in ("h","x","s","sdg","t","tdg","rz","ry"): apply1(qs[0],_one(name,theta))
        elif name=="cx": cx(qs[0],qs[1])
        elif name=="swap": cx(qs[0],qs[1]); cx(qs[1],qs[0]); cx(qs[0],qs[1])
        elif name=="cu1":
            # controlled phase
            for i in range(1<<n):
                if ((i>>qs[0])&1) and ((i>>qs[1])&1): state[i]*=cmath.exp(1j*theta)
        elif name=="ccx":
            for i in range(1<<n):
                if ((i>>qs[0])&1) and ((i>>qs[1])&1) and not ((i>>qs[2])&1): j=i|(1<<qs[2]); state[i],state[j]=state[j],state[i]
    return state

def _counts(qasm: str, shots: int) -> Dict[str,int]:
    n,nc,ops,meas=_parse(qasm); state=_simulate(n,ops)
    mapping = {q:c for q,c in meas} or {i:i for i in range(min(n,nc))}
    probs={}
    for idx,a in enumerate(state):
        p=abs(a)**2
        if p<1e-15: continue
        bits=['0']*nc
        for q,c in mapping.items(): bits[c]=str((idx>>q)&1)
        key=''.join(bits)  # c[0] is the leftmost (little-endian contract)
        probs[key]=probs.get(key,0)+p
    raw={k:int(math.floor(v*shots)) for k,v in probs.items()}; rem=shots-sum(raw.values())
    for k,_ in sorted(((k,v*shots-raw[k]) for k,v in probs.items()), key=lambda x:-x[1])[:rem]: raw[k]+=1
    return {k:v for k,v in raw.items() if v}

def transpile(qasm_str: str, target: str) -> str:
    target=target.lower()
    if target not in SUPPORTED_TARGETS: raise ValueError(f"unsupported target: {target}")
    n,nc,ops,meas=_parse(qasm_str)
    if target=="spinq": return qasm_str.strip()
    if target=="braket":
        out=['OPENQASM 3.0;','include "stdgates.inc";',f'qubit[{n}] q;',f'bit[{nc}] c;']
        for name,a,qs in ops: out.append(f'{name if name not in ("cx","cu1","ccx") else {"cx":"cnot","cu1":"cu1","ccx":"ccx"}[name]}'+(f'({a})' if a is not None else '')+' '+', '.join(f'q[{q}]' for q in qs)+';')
        out.append('c = measure q;'); return '\n'.join(out)
    out=[f'QINIT {n}',f'CREG {nc}']
    for name,a,qs in ops:
        nm={"h":"H","x":"X","s":"S","sdg":"SDAG","t":"T","tdg":"TDAG","rz":"RZ","ry":"RY","cx":"CNOT","cu1":"CU1","swap":"SWAP","ccx":"TOFFOLI"}[name]
        out.append(f'{nm}'+(f'({a})' if a is not None else '')+' '+', '.join(f'q[{q}]' for q in qs))
    for q,c in (meas or [(i,i) for i in range(min(n,nc))]): out.append(f'MEASURE q[{q}], c[{c}]')
    return '\n'.join(out)

def run(qasm_str: str, target: str, shots: int) -> Dict[str, Any]:
    if not isinstance(shots,int) or shots<=0: raise ValueError("shots must be positive")
    transpile(qasm_str,target)
    return {"backend":target,"job_id":f"local-{target}-{abs(hash(qasm_str)) & 0xffffffff:x}","shots":shots,"counts":_counts(qasm_str,shots),"bit_order":"little","timestamp":datetime.now(timezone.utc).isoformat(),"meta":{"is_mock":False,"simulator":"statevector"}}

def agent_chat(prompt: str) -> str: raise NotImplementedError("L2 is optional; implement agent_chat(prompt) to enter")
def compile_hybrid(hybrid_qasm_str: str) -> Tuple[List[str], str]: raise NotImplementedError("L3 is optional; implement compile_hybrid(hybrid_qasm_str) to enter")
