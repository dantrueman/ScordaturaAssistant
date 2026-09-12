from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from music21 import articulations, chord, note, stream

from .fingerboard import is_artificial_harmonic


def _has_explicit_harmonic_marker(host_gn) -> bool:
    """A chord is treated as an artificial harmonic only when the source flags
    it explicitly — a per-pitch diamond notehead or a Harmonic/StringHarmonic
    articulation. P4 interval alone is too aggressive (P4 double-stops are
    extremely common in normal string writing)."""
    for art in getattr(host_gn, "articulations", []) or []:
        if isinstance(art, articulations.Harmonic):
            return True
    if hasattr(host_gn, "notes"):
        for n in host_gn.notes:
            if getattr(n, "notehead", None) == "diamond":
                return True
    return False


def _notehead_for_pitch(gn: "note.GeneralNote", pitch_index: int) -> Optional[str]:
    """The notehead of an individual pitch within a Note or Chord."""
    if isinstance(gn, note.Note):
        return getattr(gn, "notehead", None)
    if isinstance(gn, chord.Chord):
        sub = gn.notes[pitch_index]
        return getattr(sub, "notehead", None)
    return None


def _string_hint_for_pitch(gn: "note.GeneralNote", pitch_index: int) -> Optional[int]:
    """The notated MusicXML string number explicitly marked (via a
    StringIndication articulation, e.g. a source
    <technical><string>N</string></technical>) on an individual pitch within
    a Note or Chord, or None if unmarked. Returned as-is (1 = highest-pitched
    string, per the MusicXML <string> convention) — NOT converted to a
    Fingerboard's 0-indexed string id; that conversion happens later, once a
    Fingerboard (and its string count) is available, via
    Fingerboard.string_id_for_notated_number."""
    if isinstance(gn, note.Note):
        target = gn
    elif isinstance(gn, chord.Chord):
        target = gn.notes[pitch_index]
    else:
        return None
    for art in getattr(target, "articulations", []) or []:
        if isinstance(art, articulations.StringIndication):
            number = getattr(art, "number", None)
            if number:
                return int(number)
    return None


@dataclass
class PitchRef:
    """Pointer back to where a pitch lives in the source part. The renderer uses
    this to mutate the corresponding note in the deep-copied parts 2 and 3."""

    general_note: "note.GeneralNote"  # the host Note or Chord
    pitch_index: int                  # index within Chord.pitches; 0 for Note
    tied_links: List["note.GeneralNote"] = field(default_factory=list)
    # tied_links: the chain of *additional* tied-to notes that should receive
    # the same string assignment when rendering (does not include the head).


@dataclass
class Event:
    onset: float                      # absolute quarterLength within the Part
    pitches: List[int]                # sounding MIDI integers, ordered
    duration_per_note: List[float]    # quarterLength, parallel to pitches
    refs: List[PitchRef]              # parallel to pitches
    kind: str                         # "single" | "chord" | "harmonic" | "natural_harmonic"
    tied_to_prev: List[bool]          # parallel to pitches; True if tied from previous event's same pitch
    is_grace: bool = False
    source_pitches: List["object"] = field(default_factory=list)
    # source_pitches[i] is the music21 Pitch object as it appeared in the source
    # (so spelling.respell can honor the source's accidental direction).
    string_hints: List[Optional[int]] = field(default_factory=list)
    # string_hints[i] is the notated MusicXML string number (1 = highest-
    # pitched string, not a Fingerboard's 0-indexed string id) explicitly
    # marked in the source (or inherited/persisted from an earlier explicit
    # marker in the same voice), or None when no hint applies to that pitch.
    # Converted to a 0-indexed string id via
    # Fingerboard.string_id_for_notated_number at the point of use.


def _voice_id_for(gn: "note.GeneralNote"):
    """Return the id of the containing Voice (e.g. "1" or "2"). When a measure
    has only a single voice, music21 doesn't wrap it in a Voice stream at all
    (there's nothing to disambiguate), so the original MusicXML <voice> number
    is lost. Default that case to "1", MusicXML's default voice number for an
    omitted/unambiguous <voice> element — this keeps a line's identity
    continuous across a boundary where a neighboring measure switches between
    single- and multi-voice notation (as happens with cross-measure ties and
    string-indication persistence)."""
    v = gn.getContextByClass(stream.Voice)
    return v.id if v is not None else "1"


def _iter_notes(part: stream.Part):
    """Yield (general_note, absolute_offset, voice_id) for every Note and
    Chord in the Part, traversing Measures and Voices. Materializes
    immediately to avoid mid-iteration mutation hazards. voice_id is the
    containing Voice's id (e.g. "1") or None when the measure has no
    voice container."""
    items: List[Tuple["note.GeneralNote", float, object]] = []
    for el in list(part.recurse(includeSelf=False).notes):
        try:
            off = el.getOffsetInHierarchy(part)
        except Exception:
            off = float(el.offset)
        items.append((el, float(off), _voice_id_for(el)))
    items.sort(key=lambda kv: kv[1])
    return items


def _is_tied_to_prev(n: "note.Note") -> bool:
    t = getattr(n, "tie", None)
    return t is not None and t.type in ("stop", "continue")


def _is_tied_to_next(n: "note.Note") -> bool:
    t = getattr(n, "tie", None)
    return t is not None and t.type in ("start", "continue")


def _build_pitch_ref(gn: "note.GeneralNote", pitch_index: int) -> PitchRef:
    return PitchRef(general_note=gn, pitch_index=pitch_index)


def _compute_voice_string_hints(raw) -> dict:
    """For every Note/Chord in onset order, work out the per-pitch string
    hint that should apply: an explicit StringIndication on that pitch, or —
    failing that — whatever string was last explicitly marked earlier in the
    same voice (the marking 'persists' until a new one appears). Returns a
    dict mapping id(general_note) -> list of Optional[int], parallel to that
    note's pitches (length 1 for a plain Note).

    Persistence is voice-scoped (via _voice_id_for) and only ever set/read by
    single-pitch notes — a multi-pitch chord's per-pitch hints are read
    independently (an unmarked chord pitch stays unhinted) since it's
    ambiguous which line within the chord a persisted single-string hint
    would continue."""
    hints: dict = {}
    voice_current: dict = {}
    for gn, _off, vid in raw:
        if isinstance(gn, note.Note):
            explicit = _string_hint_for_pitch(gn, 0)
            if explicit is not None:
                voice_current[vid] = explicit
                hints[id(gn)] = [explicit]
            else:
                hints[id(gn)] = [voice_current.get(vid)]
        elif isinstance(gn, chord.Chord):
            per_pitch = [_string_hint_for_pitch(gn, i) for i in range(len(gn.pitches))]
            if len(gn.pitches) == 1:
                if per_pitch[0] is not None:
                    voice_current[vid] = per_pitch[0]
                else:
                    per_pitch[0] = voice_current.get(vid)
            hints[id(gn)] = per_pitch
    return hints


def _resolve_tied_chains(part: stream.Part, items):
    """For each Note (or single-note Chord) that begins a tied chain, walk the
    chain forward and record which following notes share the same string
    assignment. Returns a dict mapping id(start_note) -> list of follower
    GeneralNotes, plus a set of ids of followers that should be skipped as
    separate events.

    Chains MUST stay within a single voice. When two voices play the same
    pitch at overlapping onsets, naive forward-pitch matching pairs them
    incorrectly (e.g. v1 m1 → v2 m1 misfires because they're both tie-starts,
    then v2 m1 wrongly absorbs v1 m2 into its chain). Cross-voice notes are
    skipped while walking forward, never followed."""
    chain_followers = {}
    skip_followers = set()

    flat_index = []
    for gn, off, vid in items:
        if isinstance(gn, note.Note):
            flat_index.append((gn, gn.pitch.midi, vid))
        elif isinstance(gn, chord.Chord) and len(gn.pitches) == 1:
            flat_index.append((gn, gn.pitches[0].midi, vid))

    for i, (gn, midi, vid) in enumerate(flat_index):
        target_note = gn if isinstance(gn, note.Note) else gn.notes[0]
        if not _is_tied_to_next(target_note):
            continue
        if id(gn) in skip_followers:
            continue
        followers = []
        j = i + 1
        while j < len(flat_index):
            next_gn, next_midi, next_vid = flat_index[j]
            if next_vid != vid:
                j += 1
                continue  # cross-voice — skip, don't chain
            if next_midi != midi:
                break
            next_note = next_gn if isinstance(next_gn, note.Note) else next_gn.notes[0]
            if not _is_tied_to_prev(next_note):
                break
            followers.append(next_gn)
            skip_followers.add(id(next_gn))
            if not _is_tied_to_next(next_note):
                break
            j += 1
        if followers:
            chain_followers[id(gn)] = followers

    return chain_followers, skip_followers


def parse_events(part: stream.Part) -> List[Event]:
    """Walk a music21 Part and produce solver events."""
    raw = _iter_notes(part)
    chain_followers, skip_followers = _resolve_tied_chains(part, raw)
    voice_string_hints = _compute_voice_string_hints(raw)

    # Group same-onset notes (across voices) into a single Event.
    grouped: List[List[Tuple["note.GeneralNote", float]]] = []
    last_off = None
    for gn, off, _vid in raw:
        if id(gn) in skip_followers:
            continue
        # A grace Note (as opposed to a genuine grace Chord, which is already
        # one GeneralNote) shares its offset with the note it decorates only
        # because it has zero duration to advance the timeline — it's a
        # sequential ornament, never truly simultaneous with that note or
        # with another such grace note. Force it into its own event so it
        # isn't mistaken for a same-onset double-stop (which would otherwise
        # wrongly forbid two of these sequential notes from sharing a string).
        if isinstance(gn, note.Note) and getattr(gn.duration, "isGrace", False):
            grouped.append([(gn, off)])
            last_off = None  # also force the following item into a fresh group
            continue
        if last_off is None or abs(off - last_off) > 1e-9:
            grouped.append([(gn, off)])
            last_off = off
        else:
            grouped[-1].append((gn, off))

    events: List[Event] = []
    prev_pitch_to_string_slot: List[int] = []  # we don't know assignments yet; this is just to surface tied_to_prev correctly per pitch

    for group in grouped:
        onset = group[0][1]
        pitches: List[int] = []
        durations: List[float] = []
        refs: List[PitchRef] = []
        source_pitches: List["object"] = []
        tied_to_prev: List[bool] = []
        noteheads: List[Optional[str]] = []
        string_hints: List[Optional[int]] = []

        is_grace = all(
            getattr(gn.duration, "isGrace", False) for gn, _ in group
        )

        for gn, _ in group:
            if isinstance(gn, note.Note):
                pitches.append(gn.pitch.midi)
                # Tied chain: aggregate duration across followers.
                base_dur = float(gn.duration.quarterLength)
                followers = chain_followers.get(id(gn), [])
                total_dur = base_dur + sum(
                    float(f.duration.quarterLength) for f in followers
                )
                durations.append(total_dur)
                ref = _build_pitch_ref(gn, 0)
                ref.tied_links = followers
                refs.append(ref)
                source_pitches.append(gn.pitch)
                tied_to_prev.append(_is_tied_to_prev(gn))
                noteheads.append(_notehead_for_pitch(gn, 0))
                string_hints.append(voice_string_hints.get(id(gn), [None])[0])
            elif isinstance(gn, chord.Chord):
                base_dur = float(gn.duration.quarterLength)
                # No tie aggregation for multi-pitch chords in this first cut.
                followers = []
                if len(gn.pitches) == 1:
                    followers = chain_followers.get(id(gn), [])
                    total_dur = base_dur + sum(
                        float(f.duration.quarterLength) for f in followers
                    )
                else:
                    total_dur = base_dur
                for i, p in enumerate(gn.pitches):
                    pitches.append(p.midi)
                    durations.append(total_dur)
                    ref = _build_pitch_ref(gn, i)
                    if len(gn.pitches) == 1:
                        ref.tied_links = followers
                    refs.append(ref)
                    source_pitches.append(p)
                    is_continuation = _is_tied_to_prev(gn) if gn.tie is not None else False
                    tied_to_prev.append(is_continuation)
                    noteheads.append(_notehead_for_pitch(gn, i))
                    gn_hints = voice_string_hints.get(id(gn), [])
                    string_hints.append(gn_hints[i] if i < len(gn_hints) else None)

        # Determine event kind. Look at the group as a whole.
        if len(pitches) == 1:
            kind = "single"
        elif (
            len(pitches) == 2
            and is_artificial_harmonic(pitches)
            and len(group) == 1
            and isinstance(group[0][0], chord.Chord)
            and _has_explicit_harmonic_marker(group[0][0])
        ):
            kind = "harmonic"
        else:
            kind = "chord"

        # A note (or same-onset chord) where every pitch carries a diamond
        # notehead is a natural harmonic, unless it's already claimed by the
        # 2-note artificial-harmonic case above.
        if kind != "harmonic" and pitches and all(nh == "diamond" for nh in noteheads):
            kind = "natural_harmonic"

        events.append(
            Event(
                onset=onset,
                pitches=pitches,
                duration_per_note=durations,
                refs=refs,
                kind=kind,
                tied_to_prev=tied_to_prev,
                is_grace=is_grace,
                source_pitches=source_pitches,
                string_hints=string_hints,
            )
        )

    return events
