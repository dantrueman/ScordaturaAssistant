"""Generate fixtures/example_score.musicxml — a small synthetic score that
exercises every code path the smoke test cares about:

  * Key signature with a flat (F major) — forces enharmonic spelling decisions.
  * One note on a retuned string (sounds C, played on the down-tuned D string).
  * One note on a non-retuned string (open G).
  * One double-stop spanning two adjacent strings.
  * One artificial harmonic (fundamental + perfect 4th above).
  * One open-string note (hard-pinned by the solver).
  * One tied note pair across a bar line.
  * Two simultaneous voices for one beat (independent sustain tracking).
"""
from pathlib import Path

from music21 import chord, clef, instrument, key, meter, note, stream, tie


def build() -> stream.Score:
    score = stream.Score()
    part = stream.Part(id="cello")
    part.insert(0.0, instrument.Violoncello())

    m1 = stream.Measure(number=1)
    m1.insert(0.0, clef.BassClef())
    m1.insert(0.0, key.KeySignature(-1))
    m1.insert(0.0, meter.TimeSignature("4/4"))

    # beat 1: single note on a non-retuned string (open G).
    m1.append(note.Note("G2", quarterLength=1.0))
    # beat 2: single note that's an *open string under scordatura* (C3 on the
    # D string tuned down a whole step — open-string hard pin should fire).
    m1.append(note.Note("C3", quarterLength=1.0))
    # beat 3-4: double-stop forcing two different strings.
    m1.append(chord.Chord(["G3", "D4"], quarterLength=2.0))
    part.append(m1)

    m2 = stream.Measure(number=2)
    # beat 1: an artificial harmonic, fundamental D3 + touched G3 (P4). Mark
    # it explicitly with a diamond notehead on the touched pitch; otherwise
    # the parser correctly leaves it as a plain P4 chord.
    harmonic = chord.Chord(["D3", "G3"], quarterLength=1.0)
    harmonic[1].notehead = "diamond"
    harmonic[1].noteheadFill = False
    m2.append(harmonic)
    # beat 2: a single flat-side note from the key signature.
    m2.append(note.Note("B-3", quarterLength=1.0))
    # beats 3-4: tied A3 starting here, continuing into bar 3.
    n_tie_start = note.Note("A3", quarterLength=2.0)
    n_tie_start.tie = tie.Tie("start")
    m2.append(n_tie_start)
    part.append(m2)

    m3 = stream.Measure(number=3)
    n_tie_end = note.Note("A3", quarterLength=2.0)
    n_tie_end.tie = tie.Tie("stop")
    m3.append(n_tie_end)
    m3.append(note.Note("B3", quarterLength=2.0))
    part.append(m3)

    # Measure 4: two voices throughout (no leading non-voiced content), so the
    # MusicXML round-trip keeps voice tags consistent. Voice 1 sustains a half
    # note while voice 2 plays two quarters underneath — exercises the solver's
    # overlapping-sustain collision check.
    m4 = stream.Measure(number=4)
    v1 = stream.Voice(id="1")
    v1.append(note.Note("D4", quarterLength=2.0))
    v1.append(note.Note("E4", quarterLength=2.0))
    v2 = stream.Voice(id="2")
    v2.append(note.Note("G2", quarterLength=1.0))
    v2.append(note.Note("A2", quarterLength=1.0))
    v2.append(note.Note("B2", quarterLength=1.0))
    v2.append(note.Note("C3", quarterLength=1.0))
    m4.insert(0.0, v1)
    m4.insert(0.0, v2)
    part.append(m4)

    score.insert(0, part)
    return score


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "fixtures" / "example_score.musicxml"
    score = build()
    score.write("musicxml", fp=str(out))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
