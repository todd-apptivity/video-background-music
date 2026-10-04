#!/usr/bin/env node
/**
 * Re-sync the catalog against its upstreams. Run by hand, never scheduled.
 *
 * WHY IT COPIES AND MEASURES INSTEAD OF TRANSCODING
 * ------------------------------------------------
 * The first version of this script re-encoded everything to 192 kbps mp3 with
 * `loudnorm`. That was wrong twice over, and the upstream says so in its own
 * README:
 *
 *   "MP3 files will often NOT loop seamlessly. You will notice a very short gap
 *    when the song loops. This is due to the nature of the MP3 file format...
 *    It is recommended to use these songs as OGG files."
 *
 * The bundle ships Ogg Vorbis for exactly that reason. Transcoding to mp3 would
 * have reintroduced the encoder-delay gap the author went out of their way to
 * avoid — in a library whose entire purpose is seamless beds under narration.
 *
 * And re-encoding at all is lossy-to-lossy generation loss, for no gain: these
 * files are already web-playable everywhere the renderer runs.
 *
 * So a track is copied BYTE-IDENTICAL, and loudness is *measured* rather than
 * changed. `loudnorm` here runs in analysis mode only (`-f null`), which writes
 * nothing. The consumer already has a per-project gain control, so the right
 * place to make a bed quiet is a number in that project, not a permanent
 * re-encode of a file every project shares.
 *
 * WHY IT DECIDES AND ACTS IN TWO STEPS
 * ------------------------------------
 * Without `--write` this prints a diff and stops. An upstream re-encode changes
 * the sha256 of every track it touches without changing a note of the music, so
 * a script that both computed and committed would turn "180 tracks were
 * re-encoded upstream" into a silent manifest change — and every consumer
 * pinned to the old hashes would start refusing downloads with no explanation.
 *
 * WHY THIS IS NOT ON A TIMER
 * --------------------------
 * A mirror exists so that its contents do NOT move under the people using it.
 * Somebody decides to take a new snapshot, and that decision is a commit.
 *
 * Needs `ffmpeg` and `ffprobe` on PATH (both analysis-only).
 */
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFile, execFileSync } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const run = promisify(execFile);

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const MANIFEST = path.join(REPO, "manifest.json");

/**
 * Formats published as-is. Everything here is playable by the `<audio>` element
 * in every browser the renderer uses, so there is nothing a transcode would buy.
 * A format NOT on this list is reported and skipped rather than converted —
 * converting is the decision this script got wrong once already.
 */
const PASSTHROUGH = new Set([".ogg", ".mp3", ".m4a", ".flac", ".wav"]);

/** How many ffmpeg analysis passes to keep in flight. */
const CONCURRENCY = Math.max(2, os.cpus().length - 2);

function usage(message) {
  if (message) console.error(`\n  ${message}`);
  console.error(`
  Usage: node scripts/sync-upstream.mjs --bundle <dir-or-zip> [options]

    --bundle <path>   a hand-downloaded Tallbeard loop bundle: a zip, or a
                      directory of them (itch.io has no stable direct URL)
    --freepd <path>   a sparse checkout of github.com/0lhi/FreePD
    --musopen <path>  a directory of CC0-filtered Musopen downloads
    --exclude <a,b>   substrings; any source file whose path matches is skipped
    --write           update manifest.json (default: print the diff and stop)
    --tag <v2>        release tag to record with --write (default: keep current)
    --out <dir>       where published files are staged (default: ./build)
    --no-loudness     skip the loudness pass (faster; records lufs: null)

  Files are COPIED, never re-encoded — see the header of this file.
  Nothing is uploaded: --write prints the \`gh release\` command for you to run.
`);
  process.exit(message ? 1 : 0);
}

function parseArgs(argv) {
  const opts = { sources: {}, exclude: [], write: false, tag: null, out: path.join(REPO, "build"), loudness: true };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => argv[++i] ?? usage(`${arg} needs a value`);
    if (arg === "--bundle") opts.sources.tallbeard = next();
    else if (arg === "--freepd") opts.sources.freepd = next();
    else if (arg === "--musopen") opts.sources.musopen = next();
    else if (arg === "--exclude") opts.exclude.push(...next().split(",").map((s) => s.trim()).filter(Boolean));
    else if (arg === "--write") opts.write = true;
    else if (arg === "--tag") opts.tag = next();
    else if (arg === "--out") opts.out = path.resolve(next());
    else if (arg === "--no-loudness") opts.loudness = false;
    else if (arg === "-h" || arg === "--help") usage();
    else usage(`unknown option ${arg}`);
  }
  if (!Object.keys(opts.sources).length) usage("name at least one source");
  return opts;
}

function requireTool(name) {
  try {
    execFileSync(name, ["-version"], { stdio: "ignore" });
  } catch {
    console.error(`\n  ${name} is not on PATH. It is needed to measure every track (analysis only).\n`);
    process.exit(1);
  }
}

/** The same slug rule the consumer's `safeStem` applies, so a name never changes on the way in. */
function slugify(filename) {
  return path
    .basename(filename, path.extname(filename))
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^[-.]+|[-.]+$/g, "");
}

/**
 * A readable title.
 *
 * The upstream names carry their release slot — "Week 27 - Dusty Memories",
 * "Troubadeck 04 Guinea Pig Jig" — which is noise in a picker. The slug keeps
 * the full name so two tracks can never collide; only the title is tidied.
 */
function titleFrom(basename) {
  const cleaned = basename
    .replace(/\.[a-z0-9]+$/i, "")
    .replace(/^week\s*\d+\s*[-–]\s*/i, "")
    .replace(/^troubadeck\s*\d+\s*/i, "")
    .replace(/^ludum\s+dare\s+\d+\s*/i, "LD ")
    .trim();
  return cleaned || basename;
}

function findAudio(root, exclude) {
  const found = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (PASSTHROUGH.has(path.extname(entry.name).toLowerCase())) found.push(full);
    }
  };
  walk(root);
  return found.filter((f) => !exclude.some((needle) => f.includes(needle))).sort();
}

/** Unpack one zip, or every zip in a directory, into a scratch dir. */
function unpack(location, into) {
  fs.mkdirSync(into, { recursive: true });
  const zips = location.toLowerCase().endsWith(".zip")
    ? [location]
    : fs
        .readdirSync(location)
        .filter((f) => f.toLowerCase().endsWith(".zip"))
        .map((f) => path.join(location, f))
        .sort();

  if (!zips.length) return location; // already a plain directory of audio
  for (const zip of zips) {
    // Everything, with no include patterns, and -j to flatten.
    //
    // -j because each bundle is one flat folder and the zip name would
    // otherwise end up in every slug. No include patterns because
    // `unzip … *.mp3` exits 11 ("no matching files") on an all-Ogg bundle —
    // which all of these are — and that killed the whole run. `findAudio`
    // filters by extension regardless, so the artwork and readmes that come
    // out here are simply ignored.
    execFileSync("unzip", ["-qq", "-o", "-j", zip, "-d", into], { stdio: "ignore" });
  }
  return into;
}

async function durationSeconds(file) {
  const { stdout } = await run("ffprobe", [
    "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
    file,
  ]);
  const value = Number(String(stdout).trim());
  return Number.isFinite(value) ? Math.round(value * 100) / 100 : null;
}

/**
 * Integrated loudness, measured and not applied.
 *
 * `-f null` discards the output: this decodes the file and prints what it would
 * have done, changing nothing on disk. Recorded so a bed that is far louder
 * than the rest is visible when choosing a seed, instead of being discovered by
 * ear after a render.
 */
async function loudness(file) {
  try {
    const { stderr } = await run(
      "ffmpeg",
      ["-hide_banner", "-nostats", "-i", file, "-af", "loudnorm=print_format=json", "-f", "null", "-"],
      { maxBuffer: 8 * 1024 * 1024 },
    );
    const match = String(stderr).match(/"input_i"\s*:\s*"(-?[\d.]+)"/);
    return match ? Number(match[1]) : null;
  } catch {
    return null;
  }
}

/** Run `worker` over `items`, at most CONCURRENCY at a time, preserving order. */
async function mapLimit(items, worker) {
  const results = new Array(items.length);
  let cursor = 0;
  const runners = Array.from({ length: Math.min(CONCURRENCY, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor++;
      results[index] = await worker(items[index], index);
    }
  });
  await Promise.all(runners);
  return results;
}

async function describe(input, source, outDir, { measureLoudness }) {
  const ext = path.extname(input).toLowerCase();
  const id = slugify(input);
  const file = `${id}${ext}`;
  const target = path.join(outDir, file);

  // The copy IS the publish step. Byte-identical, so the hash below is the hash
  // of what a consumer downloads and verifies.
  fs.copyFileSync(input, target);

  const [durationS, lufs] = await Promise.all([
    durationSeconds(target),
    measureLoudness ? loudness(target) : Promise.resolve(null),
  ]);

  return {
    id,
    title: titleFrom(path.basename(input)),
    file,
    license: "CC0-1.0",
    attribution: "",
    source,
    mood: "",
    // Every track in the Tallbeard bundle is authored as a seamless loop; that
    // is what the bundle is. Other sources are not, and must not claim to be.
    loopable: source === "tallbeard",
    durationS,
    lufs,
    bytes: fs.statSync(target).size,
    sha256: crypto.createHash("sha256").update(fs.readFileSync(target)).digest("hex"),
    seeded: false,
  };
}

/** The fields scripts/tag-tracks.py owns. Keep in step with OWNED_FIELDS there. */
const TAG_FIELDS = ["bpm", "bpmConfident", "tempo", "key", "energy", "mood", "moods", "styles", "genres"];

/** `track` with `old`'s tag fields placed after `source`, the order tag-tracks.py writes. */
function withTagFields(track, old) {
  const out = {};
  for (const [key, value] of Object.entries(track)) {
    if (TAG_FIELDS.includes(key)) continue;
    out[key] = value;
    if (key === "source") {
      for (const field of TAG_FIELDS) if (field in old) out[field] = old[field];
    }
  }
  if (!("mood" in out)) out.mood = track.mood;
  return out;
}

function diff(before, after) {
  const was = new Map(before.map((t) => [t.id, t]));
  const now = new Map(after.map((t) => [t.id, t]));
  return {
    added: after.filter((t) => !was.has(t.id)),
    removed: before.filter((t) => !now.has(t.id)),
    changed: after.filter((t) => was.has(t.id) && was.get(t.id).sha256 !== t.sha256),
    unchanged: after.filter((t) => was.has(t.id) && was.get(t.id).sha256 === t.sha256),
  };
}

function report({ added, removed, changed, unchanged }) {
  console.log("");
  for (const t of added) console.log(`  + ${t.id.padEnd(44)} ${String(t.durationS ?? "?").padStart(6)}s  ${t.source}`);
  for (const t of changed) console.log(`  ~ ${t.id.padEnd(44)} bytes differ — upstream re-encode?`);
  for (const t of removed) console.log(`  - ${t.id.padEnd(44)} gone from upstream`);
  console.log(
    `\n  ${added.length} added, ${changed.length} changed, ${removed.length} removed, ${unchanged.length} unchanged\n`,
  );

  if (changed.length > 5) {
    console.log(
      `  ⚠ ${changed.length} tracks changed bytes without changing id. That is usually an\n` +
        "    upstream re-encode. Publishing it bumps every consumer's hash for the same\n" +
        "    music — check a couple by ear before --write.\n",
    );
  }
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  requireTool("ffprobe");
  if (opts.loudness) requireTool("ffmpeg");

  const manifest = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));
  const before = manifest.tracks ?? [];

  fs.rmSync(opts.out, { recursive: true, force: true });
  fs.mkdirSync(opts.out, { recursive: true });
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "vbm-sync-"));

  let after = [];
  try {
    for (const [source, location] of Object.entries(opts.sources)) {
      const root = unpack(location, path.join(scratch, source));
      const inputs = findAudio(root, opts.exclude);
      console.log(`  ${source}: ${inputs.length} file(s)`);
      const described = await mapLimit(inputs, (input) => describe(input, source, opts.out, { measureLoudness: opts.loudness }));
      after.push(...described);
    }
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }

  // An id collision would mean two files publishing to one release asset, with
  // the second silently winning. Refuse rather than pick.
  const seen = new Map();
  const collisions = [];
  for (const track of after) {
    if (seen.has(track.id)) collisions.push(track.id);
    seen.set(track.id, track);
  }
  if (collisions.length) {
    console.error(`\n  refusing: ${collisions.length} duplicate id(s) — ${collisions.slice(0, 5).join(", ")}\n`);
    process.exit(1);
  }

  after.sort((a, b) => a.id.localeCompare(b.id));
  report(diff(before, after));

  if (!opts.write) {
    console.log("  Dry run. Re-run with --write to update manifest.json.\n");
    return;
  }

  // `seeded` is a decision about the CONSUMER's committed seed, not about this
  // mirror, so a re-sync must never clear it: whoever chose to commit a track
  // chose that, and rediscovering it upstream is not a reason to un-choose it.
  const wasSeeded = new Set(before.filter((t) => t.seeded).map((t) => t.id));
  for (const track of after) track.seeded = wasSeeded.has(track.id);

  // Tags are measured from the audio by scripts/tag-tracks.py, which takes a
  // model run this script does not do. Same bytes, same measurements: carry
  // them forward. Changed bytes drop them, so a re-encode is re-measured
  // rather than wearing the old file's tags.
  const previous = new Map(before.map((t) => [t.id, t]));
  after = after.map((track) => {
    const old = previous.get(track.id);
    return old && old.sha256 === track.sha256 ? withTagFields(track, old) : track;
  });

  const tag = opts.tag ?? manifest.releaseTag;
  fs.writeFileSync(MANIFEST, `${JSON.stringify({ ...manifest, releaseTag: tag, tracks: after }, null, 2)}\n`);
  console.log(`  ✓ manifest.json updated — releaseTag ${tag}, ${after.length} track(s)\n`);
  console.log("  Now publish the audio as release assets:\n");
  console.log(`    gh release create ${tag} --title ${tag} --notes "music sync" ${opts.out}/*\n`);
  console.log("  Then copy manifest.json into the consumer's library/manifest.json.\n");
}

await main();
