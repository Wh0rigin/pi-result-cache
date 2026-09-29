# pi-result-cache

Pi Coding Agent extension that suppresses exact duplicate `read` and `grep` results in later model requests.

## What it does

- Hashes eligible text-only `read` and `grep` results with SHA-256 while assembling each model request.
- Keeps the first copy verbatim and replaces later byte-identical copies with a short pointer to the earlier result.
- Only considers successful text results of at least 256 characters. Errors, image-containing results, other tools, and short output stay unchanged.
- Transforms request context only. It does not edit the persisted Pi session or skip the underlying file read/search.
- Uses no model calls, network access, or persistent cache database.

This can reduce repeated result text in later input contexts. It does not remove the tool call, its arguments, or the first full result. Provider prompt caching can affect billed cost, so compare Pi's input/cache usage fields rather than treating the extension's `chars / 4` estimate as a bill.

## Overall call flow

The diagram shows the complete path from the user prompt and tool execution through the `context` event to the model request, including the expiry rule that preserves and refreshes the full-result anchor after 15 intervening tool results by default.

![pi-result-cache overall call flow](assets/call-flow.svg)

The source diagram is [`docs/call-flow.dot`](docs/call-flow.dot).

## Install

From this project directory:

```bash
pi install -l .
```

Or try it for one session:

```bash
pi --extension ./index.ts
```

The command `/result-cache` shows counters. Use `/result-cache off`, `/result-cache on`, or `/result-cache reset` to control the current Pi process. `/result-cache expire-after 15` changes the expiry threshold for the current session.

The default expiry threshold is 15 intervening tool results. At the threshold, the old hash expires and the current full result becomes the new comparison anchor. All tool results count toward the gap, including tools other than `read` and `grep`. Set `PI_RESULT_CACHE_EXPIRE_AFTER` or use `/result-cache expire-after <count>` to change the threshold; values must be at least 1. The older `PI_RESULT_CACHE_MAX_GAP` variable remains supported.

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

The long-session experiment performs 4, 8, and 12 identical reads in one Pi process and also records the per-request growth trajectory:

```bash
npm run benchmark:long
npm run charts:long
```

See the [full long-session validation report](docs/long-session-validation.md) for all charts and findings.

Across three paired seeds, the mean prompt-token reduction grew with session length: 46.7% at 4 reads, 71.7% at 8 reads, and 79.6% at 12 reads. At 12 reads, baseline exact-answer accuracy was 2/3 versus 3/3 with deduplication; strict read-protocol adherence was 0/3 versus 3/3. See the report for the detailed data and limitations.

The distant-query experiment inserts 0, 6, or 12 distinct filler reads between two reads of the same target file:

```bash
npm run benchmark:distance
npm run charts:distance
```

It measures the effect when the repeated query is far apart in the conversation while the earlier result may still be retained in context. See the [full distant-query validation report](docs/distant-query-validation.md) for all charts.

Across three paired seeds per condition, mean prompt-token reduction was 21.5%, 4.4%, and 2.3% with 0, 6, and 12 filler reads. Both arms achieved 3/3 exact answers and 3/3 complete read sequences at every gap. As the gap grows, filler content dominates the prompt, so the fixed repeated target result represents a smaller share of total usage.

The expiry-boundary experiment compares 12 and 15 filler reads with the default expiry threshold of 15 intervening tool results:

```bash
npm run benchmark:expiry
npm run charts:expiry
```

See the [full distant-query expiry report](docs/distant-query-expiry-validation.md) for all charts.

In the measured runs, all three 12-filler cases hit and suppressed about 916 estimated tokens each. At 15 fillers, all three old results expired with zero hits and zero suppression; answer accuracy and read-protocol adherence were 3/3 in both arms. The 0.4% mean prompt-usage difference is run variation, not cache savings.

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
