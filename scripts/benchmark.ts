import { spawnSync, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

type Arm = "baseline" | "dedupe";

interface PiUsage {
  input?: number;
  output?: number;
  cacheRead?: number;
  cacheWrite?: number;
  totalTokens?: number;
}

interface ReadCall {
  toolName: string;
  args: Record<string, unknown>;
}

interface RunResult {
  arm: Arm;
  seed: number;
  model: string;
  inputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  promptTokensProcessed: number;
  outputTokens: number;
  totalTokensProcessed: number;
  assistantRequests: number;
  wallSeconds: number;
  readCalls: number;
  sameReadArguments: boolean;
  protocolCompliant: boolean;
  expectedAnswer: string;
  answer: string;
  answerCorrect: boolean;
  duplicateOccurrencesSuppressed: number;
  estimatedTokensSuppressed: number;
  suppressedCharacters: number;
  error?: string;
}

interface JsonEvent {
  type?: string;
  toolName?: string;
  args?: Record<string, unknown>;
  message?: {
    role?: string;
    content?: Array<{ type?: string; text?: string }>;
    usage?: PiUsage;
  };
}

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const EXTENSION = join(ROOT, "index.ts");
const RESULT_PATH = join(ROOT, "results", "benchmark-results.json");
const MODEL = process.env.PI_BENCH_MODEL ?? "cc-switch-packy-code/glm-5.3-flash";
const ROW_COUNT = 180;
const TARGET_ROW = 137;
const READS_PER_RUN = 4;
const TASK =
  `Use the read tool to read benchmark-data.txt exactly ${READS_PER_RUN} times in ` +
  "four separate, strictly sequential tool calls. Use the exact same path and arguments each time. " +
  "Wait for one read result before making the next read call. Do not use any other tool. " +
  `After the fourth result, find row R${String(TARGET_ROW).padStart(3, "0")} and return only ` +
  "the value after 'answer=' on that row, as one line with no quotes or punctuation. " +
  "Do not guess or claim a read without making the tool call.";

function parsePositiveIntFlag(name: string, fallback: number): number {
  const index = process.argv.indexOf(name);
  const raw = index >= 0 ? process.argv[index + 1] : undefined;
  const value = Number(raw);
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

function createFixture(seed: number): { text: string; answer: string } {
  const answer = `TOKEN-${createHash("sha256").update(`fixture-${seed}`).digest("hex").slice(0, 10).toUpperCase()}`;
  const tags = ["amber", "birch", "cobalt", "delta", "elm", "fjord", "granite", "harbor"];
  const rows: string[] = [];

  for (let row = 1; row <= ROW_COUNT; row += 1) {
    const amount = String((row * 7919 + seed * 104729) % 10000).padStart(4, "0");
    const tag = tags[(row * 7 + seed) % tags.length];
    const note = `sample-${String((row * 37 + seed) % 997).padStart(3, "0")}-record-${row}`;
    const marker = row === TARGET_ROW ? `|answer=${answer}` : "";
    rows.push(`R${String(row).padStart(3, "0")}|amount=${amount}|tag=${tag}|note=${note}${marker}`);
  }

  return { text: `${rows.join("\n")}\n`, answer };
}

function piEntryPoint(): string {
  // Windows commonly exposes npm as npm.ps1, which cannot be spawned directly
  // by Node. Its global package root has a stable location under APPDATA.
  const npmRoot = process.env.PI_BENCH_NPM_ROOT ?? (process.platform === "win32"
    ? join(process.env.APPDATA ?? "", "npm", "node_modules")
    : execFileSync("npm", ["root", "-g"], { encoding: "utf8" }).trim());
  const candidate = join(npmRoot, "@earendil-works", "pi-coding-agent", "dist", "bundle", "cli.js");
  if (!existsSync(candidate)) throw new Error(`Pi CLI entry point not found: ${candidate}`);
  return candidate;
}

function parseEvents(stdout: string): {
  usage: {
    input: number;
    output: number;
    cacheRead: number;
    cacheWrite: number;
    total: number;
    requests: number;
  };
  readCalls: ReadCall[];
  finalText: string;
} {
  const usage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0, requests: 0 };
  const readCalls: ReadCall[] = [];
  let finalText = "";

  for (const line of stdout.split(/\r?\n/)) {
    if (!line.trim()) continue;
    let event: JsonEvent;
    try {
      event = JSON.parse(line) as JsonEvent;
    } catch {
      continue;
    }

    if (event.type === "tool_execution_start" && typeof event.toolName === "string") {
      readCalls.push({ toolName: event.toolName, args: event.args ?? {} });
    }
    if (event.type !== "message_end" || event.message?.role !== "assistant") continue;

    const message = event.message;
    const text = (message.content ?? [])
      .filter((block) => block.type === "text" && typeof block.text === "string")
      .map((block) => block.text ?? "")
      .join("\n");
    if (text.trim()) finalText = text.trim();

    const u = message.usage;
    if (!u) continue;
    usage.requests += 1;
    usage.input += u.input ?? 0;
    usage.output += u.output ?? 0;
    usage.cacheRead += u.cacheRead ?? 0;
    usage.cacheWrite += u.cacheWrite ?? 0;
    usage.total += u.totalTokens ?? 0;
  }

  return { usage, readCalls, finalText };
}

function sameFileReadArguments(calls: ReadCall[], workspace: string): boolean {
  const read = calls.filter((call) => call.toolName === "read");
  if (read.length !== READS_PER_RUN) return false;

  const normalized = read.map((call) => {
    const path = call.args.path;
    if (typeof path !== "string") return null;
    const resolvedPath = resolve(workspace, path).toLowerCase();
    if (basename(resolvedPath) !== "benchmark-data.txt" || call.args.offset !== undefined || call.args.limit !== undefined) {
      return null;
    }
    return JSON.stringify({ path: resolvedPath, offset: null, limit: null });
  });
  return normalized.every((value) => value !== null && value === normalized[0]);
}

function readPluginMetrics(path: string): {
  duplicateOccurrences: number;
  estimatedTokensSuppressed: number;
  suppressedCharacters: number;
} {
  try {
    const lines = readFileSync(path, "utf8").split(/\r?\n/).filter(Boolean);
    return lines.reduce(
      (sum, line) => {
        const stats = JSON.parse(line) as {
          duplicateOccurrences?: number;
          estimatedTokensSuppressed?: number;
          suppressedCharacters?: number;
        };
        sum.duplicateOccurrences += stats.duplicateOccurrences ?? 0;
        sum.estimatedTokensSuppressed += stats.estimatedTokensSuppressed ?? 0;
        sum.suppressedCharacters += stats.suppressedCharacters ?? 0;
        return sum;
      },
      { duplicateOccurrences: 0, estimatedTokensSuppressed: 0, suppressedCharacters: 0 },
    );
  } catch {
    return { duplicateOccurrences: 0, estimatedTokensSuppressed: 0, suppressedCharacters: 0 };
  }
}

function runOne(arm: Arm, seed: number, workspace: string, expectedAnswer: string): RunResult {
  const metricsPath = join(workspace, "result-cache-metrics.jsonl");
  const args = [
    "--mode", "json",
    "--print",
    "--no-session",
    "--no-extensions",
    "--no-context-files",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--thinking", "low",
    "--model", MODEL,
    "--tools", "read",
  ];
  if (arm === "dedupe") args.push("--extension", EXTENSION);
  args.push("--", TASK);

  const started = Date.now();
  const result = spawnSync(process.execPath, [piEntryPoint(), ...args], {
    cwd: workspace,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    timeout: 4 * 60 * 1000,
    env: {
      ...process.env,
      PI_SKIP_VERSION_CHECK: "1",
      ...(arm === "dedupe" ? { PI_RESULT_CACHE_STATS_FILE: metricsPath } : {}),
    },
  });
  const wallSeconds = (Date.now() - started) / 1000;

  if (result.error) throw result.error;
  const parsed = parseEvents(result.stdout ?? "");
  const metrics = arm === "dedupe"
    ? readPluginMetrics(metricsPath)
    : { duplicateOccurrences: 0, estimatedTokensSuppressed: 0, suppressedCharacters: 0 };
  const inputTokens = parsed.usage.input;
  const promptTokensProcessed = parsed.usage.input + parsed.usage.cacheRead + parsed.usage.cacheWrite;
  const sameReadArguments = sameFileReadArguments(parsed.readCalls, workspace);
  const protocolCompliant = sameReadArguments && parsed.readCalls.every((call) => call.toolName === "read");
  const answer = parsed.finalText.trim();
  const error = result.status === 0 ? undefined : `pi exited ${result.status}; final answer or usage may be incomplete`;

  return {
    arm,
    seed,
    model: MODEL,
    inputTokens,
    cacheReadTokens: parsed.usage.cacheRead,
    cacheWriteTokens: parsed.usage.cacheWrite,
    promptTokensProcessed,
    outputTokens: parsed.usage.output,
    totalTokensProcessed: promptTokensProcessed + parsed.usage.output,
    assistantRequests: parsed.usage.requests,
    wallSeconds,
    readCalls: parsed.readCalls.filter((call) => call.toolName === "read").length,
    sameReadArguments,
    protocolCompliant,
    expectedAnswer,
    answer,
    answerCorrect: answer === expectedAnswer,
    duplicateOccurrencesSuppressed: metrics.duplicateOccurrences,
    estimatedTokensSuppressed: metrics.estimatedTokensSuppressed,
    suppressedCharacters: metrics.suppressedCharacters,
    ...(error ? { error } : {}),
  };
}

function safeRemoveWorkspace(workspace: string): void {
  const tempRoot = resolve(tmpdir()).toLowerCase();
  const target = resolve(workspace).toLowerCase();
  if (target.startsWith(`${tempRoot}${process.platform === "win32" ? "\\" : "/"}`) && target !== tempRoot) {
    rmSync(workspace, { recursive: true, force: true });
  }
}

async function main(): Promise<void> {
  const seeds = parsePositiveIntFlag("--seeds", 5);
  const seedStart = parsePositiveIntFlag("--seed-start", 1);
  const appendResults = process.argv.includes("--append");
  if (!existsSync(EXTENSION)) throw new Error(`Extension not found: ${EXTENSION}`);

  console.log(`Pi result-cache A/B benchmark: ${seeds} paired repetitions`);
  console.log(`model=${MODEL}; task=4 identical full-file reads; cache mode suppresses repeated context payloads`);

  const results: RunResult[] = [];
  for (let offset = 0; offset < seeds; offset += 1) {
    const seed = seedStart + offset;
    const fixture = createFixture(seed);
    const workspace = mkdtempSync(join(tmpdir(), `pi-result-cache-${seed}-`));
    writeFileSync(join(workspace, "benchmark-data.txt"), fixture.text, "utf8");

    const order: Arm[] = seed % 2 === 1 ? ["baseline", "dedupe"] : ["dedupe", "baseline"];
    try {
      for (const arm of order) {
        process.stdout.write(`[seed ${seed}] ${arm} ... `);
        const run = runOne(arm, seed, workspace, fixture.answer);
        results.push(run);
        console.log(
          `${run.wallSeconds.toFixed(1)}s | prompt ${run.promptTokensProcessed} | out ${run.outputTokens} | ` +
          `reads ${run.readCalls}/${READS_PER_RUN} | ${run.answerCorrect ? "correct" : "wrong"}` +
          (arm === "dedupe" ? ` | suppressed≈${run.estimatedTokensSuppressed} tokens` : "") +
          (run.error ? ` | ${run.error}` : ""),
        );
      }
    } catch (error) {
      console.log(`ERROR ${(error as Error).message}`);
      for (const arm of order) {
        if (!results.some((run) => run.seed === seed && run.arm === arm)) {
          results.push({
            arm,
            seed,
            model: MODEL,
            inputTokens: 0,
            cacheReadTokens: 0,
            cacheWriteTokens: 0,
            promptTokensProcessed: 0,
            outputTokens: 0,
            totalTokensProcessed: 0,
            assistantRequests: 0,
            wallSeconds: 0,
            readCalls: 0,
            sameReadArguments: false,
            protocolCompliant: false,
            expectedAnswer: fixture.answer,
            answer: "",
            answerCorrect: false,
            duplicateOccurrencesSuppressed: 0,
            estimatedTokensSuppressed: 0,
            suppressedCharacters: 0,
            error: (error as Error).message,
          });
        }
      }
    } finally {
      safeRemoveWorkspace(workspace);
    }
  }

  mkdirSync(dirname(RESULT_PATH), { recursive: true });
  let combinedResults = results;
  if (appendResults && existsSync(RESULT_PATH)) {
    try {
      const previous = JSON.parse(readFileSync(RESULT_PATH, "utf8")) as { results?: RunResult[] };
      const keys = new Set(results.map((run) => `${run.seed}:${run.arm}`));
      combinedResults = [
        ...(previous.results ?? []).filter((run) => !keys.has(`${run.seed}:${run.arm}`)),
        ...results,
      ];
    } catch {
      throw new Error("Cannot append: existing benchmark-results.json is not valid JSON");
    }
  }
  combinedResults.sort((a, b) => a.seed - b.seed || a.arm.localeCompare(b.arm));
  const uniqueSeeds = new Set(combinedResults.map((run) => run.seed));
  const report = {
    generatedAt: new Date().toISOString(),
    model: MODEL,
    repetitionsPerArm: uniqueSeeds.size,
    task: {
      rowCount: ROW_COUNT,
      targetRow: TARGET_ROW,
      identicalReadsPerRun: READS_PER_RUN,
      arms: ["baseline (extension disabled)", "dedupe (request-local context deduplication)"],
    },
    results: combinedResults,
  };
  writeFileSync(RESULT_PATH, `${JSON.stringify(report, null, 2)}\n`, "utf8");

  console.log(`\nSaved aggregate results to ${RESULT_PATH}`);
  for (const run of report.results) {
    console.log(
      `${run.arm.padEnd(8)} seed=${run.seed} prompt=${run.promptTokensProcessed} ` +
      `out=${run.outputTokens} correct=${run.answerCorrect} protocol=${run.protocolCompliant} ` +
      `reads=${run.readCalls} hits=${run.duplicateOccurrencesSuppressed}`,
    );
  }
}

main().catch((error) => {
  console.error(`benchmark failed: ${(error as Error).message}`);
  process.exitCode = 1;
});
