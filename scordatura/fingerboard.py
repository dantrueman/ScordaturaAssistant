from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple


PERFECT_FOURTH_SEMITONES = 5

# Standard natural-harmonic touch/partial table: touching a string `node`
# semitones above the open string produces a sounding pitch `node + extra`
# semitones above the open string (i.e. the given partial of the open
# string). Ordered from the most common/reliable partial (2nd) to the
# least (6th) so index == partial rank for tie-breaking purposes.
NATURAL_HARMONIC_NODES: List[Tuple[int, int]] = [
    (12, 0),   # P8 touch -> 2nd partial (P8 above open)
    (7, 12),   # P5 touch -> 3rd partial (P8+P5 above open)
    (5, 19),   # P4 touch -> 4th partial (2x P8 above open)
    (4, 24),   # M3 touch -> 5th partial (2x P8+M3 above open)
    (3, 28),   # m3 touch -> 6th partial (2x P8+P5 above open)
]

# The octave (2nd-partial) natural harmonic is conventionally notated with a
# regular notehead plus a small harmonic "circle" mark, rather than the
# diamond notehead used for the other (3rd-6th partial) harmonics.
OCTAVE_HARMONIC_NODE = 12


@dataclass(frozen=True)
class Fingerboard:
    n_strings: int
    base_pitches: List[int]
    offsets: List[int] = field(default_factory=list)
    max_fret: List[int] = field(default_factory=list)

    def __post_init__(self):
        if len(self.base_pitches) != self.n_strings:
            raise ValueError(
                f"base_pitches has {len(self.base_pitches)} entries, expected {self.n_strings}"
            )
        if not self.offsets:
            object.__setattr__(self, "offsets", [0] * self.n_strings)
        if not self.max_fret:
            object.__setattr__(self, "max_fret", [24] * self.n_strings)
        if len(self.offsets) != self.n_strings:
            raise ValueError(
                f"offsets has {len(self.offsets)} entries, expected {self.n_strings}"
            )
        if len(self.max_fret) != self.n_strings:
            raise ValueError(
                f"max_fret has {len(self.max_fret)} entries, expected {self.n_strings}"
            )
        if list(self.base_pitches) != sorted(self.base_pitches):
            raise ValueError("base_pitches must be sorted ascending (low → high)")

    def sounding_open(self, string_id: int) -> int:
        return self.base_pitches[string_id] + self.offsets[string_id]

    def sounding_max(self, string_id: int) -> int:
        return self.sounding_open(string_id) + self.max_fret[string_id]

    def read_pitch_for(self, sounding_midi: int, string_id: int) -> int:
        # Hand-grip notation: player reads (sounding - offset).
        # Scordatura down a semitone (offset = -1) on the C string: sounding C → read C#.
        return sounding_midi - self.offsets[string_id]

    def valid_strings_for(self, sounding_midi: int) -> List[int]:
        return [
            i
            for i in range(self.n_strings)
            if self.sounding_open(i) <= sounding_midi <= self.sounding_max(i)
        ]

    def is_open_string(self, sounding_midi: int) -> Optional[int]:
        matches = [
            i for i in range(self.n_strings) if self.sounding_open(i) == sounding_midi
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def notated_string_number(self, string_id: int) -> int:
        """Convert a 0-indexed fingerboard string id (index 0 = lowest-pitched
        string, per base_pitches' required ascending order) to the notated
        MusicXML string number. Per the MusicXML <string> element spec,
        "Strings are numbered from high to low, with 1 being the highest
        pitched full-length string" — the same convention used for Roman
        numeral string indications in standard notation (e.g. violin string I
        = E, the highest string). This is the mirror image of base_pitches'
        internal low-to-high ordering."""
        return self.n_strings - string_id

    def string_id_for_notated_number(self, number: int) -> int:
        """Inverse of notated_string_number: convert a notated MusicXML/
        Roman-numeral string number (1 = highest-pitched string) to this
        Fingerboard's 0-indexed string id (0 = lowest-pitched string)."""
        return self.n_strings - number

    def natural_harmonic_candidates(self, sounding_midi: int) -> List[Tuple[int, int]]:
        """Every (string_id, touch_node) whose natural harmonic sounds `sounding_midi`."""
        out: List[Tuple[int, int]] = []
        for s in range(self.n_strings):
            open_s = self.sounding_open(s)
            for node, extra in NATURAL_HARMONIC_NODES:
                if node > self.max_fret[s]:
                    continue
                if open_s + node + extra == sounding_midi:
                    out.append((s, node))
        return out


def is_artificial_harmonic(pitches: Sequence[int]) -> bool:
    if len(pitches) != 2:
        return False
    low, high = sorted(pitches)
    return high - low == PERFECT_FOURTH_SEMITONES
