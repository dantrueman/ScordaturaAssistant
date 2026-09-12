from dataclasses import dataclass
from itertools import product
from typing import Callable, List, Optional, Tuple, Union

from .events import Event
from .fingerboard import NATURAL_HARMONIC_NODES, Fingerboard


Strategy = Callable[[int, Fingerboard], float]


def favor_upper(string_id: int, fingerboard: Fingerboard) -> float:
    return -float(string_id)


def favor_lower(string_id: int, fingerboard: Fingerboard) -> float:
    return float(string_id)


def no_preference(string_id: int, fingerboard: Fingerboard) -> float:
    """No directional bias at all: string choice is left entirely to the
    open-string bonus, string hints, and transition smoothness."""
    return 0.0


def favor_middle(string_id: int, fingerboard: Fingerboard) -> float:
    """Prefers strings near the middle of the fingerboard (distance from the
    center string index), as an explicit third fingering philosophy distinct
    from both extremes."""
    center = (fingerboard.n_strings - 1) / 2.0
    return abs(string_id - center)


@dataclass
class SolverParams:
    # Emission preferences (favor_upper/favor_lower) return signed integers in
    # [-n_strings, n_strings]. Transition costs (semitone position shift +
    # string-change count) are weighted small so the strategy is the primary
    # signal and transitions only break ties / suppress wild jumps.
    alpha: float = 0.05         # position-shift weight (per semitone)
    beta: float = 0.05          # string-change weight (per string)
    open_string_bonus: float = -3.0  # emission bonus per quarter-note when a
                                     # pitch lands on its natural open string.
                                     # Multiplied by note duration so a long
                                     # sustained open string outranks several
                                     # short ornamental ones competing for it.
                                     # Still soft — Viterbi can sacrifice it.
    partial_weight: float = 0.1  # per quarter-note soft cost per partial rank,
                                 # so natural harmonics prefer the more common/
                                 # reliable partials (2nd-4th) when a pitch is
                                 # reachable via more than one (string, node).
    string_hint_bonus: float = -1000.0  # emission bonus, flat (not duration-
                                     # weighted, so even a zero-length grace
                                     # note is covered), applied when a pitch
                                     # lands on the string explicitly marked
                                     # (or persisted from an earlier marker in
                                     # the same voice) in the source. Large
                                     # enough to dominate strategy/open-string/
                                     # transition costs, so the marked string
                                     # wins whenever it's actually reachable —
                                     # still soft: if it's blocked by a sustain
                                     # conflict, the solver falls back to
                                     # another valid string instead of forcing
                                     # an unplayable assignment.
    duplicate_penalty: float = 500.0  # emission penalty, flat (not duration-
                                     # weighted, mirroring string_hint_bonus),
                                     # added per pitch when a candidate would
                                     # land that pitch on the exact same
                                     # string as the OTHER strategy's solved
                                     # assignment for it (see `avoid` in
                                     # solve()), but only when a genuinely
                                     # different valid string also exists for
                                     # that pitch. Large enough to dominate
                                     # strategy/open-string/transition costs,
                                     # so favor_lower and favor_upper diverge
                                     # wherever there's a real choice — but
                                     # never applied to a pitch that carries
                                     # an explicit/persisted string hint
                                     # matching the candidate, so hints always
                                     # take precedence over de-duplication.
    beam: Optional[int] = None


# An Assignment element is either a plain string id (stopped note), a
# (string_id, node) tuple (natural-harmonic touch point), or None (unassigned).
# For kind="harmonic", the Assignment has length 1 (fundamental's string only)
# even though the Event has 2 pitches; the renderer expands.
AssignmentElem = Optional[Union[int, Tuple[int, int]]]
Assignment = Tuple[AssignmentElem, ...]


_PARTIAL_RANK_BY_NODE = {node: rank for rank, (node, _extra) in enumerate(NATURAL_HARMONIC_NODES)}


def _string_of(elem: AssignmentElem) -> Optional[int]:
    """The occupied string id for any assignment element shape."""
    if elem is None:
        return None
    if isinstance(elem, tuple):
        return elem[0]
    return elem


def _string_hint(event: Event, index: int, fb: Fingerboard) -> Optional[int]:
    """The explicit/persisted string hint for pitch `index` of `event`, if
    any, converted from the source's notated MusicXML string number (1 =
    highest-pitched string) to fb's 0-indexed string id (0 = lowest-pitched
    string)."""
    hints = event.string_hints
    notated = hints[index] if index < len(hints) else None
    return fb.string_id_for_notated_number(notated) if notated is not None else None


def _candidates_for_event(event: Event, fb: Fingerboard, warnings: List[str]) -> List[Assignment]:
    if event.kind == "harmonic":
        # Only the fundamental (lower pitch) drives assignment.
        fundamental_midi = min(event.pitches)
        fund_idx = event.pitches.index(fundamental_midi)
        candidates = [(s,) for s in fb.valid_strings_for(fundamental_midi)]
        if not candidates:
            warnings.append(
                f"event onset={event.onset}: harmonic fundamental midi {fundamental_midi} out of range"
            )
            return [(None,)]
        hint = _string_hint(event, fund_idx, fb)
        if hint is not None and hint not in [c[0] for c in candidates]:
            warnings.append(
                f"event onset={event.onset}: string hint {fb.notated_string_number(hint)} "
                f"not reachable for harmonic fundamental midi {fundamental_midi}"
            )
        return candidates

    per_pitch_options: List[List[AssignmentElem]] = []
    for pitch_idx, midi in enumerate(event.pitches):
        hint = _string_hint(event, pitch_idx, fb)
        if event.kind == "natural_harmonic":
            valids: List[AssignmentElem] = fb.natural_harmonic_candidates(midi)
            if not valids:
                warnings.append(
                    f"event onset={event.onset}: natural harmonic midi {midi} "
                    "not reachable on any string/partial"
                )
            elif hint is not None and hint not in [s for s, _node in valids]:
                warnings.append(
                    f"event onset={event.onset}: string hint {fb.notated_string_number(hint)} "
                    f"not reachable for natural harmonic midi {midi}"
                )
        else:
            valids = list(fb.valid_strings_for(midi))
            if not valids:
                warnings.append(
                    f"event onset={event.onset}: midi {midi} out of fingerboard range"
                )
            elif hint is not None and hint not in valids:
                warnings.append(
                    f"event onset={event.onset}: string hint {fb.notated_string_number(hint)} "
                    f"not reachable for midi {midi}"
                )
        # Always allow None as a fallback, so the solver can proceed even if
        # a note is out of range or blocked by sustains.
        per_pitch_options.append(list(valids) + [None])

    out: List[Assignment] = []
    for combo in product(*per_pitch_options):
        used = [_string_of(elem) for elem in combo if elem is not None]
        if len(used) != len(set(used)):
            continue  # same-event string collision
        if event.kind == "natural_harmonic" and len(used) >= 2 and max(used) - min(used) > 1:
            # A simultaneous natural-harmonic dyad/chord can only be bowed on
            # adjacent strings, unlike stopped double-stops (which can span
            # non-adjacent strings via arpeggiation or aren't touch-point
            # dependent). Reject non-adjacent (string, node) combinations.
            continue
        out.append(tuple(combo))
    return out


def _used_string_set(assignment: Assignment) -> frozenset:
    """The overall set of strings occupied by an Assignment (ignoring
    unassigned/None pitches)."""
    return frozenset(s for s in (_string_of(elem) for elem in assignment) if s is not None)


def _event_has_alternative_string_set(
    candidates: List[Assignment], avoid_set: frozenset
) -> bool:
    """Whether at least one fully-assigned candidate for this event uses a
    genuinely different overall SET of strings than `avoid_set`. De-
    duplication is judged at the whole-event/string-set level (not per
    pitch): a simultaneous chord (e.g. a held pedal note plus a moving
    melodic voice) can otherwise "dodge" the per-pitch penalty by simply
    swapping which pitch occupies which string, while still occupying the
    exact same pair of strings as the other strategy — sacrificing whatever
    made one of them attractive (e.g. an open string) for zero genuine
    diversification. Candidates with any unassigned pitch are ignored here,
    so a degenerate "drop a note" combo never counts as a real alternative."""
    for cand in candidates:
        if any(elem is None for elem in cand):
            continue
        if _used_string_set(cand) != avoid_set:
            return True
    return False


def _event_duplicate_cost(
    assignment: Assignment,
    event: Event,
    fb: Fingerboard,
    avoid: Optional[Assignment],
    has_alternative_set: bool,
    duplicate_penalty: float,
) -> float:
    """Flat, whole-event penalty for this candidate occupying the exact same
    SET of strings as the OTHER strategy's solved assignment for this event,
    provided a genuinely different set of strings is actually reachable
    (see _event_has_alternative_string_set). Never applied when every
    occupied string in this candidate matches that pitch's own explicit/
    persisted string hint — hints always outrank de-duplication."""
    if avoid is None or not has_alternative_set:
        return 0.0
    avoid_set = _used_string_set(avoid)
    cand_set = _used_string_set(assignment)
    if not cand_set or cand_set != avoid_set:
        return 0.0
    for i, elem in enumerate(assignment):
        s = _string_of(elem)
        if s is None:
            continue
        if _string_hint(event, i, fb) != s:
            return duplicate_penalty
    return 0.0


def _emission_cost(
    assignment: Assignment,
    event: Event,
    fb: Fingerboard,
    strategy: Strategy,
    open_string_bonus: float,
    partial_weight: float = 0.0,
    string_hint_bonus: float = 0.0,
    avoid: Optional[Assignment] = None,
    duplicate_penalty: float = 0.0,
    has_alternative_set: bool = False,
) -> float:
    """Both strategy preference and open-string bonus are weighted by note
    duration. A long sustained note's choice of string matters more than a
    fast ornament's, and otherwise a passage of fast notes can collectively
    outvote a single long note for a contested resource (e.g. two voices
    wanting the same open string). string_hint_bonus is deliberately flat
    (not duration-weighted) so it still applies fully even to a zero-length
    grace note. `avoid`, when given, is the OTHER strategy's solved
    assignment for this same event, used to discourage (not forbid) this
    whole event occupying the exact same SET of strings it did — judged at
    the event level (see _event_duplicate_cost) rather than per pitch, so a
    chord can't "dodge" the penalty by merely swapping which pitch occupies
    which string while still using the same strings overall. `duplicate_penalty`
    is likewise flat; `has_alternative_set`, precomputed once per event by
    solve(), says whether a genuinely different set of strings is reachable
    at all for this event."""
    total = 0.0
    if event.kind == "harmonic":
        fundamental = min(event.pitches)
        fund_idx = event.pitches.index(fundamental)
        s = assignment[0]
        dur = event.duration_per_note[0] if event.duration_per_note else 1.0
        if s is not None:
            total += strategy(s, fb) * dur
            if fb.sounding_open(s) == fundamental:
                total += open_string_bonus * dur
            hint = _string_hint(event, fund_idx, fb)
            if hint == s:
                total += string_hint_bonus
        else:
            total += 1000.0 * dur
        total += _event_duplicate_cost(
            assignment, event, fb, avoid, has_alternative_set, duplicate_penalty
        )
        return total
    if event.kind == "natural_harmonic":
        for i, (elem, midi) in enumerate(zip(assignment, event.pitches)):
            dur = event.duration_per_note[i] if i < len(event.duration_per_note) else 1.0
            if elem is None:
                total += 1000.0 * dur
                continue
            s, node = elem
            total += strategy(s, fb) * dur
            total += _PARTIAL_RANK_BY_NODE.get(node, 0) * partial_weight * dur
            hint = _string_hint(event, i, fb)
            if hint == s:
                total += string_hint_bonus
        total += _event_duplicate_cost(
            assignment, event, fb, avoid, has_alternative_set, duplicate_penalty
        )
        return total
    for i, (s, midi) in enumerate(zip(assignment, event.pitches)):
        dur = event.duration_per_note[i] if i < len(event.duration_per_note) else 1.0
        if s is None:
            # High penalty for unassigned note, so it's only used as a last resort.
            total += 1000.0 * dur
            continue
        total += strategy(s, fb) * dur
        if fb.sounding_open(s) == midi:
            total += open_string_bonus * dur
        hint = _string_hint(event, i, fb)
        if hint == s:
            total += string_hint_bonus
    total += _event_duplicate_cost(
        assignment, event, fb, avoid, has_alternative_set, duplicate_penalty
    )
    return total


def _mean_fret(assignment: Assignment, event: Event, fb: Fingerboard) -> Optional[float]:
    if event.kind == "harmonic":
        fundamental = min(event.pitches)
        s = assignment[0]
        if s is None:
            return None
        return float(fundamental - fb.sounding_open(s))
    frets = []
    for elem, midi in zip(assignment, event.pitches):
        if elem is None:
            continue
        if isinstance(elem, tuple):
            # Natural harmonic: the physical touch point is the effective
            # fret/position for transition-smoothness continuity.
            _, node = elem
            frets.append(node)
        else:
            frets.append(midi - fb.sounding_open(elem))
    if not frets:
        return None
    return sum(frets) / len(frets)


def _transition_cost(
    prev_assignment: Assignment,
    curr_assignment: Assignment,
    prev_event: Event,
    curr_event: Event,
    fb: Fingerboard,
    alpha: float,
    beta: float,
) -> float:
    prev_strings = {_string_of(elem) for elem in prev_assignment if elem is not None}
    curr_strings = {_string_of(elem) for elem in curr_assignment if elem is not None}
    if not prev_strings or not curr_strings:
        return 0.0
    prev_fret = _mean_fret(prev_assignment, prev_event, fb)
    curr_fret = _mean_fret(curr_assignment, curr_event, fb)
    shift = abs(curr_fret - prev_fret) if (prev_fret is not None and curr_fret is not None) else 0.0
    string_change = len(prev_strings.symmetric_difference(curr_strings))
    return alpha * shift + beta * string_change


def _next_sustains(
    prev_sustains: frozenset,
    assignment: Assignment,
    event: Event,
) -> frozenset:
    if event.is_grace:
        return prev_sustains
    # Same-event durations: assignment may be shorter than pitches (harmonic).
    if event.kind == "harmonic":
        durations = [event.duration_per_note[0]]
    else:
        durations = event.duration_per_note
    additions = []
    for elem, d in zip(assignment, durations):
        if elem is None:
            continue
        additions.append((_string_of(elem), event.onset + float(d)))
    return prev_sustains | frozenset(additions)


def solve(
    events: List[Event],
    fingerboard: Fingerboard,
    strategy: Strategy,
    params: Optional[SolverParams] = None,
    avoid: Optional[List[Assignment]] = None,
    candidate_override: Optional[List[List[Assignment]]] = None,
) -> Tuple[List[Assignment], List[str]]:
    """`avoid`, when given, is another solve()'s resulting per-event
    Assignment list (e.g. favor_upper's, when solving favor_lower), used to
    softly steer this solve away from duplicating the other strategy's
    overall per-event string usage — i.e. occupying the exact same SET of
    strings for that event — wherever a genuinely different set of strings
    is reachable (see SolverParams.duplicate_penalty). This is judged at
    the whole-event level rather than per pitch, so a simultaneous chord
    can't dodge the penalty by merely swapping which pitch uses which
    string while still using the same strings overall.

    `candidate_override`, when given, replaces the normal "every valid
    string" candidate set per event with a caller-supplied, restricted list
    (e.g. just two other solves' per-event Assignments) — used to stitch a
    hybrid solution together out of a small number of already-solved
    alternatives instead of exploring the full fingerboard again."""
    params = params or SolverParams()
    warnings: List[str] = []
    if candidate_override is not None:
        candidates_per_event = candidate_override
    else:
        candidates_per_event = [_candidates_for_event(ev, fingerboard, warnings) for ev in events]

    if not events:
        return [], warnings

    INF = float("inf")
    INITIAL_STATE = ((), frozenset())
    dp_prev = {INITIAL_STATE: 0.0}
    backpointers: List[dict] = []

    for i, ev in enumerate(events):
        cands = candidates_per_event[i]
        dp_curr: dict = {}
        bp_curr: dict = {}
        if not cands:
            warnings.append(f"event onset={ev.onset}: no candidates available")
            return [], warnings

        avoid_assignment = avoid[i] if avoid is not None and i < len(avoid) else None
        has_alt_set = (
            _event_has_alternative_string_set(cands, _used_string_set(avoid_assignment))
            if avoid_assignment is not None
            else False
        )

        for prev_state, prev_cost in dp_prev.items():
            prev_assignment, prev_sustains = prev_state
            live = frozenset((s, t) for (s, t) in prev_sustains if t > ev.onset + 1e-9)
            occupied = {s for (s, _) in live}

            for cand in cands:
                used = {_string_of(elem) for elem in cand if elem is not None}
                if used & occupied:
                    continue
                emission = _emission_cost(
                    cand, ev, fingerboard, strategy, params.open_string_bonus,
                    params.partial_weight, params.string_hint_bonus,
                    avoid_assignment, params.duplicate_penalty, has_alt_set,
                )
                if i == 0 or not prev_assignment:
                    transition = 0.0
                else:
                    transition = _transition_cost(
                        prev_assignment, cand, events[i - 1], ev,
                        fingerboard, params.alpha, params.beta,
                    )
                new_sustains = _next_sustains(live, cand, ev)
                new_state = (cand, new_sustains)
                new_cost = prev_cost + emission + transition
                existing = dp_curr.get(new_state)
                if existing is None or existing > new_cost:
                    dp_curr[new_state] = new_cost
                    bp_curr[new_state] = prev_state

        if not dp_curr:
            warnings.append(
                f"event onset={ev.onset}: every candidate blocked by sustains"
            )
            return [], warnings

        if params.beam is not None and len(dp_curr) > params.beam:
            top = sorted(dp_curr.items(), key=lambda kv: kv[1])[: params.beam]
            keep_states = {s for s, _ in top}
            dp_curr = {s: c for s, c in dp_curr.items() if s in keep_states}
            bp_curr = {s: bp_curr[s] for s in dp_curr}

        backpointers.append(bp_curr)
        dp_prev = dp_curr

    final_state = min(dp_prev.items(), key=lambda kv: kv[1])[0]
    path_states = [final_state]
    for i in range(len(events) - 1, 0, -1):
        prev_state = backpointers[i][path_states[-1]]
        path_states.append(prev_state)
    path_states.reverse()
    assignments = [state[0] for state in path_states]

    # Surface warnings for unassigned notes.
    for i, (ev, asg) in enumerate(zip(events, assignments)):
        for j, s in enumerate(asg):
            if s is None:
                midi = ev.pitches[j]
                if ev.kind == "natural_harmonic":
                    has_candidates = bool(fingerboard.natural_harmonic_candidates(midi))
                else:
                    has_candidates = bool(fingerboard.valid_strings_for(midi))
                if not has_candidates:
                    # Already warned in _candidates_for_event
                    pass
                else:
                    warnings.append(
                        f"event onset={ev.onset}: midi {midi} unassigned (all valid strings occupied)"
                    )

    return assignments, warnings
