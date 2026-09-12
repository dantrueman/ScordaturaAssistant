"""End-to-end smoke test:

  1. Build the fixture (generates fixtures/example_score.musicxml).
  2. Run the CLI to produce fixtures/example_output.musicxml.
  3. Inspect the written MusicXML directly for the notation features that
     downstream engravers (MuseScore, Dorico, Finale) will render.
  4. Re-parse with music21 to verify structural integrity (3 named Parts).

  music21's MusicXML *reader* drops per-pitch chord noteheads on round-trip,
  so diamond-notehead checks read the file as text rather than re-parsing.
"""
import re
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from music21 import converter, instrument, meter, note, stream  # noqa: E402

from scordatura.cli import main as cli_main  # noqa: E402
from scordatura.fingerboard import Fingerboard  # noqa: E402
from scordatura.render import _build_fingered_part  # noqa: E402
from scordatura.solver import SolverParams, favor_upper  # noqa: E402
from scripts import build_fixture  # noqa: E402


def _ensure_fixture():
    out = ROOT / "fixtures" / "example_score.musicxml"
    if not out.exists():
        build_fixture.main()


def _run_artificial_harmonic_check() -> None:
    input_path = ROOT / "fixtures" / "example_score.musicxml"
    output_path = ROOT / "fixtures" / "example_output.musicxml"
    config_path = ROOT / "fixtures" / "example_config.json"

    rc = cli_main([
        "--input", str(input_path),
        "--output", str(output_path),
        "--config", str(config_path),
    ])
    assert rc == 0, f"CLI exit code {rc}"

    xml = output_path.read_text()

    string_tags = re.findall(r"<string>\d+</string>", xml)
    diamond_tags = re.findall(r"<notehead[^>]*>diamond</notehead>", xml)
    harmonic_tags = re.findall(r"<harmonic>", xml)
    artificial_tags = re.findall(r"<artificial[^/]*/?>", xml)

    print(f"  StringIndication tags: {len(string_tags)}")
    print(f"  diamond notehead tags: {len(diamond_tags)}")
    print(f"  <harmonic> tags:       {len(harmonic_tags)}")
    print(f"  <artificial> tags:     {len(artificial_tags)}")

    assert string_tags, "no StringIndication tags found in output"
    assert diamond_tags, "no diamond noteheads found in output"
    assert harmonic_tags, "no <harmonic> tags found in output"
    assert artificial_tags, "no <artificial> harmonic subtype tag found"

    parsed = converter.parse(str(output_path))
    parts = list(parsed.parts)
    assert len(parts) == 6, f"expected 6 Parts, got {len(parts)}"
    names = [p.partName for p in parts]
    assert names == [
        "Sounding (source)",
        "Favor upper",
        "Favor lower",
        "Hybrid (upper+lower)",
        "No preference",
        "Favor middle",
    ], names

    for prt in parts:
        notes_count = len(list(prt.recurse().notes))
        print(f"  {prt.id} {prt.partName!r}: {notes_count} notes")


def _run_natural_harmonic_check() -> None:
    input_path = ROOT / "examples" / "Harmonics.musicxml"
    output_path = ROOT / "fixtures" / "harmonics_output.musicxml"
    config_path = ROOT / "fixtures" / "harmonics_config.json"

    rc = cli_main([
        "--input", str(input_path),
        "--output", str(output_path),
        "--config", str(config_path),
    ])
    assert rc == 0, f"CLI exit code {rc}"

    xml = output_path.read_text()

    natural_tags = re.findall(r"<natural\s*/?>", xml)
    diamond_tags = re.findall(r"<notehead[^>]*>diamond</notehead>", xml)

    print(f"  <natural> tags:        {len(natural_tags)}")
    print(f"  diamond notehead tags: {len(diamond_tags)}")

    assert natural_tags, "no natural harmonicType tags found in output"
    assert diamond_tags, "no diamond noteheads found in output"

    # Split the raw XML into per-part blocks so pitch checks are scoped to
    # the "Favor upper" staff, which the hand-verified reference part
    # matches for every melodic natural harmonic (D4/D5/A5/E6 stopped notes,
    # then B5/A5/G#5 touched harmonics).
    part_blocks = re.split(r'<part id="[^"]+">', xml)
    assert len(part_blocks) == 7, f"expected 6 parts, got {len(part_blocks) - 1}"
    favor_upper_xml = part_blocks[2]

    def _pitch_present(step: str, octave: int, alter: Optional[str] = None) -> bool:
        alter_pattern = rf"<alter>{alter}</alter>\s*" if alter is not None else r"(?:<alter>[^<]*</alter>\s*)?"
        pattern = rf"<step>{step}</step>\s*{alter_pattern}<octave>{octave}</octave>"
        return re.search(pattern, favor_upper_xml) is not None

    # Melodic natural harmonics: G6->B5, C7->A5, E7->G#5 (per NATURAL_HARMONIC_NODES).
    for step, octave in [("B", 5), ("A", 5), ("G", 5)]:
        assert _pitch_present(step, octave), (
            f"expected touched pitch {step}{octave} not found in Favor upper part"
        )

    # Measure 4's same-onset dyad of two independent natural harmonics
    # (G6+E6) must resolve to D5+F#4 on two adjacent strings, matching the
    # reference part -- not the non-adjacent-string B5+F#4/G-4 combination a
    # bare partial-preference cost would otherwise pick (B5 is legitimately
    # used elsewhere by the melodic harmonics, so this check is scoped to
    # measure 4's own text rather than the whole part).
    measure4_match = re.search(
        r'<measure number="4"[^>]*>(.*?)</measure>', favor_upper_xml, re.DOTALL
    )
    assert measure4_match, "measure 4 not found in Favor upper part"
    measure4_xml = measure4_match.group(1)

    def _measure4_pitch_present(step: str, octave: int, alter: Optional[str] = None) -> bool:
        alter_pattern = rf"<alter>{alter}</alter>\s*" if alter is not None else r"(?:<alter>[^<]*</alter>\s*)?"
        pattern = rf"<step>{step}</step>\s*{alter_pattern}<octave>{octave}</octave>"
        return re.search(pattern, measure4_xml) is not None

    assert _measure4_pitch_present("D", 5), "expected touched pitch D5 in measure 4 dyad"
    assert _measure4_pitch_present("F", 4, alter="1") or _measure4_pitch_present("G", 4, alter="-1"), (
        "expected touched pitch F#4/Gb4 in measure 4 dyad"
    )
    assert not _measure4_pitch_present("B", 5), (
        "measure 4 dyad resolved to non-adjacent-string B5 instead of D5 "
        "(string-adjacency regression for simultaneous natural harmonics)"
    )

    # Regression guard: the source file's Ottava (octave-shift) direction,
    # combined with the <backup>/<forward> pair used to place it, used to
    # trigger a music21 write-time bug that shifted tied natural-harmonic
    # pitches down an octave (B5->B4, G#5->G#4). Assert those corrupted
    # pitches are absent (G-4, i.e. G-flat 4, is a legitimate, unrelated
    # pitch elsewhere in the part, so only the sharp G#4 is checked here).
    assert not _pitch_present("B", 4), (
        "corrupted touched pitch B4 found in Favor upper part "
        "(Ottava/backup-forward write bug regression)"
    )
    assert not _pitch_present("G", 4, alter="1"), (
        "corrupted touched pitch G#4 found in Favor upper part "
        "(Ottava/backup-forward write bug regression)"
    )

    # The passthrough "Sounding (source)" part must reproduce the input
    # pitches exactly (same Ottava/backup-forward bug used to also corrupt
    # this untouched staff).
    source_pitches = [
        pp.nameWithOctave
        for n in converter.parse(str(input_path)).parts[0].recurse().notes
        for pp in n.pitches
    ]
    output_source_pitches = [
        pp.nameWithOctave
        for n in converter.parse(str(output_path)).parts[0].recurse().notes
        for pp in n.pitches
    ]
    assert output_source_pitches == source_pitches, (
        f"Sounding (source) part diverged from input: "
        f"{output_source_pitches} != {source_pitches}"
    )

    print("natural-harmonic pitches verified against reference part")


def _run_octave_harmonic_notehead_check() -> None:
    # The octave (2nd-partial) natural harmonic is conventionally notated
    # with a regular notehead plus the harmonic "circle" mark, unlike the
    # other (3rd-6th partial) harmonics which keep the diamond notehead.
    fb = Fingerboard(
        n_strings=5,
        base_pitches=[48, 55, 62, 69, 76],
        offsets=[0, 1, -2, -2, -4],
        max_fret=[24] * 5,
    )

    def _render_single_harmonic(sounding_midi: int) -> note.Note:
        part = stream.Part()
        part.append(meter.TimeSignature("4/4"))
        n = note.Note()
        n.pitch.midi = sounding_midi
        n.duration.quarterLength = 1.0
        n.notehead = "diamond"
        n.noteheadFill = False
        part.append(n)
        part.makeMeasures(inPlace=True)

        new_part, warnings, _assignments = _build_fingered_part(
            part, fb, favor_upper, SolverParams(), "P2", "Favor upper", instrument.Instrument()
        )
        assert not warnings, f"unexpected warnings: {warnings}"
        notes = list(new_part.recurse().notes)
        assert len(notes) == 1, f"expected 1 note, got {len(notes)}"
        return notes[0]

    # String 0 (open sounding 48): touch node=12 (octave, 2nd partial) -> sounds 60.
    octave_note = _render_single_harmonic(60)
    assert octave_note.notehead == "normal", (
        f"expected normal notehead for octave harmonic, got {octave_note.notehead!r}"
    )
    harmonic_marks = [
        a for a in octave_note.articulations if type(a).__name__ == "StringHarmonic"
    ]
    assert harmonic_marks and harmonic_marks[0].harmonicType == "natural", (
        "expected a natural StringHarmonic mark on the octave harmonic"
    )

    # String 0: touch node=7 (3rd partial) -> sounds 48+7+12=67.
    third_partial_note = _render_single_harmonic(67)
    assert third_partial_note.notehead == "diamond", (
        f"expected diamond notehead for non-octave harmonic, got {third_partial_note.notehead!r}"
    )

    print("octave-harmonic notehead convention verified (normal notehead + circle)")


def run() -> int:
    _ensure_fixture()
    _run_artificial_harmonic_check()
    _run_natural_harmonic_check()
    _run_octave_harmonic_notehead_check()

    print("smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
