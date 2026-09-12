from typing import Optional

from music21 import key, pitch


# pitchClass -> (sharp-side spelling, flat-side spelling) for the 5 enharmonic pairs.
# Naturals collapse to a single spelling and ignore the flat/sharp choice.
_PC_SHARP_FLAT = {
    0: ("C", "C"),
    1: ("C#", "D-"),
    2: ("D", "D"),
    3: ("D#", "E-"),
    4: ("E", "E"),
    5: ("F", "F"),
    6: ("F#", "G-"),
    7: ("G", "G"),
    8: ("G#", "A-"),
    9: ("A", "A"),
    10: ("A#", "B-"),
    11: ("B", "B"),
}


def _pc(midi: int) -> int:
    return midi % 12


def _is_natural_only(midi: int) -> bool:
    sharp, flat = _PC_SHARP_FLAT[_pc(midi)]
    return sharp == flat


def _pick_side(
    source_pitch: Optional[pitch.Pitch],
    key_sig: Optional[key.KeySignature],
    contour: int,
) -> str:
    """Return 'sharp' or 'flat' based on (in order) source spelling direction,
    key signature, then contour."""
    # 1. Source spelling direction wins when present and unambiguous.
    if source_pitch is not None and source_pitch.accidental is not None:
        alter = source_pitch.accidental.alter
        if alter > 0:
            return "sharp"
        if alter < 0:
            return "flat"

    # 2. Key signature: sharps positive, flats negative.
    if key_sig is not None:
        sharps = key_sig.sharps or 0
        if sharps > 0:
            return "sharp"
        if sharps < 0:
            return "flat"

    # 3. Contour tie-break.
    if contour > 0:
        return "sharp"
    if contour < 0:
        return "flat"
    return "sharp"


def _midi_to_name_octave(midi: int, side: str) -> str:
    sharp_name, flat_name = _PC_SHARP_FLAT[_pc(midi)]
    name = sharp_name if side == "sharp" else flat_name
    # music21 octave convention: MIDI 60 = C4, so octave = midi // 12 - 1.
    # For B# the letter is C of the next octave down's neighbor; we keep it
    # simple and only use natural/single-accidental names from _PC_SHARP_FLAT,
    # so plain (midi // 12 - 1) is correct.
    octave = midi // 12 - 1
    return f"{name}{octave}"


def respell(
    read_midi: int,
    sounding_midi: int,
    source_pitch: Optional[pitch.Pitch],
    key_sig: Optional[key.KeySignature],
    contour: int = 0,
) -> pitch.Pitch:
    """Return a music21 Pitch for `read_midi`, spelled to honor the source's
    key signature and accidental direction. `contour` is a signed semitone
    delta from the previous note in the same line (used as a tie-break)."""
    # If no transposition is needed (offset == 0 on this string), preserve the
    # source spelling exactly.
    if (
        read_midi == sounding_midi
        and source_pitch is not None
        and source_pitch.midi == read_midi
    ):
        p = pitch.Pitch(source_pitch.nameWithOctave)
        if p.accidental is not None:
            p.accidental.displayStatus = True
        return p

    # Naturals don't need a side decision.
    if _is_natural_only(read_midi):
        p = pitch.Pitch(_midi_to_name_octave(read_midi, "sharp"))
        # Force a natural sign so the player isn't confused by a stale accidental.
        p.accidental = pitch.Accidental("natural")
        p.accidental.displayStatus = True
        return p

    side = _pick_side(source_pitch, key_sig, contour)
    p = pitch.Pitch(_midi_to_name_octave(read_midi, side))
    if p.accidental is not None:
        p.accidental.displayStatus = True
    return p
