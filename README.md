# dic

**dic** is a Romanian etymology browser. It groups inflected forms under their
main word, shows the source etymology directly in each row, and lets you search
and browse Latin, Slavic, Turkic, French, and other origins.

The browser reads **SQLite itself**, using a bundled WebAssembly SQLite engine.
The published site consists entirely of static files. Python and Ollama are used
to prepare data; neither is required by the published website.

## Run

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Ruby/Rake, and Node
for development/tests. The repository contains the populated dictionary database.

```bash
rake setup
rake run                       # http://<this-computer>:8040
```

The server binds to `0.0.0.0`, so another LAN device can open its hostname or IP.
Use `HOST=127.0.0.1 rake run` for localhost only, or `PORT=8050 rake run` for
another port. Press `/` outside an editable field to focus the search box.

Search offers **autocomplete queried from SQLite**. It recognizes diacritics,
older `î` spellings, grouped forms, Latin etymons, and the optional verb prefix
`a`: `a cîștiga` finds `câștiga`, and `conventum` finds `cuvânt`. Original
headwords and embedding inputs retain the source spelling.

## Architecture

Two halves that meet exactly once, at build time:

```text
data/source.json → pinned XML dump → data/dic.sqlite → dist/ → browser
  (manifest)       (dumps.py)     (parser + database)  (site.py)  (sql.js WASM + JS)
                                           ▲                    ▲
                                           └─ embeddings.py → data/vectors.sqlite
                                              (scores published into dic.sqlite)
```

**Python package `dic` (`src/dic/`) — build-time only.** The `dic` CLI
(`cli.py`: `download`, `import`, `build`, `embed`, `serve`) is wired into rake
tasks by the `Rakefile`.

- `dumps.py` downloads the pinned Wiktionary XML snapshot and verifies its
  SHA-1; `download_pinned` never follows a moving `latest` URL.
- `parser.py` extracts entries conservatively with mwparserfromhell: Romanian
  sections, etymology blocks, origins from `inh`/`bor`/`der`/`etim` templates or
  explicit `din …` prose, inflected forms from paradigm templates. No template
  expansion or guessed stemming; raw wikitext is kept for audit.
- `database.py` streams the XML (`iterparse`) into SQLite: `entries`,
  `etymologies`, `origins`, `forms`, `form_links`, `metadata`, plus the
  `latin_inheritance`/`latin_candidates` views. The import builds a `.building`
  file and atomically replaces the old database only after an integrity check.
- `embeddings.py` (optional; needs Ollama) embeds words, ancestors and attested
  definitions with BGE-M3 into the local cache `data/vectors.sqlite`
  (normalized float32 blobs keyed by model digest + text hash; resumable runs),
  and stores exact cosine distances per word–ancestor pair in `pair_scores`.
- `webdata.py` prepares the publishable database in place: `etymology_display`
  (plain-text etymologies), `search_aliases` (accent- and î/â-folded keys for
  words, forms, ancestors), and `origin_scores` (published distances joined by
  origin id, matched to the dictionary by dump SHA-1 + run id).
- `site.py` renders `web/index.html` into `dist/`: substitutes the
  `__PROJECT_NAME*__` placeholders from the `PROJECT_NAME` constant, inlines
  `sqlite.mjs` + `ranking.mjs` + `explorer.js` into one HTML file, injects a
  small boot JSON (`metadata` + database filename, HTML-escaped) into
  `<script id="dic-data">`, copies the dictionary as `dist/dic.sqlite`, and
  copies the pinned sql.js WASM assets to `dist/vendor/`.

**Web app (`src/dic/web/`) — runs entirely in the browser.**

- `index.html` is the template: markup, CSS, the boot payload, and the inlined
  scripts. `PROJECT_NAME` is the single constant behind the displayed name
  (title, brand, footer).
- `sqlite.mjs` loads the dictionary: `hydrateDictionary` reads `metadata`,
  lemma `entries`, `forms`, etymologies with display text, and `origins` with
  scores into JS objects; `autocomplete` is a prefix range query over
  `search_aliases`.
- `ranking.mjs` holds the pure ranking rules (origin tabs, evidence filters,
  search folding including î/â variants and the `a ` verb prefix, scoring,
  sorting), shared verbatim with the Node test suite.
- `explorer.js` is the app: reads the boot JSON, initializes sql.js with the
  WASM build, fetches `dic.sqlite`, hydrates, renders tabs/results/pagination,
  and keeps shareable state in the URL hash.

**How they interact:** only at build time. `site.build()` reads the prepared
SQLite and produces a self-contained static `dist/`. At runtime there is no
server API: the browser fetches `dic.sqlite`, opens it in memory with the
WebAssembly SQLite engine, and every search, suggestion and ranking query runs
locally. Python, Ollama and the XML dump are never needed by the published site.

## Static hosting, including GitHub Pages

```bash
rake build
python3 -m http.server 8040 --directory dist
```

Publish the **whole `dist/` folder**, containing:

- `index.html`: interface and application code;
- `dic.sqlite`: the searchable dictionary, etymology text, aliases and scores;
- `vendor/sql-wasm.js`, `vendor/sql-wasm.wasm`, and the sql.js license.

No API, database server, CDN, or Ollama connection is needed for browsing. The
browser downloads a read-only copy of SQLite and runs SQL locally. Search
suggestions are SQL queries, not a hard-coded word list. Browser changes are not
written back to the hosted file. Serve the directory over HTTP; opening the HTML
as a `file://` URL cannot reliably fetch the SQLite/WASM assets.

`rake build` uses the committed SQLite database and does **not** download the
Wiktionary dump. This also works in a clean clone without `data/vectors.sqlite`:
the published scores are already stored in the versioned dictionary.

## Versioned data and local caches

**Committed:** `data/dic.sqlite`, `data/source.json`, the lockfiles, source code,
tests, frozen BGE-M3 test vectors, and the bundled sql.js runtime/license.

**Ignored:** compressed XML dumps, `.venv/`, `node_modules/`, `dist/`, and the
large local embedding cache `data/vectors.sqlite`.

The dictionary tables are `entries`, `etymologies`, `origins`, `forms`, and
`form_links`. Public web tables are `etymology_display`, `search_aliases`, and
`origin_scores`. `metadata` identifies the source checksum and published model
run. Etymologies preserve raw wikitext and separate numbered homographs; each
word links to the source page and imported revision.

## Reproduce the Wiktionary import

`data/source.json` pins the edition, exact dump date, immutable download URL,
compressed size and SHA-1. The current source is Romanian Wiktionary's
**2026-11-01** articles dump, SHA-1
`efa92d9d0414c222eddad18ac05d8f75743ee7f4`.

```bash
rake download                  # download this exact snapshot; verify/reuse cache
rake populate                  # download → parse XML → populate SQLite, incrementally
rake import                    # force the same import again
rake build                     # publish the SQLite file and static site
rake refresh                   # populate, then build
rake -P                        # inspect the Rake dependency graph
```

The dependencies are:

```text
data/source.json → pinned XML dump → data/dic.sqlite
                       download          populate
                                    ↓
                              static site build
```

`populate` depends on `download` and the SQLite file task. That file task depends
on the pinned dump, source manifest, Python source and uv lockfile. An unchanged
valid dump is not downloaded again, and an up-to-date database is not reimported.
`import` forces the file task when a clean replay is wanted. Importing streams
XML and creates a replacement database; the previous database remains usable
until a successful import and integrity check.

The task sets `SOURCE_DATE_EPOCH` from the pinned dump date. Together with the
locked parser dependencies and deterministic insertion order, this removes
wall-clock timestamps from the lexical rebuild. Unit tests repeat imports both in-process and under different Python hash seeds,
asserting byte-identical SQLite output in the same runtime. Across SQLite
versions, compare logical rows/checksums rather than promising identical page
layouts. Download manifests are deterministic and imports verify their checksum.

A lexical rebuild does not invent or recompute embedding scores. `rake build`
can republish a matching local vector-cache run; otherwise `rake embed` computes
it explicitly. The committed database captures the published score values, so
serving/testing a clone does not depend on an available embedding service.

Rebuilding requires the pinned bytes to remain available upstream or in the local
dump cache; serving the committed database does not.

To intentionally use another snapshot, update **all** source-manifest fields,
then run `rake refresh`. Do not replace the pin with a moving `latest` URL.
`SOURCE`, `DUMP`, `DB`, `OUTPUT`, and `VECTORS` allow explicit alternative paths.
The dump filename must match its manifest. The downloader also supports an
explicit latest-edition discovery through `uv run dic download --edition en`,
but that is separate from the reproducible default Rake pipeline.

## Embeddings and ranking

```bash
rake embed                     # compute/resume, store local vectors, publish scores
MODEL=bge-m3:567m-fp16 OLLAMA_URL=http://zmeu.local:11434 rake embed
```

The default is BGE-M3 FP16, 1024 dimensions. Identical input strings are deduplicated.
The cache stores normalized little-endian float32 vector BLOBs, model digest,
exact input text/hash, definition provenance, runs and checkpoints. Each batch
is committed; rerunning resumes completed work. Model digests isolate caches.
Invalid, nonfinite, zero or inconsistent-dimension vectors are rejected.

We compare known word–etymon pairs, so exact cosine distances in SQLite are more
appropriate than approximate nearest-neighbour lookup in FAISS. Distance is
`1 − dot(normalized_word, normalized_etymon)`, clamped to [0, 2] (0 = identical
vectors, 2 = opposite). The static database contains
scores and their textual evidence, **not** the large vectors.

Pipeline detail (mirrors the “How the distances are computed” section at the end of
the published page): at `rake embed` time, BGE-M3 embeds each unique input once;
vectors are L2-normalized float32 BLOBs in `data/vectors.sqlite`, keyed by model
digest + SHA-256 of the exact input text, resumable per batch, with empty,
non-finite, zero-norm or inconsistent-dimension vectors rejected. Scores are
computed per known word–ancestor pair under the recipe
`bare-words-and-first-attested-definition-v1`, rounded to 6 decimals, and
published into `origin_scores` — the browser only reads them, it never computes
embeddings. Two metrics exist: bare-word (word vs ancestor string) and attested
definitions (first attested definition of the Romanian etymon vs an attested
meaning of the source word, headwords stripped; an inline gloss quoted in the
etymology takes precedence).

Choose a scope and **Largest distance first** or **Smallest distance first**.
For multiple eligible etymons, descending order uses the largest pair distance;
ascending order uses the smallest. The highlighted source supplies the score.
The ranking respects the selected origin, evidence and ancestor depth. Missing
scores sort last, and **Scored words only** hides them.

Enable **Include deeper ancestors** to compare `pământ` with `pavimentum`, or
`bătrân` with `veteranus`. The immediate recorded forms are `paumentum` and
`betranus`. Explicit source equivalents marked `= …` are retained separately
from immediate donors. Multiple homographs/origins remain visible in the
etymology column even when a scope selects one of their origins.

### What the distances mean

**Bare-word embeddings do not signal language.** Romanian `casa` and Latin
`casa` are sent as the same string and get the same vector. The stored language
labels are metadata, not hidden prompts. Word distances can reflect spelling,
language, polysemy and training coverage; they are candidates for review, not
measurements or proof of historical semantic change.

**Attested definitions** is a separate metric. It embeds the first definition
of the matching Romanian etymology and an attested meaning of the source word,
without headwords. A meaning quoted next to that etymon takes precedence over a
source dictionary definition. **Compare inputs** shows the actual texts. No
meaning is generated by an LLM, and missing meanings never fall back silently
to word distance. Coverage is shown in the interface.

Frozen BGE-M3 vectors test that `cuvânt/conventum` (~0.511) and
`bătrân/veteranus` (~0.408) have relatively large distances compared with
`casă/casa` (~0.114). These are reproducible regression examples, not a claim
that a generic embedder can reliably establish a semantic-change ranking.

## Tests and CI

```bash
rake test                      # every Python, JavaScript and Playwright test
rake ci                        # setup, all tests, Python distributions, Docker image
```

`rake test` installs the locked Playwright Chromium revision if needed, runs
Python units (including JavaScript ranking units), builds a test site from a
**temporary copy** of the versioned SQLite DB, and runs Playwright through a
plain static HTTP server. It does not download a Wiki dump or call Ollama.
Browser failures fail the Rake task; the browser suite is not optional.

Coverage includes visible etymologies, static SQLite/WASM loading, autocomplete,
`/` focus, both distance directions, scope isolation, form grouping, Unicode,
older spellings, model-cache resumption, malformed vectors, source provenance,
pinned-download checksums and reproducible imports. Explicit word fixtures
cover `conventum`, `frînghie`, `pămînt`, `mormînt`, `bătrîn`, `a cîștiga`, and
`rost`, including `cuvânt → conventum` and `bătrân → veteranus`.

## Docker deployment

```bash
rake ci                                                  # tests, then publishes mini.local:5000/editii/conventum:0.0.1-SNAPSHOT
# on srv2.local (only pulls the published image and starts it):
ssh srv2.local
docker compose -f ~/work/conventum/scripts/docker/conventum/docker-compose.yml up -d
```

The image is multi-stage (`Dockerfile`): it renders `dist/` from the committed
`data/dic.sqlite` and serves it with `python3 -m http.server` — no XML dump,
Ollama, or API is needed at runtime. `rake ci` builds it and pushes it to
`mini.local:5000/editii/conventum:<version>`; the compose runner on srv2.local
only pulls that image (single **prod** env, `scripts/docker/conventum/prod.env`
sets `HOST`/`PORT`) and publishes it on `srv2.local:8040`. Caddy runs on
**zmeu.local** and reads `scripts/caddy/Caddyfile` from the `scripts` repo: push,
`git pull` on zmeu.local, reload caddy. The Caddyfile maps
`scriptorium.ro/conventum` and `/conventum/*` to `srv2.local:8040`, stripping the
prefix so the site's relative asset URLs (`dic.sqlite`, `vendor/`) resolve.

## Profiles

Secret-free operation uses the built-in `local` profile, or `dic/<hostname>`
when that machine profile exists. `PROFILE=ci rake test` and task arguments such
as `rake 'run[yoga]'` use the pass store's
`${PASSWORD_STORE_DIR:-~/.password-store}/load-env.sh dic/<profile>`. Values stay
in the environment and are never logged or written to an env file. Tests use
temporary databases. No secrets are required for the static app.

## Attribution

The project's own code is released under the [Apache License 2.0](LICENSE).

Dictionary text comes from [Romanian Wiktionary](https://ro.wiktionary.org/) via
[Wikimedia XML dumps](https://dumps.wikimedia.org/rowiktionary/), under the source
pages' applicable [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
terms. Preserve the page/revision links and license when redistributing.
[BGE-M3](https://huggingface.co/BAAI/bge-m3) supplies the embeddings.
[sql.js](https://github.com/sql-js/sql.js) supplies the SQLite WebAssembly runtime
under its bundled MIT license. The npm lockfile pins its version and integrity.
