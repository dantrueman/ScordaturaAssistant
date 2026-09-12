import copy
from typing import List, Optional, Tuple

from music21 import articulations, chord, instrument, key, note, pitch, spanner, stream

from .events import Event, parse_events
from .fingerboard import Fingerboard, OCTAVE_HARMONIC_NODE
from .solver import (
    Assignment,
    SolverParams,
    Strategy,
    favor_lower,
    favor_middle,
    favor_upper,
    no_preference,
    solve,
)
from .spelling import respell


def _active_key_signature(part: stream.Part) -> Optional[key.KeySignature]:
    for ks in part.recurse().getElementsByClass(key.KeySignature):
        return ks
    return None


def _sanitize_part_for_export(part: stream.Part) -> None:
    """Strip source-notation artifacts that trigger known music21 MusicXML
    write/round-trip bugs, so the exported score matches what was parsed:

    - Ottava (octave-shift) spanners: on write, music21 can incorrectly
      re-transpose the pitch of the tied notes at the span's boundaries
      (observed shifting a tied note's pitch down an octave).
    - Rests that music21 synthesizes to fill a <backup>/<forward> pair used
      purely to reposition a direction (e.g. the octave-shift line) — they
      overlap in time with a real note in the same (voice-less) measure and
      aren't a genuine second voice; left in place they desync all
      subsequent offsets on write.
    """
    for ottava in list(part.recurse().getElementsByClass(spanner.Ottava)):
        part.remove(ottava, recurse=True)

    for m in part.getElementsByClass(stream.Measure):
        if m.voices:
            continue  # genuine multi-voice measure: overlaps are legitimate
        notes = list(m.notes)
        for r in list(m.getElementsByClass(note.Rest)):
            r_start = r.offset
            r_end = r_start + r.duration.quarterLength
            for n in notes:
                n_start = n.offset
                n_end = n_start + n.duration.quarterLength
                if r_start < n_end and n_start < r_end:
                    m.remove(r)
                    break


def _set_pitch_on_note(n: "note.Note", new_pitch: pitch.Pitch) -> None:
    n.pitch = new_pitch


def _set_pitch_on_chord(c: chord.Chord, pitch_index: int, new_pitch: pitch.Pitch) -> None:
    pitches = list(c.pitches)
    pitches[pitch_index] = new_pitch
    c.pitches = tuple(pitches)


def _attach_string_indication_chord(c: chord.Chord, string_number: int) -> None:
    c.articulations.append(articulations.StringIndication(string_number))


def _attach_string_indication_note(n: "note.Note", string_number: int) -> None:
    n.articulations.append(articulations.StringIndication(string_number))


def _apply_event(
    event: Event,
    assignment: Assignment,
    fb: Fingerboard,
    key_sig: Optional[key.KeySignature],
    prev_sounding_midi: Optional[int],
) -> Optional[int]:
    """Mutate the event's source_refs in place. Returns the last sounding MIDI
    handled (so callers can track contour for spelling)."""

    def _respell(read_midi: int, sounding_midi: int, source_p):
        contour = 0
        if prev_sounding_midi is not None:
            contour = sounding_midi - prev_sounding_midi
        return respell(
            read_midi=read_midi,
            sounding_midi=sounding_midi,
            source_pitch=source_p,
            key_sig=key_sig,
            contour=contour,
        )

    if event.kind == "harmonic":
        # Length-1 assignment; both notes transpose by -offset of the assigned string.
        string_id = assignment[0]
        if string_id is None:
            return prev_sounding_midi
        host_gn = event.refs[0].general_note
        if not isinstance(host_gn, chord.Chord):
            return prev_sounding_midi
        offset = fb.offsets[string_id]

        # Find which chord index is the fundamental (lower MIDI) and which is
        # the touched note (higher MIDI). music21's chord pitch order is NOT
        # guaranteed low-to-high after MusicXML parse, so we map by MIDI.
        fund_idx = event.pitches.index(min(event.pitches))
        touched_idx = event.pitches.index(max(event.pitches))

        fund_read = event.pitches[fund_idx] - offset
        touched_read = event.pitches[touched_idx] - offset
        fund_new = _respell(fund_read, event.pitches[fund_idx], event.source_pitches[fund_idx])
        touched_new = _respell(touched_read, event.pitches[touched_idx], event.source_pitches[touched_idx])

        # Reorder the chord so fundamental is at index 0, touched at index 1.
        # That way a single StringIndication appended to the chord maps to the
        # fundamental (per music21's pitch-order articulation export), and the
        # diamond notehead lands on the touched note at index 1.
        host_gn.pitches = (fund_new, touched_new)
        try:
            host_gn[1].notehead = "diamond"
            host_gn[1].noteheadFill = False
        except Exception:
            pass

        _attach_string_indication_chord(host_gn, fb.notated_string_number(string_id))
        sh = articulations.StringHarmonic()
        sh.harmonicType = "artificial"
        host_gn.articulations.append(sh)

        for follower in event.refs[0].tied_links:
            if isinstance(follower, chord.Chord) and len(follower.pitches) == 2:
                follower.pitches = (fund_new, touched_new)
        return event.pitches[fund_idx]

    if event.kind == "natural_harmonic":
        # Each pitch is an independent natural-harmonic touch: assignment
        # elements are (string_id, node) tuples (or None if unassigned).
        #
        # Chord.pitches is a setter that rebuilds ALL of the chord's internal
        # Note objects from scratch (see music21's chord.py), which wipes out
        # any notehead already set on a sibling pitch of the same chord. For
        # a same-onset dyad of two natural harmonics (both pitches on the
        # SAME Chord object), that means marking pitch 0's notehead diamond
        # and THEN replacing pitch 1's pitch would silently un-diamond
        # pitch 0. So every pitch replacement for this event (including tied
        # followers) must happen first, and noteheads are only marked diamond
        # (or reset to normal, for octave harmonics) in a second pass
        # afterward.
        last_sounding = prev_sounding_midi
        notehead_targets: List[Tuple[object, Optional[int], int]] = []
        for i, (ref, source_p, sounding) in enumerate(
            zip(event.refs, event.source_pitches, event.pitches)
        ):
            elem = assignment[i] if i < len(assignment) else None
            if elem is None:
                last_sounding = sounding
                continue
            string_id, node = elem
            read = fb.base_pitches[string_id] + node
            new_p = _respell(read, sounding, source_p)
            gn = ref.general_note
            if isinstance(gn, note.Note):
                _set_pitch_on_note(gn, new_p)
                notehead_targets.append((gn, None, node))
            elif isinstance(gn, chord.Chord):
                _set_pitch_on_chord(gn, ref.pitch_index, new_p)
                notehead_targets.append((gn, ref.pitch_index, node))
            for follower in ref.tied_links:
                follower_new_p = _respell(read, sounding, source_p)
                if isinstance(follower, note.Note):
                    _set_pitch_on_note(follower, follower_new_p)
                    notehead_targets.append((follower, None, node))
                elif isinstance(follower, chord.Chord) and len(follower.pitches) > ref.pitch_index:
                    _set_pitch_on_chord(follower, ref.pitch_index, follower_new_p)
                    notehead_targets.append((follower, ref.pitch_index, node))
            last_sounding = sounding

        # Octave (2nd-partial) harmonics are conventionally written with a
        # regular notehead plus the harmonic "circle" mark (attached below),
        # not the diamond notehead used for the other partials.
        for host, idx, node in notehead_targets:
            is_octave = node == OCTAVE_HARMONIC_NODE
            if idx is None:
                host.notehead = "normal" if is_octave else "diamond"
                host.noteheadFill = None if is_octave else False
            else:
                try:
                    host[idx].notehead = "normal" if is_octave else "diamond"
                    host[idx].noteheadFill = None if is_octave else False
                except Exception:
                    pass

        # Attach string indication + natural StringHarmonic marking, in pitch
        # order, AFTER pitches are updated so MusicXML serialization sees the
        # right order.
        #
        # Caveat: music21's MusicXML exporter only ever writes non-Fingering
        # articulations (which includes StringIndication and StringHarmonic)
        # onto the FIRST note of a Chord -- unlike Fingering, there is no
        # per-chord-note distribution for these marks. So for a same-onset
        # chord of 2+ independent natural-harmonic touches, attaching one
        # mark per pitch would pile every mark onto one note while the other
        # gets none, producing structurally confusing/duplicated output. The
        # hand-verified reference score itself omits these technical marks
        # for that exact case, relying on the diamond notehead alone, so we
        # match it here. Single-note (melodic) natural harmonics are
        # unaffected and keep full string/harmonic markings.
        is_chord_dyad = len(event.pitches) > 1
        hosts_seen = []
        for i, ref in enumerate(event.refs):
            elem = assignment[i] if i < len(assignment) else None
            if elem is None:
                continue
            string_id, _node = elem
            gn = ref.general_note
            if isinstance(gn, note.Note):
                if id(gn) not in hosts_seen:
                    _attach_string_indication_note(gn, fb.notated_string_number(string_id))
                    sh = articulations.StringHarmonic()
                    sh.harmonicType = "natural"
                    gn.articulations.append(sh)
                    hosts_seen.append(id(gn))
            elif isinstance(gn, chord.Chord) and not is_chord_dyad:
                _attach_string_indication_chord(gn, fb.notated_string_number(string_id))
                sh = articulations.StringHarmonic()
                sh.harmonicType = "natural"
                gn.articulations.append(sh)

        return last_sounding

    # single or chord
    last_sounding = prev_sounding_midi
    for i, (ref, source_p, sounding) in enumerate(
        zip(event.refs, event.source_pitches, event.pitches)
    ):
        string_id = assignment[i] if i < len(assignment) else None
        if string_id is None:
            last_sounding = sounding
            continue
        offset = fb.offsets[string_id]
        read = sounding - offset
        new_p = _respell(read, sounding, source_p)
        gn = ref.general_note
        if isinstance(gn, note.Note):
            _set_pitch_on_note(gn, new_p)
        elif isinstance(gn, chord.Chord):
            _set_pitch_on_chord(gn, ref.pitch_index, new_p)
        for follower in ref.tied_links:
            if isinstance(follower, note.Note):
                _set_pitch_on_note(follower, _respell(read, sounding, source_p))
            elif isinstance(follower, chord.Chord) and len(follower.pitches) > ref.pitch_index:
                _set_pitch_on_chord(
                    follower, ref.pitch_index, _respell(read, sounding, source_p)
                )
        last_sounding = sounding

    # Attach string indications. For Chord, in pitch order. For Note, on the note.
    # We need to attach AFTER pitches are updated so MusicXML serialization sees
    # the right order.
    hosts_seen = []
    for i, ref in enumerate(event.refs):
        s = assignment[i] if i < len(assignment) else None
        if s is None:
            continue
        gn = ref.general_note
        if isinstance(gn, note.Note):
            if id(gn) not in hosts_seen:
                _attach_string_indication_note(gn, fb.notated_string_number(s))
                hosts_seen.append(id(gn))
        elif isinstance(gn, chord.Chord):
            _attach_string_indication_chord(gn, fb.notated_string_number(s))

    return last_sounding


def _build_fingered_part(
    source_part: stream.Part,
    fb: Fingerboard,
    strategy: Strategy,
    params: SolverParams,
    part_id: str,
    part_name: str,
    instrument_obj: instrument.Instrument,
    avoid: Optional[List[Assignment]] = None,
    candidate_override: Optional[List[List[Assignment]]] = None,
) -> Tuple[stream.Part, List[str], List[Assignment]]:
    """avoid, when given, is another strategy's already-solved per-event
    Assignment list for this same source_part (see solve()'s avoid), used
    to steer this strategy away from duplicating its overall per-event
    string usage. candidate_override, when given, is passed straight through to
    solve() to restrict candidates to a caller-supplied set (see solve()'s
    candidate_override, used to build a hybrid staff out of two other
    already-solved staves). Returns the built part, warnings, and this
    solve's own Assignment list (so callers can feed it as avoid or as a
    hybrid input into a subsequent call)."""
    new_part = copy.deepcopy(source_part)
    new_part.id = part_id
    new_part.partName = part_name
    new_part.partAbbreviation = part_name[:8]
    # Insert/replace instrument at offset 0.
    existing_instr = new_part.getElementsByClass(instrument.Instrument)
    for ei in list(existing_instr):
        new_part.remove(ei)
    new_part.insert(0.0, copy.deepcopy(instrument_obj))

    events = parse_events(new_part)
    assignments, warnings = solve(
        events, fb, strategy, params, avoid=avoid, candidate_override=candidate_override
    )

    if not assignments:
        _sanitize_part_for_export(new_part)
        return new_part, warnings, assignments

    ks = _active_key_signature(new_part)
    prev_sounding = None
    for ev, asg in zip(events, assignments):
        prev_sounding = _apply_event(ev, asg, fb, ks, prev_sounding)

    _sanitize_part_for_export(new_part)
    return new_part, warnings, assignments


def _hybrid_candidate_override(
    asg_a: List[Assignment], asg_b: List[Assignment]
) -> List[List[Assignment]]:
    """Per-event candidate set combining two already-solved Assignment lists:
    each event's candidates are the (deduplicated) pair of what `asg_a` and
    `asg_b` chose there. Feeding this into solve() via candidate_override
    stitches together a genuine note-by-note blend of the two solutions —
    picking whichever side is locally better under a neutral cost, with the
    normal transition cost still discouraging pointless string-hopping —
    instead of re-exploring the whole fingerboard from scratch."""
    out: List[List[Assignment]] = []
    for a, b in zip(asg_a, asg_b):
        out.append([a] if a == b else [a, b])
    return out


def _passthrough_part(
    source_part: stream.Part,
    part_id: str,
    part_name: str,
    instrument_obj: instrument.Instrument,
) -> stream.Part:
    new_part = copy.deepcopy(source_part)
    new_part.id = part_id
    new_part.partName = part_name
    new_part.partAbbreviation = part_name[:8]
    existing_instr = new_part.getElementsByClass(instrument.Instrument)
    for ei in list(existing_instr):
        new_part.remove(ei)
    new_part.insert(0.0, copy.deepcopy(instrument_obj))
    _sanitize_part_for_export(new_part)
    return new_part


def build_score(
    source_part: stream.Part,
    fingerboard: Fingerboard,
    params: Optional[SolverParams] = None,
    instrument_obj: Optional[instrument.Instrument] = None,
) -> Tuple[stream.Score, List[str]]:
    params = params or SolverParams()
    instrument_obj = instrument_obj or instrument.Violoncello()

    p1 = _passthrough_part(source_part, "P1", "Sounding (source)", instrument_obj)
    p2, w2, asg2 = _build_fingered_part(
        source_part, fingerboard, favor_upper, params, "P2", "Favor upper", instrument_obj
    )
    # Feed favor_upper's per-event string choices in as favor_lower's "avoid"
    # list, so the two staves diverge (see SolverParams.duplicate_penalty)
    # wherever a genuinely different set of strings is reachable for that
    # event, rather than both strategies silently converging on the same
    # overall fingering (judged per event, not per pitch, so a chord can't
    # "dodge" the check by merely swapping which pitch uses which string).
    p3, w3, asg3 = _build_fingered_part(
        source_part, fingerboard, favor_lower, params, "P3", "Favor lower", instrument_obj,
        avoid=asg2,
    )

    # Three exploratory "combined" alternatives, each solved independently
    # of P2/P3's de-duplication (they may legitimately converge with either
    # staff wherever that's genuinely the best choice):
    #
    # P4 "Hybrid": restricted, event-by-event, to whichever of P2's/P3's own
    # choice is locally better under a neutral (non-directional) cost — a
    # literal note-by-note stitch of the two existing solutions.
    hybrid_override = None
    if asg2 and asg3 and len(asg2) == len(asg3):
        hybrid_override = _hybrid_candidate_override(asg2, asg3)
    p4, w4, _asg4 = _build_fingered_part(
        source_part, fingerboard, no_preference, params, "P4", "Hybrid (upper+lower)",
        instrument_obj, candidate_override=hybrid_override,
    )
    # P5 "No preference": a fresh, independent solve with zero string-index
    # bias — open-string bonus, hints, and transition smoothness decide
    # freely, often landing naturally between the two extremes.
    p5, w5, _asg5 = _build_fingered_part(
        source_part, fingerboard, no_preference, params, "P5", "No preference", instrument_obj
    )
    # P6 "Favor middle": a fresh, independent solve that explicitly prefers
    # strings near the center of the fingerboard.
    p6, w6, _asg6 = _build_fingered_part(
        source_part, fingerboard, favor_middle, params, "P6", "Favor middle", instrument_obj
    )

    score = stream.Score()
    score.insert(0, p1)
    score.insert(0, p2)
    score.insert(0, p3)
    score.insert(0, p4)
    score.insert(0, p5)
    score.insert(0, p6)

    return score, w2 + w3 + w4 + w5 + w6
