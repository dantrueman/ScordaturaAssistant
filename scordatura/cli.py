import argparse
import re
import sys
from typing import Tuple

from music21 import converter, stream

from .config import load_config
from .render import build_score


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


def _snap_microtonal_alters(xml_text: str) -> Tuple[str, int]:
    """music21's pitch.Accidental only accepts alters in multiples of 0.5
    (natural, sharp, half-sharp, ...). Microtonal systems like HEJI encode
    finer adjustments — e.g. a syntonic comma is ~0.21 semitones — and
    music21 rejects the parse with AccidentalException. Snap any
    out-of-quantum value to the nearest 0.5 so the file parses. The
    microtonal nuance is lost on the round-trip; preserving it through
    the 3-staff output is a separate, harder problem (HEJI on a fingered
    scordatura staff is ambiguous when an open string is assigned).
    Returns (rewritten_xml, count_of_snapped_values)."""
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        v = float(m.group(1))
        snapped = round(v * 2) / 2
        if snapped != v:
            count += 1
        return f"<alter>{snapped}</alter>"

    return _ALTER_RE.sub(repl, xml_text), count


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
    args = parser.parse_args(argv)

    fb, params, instrument_obj = load_config(args.config)
    with open(args.input, "r", encoding="utf-8") as f:
        xml_text = f.read()
    xml_text, snapped = _snap_microtonal_alters(xml_text)
    if snapped:
        print(
            f"note: snapped {snapped} microtonal <alter> value(s) to nearest "
            f"quarter-tone — HEJI/microtonal markings are not preserved.",
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
        source_part, fb, params=params, instrument_obj=instrument_obj
    )

    out_score.write("musicxml", fp=args.output)

    print(f"wrote {args.output}")
    if warnings:
        print(f"{len(warnings)} warning(s):", file=sys.stderr)
        for w in warnings:
            print(f"  {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
