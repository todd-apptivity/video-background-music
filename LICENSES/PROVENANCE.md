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
- Changed: transcoded to 192 kbps mp3, loudness-normalised, filenames slugified
- Notes: itch.io has no stable direct download URL, which is the main reason
  this mirror exists. The author asks that assets not be resold unmodified and
  not be used for NFT or AI/ML projects; a free mirror for use as a video music
  bed is neither.

### FreePD

- URL: <https://github.com/0lhi/FreePD> (mirror; freepd.com closed in 2025)
- Licence: CC0 1.0 (repository `LICENSE` is CC0-1.0)
- Retrieved: _(fill in on first sync)_
- Taken: _(fill in — which genre folders)_
- Changed: transcoded to 192 kbps mp3, loudness-normalised, filenames slugified
- Notes: ~6 GB repository. Sparse-checkout the folders in use; do not clone it
  whole.

### Musopen

- URL: <https://musopen.org/search/?license=cc0>
- Licence: **CC0 subset only.** The catalog mixes PD, CC0 and CC-BY-SA, and the
  `license=cc0` filter is mandatory — a CC-BY-SA recording in here would
  silently impose share-alike on every consumer.
- Retrieved: _(fill in on first sync)_
- Taken: _(fill in)_
- Changed: transcoded to 192 kbps mp3, loudness-normalised, filenames slugified
