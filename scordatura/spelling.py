import math
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


def _respell_quarter_tone(
    read_ps: float,
    source_pitch: Optional[pitch.Pitch],
    key_sig: Optional[key.KeySignature],
    contour: float,
) -> pitch.Pitch:
    """Spell a pitch lying exactly halfway between two semitones. The
    quarter-tone is the residual; the semitone neighbor it hangs off is
    spelled like any other note. Sharp side: lower neighbor + 0.5 (C~, C#~).
    Flat side: upper neighbor - 0.5 (D`, E-`)."""
    lower = int(math.floor(read_ps))
    side = _pick_side(source_pitch, key_sig, int(contour))
    if side == "sharp":
        p = pitch.Pitch(_midi_to_name_octave(lower, "sharp"))
        delta = 0.5
    else:
        p = pitch.Pitch(_midi_to_name_octave(lower + 1, "flat"))
        delta = -0.5
    base_alter = p.accidental.alter if p.accidental is not None else 0.0
    p.accidental = pitch.Accidental(base_alter + delta)
    p.accidental.displayStatus = True
    return p


def respell(
    read_midi: float,
    sounding_midi: float,
    source_pitch: Optional[pitch.Pitch],
    key_sig: Optional[key.KeySignature],
    contour: float = 0,
    translate_24edo: bool = False,
) -> pitch.Pitch:
    """Return a music21 Pitch for `read_midi`, spelled to honor the source's
    key signature and accidental direction. `contour` is a signed semitone
    delta from the previous note in the same line (used as a tie-break).
    With translate_24edo False, pitches are whole semitones and the source
    spelling is reused by whole-semitone (MIDI) match, as before quarter-tone
    support existed."""
    # Quarter-tones (fractional .5) keep their microtonal accidental; the
    # semitone they hang off is spelled by the normal rules.
    if translate_24edo and abs(read_midi - round(read_midi)) > 1e-6:
        if (
            read_midi == sounding_midi
            and source_pitch is not None
            and abs(source_pitch.ps - read_midi) < 1e-6
        ):
            p = pitch.Pitch(source_pitch.nameWithOctave)
            p.accidental.displayStatus = True
            return p
        return _respell_quarter_tone(read_midi, source_pitch, key_sig, contour)
    read_midi = int(round(read_midi))

    # If no transposition is needed (offset == 0 on this string), preserve the
    # source spelling exactly.
    if (
        read_midi == sounding_midi
        and source_pitch is not None
        and (
            abs(source_pitch.ps - read_midi) < 1e-6
            if translate_24edo
            else source_pitch.midi == read_midi
        )
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
