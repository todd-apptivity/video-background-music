#!/usr/bin/env python3
"""
Tag every track in manifest.json for tempo, key, energy, mood and style.
Run by hand, like sync-upstream. Dry by default; --write updates the manifest.

TWO STAGES, AND WHY
-------------------
1. Analysis (slow, ~1 s per track plus model loading). Each audio file is
   measured once and the result is cached under build/analysis/<sha256>.json.
   The cache is keyed by the bytes, so an upstream re-encode is re-measured and
   an unchanged track never is.
2. Scoring (fast). The cached measurements are scored against
   scripts/tag-vocabulary.json and the result is written into the manifest.
   Editing the vocabulary or the thresholds below only repeats this stage.

WHAT IS MEASURED, AND WITH WHAT
-------------------------------
- Tempo: three independent estimators. librosa's beat tracker and Essentia's
  RhythmExtractor2013 are signal processing; TempoCNN is a small neural net.
  Beat trackers routinely land on half, double or 2/3 of the true tempo, so
  `bpm` is only marked confident when two of the three agree.
- Key and mode: Essentia's KeyExtractor (signal processing).
- Energy: loudness (the manifest's `lufs`) plus note-onset density, ranked
  against the rest of the catalog and split into thirds.
- Mood and style (`mood`, `moods`, `styles`): two models, combined.
    * LAION-CLAP (music checkpoint, CC0), zero-shot: each tag's prompts are
      compared with the audio. This is what makes the vocabulary editable.
    * Essentia's Discogs-EffNet classifiers (genre, mood/theme, instrument,
      binary moods). Where a tag maps to their classes, they vote too.

ALL TAGS ARE RELATIVE TO THIS CATALOG
-------------------------------------
Neither model gives a calibrated "this is jazz" probability for a vocabulary it
was not trained on. So each tag's score is standardised across the catalog
(a z-score) and a track gets the tag when it scores clearly above the catalog
for it. "calm" means calm for this library, which is the question a person
browsing it is asking.

LICENCES OF THE MODELS
----------------------
The CLAP checkpoint is CC0. The Essentia models are CC BY-NC-SA 4.0: free for
non-commercial use, with attribution (see LICENSES/PROVENANCE.md). Essentia
itself is AGPL-3.0; this script imports it and does not redistribute it.

    pip install -r scripts/requirements-tagging.txt
    python scripts/tag-tracks.py --audio ~/vbm-audio           # dry run
    python scripts/tag-tracks.py --audio ~/vbm-audio --write

Missing audio is downloaded from the release into --audio and checked against
the manifest's sha256. Missing models are downloaded into build/models (the
CLAP checkpoint is 2.35 GB).
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "manifest.json"
VOCABULARY = REPO / "scripts" / "tag-vocabulary.json"
BUILD = REPO / "build"

# Bump when the analysis stage changes, so stale cache entries are re-measured.
ANALYSIS_VERSION = 1

ESSENTIA_MODELS = "https://essentia.upf.edu/models"
MODELS = {
    "effnet": "feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb",
    "genre": "classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1",
    "moodtheme": "classification-heads/mtg_jamendo_moodtheme/mtg_jamendo_moodtheme-discogs-effnet-1",
    "instrument": "classification-heads/mtg_jamendo_instrument/mtg_jamendo_instrument-discogs-effnet-1",
    "tempocnn": "tempo/tempocnn/deeptemp-k16-3.pb",
}
BINARY_HEADS = ["mood_happy", "mood_sad", "mood_relaxed", "mood_aggressive", "mood_party", "danceability"]
CLAP_URL = "https://huggingface.co/lukewys/laion_clap/resolve/main/music_audioset_epoch_15_esc_90.14.pt"

# Scoring thresholds, in catalog standard deviations. See "relative" above.
MOOD_Z = 1.0
STYLE_Z = 1.0
MAX_MOODS = 2
MAX_STYLES = 3
# Both models must rate a track at least this far above average for a tag
# they both score; a tag only CLAP scores needs this much on its own.
AGREE_Z = 0.25
CLAP_ONLY_Z = 1.25

# Two tempo estimates "agree" within this ratio (4% is about 5 BPM at 120).
BPM_AGREE = 0.04
TEMPO_BANDS = [(90, "slow"), (125, "medium"), (float("inf"), "fast")]

# The fields this script owns. sync-upstream carries them forward for tracks
# whose bytes did not change; keep the two lists in step.
OWNED_FIELDS = ["bpm", "bpmConfident", "tempo", "key", "energy", "mood", "moods", "styles", "genres"]


def log(message=""):
    print(message, file=sys.stderr, flush=True)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    log(f"  fetching {url}")
    with urllib.request.urlopen(url) as response, open(partial, "wb") as out:
        while block := response.read(1 << 20):
            out.write(block)
    partial.rename(target)


def ensure_models(models_dir):
    paths = {}
    for name, rel in MODELS.items():
        pb = rel if rel.endswith(".pb") else rel + ".pb"
        paths[name] = models_dir / Path(pb).name
        if not paths[name].exists():
            fetch(f"{ESSENTIA_MODELS}/{pb}", paths[name])
        if not rel.endswith(".pb"):
            meta = models_dir / (Path(rel).name + ".json")
            if not meta.exists():
                fetch(f"{ESSENTIA_MODELS}/{rel}.json", meta)
            paths[name + ".json"] = meta
    for head in BINARY_HEADS:
        rel = f"classification-heads/{head}/{head}-discogs-effnet-1"
        for ext in (".pb", ".json"):
            target = models_dir / (Path(rel).name + ext)
            if not target.exists():
                fetch(f"{ESSENTIA_MODELS}/{rel}{ext}", target)
        paths[head] = models_dir / (Path(rel).name + ".pb")
        paths[head + ".json"] = models_dir / (Path(rel).name + ".json")
    paths["clap"] = models_dir / Path(CLAP_URL).name
    if not paths["clap"].exists():
        fetch(CLAP_URL, paths["clap"])
    return paths


def ensure_audio(track, audio_dir, release_url):
    path = audio_dir / track["file"]
    if not path.exists():
        fetch(f"{release_url}/{track['file']}", path)
    actual = sha256(path)
    if actual != track["sha256"]:
        raise SystemExit(f"  {track['file']}: sha256 {actual[:12]} does not match the manifest. Refusing to tag it.")
    return path


def load_clap(checkpoint):
    import laion_clap
    import torch

    torch.set_grad_enabled(False)
    model = laion_clap.CLAP_Module(enable_fusion=False, amodel="HTSAT-base")
    model.load_ckpt(str(checkpoint), verbose=False)
    return model


class Analyser:
    """Every model, loaded once and reused for each track."""

    def __init__(self, paths):
        import essentia.standard as es

        self.es = es
        self.paths = paths
        self.effnet = es.TensorflowPredictEffnetDiscogs(graphFilename=str(paths["effnet"]), output="PartitionedCall:1")
        self.heads = {}
        for name in ["genre", "moodtheme", "instrument", *BINARY_HEADS]:
            meta = json.loads(Path(paths[name + ".json"]).read_text())
            schema = meta["schema"]
            output = next(o["name"] for o in schema["outputs"] if o.get("output_purpose") == "predictions")
            algo = es.TensorflowPredict2D(graphFilename=str(paths[name]), input=schema["inputs"][0]["name"], output=output)
            self.heads[name] = (algo, meta["classes"])
        self.tempocnn = es.TempoCNN(graphFilename=str(paths["tempocnn"]))
        self.rhythm = es.RhythmExtractor2013(method="multifeature")
        self.key = es.KeyExtractor()
        self.clap = load_clap(paths["clap"])

    def __call__(self, path):
        import librosa

        es = self.es
        y48, _ = librosa.load(path, sr=48000, mono=True)
        duration = len(y48) / 48000
        # Models want several seconds of audio; the shortest stingers are
        # under two. Repeating a loop to 10 s is faithful to how it is used.
        # Onset density is measured on the untiled audio.
        tiled = np.resize(y48, max(len(y48), 48000 * 10)).astype(np.float32)
        y44 = librosa.resample(tiled, orig_sr=48000, target_sr=44100).astype(np.float32)
        y16 = librosa.resample(tiled, orig_sr=48000, target_sr=16000).astype(np.float32)
        y11 = librosa.resample(tiled, orig_sr=48000, target_sr=11025).astype(np.float32)
        y44_raw = librosa.resample(y48, orig_sr=48000, target_sr=44100)

        bpm_librosa, _ = librosa.beat.beat_track(y=y44, sr=44100)
        bpm_essentia, _, rhythm_confidence, _, _ = self.rhythm(y44)
        bpm_cnn, _, _ = self.tempocnn(y11)
        key, scale, key_strength = self.key(y44)
        onsets = librosa.onset.onset_detect(y=y44_raw, sr=44100)
        centroid = librosa.feature.spectral_centroid(y=y44_raw, sr=44100)

        embeddings = self.effnet(y16)
        essentia = {}
        for name, (algo, classes) in self.heads.items():
            mean = np.asarray(algo(embeddings)).mean(axis=0)
            if name in BINARY_HEADS:
                positive = next(i for i, c in enumerate(classes) if not c.startswith(("non_", "not_")))
                essentia.setdefault("binary", {})[name.replace("mood_", "")] = round(float(mean[positive]), 4)
            else:
                essentia[name] = {c: round(float(p), 4) for c, p in zip(classes, mean)}

        window = 48000 * 10
        count = int(np.ceil(len(tiled) / window))
        windows = np.resize(tiled, count * window).reshape(count, window)
        clap = self.clap.get_audio_embedding_from_data(x=windows, use_tensor=False).mean(axis=0)
        clap = clap / np.linalg.norm(clap)

        return {
            "analysisVersion": ANALYSIS_VERSION,
            "features": {
                "durationS": round(duration, 2),
                "bpmLibrosa": round(float(np.atleast_1d(bpm_librosa)[0]), 1),
                "bpmEssentia": round(float(bpm_essentia), 1),
                "bpmEssentiaConfidence": round(float(rhythm_confidence), 2),
                "bpmTempoCNN": round(float(bpm_cnn), 1),
                "key": key,
                "scale": scale,
                "keyStrength": round(float(key_strength), 3),
                "onsetsPerS": round(len(onsets) / max(duration, 1e-6), 2),
                "centroidHz": round(float(centroid.mean()), 0),
            },
            "essentia": essentia,
            "clap": [round(float(v), 6) for v in clap],
        }


def analyse(tracks, audio_dir, release_url, cache_dir, models_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    results, todo = {}, []
    for track in tracks:
        cached = cache_dir / f"{track['sha256']}.json"
        if cached.exists():
            data = json.loads(cached.read_text())
            if data.get("analysisVersion") == ANALYSIS_VERSION:
                results[track["id"]] = data
                continue
        todo.append(track)
    log(f"  analysis: {len(results)} cached, {len(todo)} to measure")
    if not todo:
        return results, None

    analyser = Analyser(ensure_models(models_dir))
    for n, track in enumerate(todo, 1):
        path = ensure_audio(track, audio_dir, release_url)
        data = analyser(path)
        (cache_dir / f"{track['sha256']}.json").write_text(json.dumps(data))
        results[track["id"]] = data
        log(f"  [{n}/{len(todo)}] {track['id']}")
    return results, analyser.clap


def text_embeddings(vocabulary, cache_dir, models_dir, clap):
    """One embedding per tag: its prompts embedded, averaged and renormalised."""
    prompts = {axis: {tag: spec["prompts"] for tag, spec in vocabulary[axis].items()} for axis in ("mood", "style")}
    key = hashlib.sha256(json.dumps(prompts, sort_keys=True).encode()).hexdigest()[:16]
    cached = cache_dir / f"text-{key}.json"
    if cached.exists():
        return json.loads(cached.read_text())
    if clap is None:
        clap = load_clap(ensure_models(models_dir)["clap"])
    out = {}
    for axis, tags in prompts.items():
        out[axis] = {}
        for tag, texts in tags.items():
            vectors = clap.get_text_embedding(texts)
            vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
            mean = vectors.mean(axis=0)
            out[axis][tag] = [float(v) for v in mean / np.linalg.norm(mean)]
    cached.write_text(json.dumps(out))
    return out


def zscore(matrix):
    std = matrix.std(axis=0)
    return (matrix - matrix.mean(axis=0)) / np.where(std > 0, std, 1)


def essentia_class(analysis, family, name):
    """One mapped class's probability. A name ending in * is the strongest class with that prefix."""
    scores = analysis["essentia"][family]
    if name.endswith("*"):
        return max(p for c, p in scores.items() if c.startswith(name[:-1]))
    if name not in scores:
        raise SystemExit(f"  vocabulary names unknown {family} class {name!r}")
    return scores[name]


def essentia_z(tracks, analyses, mapping):
    """A tag's Essentia vote: each mapped class standardised across the catalog, then averaged.

    Standardising first matters. The classes have very different base rates
    (a binary mood head averages ~0.15, a Discogs genre ~0.01), and a raw max
    or mean would let the commonest class drown out the rest.
    """
    columns = [
        np.array([essentia_class(analyses[t["id"]], family, name) for t in tracks])
        for family, names in mapping.items()
        for name in names
    ]
    return zscore(zscore(np.stack(columns, axis=1)).mean(axis=1, keepdims=True))[:, 0]


def score_axis(tracks, analyses, vocabulary, embeddings, axis):
    """For one axis: the tags, each (track, tag) score, and whether the tag may be applied at all.

    A tag with Essentia classes may only be applied when both models put the
    track above the catalog average for it. They are independent models, so a
    tag they disagree on is one neither should be trusted for. A tag CLAP
    alone scores has no second opinion and must clear a higher bar instead.
    """
    tags = list(vocabulary[axis])
    audio = np.array([analyses[t["id"]]["clap"] for t in tracks])
    text = np.array([embeddings[axis][tag] for tag in tags])
    clap_z = zscore(audio @ text.T)
    combined = clap_z.copy()
    allowed = clap_z >= CLAP_ONLY_Z
    for j, tag in enumerate(tags):
        mapping = vocabulary[axis][tag].get("essentia")
        if not mapping:
            continue
        es_z = essentia_z(tracks, analyses, mapping)
        combined[:, j] = zscore(((clap_z[:, j] + es_z) / 2)[:, None])[:, 0]
        allowed[:, j] = (clap_z[:, j] >= AGREE_Z) & (es_z >= AGREE_Z)
    return tags, combined, allowed


def pick(row, allowed, tags, threshold, limit):
    order = np.argsort(-row)
    return [tags[i] for i in order if row[i] >= threshold and allowed[i]][:limit]


def resolve_bpm(features):
    """Two of three estimators agreeing is a confident tempo; otherwise TempoCNN's guess."""
    estimates = [features["bpmEssentia"], features["bpmTempoCNN"], features["bpmLibrosa"]]
    for i in range(3):
        for j in range(i + 1, 3):
            a, b = estimates[i], estimates[j]
            if a > 0 and b > 0 and abs(a - b) / max(a, b) <= BPM_AGREE:
                return round((a + b) / 2), True
    return round(features["bpmTempoCNN"]), False


def tag(tracks, analyses, vocabulary, embeddings):
    mood_tags, mood_z, mood_ok = score_axis(tracks, analyses, vocabulary, embeddings, "mood")
    style_tags, style_z, style_ok = score_axis(tracks, analyses, vocabulary, embeddings, "style")

    # Energy: louder and busier than the rest of the catalog. The manifest's
    # `lufs` is used when present; tracks without it rank on onsets alone.
    onsets = np.array([analyses[t["id"]]["features"]["onsetsPerS"] for t in tracks])
    lufs = np.array([t["lufs"] if t.get("lufs") is not None else np.nan for t in tracks])
    lufs = np.where(np.isnan(lufs), np.nanmean(lufs), lufs)
    energy = (zscore(onsets[:, None]) + zscore(lufs[:, None]))[:, 0]
    low, high = np.quantile(energy, [1 / 3, 2 / 3])

    out = {}
    for i, track in enumerate(tracks):
        analysis = analyses[track["id"]]
        features = analysis["features"]
        bpm, confident = resolve_bpm(features)
        moods = pick(mood_z[i], mood_ok[i], mood_tags, MOOD_Z, MAX_MOODS)
        styles = pick(style_z[i], style_ok[i], style_tags, STYLE_Z, MAX_STYLES)
        genres = sorted(analysis["essentia"]["genre"].items(), key=lambda kv: -kv[1])
        out[track["id"]] = {
            "bpm": bpm,
            "bpmConfident": confident,
            "tempo": next(name for limit, name in TEMPO_BANDS if bpm < limit),
            "key": f"{features['key']} {features['scale']}",
            "energy": "low" if energy[i] < low else "high" if energy[i] >= high else "medium",
            # The single best mood, set even when nothing clears the bar for
            # `moods`: the best one both models agree on, or failing that the
            # best combined score.
            "mood": mood_tags[int(np.argmax(np.where(mood_ok[i], mood_z[i], -np.inf) if mood_ok[i].any() else mood_z[i]))],
            "moods": moods,
            "styles": styles,
            # Discogs style names, verbatim from Essentia's genre model.
            "genres": [name for name, p in genres[:3] if p >= 0.1],
        }
    return out


def with_fields(track, fields):
    """The track with `fields` placed after `source` (where `mood` already sits), key order otherwise kept."""
    out = {}
    for key, value in track.items():
        if key in OWNED_FIELDS:
            continue
        out[key] = value
        if key == "source":
            out.update(fields)
    if not all(k in out for k in fields):
        out.update(fields)
    return out


def report(tracks, tagged):
    from collections import Counter

    print()
    for track in tracks:
        t = tagged[track["id"]]
        mark = " " if t["bpmConfident"] else "?"
        print(f"  {track['id'][:44]:44} {t['bpm']:4}{mark} {t['tempo']:6} {t['energy']:6} {t['key']:9} {', '.join(t['moods']):24} {', '.join(t['styles'])}")
    print()
    print(f"  {len(tracks)} tracks, {sum(not t['bpmConfident'] for t in tagged.values())} with an unconfirmed bpm (marked ?)")
    print("  tempo:  " + ", ".join(f"{k} {v}" for k, v in Counter(t["tempo"] for t in tagged.values()).most_common()))
    print("  energy: " + ", ".join(f"{k} {v}" for k, v in Counter(t["energy"] for t in tagged.values()).most_common()))
    print("  mood:   " + ", ".join(f"{k} {v}" for k, v in Counter(t["mood"] for t in tagged.values()).most_common()))
    print("  moods:  " + ", ".join(f"{k} {v}" for k, v in Counter(m for t in tagged.values() for m in t["moods"]).most_common()))
    print("  styles: " + ", ".join(f"{k} {v}" for k, v in Counter(s for t in tagged.values() for s in t["styles"]).most_common()))
    print(f"  no mood above the bar: {sum(not t['moods'] for t in tagged.values())}, no style: {sum(not t['styles'] for t in tagged.values())}")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--audio", type=Path, default=BUILD / "audio", help="directory of the release's audio files (missing ones are downloaded)")
    parser.add_argument("--models", type=Path, default=BUILD / "models", help="model directory (missing models are downloaded)")
    parser.add_argument("--cache", type=Path, default=BUILD / "analysis", help="analysis cache, keyed by sha256")
    parser.add_argument("--write", action="store_true", help="update manifest.json (default: print and stop)")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    vocabulary = json.loads(VOCABULARY.read_text())
    tracks = manifest["tracks"]
    release_url = f"{manifest['releaseBaseUrl']}/{manifest['releaseTag']}"

    analyses, clap = analyse(tracks, args.audio.expanduser(), release_url, args.cache, args.models)
    embeddings = text_embeddings(vocabulary, args.cache, args.models, clap)
    tagged = tag(tracks, analyses, vocabulary, embeddings)
    report(tracks, tagged)

    if not args.write:
        print("  Dry run. Re-run with --write to update manifest.json.\n")
        return
    manifest["tracks"] = [with_fields(t, tagged[t["id"]]) for t in tracks]
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"  ✓ manifest.json updated, {len(tracks)} track(s) tagged\n")


if __name__ == "__main__":
    main()
