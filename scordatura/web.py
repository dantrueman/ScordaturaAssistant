"""Local Flask wrapper around the CLI. Run with `python -m scordatura.web`;
opens a browser to localhost. Same translator pipeline as the CLI; the form
just builds Fingerboard/SolverParams from user input instead of reading JSON."""
import io
import os
import tempfile
import threading
import time
import webbrowser
from typing import List

from flask import Flask, render_template, request, send_file
from music21 import converter, instrument as m21_instr

from .cli import _pick_part, _promote_string_indications, _snap_microtonal_alters
from .config import _INSTRUMENT_MAP
from .fingerboard import Fingerboard
from .render import build_score
from .solver import SolverParams


app = Flask(__name__)


def _parse_int_list(s: str) -> List[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/process", methods=["POST"])
def process():
    f = request.files.get("score")
    if f is None or not f.filename:
        return "No file uploaded.", 400
    try:
        xml_text = f.read().decode("utf-8")
    except UnicodeDecodeError:
        return "Could not decode file as UTF-8.", 400

    try:
        instr_name = (request.form.get("instrument") or "violin").lower()
        base_pitches = _parse_int_list(request.form["base_pitches"])
        offsets = _parse_int_list(request.form["offsets"])
        # Infer n_strings from base_pitches so the hidden n_strings selector
        # can never silently truncate a manually-typed list.
        n_strings = len(base_pitches)
        max_fret_raw = request.form.get("max_fret", "").strip()
        max_fret = _parse_int_list(max_fret_raw) if max_fret_raw else [24] * n_strings
    except (KeyError, ValueError) as e:
        return f"Invalid tuning input: {e}", 400

    if n_strings < 1:
        return "base_pitches must contain at least one value.", 400
    if len(offsets) != n_strings:
        return f"Expected {n_strings} offsets (one per string in base_pitches), got {len(offsets)}.", 400
    if len(max_fret) != n_strings:
        return f"Expected {n_strings} max-fret values, got {len(max_fret)}.", 400

    try:
        fb = Fingerboard(
            n_strings=n_strings,
            base_pitches=base_pitches,
            offsets=offsets,
            max_fret=max_fret,
        )
    except ValueError as e:
        return f"Fingerboard error: {e}", 400

    params = SolverParams()
    instr_cls = _INSTRUMENT_MAP.get(instr_name, m21_instr.Violin)
    instr_obj = instr_cls()

    xml_text, _snapped = _snap_microtonal_alters(xml_text)
    xml_text, _promoted = _promote_string_indications(xml_text)
    try:
        source_score = converter.parse(xml_text, format="musicxml")
    except Exception as e:
        return f"MusicXML parse failed: {e}", 400

    source_part = _pick_part(source_score)
    out_score, warnings = build_score(
        source_part, fb, params=params, instrument_obj=instr_obj
    )

    if warnings:
        print(f"Warnings for {f.filename}:")
        for w in warnings:
            print(f"  {w}")

    tmp = tempfile.NamedTemporaryFile(suffix=".musicxml", delete=False)
    tmp.close()
    out_score.write("musicxml", fp=tmp.name)

    base = os.path.splitext(os.path.basename(f.filename))[0] or "score"
    download_name = f"{base}_scordatura.musicxml"

    with open(tmp.name, "rb") as out_f:
        data = out_f.read()
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.recordare.musicxml+xml",
    )


def main() -> int:
    port = 5005
    url = f"http://127.0.0.1:{port}"

    def _open():
        time.sleep(0.6)
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()
    print(f"Scordatura Assistant running at {url}  (Ctrl-C to stop)")
    app.run(host="127.0.0.1", port=port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
