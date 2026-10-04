#!/usr/bin/env python3
"""
A local, graphical editor for the catalog's metadata.

    python3 scripts/catalog-editor.py                      # audio streams from the release
    python3 scripts/catalog-editor.py --audio ~/vbm-audio  # local copies first, if you have them

Opens http://127.0.0.1:8765. Standard library only; nothing to install.

WHERE AN EDIT GOES
------------------
Twice, on purpose:

1. Into manifest.json, at once. The manifest is the catalog consumers pin, and
   a person's correction should be in it the moment it is made, visible as an
   ordinary git diff.
2. Into manifest-edits.json, as the record of what a person decided. Both
   scripts that write the manifest (tag-tracks.py and sync-upstream.mjs)
   re-apply it after they run, so re-tagging or re-syncing never undoes a
   person's correction. It also keeps the value each field had before the
   first edit (`was`), which is what "Revert" restores and what a tuning pass
   of the tagger compares against.

Review state (`reviewed`, `note`) lives only in manifest-edits.json: it is
about this catalog's upkeep, not about the music, so consumers never see it.

The server binds to 127.0.0.1 only, and only serves audio files the manifest
lists.
"""
import argparse
import json
import os
import re
import tempfile
import threading
import webbrowser
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "catalog-editor.html"
MANIFEST = REPO / "manifest.json"
EDITS = REPO / "manifest-edits.json"
VOCABULARY = REPO / "scripts" / "tag-vocabulary.json"

# Keep in step with TEMPO_BANDS in tag-tracks.py.
TEMPO_BANDS = [(90, "slow"), (125, "medium"), (float("inf"), "fast")]
KEYS = [f"{tonic} {mode}" for mode in ("major", "minor") for tonic in ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")]
ENERGIES = ["low", "medium", "high"]

lock = threading.Lock()


def write_json(path, value):
    """Same formatting as the other writers (2-space indent, raw UTF-8, trailing newline), written atomically."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def load_edits():
    if not EDITS.exists():
        return {
            "_comment": "Corrections made by a person in scripts/catalog-editor.py. `set` is re-applied over manifest.json by tag-tracks.py and sync-upstream.mjs, so it always wins over the tagger. `was` is each field's value before its first edit. `reviewed` and `note` stay here and never reach the manifest.",
            "tracks": {},
        }
    return json.loads(EDITS.read_text(encoding="utf-8"))


def tempo_for(bpm):
    return next(name for limit, name in TEMPO_BANDS if bpm < limit)


def validate_set(fields, vocabulary):
    """The editable fields, checked; derived fields filled in. Raises ValueError with a readable message."""
    moods, styles = set(vocabulary["mood"]), set(vocabulary["style"])
    out = {}
    for key, value in fields.items():
        if key == "title":
            if not isinstance(value, str) or not value.strip() or len(value) > 120:
                raise ValueError("Title must be 1 to 120 characters.")
            out[key] = value.strip()
        elif key == "mood":
            if value not in moods:
                raise ValueError(f"Unknown mood {value!r}.")
            out[key] = value
        elif key in ("moods", "styles"):
            allowed = moods if key == "moods" else styles
            if not isinstance(value, list) or any(v not in allowed for v in value):
                raise ValueError(f"{key} must only use tags from the vocabulary.")
            out[key] = list(dict.fromkeys(value))
        elif key == "bpm":
            if not isinstance(value, (int, float)) or not 20 <= value <= 300:
                raise ValueError("BPM must be a number from 20 to 300.")
            # A tempo a person heard is confirmed, and its band follows from it.
            out["bpm"] = round(value)
            out["bpmConfident"] = True
            out["tempo"] = tempo_for(out["bpm"])
        elif key == "energy":
            if value not in ENERGIES:
                raise ValueError("Energy must be low, medium or high.")
            out[key] = value
        elif key == "key":
            if value not in KEYS:
                raise ValueError(f"Unknown key {value!r}.")
            out[key] = value
        elif key in ("bpmConfident", "tempo"):
            continue  # derived from bpm above
        else:
            raise ValueError(f"{key!r} is not editable here.")
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "vbm-catalog-editor"

    def log_message(self, fmt, *args):
        if not self.path.startswith("/audio/"):
            super().log_message(fmt, *args)

    def send_json(self, value, status=HTTPStatus.OK):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, message, status=HTTPStatus.BAD_REQUEST):
        self.send_json({"error": message}, status)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 256 * 1024:
            raise ValueError("Request too large.")
        body = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON object.")
        return body

    def track_id(self, suffix=""):
        match = re.fullmatch(r"/api/tracks/([A-Za-z0-9._-]+)" + suffix, self.path)
        return match[1] if match else None

    # GET ------------------------------------------------------------------

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body = PAGE.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/catalog":
            with lock:
                self.send_json({
                    "manifest": json.loads(MANIFEST.read_text(encoding="utf-8")),
                    "vocabulary": json.loads(VOCABULARY.read_text(encoding="utf-8")),
                    "edits": load_edits()["tracks"],
                    "keys": KEYS,
                    "tempoBands": [[None if limit == float("inf") else limit, name] for limit, name in TEMPO_BANDS],
                    "localAudio": self.server.local_files,
                })
        elif path.startswith("/audio/"):
            self.send_audio(path[len("/audio/"):])
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def send_audio(self, name):
        if name not in self.server.audio_files:
            return self.send_error(HTTPStatus.NOT_FOUND)
        local = self.server.audio_dir / name if self.server.audio_dir else None
        if local is None or not local.is_file():
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", f"{self.server.release_url}/{name}")
            self.end_headers()
            return
        # Browsers seek with Range requests; without them the scrub bar does nothing.
        size = local.stat().st_size
        start, end = 0, size - 1
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        partial = bool(match and (match[1] or match[2]))
        if partial:
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), size - 1) if match[2] else size - 1
            else:
                start = max(size - int(match[2]), 0)
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        types = {".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".flac": "audio/flac", ".wav": "audio/wav"}
        self.send_header("Content-Type", types.get(local.suffix.lower(), "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(local, "rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0 and (chunk := handle.read(min(1 << 16, remaining))):
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the player moved on, which is normal when seeking

    # PUT / DELETE ---------------------------------------------------------

    def do_PUT(self):
        """Save one track: {"set": {field: value}, "reviewed": bool, "note": str}.

        `set` holds every field a person has changed on this track, not just
        the latest one, so the client sends the whole record each time.
        """
        track_id = self.track_id()
        if track_id is None:
            return self.fail("Unknown path.", HTTPStatus.NOT_FOUND)
        try:
            body = self.read_body()
            vocabulary = json.loads(VOCABULARY.read_text(encoding="utf-8"))
            wanted = validate_set(body.get("set") or {}, vocabulary)
            reviewed = body.get("reviewed", False)
            note = body.get("note", "")
            if not isinstance(reviewed, bool) or not isinstance(note, str) or len(note) > 2000:
                raise ValueError("reviewed must be true or false, and the note at most 2000 characters.")
        except (ValueError, json.JSONDecodeError) as error:
            return self.fail(str(error))

        with lock:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            track = next((t for t in manifest["tracks"] if t["id"] == track_id), None)
            if track is None:
                return self.fail("That track is not in manifest.json.", HTTPStatus.NOT_FOUND)
            edits = load_edits()
            record = edits["tracks"].get(track_id, {})
            was = dict(record.get("was", {}))

            # Restore fields the person no longer overrides, then apply the rest.
            for field in list(was):
                if field not in wanted:
                    track[field] = was.pop(field)
            for field, value in wanted.items():
                if field not in was:
                    was[field] = track.get(field)
                track[field] = value
            # An edit that lands back on the original value is no edit at all.
            # bpm and the two fields derived from it go together: typing in
            # the tagger's own bpm still confirms it, and that is an edit.
            for group in [["bpm", "bpmConfident", "tempo"], *[[f] for f in wanted if f not in ("bpm", "bpmConfident", "tempo")]]:
                if all(f in wanted and wanted[f] == was.get(f) for f in group):
                    for f in group:
                        wanted.pop(f)
                        was.pop(f)

            record = {
                "reviewed": reviewed,
                **({"reviewedOn": record.get("reviewedOn") if record.get("reviewed") else date.today().isoformat()} if reviewed else {}),
                **({"note": note.strip()} if note.strip() else {}),
                **({"set": wanted, "was": was} if wanted else {}),
                "sha256": track["sha256"],
            }
            if record == {"reviewed": False, "sha256": track["sha256"]}:
                edits["tracks"].pop(track_id, None)
            else:
                edits["tracks"][track_id] = record
            edits["tracks"] = dict(sorted(edits["tracks"].items()))
            write_json(MANIFEST, manifest)
            write_json(EDITS, edits)
        self.send_json({"track": track, "edit": edits["tracks"].get(track_id)})


def main():
    parser = argparse.ArgumentParser(description="A local, graphical editor for the catalog's metadata.")
    parser.add_argument("--audio", type=Path, default=REPO / "build" / "audio",
                        help="folder of the release's audio files; tracks not found there stream from the release (default: build/audio)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.audio_files = {t["file"] for t in manifest["tracks"]}
    server.release_url = f"{manifest['releaseBaseUrl']}/{manifest['releaseTag']}"
    audio_dir = args.audio.expanduser()
    server.audio_dir = audio_dir if audio_dir.is_dir() else None
    server.local_files = sum((audio_dir / f).is_file() for f in server.audio_files) if server.audio_dir else 0

    url = f"http://127.0.0.1:{args.port}/"
    print(f"\n  Catalog editor: {url}")
    if server.local_files:
        print(f"  Audio: {server.local_files} of {len(server.audio_files)} tracks from {audio_dir}, the rest from the release")
    else:
        print("  Audio: streaming from the GitHub release")
    print(f"  Edits save to manifest.json and {EDITS.name} as you make them. Ctrl-C to stop.\n")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
