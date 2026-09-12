import json
from typing import Tuple

from music21 import instrument

from .fingerboard import Fingerboard
from .solver import SolverParams


_INSTRUMENT_MAP = {
    "violin": instrument.Violin,
    "viola": instrument.Viola,
    "violoncello": instrument.Violoncello,
    "cello": instrument.Violoncello,
    "contrabass": instrument.Contrabass,
    "double-bass": instrument.Contrabass,
    "doublebass": instrument.Contrabass,
}


def load_config(path: str) -> Tuple[Fingerboard, SolverParams, instrument.Instrument]:
    with open(path, "r") as f:
        data = json.load(f)

    fb_data = data["fingerboard"]
    fb = Fingerboard(
        n_strings=int(fb_data["n_strings"]),
        base_pitches=list(fb_data["base_pitches"]),
        offsets=list(fb_data.get("offsets") or []),
        max_fret=list(fb_data.get("max_fret") or []),
    )

    solver_data = data.get("solver", {})
    params = SolverParams(
        alpha=float(solver_data.get("alpha", 0.05)),
        beta=float(solver_data.get("beta", 0.05)),
        beam=solver_data.get("beam"),
    )

    instr_name = (data.get("instrument") or "violoncello").lower()
    instr_cls = _INSTRUMENT_MAP.get(instr_name, instrument.Violoncello)
    instr_obj = instr_cls()

    return fb, params, instr_obj
