# video-background-music

A mirror of CC0 background-music tracks, for
[`product-demo-template`](../product-demo-template) to seed and download from.

**Thin git, heavy releases.** This repository tracks a catalog, some licence
paperwork, and one script. The audio lives in **GitHub release assets**.

```
manifest.json          # the catalog — the only thing git knows about the audio
LICENSE                # MIT, and it covers THIS REPO'S CODE ONLY
LICENSES/              # CC0 deed pointer, and per-source provenance
scripts/sync-upstream  # run by hand; never scheduled
(release assets)       # v1/ambient-drift.mp3, … the actual tracks
```

## Why release assets and not git, or LFS

| | per file | total | bandwidth | failure mode |
| --- | --- | --- | --- | --- |
| git history | 100 MiB hard | "under 1 GB ideally" | every clone pays, forever | 600 MB of mp3 in history is permanent |
| Git LFS | 2 GB | — | **10 GiB/mo, then LFS is disabled** | the library goes dark mid-month |
| **release assets** | 2 GiB | **no limit** | **unmetered** | — (1000 assets per release) |

Downloads go to `…/releases/download/<tag>/<file>`, which is CDN-backed and
tag-pinned, so a consumer pinned to `v1` resolves the same bytes in a year.
`raw.githubusercontent.com` is deliberately not used: it is IP-rate-limited for
unauthenticated requests and answers 429 under load.

## These tracks are not ours

Everything here is **CC0 1.0** — dedicated to the public domain by its
composer. The `LICENSE` file is **MIT, and it covers `scripts/`,
`manifest.json` and the docs only** — not the audio in the release assets. CC0
permits redistribution; it does not permit re-licensing someone else's work, so
no claim is made over the tracks. Per-track licence lives in `manifest.json`.

With thanks to:

- **Abstraction / Tallbeard Studios** — the [Music Loop Bundle](https://tallbeard.itch.io/music-loop-bundle),
  200+ seamless loops released CC0. They ask that the assets not be resold
  unmodified, and not be used for NFT or AI/ML projects. A free mirror is
  neither, and the request is honoured here.
- **FreePD / Kevin MacLeod and others** — freepd.com ran for 17 years and
  closed in 2025. The surviving CC0 corpus is mirrored at
  [0lhi/FreePD](https://github.com/0lhi/FreePD), which is where these come from.
- **Musopen** — CC0 recordings only; their catalog mixes CC0, PD and CC-BY-SA,
  and the filter is not optional.

Deliberately **excluded**, despite being free to use: Pixabay and the YouTube
Audio Library. Both licences forbid distributing content standalone where no
creative effort was applied, and a mirror of unmodified tracks is exactly that.

## Adding tracks

Only CC0. The consumer is a template other people clone, and a CC-BY track
would hand every one of them an attribution obligation they never agreed to.
`manifest.json` has `license` and `attribution` fields so a non-CC0 track
*could* be added deliberately and surfaced in the consumer's UI — not so one
can arrive quietly.

```bash
# dry by default: prints added / changed / removed / unchanged and stops
node scripts/sync-upstream.mjs --bundle ~/Downloads/music-loop-bundle.zip

# writes manifest.json and prints the gh command to upload the new release
node scripts/sync-upstream.mjs --bundle ~/Downloads/... --write --tag v2
```

**The sync is manual and occasional, by design.** Abstraction is still
publishing to that bundle. A scheduled sync would quietly redefine what this
mirror contains, and the whole point of a mirror is that it does not change
under its consumers.

Needs `ffmpeg` on PATH, for transcode and loudness normalisation.
