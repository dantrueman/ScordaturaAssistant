import argparse
import math
import re
import sys
from typing import Tuple

from music21 import converter, stream

from .config import load_config
from .render import build_score, write_musicxml


_ALTER_RE = re.compile(r"<alter>([-+]?\d*\.?\d+)</alter>")

# Some engravers (observed in Dorico) notate an explicit string number not
# with the standard MusicXML <technical><string>N</string></technical>
# element, but as a free-form <other-technical smufl="guitarStringN"/>
# glyph. music21's importer only reads <other-technical>'s text content, not
# its `smufl` attribute, so this information is otherwise silently lost.
# Rewrite it into the standard <string> element before parsing so it comes
# through as a normal StringIndication articulation.
_OTHER_TECHNICAL_RE = re.compile(
    r"<other-technical\b([^>]*?)(?:/>|>(.*?)</other-technical>)", re.DOTALL
)
_GUITAR_STRING_SMUFL_RE = re.compile(r'smufl="guitarString(\d+)"')


def _promote_string_indications(xml_text: str) -> Tuple[str, int]:
    """Rewrite <other-technical smufl="guitarStringN".../> markers into
    <string>N</string> so music21 parses them as a StringIndication
    articulation. Returns (rewritten_xml, count_of_promoted_markers)."""
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        attrs = m.group(1) or ""
        gm = _GUITAR_STRING_SMUFL_RE.search(attrs)
        if gm is None:
            return m.group(0)
        count += 1
        return f"<string>{gm.group(1)}</string>"

    return _OTHER_TECHNICAL_RE.sub(repl, xml_text), count


def _snap_alter(v: float) -> float:
    """Quarter-tone alters (multiples of 0.5) pass through. A value within
    0.1 of a quarter-tone (e.g. an 11-limit HEJI flag, 0.522) snaps to it; any
    other fractional alter (HEJI commas such as -0.266 or 0.903) snaps to the
    nearest semitone, so a comma never turns into a spurious quarter-tone
    glyph."""
    quarter = round(v * 2) / 2
    return quarter if abs(v - quarter) <= 0.1 else float(round(v))


# Standard (12-EDO) <accidental> names; anything else (quarter-sharp,
# three-quarters-flat, slash-*, arrow/sori/koron forms, ...) is microtonal.
_STANDARD_ACCIDENTALS = {
    "sharp", "flat", "natural", "double-sharp", "sharp-sharp", "flat-flat",
    "natural-sharp", "natural-flat",
}
_ACCIDENTAL_FOR_ALTER = {
    -2: "flat-flat", -1: "flat", 0: "natural", 1: "sharp", 2: "double-sharp",
}
_NOTE_RE = re.compile(r"<note\b.*?</note>", re.DOTALL)
_ACCIDENTAL_RE = re.compile(r"(<accidental\b[^>]*>)([^<]*)(</accidental>)")


def _round_half_up(v: float) -> int:
    """Nearest semitone, exact quarter-tones going up (as MIDI rounding did)."""
    return int(math.floor(v + 0.5))


def _snap_microtonal_alters(xml_text: str, translate_24edo: bool = False) -> Tuple[str, int]:
    """music21's pitch.Accidental only accepts alters in multiples of 0.5
    (natural, sharp, half-sharp, ...). Microtonal systems like HEJI encode
    finer adjustments — e.g. a syntonic comma is ~0.21 semitones — and
    music21 rejects the parse with AccidentalException. Snap every
    out-of-quantum value so the file parses. The finer nuance is lost:
    neither Dorico (which draws no accidental for a bare fractional alter and
    ignores SMuFL glyph names) nor music21 can carry it through.

    With translate_24edo, via _snap_alter (near a quarter-tone -> that
    quarter-tone, else the nearest semitone), leaving quarter-tones alone.
    Without it, every alter is rounded to the nearest 12-EDO semitone and any
    microtonal <accidental> name (quarter-sharp, ...) is rewritten to match,
    so no quarter-tone accidental survives anywhere in the output.
    Returns (rewritten_xml, count_of_changed_values)."""
    count = 0

    if translate_24edo:
        def repl(m: re.Match) -> str:
            nonlocal count
            v = float(m.group(1))
            snapped = _snap_alter(v)
            if snapped != v:
                count += 1
            return f"<alter>{snapped}</alter>"

        return _ALTER_RE.sub(repl, xml_text), count

    def fix_note(nm: re.Match) -> str:
        nonlocal count
        note_xml = nm.group(0)
        new_alter = None

        def alter_repl(m: re.Match) -> str:
            nonlocal count, new_alter
            v = float(m.group(1))
            new_alter = _round_half_up(v)
            if new_alter != v:
                count += 1
            return f"<alter>{new_alter}</alter>"

        note_xml = _ALTER_RE.sub(alter_repl, note_xml, count=1)

        def acc_repl(m: re.Match) -> str:
            name = m.group(2).strip()
            if name in _STANDARD_ACCIDENTALS or new_alter not in _ACCIDENTAL_FOR_ALTER:
                return m.group(0)
            return m.group(1) + _ACCIDENTAL_FOR_ALTER[new_alter] + m.group(3)

        return _ACCIDENTAL_RE.sub(acc_repl, note_xml)

    return _NOTE_RE.sub(fix_note, xml_text), count


def _pick_part(score) -> stream.Part:
    """Pick the first Part. If the input is a Score, take parts[0]; if it's a
    bare Part, return it directly."""
    if isinstance(score, stream.Part):
        return score
    if isinstance(score, stream.Score) and score.parts:
        return score.parts[0]
    parts = list(score.getElementsByClass(stream.Part)) if hasattr(score, "getElementsByClass") else []
    if parts:
        return parts[0]
    raise ValueError("No Part found in the input MusicXML")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scordatura")
    parser.add_argument("--input", required=True, help="Input MusicXML path")
    parser.add_argument("--output", required=True, help="Output MusicXML path")
    parser.add_argument("--config", required=True, help="JSON config path")
    parser.add_argument(
        "--translate-24edo",
        action="store_true",
        help="Keep quarter-tone accidentals as quarter-tones (24-EDO). Off by "
        "default: quarter-tones are rounded to semitones, which suits HEJI "
        "scores whose commas would otherwise read as strange 24-EDO music.",
    )
    args = parser.parse_args(argv)

    fb, params, instrument_obj = load_config(args.config)
    with open(args.input, "r", encoding="utf-8") as f:
        xml_text = f.read()
    xml_text, snapped = _snap_microtonal_alters(xml_text, args.translate_24edo)
    if snapped:
        print(
            f"note: snapped {snapped} microtonal <alter> value(s) — "
            f"HEJI commas are not preserved.",
            file=sys.stderr,
        )
    xml_text, promoted = _promote_string_indications(xml_text)
    if promoted:
        print(
            f"note: promoted {promoted} <other-technical smufl=\"guitarStringN\"> "
            f"marker(s) to string indications.",
            file=sys.stderr,
        )
    source_score = converter.parse(xml_text, format="musicxml")
    source_part = _pick_part(source_score)

    out_score, warnings = build_score(
        source_part, fb, params=params, instrument_obj=instrument_obj,
        translate_24edo=args.translate_24edo,
    )

    write_musicxml(out_score, args.output)

    print(f"wrote {args.output}")
    if warnings:
        print(f"{len(warnings)} warning(s):", file=sys.stderr)
        for w in warnings:
            print(f"  {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
