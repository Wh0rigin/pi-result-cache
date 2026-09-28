# pi-result-cache

Pi Coding Agent extension that suppresses exact duplicate `read` and `grep` results in later model requests.

## What it does

- Hashes eligible text-only `read` and `grep` results with SHA-256 while assembling each model request.
- Keeps the first copy verbatim and replaces later byte-identical copies with a short pointer to the earlier result.
- Only considers successful text results of at least 256 characters. Errors, image-containing results, other tools, and short output stay unchanged.
- Transforms request context only. It does not edit the persisted Pi session or skip the underlying file read/search.
- Uses no model calls, network access, or persistent cache database.

This can reduce repeated result text in later input contexts. It does not remove the tool call, its arguments, or the first full result. Provider prompt caching can affect billed cost, so compare Pi's input/cache usage fields rather than treating the extension's `chars / 4` estimate as a bill.

## Install

From this project directory:

```bash
pi install -l .
```

Or try it for one session:

```bash
pi --extension ./index.ts
```

The command `/result-cache` shows counters. Use `/result-cache off`, `/result-cache on`, or `/result-cache reset` to control the current Pi process.

## Verify locally

```bash
npm test
```

## Pi A/B experiment

The benchmark runs the same task with the extension disabled and enabled. Each run uses a fresh random target in a 180-line file and asks Pi to read the exact same file four times before returning the target. The script validates both exact answer correctness and the four identical tool arguments. Pi usage comes from `message_end.usage` in JSON mode.

```bash
npm run benchmark
python scripts/charts.py
```

Override the model or number of paired runs:

```powershell
$env:PI_BENCH_MODEL = "cc-switch-packy-code/glm-5.3-flash"
npm run benchmark -- --seeds 5
```

The experiment records only usage counts, tool-call arguments needed for protocol checks, the expected/returned short token, and numeric extension metrics. Raw Pi transcripts are not written to disk. Results go to `results/benchmark-results.json`; charts and the generated validation write-up go to `assets/` and `docs/validation.md`.

### Initial measured result

On 2026-09-28, five paired runs with Pi 0.87.1 and `cc-switch-packy-code/glm-5.3-flash` processed an average of 47,283 prompt tokens per baseline run and 21,830 with result deduplication, a 53.8% reduction in this repeated-read task. Exact-answer accuracy and the four-read protocol were both 5/5 in each arm. This is a small, controlled experiment; it does not establish the average savings or accuracy effect on general coding tasks.

![Pi prompt-token comparison](assets/input-token-comparison.png)

![Answer accuracy and repeated-read protocol](assets/accuracy-and-protocol.png)

See [the full validation report](docs/validation.md) for all five plots, per-run measurements, and limitations.

## Repository layout

```text
index.ts                  Pi extension entry point and command
src/deduplicate.ts        SHA-256 result matching and request-local replacement
test/                     Unit tests for equality, eligibility, and safety boundaries
scripts/benchmark.ts      Paired real-Pi A/B experiment
scripts/charts.py         Reproducible comparison plots and validation report
results/                  Aggregate run data (no raw model transcripts)
assets/                   Generated charts
docs/validation.md        Experiment method, findings, charts, and limitations
```

Python chart dependencies are listed in `requirements.txt`.
