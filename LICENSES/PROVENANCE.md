# Provenance

Where every track came from, and what was done to it. One entry per upstream.

This file exists because `manifest.json` is machine-written and records
per-track facts; the *source-level* story — what was taken, when, under what
terms, and what was changed — is a human record and belongs somewhere a diff
reads well.

## The licence text

Every track here is **CC0 1.0 Universal**. The canonical deed and legal text
are at <https://creativecommons.org/publicdomain/zero/1.0/>. They are
deliberately **not** reproduced in this directory: a paraphrased or
misremembered copy of a licence is worse than a link to the authoritative one.

Fetch the canonical text alongside a release if a self-contained copy is wanted:

```bash
curl -o LICENSES/CC0-1.0.txt https://creativecommons.org/publicdomain/zero/1.0/legalcode.txt
```

## What CC0 does and does not permit here

It permits redistribution, modification and commercial use with no attribution.
It does **not** permit re-licensing someone else's work: this repository's own
`LICENSE` is MIT and covers `scripts/`, `manifest.json` and the docs, making no
claim over the audio — a scope note at the bottom of that file says so
explicitly, because "MIT" on a repository full of other people's music is
exactly the kind of thing a reader would otherwise assume applies to all of it.
Credit is given in `README.md` as a courtesy, not as a licence term.

## Sources

### Abstraction / Tallbeard Studios — Music Loop Bundle

- URL: <https://tallbeard.itch.io/music-loop-bundle>
- Licence: CC0 1.0, stated on the itch.io page
- Retrieved: _(fill in on first sync)_
- Taken: _(fill in — which folders, how many tracks)_
- Changed: nothing in the audio. Files are copied byte-identical and only the
  filenames are slugified; loudness is measured, not applied (see the header of
  `scripts/sync-upstream.mjs`)
- Notes: itch.io has no stable direct download URL, which is the main reason
  this mirror exists. The author asks that assets not be resold unmodified and
  says they do not endorse use in projects relating to NFTs or AI/ML; a free
  mirror for use as a video music bed is neither.
- Tagging: the tempo, mood and style fields in `manifest.json` were produced by
  running pretrained models over these files locally (see "Analysis models"
  below). No track was used to train or fine-tune anything, and no audio was
  sent to a third-party service. The owner of this repository read that as
  outside the author's AI/ML request, since the project is a music library
  and the models only label it; anyone who reads the request more strictly
  can drop the tag fields and keep the audio.

### FreePD

- URL: <https://github.com/0lhi/FreePD> (mirror; freepd.com closed in 2025)
- Licence: CC0 1.0 (repository `LICENSE` is CC0-1.0)
- Retrieved: _(fill in on first sync)_
- Taken: _(fill in — which genre folders)_
- Changed: nothing in the audio. Files are copied byte-identical and only the
  filenames are slugified; loudness is measured, not applied (see the header of
  `scripts/sync-upstream.mjs`)
- Notes: ~6 GB repository. Sparse-checkout the folders in use; do not clone it
  whole.

### Musopen

- URL: <https://musopen.org/search/?license=cc0>
- Licence: **CC0 subset only.** The catalog mixes PD, CC0 and CC-BY-SA, and the
  `license=cc0` filter is mandatory — a CC-BY-SA recording in here would
  silently impose share-alike on every consumer.
- Retrieved: _(fill in on first sync)_
- Taken: _(fill in)_
- Changed: nothing in the audio. Files are copied byte-identical and only the
  filenames are slugified; loudness is measured, not applied (see the header of
  `scripts/sync-upstream.mjs`)

## Analysis models

`scripts/tag-tracks.py` writes `bpm`, `bpmConfident`, `tempo`, `key`,
`energy`, `mood`, `moods`, `styles` and `genres` into `manifest.json`. These are
measurements of the audio, not part of it. They come from:

- **LAION-CLAP**, checkpoint `music_audioset_epoch_15_esc_90.14.pt`
  (<https://huggingface.co/lukewys/laion_clap>). CC0 1.0. Scores `mood`,
  `moods` and `styles` against `scripts/tag-vocabulary.json`.
- **Essentia models** by the Music Technology Group, Universitat Pompeu Fabra
  (<https://essentia.upf.edu/models.html>): Discogs-EffNet with its
  genre_discogs400, mtg_jamendo_moodtheme, mtg_jamendo_instrument, mood and
  danceability heads, and TempoCNN (deeptemp-k16). **CC BY-NC-SA 4.0**, which
  is free for non-commercial use with attribution; a commercial licence is
  available from MTG on request. They give a second opinion on the moods and
  styles, one of three tempo estimates behind `bpm`, and all of `genres`
  (Discogs style names, verbatim).
- **Essentia** (AGPL-3.0) and **librosa** (ISC), as libraries: beat tracking,
  key detection and onset counting. Signal processing, no trained model.

Using the Essentia models here rests on this being a free, public catalog that
charges nobody: under the licence, "NonCommercial means not primarily intended
for or directed towards commercial advantage or monetary compensation." If the
tagging ever becomes part of paid work, get MTG's commercial licence or remove
the Essentia votes. The fields they produce are treated as facts about the
audio, not as an adaptation of the models, so the manifest stays under this
repository's MIT licence. That is this project's reading of the licence, not
settled law.

`analysis/` holds the raw measurements the tags are scored from, one file per
track: the three tempo estimates, key, onset density, every Essentia class
probability and the track's CLAP embedding, plus the CLAP embeddings of the
vocabulary. They are kept so a new track only needs measuring once. The same
reading applies to them as to the manifest fields: outputs of the models,
covered by this repository's MIT licence, with the Essentia part credited
above.
