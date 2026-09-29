import { spawnSync, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { DEFAULT_MAX_INTERVENING_TOOL_RESULTS } from "../src/deduplicate.ts";

type Arm = "baseline" | "dedupe";

interface PiUsage {
  input?: number;
  output?: number;
  cacheRead?: number;
  cacheWrite?: number;
}

interface ReadCall {
  toolName: string;
  args: Record<string, unknown>;
}

interface RequestUsage {
  request: number;
  inputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  promptTokensProcessed: number;
  outputTokens: number;
  totalTokensProcessed: number;
}

interface RunResult {
  arm: Arm;
  seed: number;
  gap: number;
  model: string;
  targetFile: string;
  fillerFiles: number;
  inputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  promptTokensProcessed: number;
  outputTokens: number;
  totalTokensProcessed: number;
  assistantRequests: number;
  wallSeconds: number;
  readCalls: number;
  targetReadCalls: number;
  fillerReadCalls: number;
  actualReadSequence: string[];
  expectedReadSequence: string[];
  protocolCompliant: boolean;
  expectedAnswer: string;
  answer: string;
  answerCorrect: boolean;
  duplicateOccurrencesSuppressed: number;
  expiredOccurrences: number;
  estimatedTokensSuppressed: number;
  suppressedCharacters: number;
  requestUsage: RequestUsage[];
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
const DEFAULT_RESULT_PATH = join(ROOT, "results", "distant-query-results.json");
const MODEL = process.env.PI_BENCH_MODEL ?? "cc-switch-packy-code/glm-5.3-flash";
const TARGET_ROW_COUNT = 72;
const TARGET_ROW = 53;
const FILLER_ROW_COUNT = 18;
const DEFAULT_GAPS = [0, 6, 12];

function parsePositiveIntFlag(name: string, fallback: number): number {
  const index = process.argv.indexOf(name);
  const raw = index >= 0 ? process.argv[index + 1] : undefined;
  const value = Number(raw);
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

function parseNonNegativeIntFlag(name: string, fallback: number): number {
  const index = process.argv.indexOf(name);
  if (index < 0) return fallback;
  const value = Number(process.argv[index + 1]);
  return Number.isInteger(value) && value >= 0 ? value : fallback;
}

function parseResultPath(): string {
  const index = process.argv.indexOf("--results");
  const value = index >= 0 ? process.argv[index + 1] : undefined;
  return value ? resolve(ROOT, value) : DEFAULT_RESULT_PATH;
}

function parseGaps(): number[] {
  const index = process.argv.indexOf("--gaps");
  if (index < 0) return DEFAULT_GAPS;
  const values = (process.argv[index + 1] ?? "")
    .split(",")
    .map((value) => Number(value.trim()))
    .filter((value) => Number.isInteger(value) && value >= 0 && value <= 20);
  const unique = [...new Set(values)].sort((a, b) => a - b);
  if (unique.length === 0) throw new Error("--gaps must contain integers from 0 through 20");
  return unique;
}

function createTargetFixture(seed: number): { text: string; answer: string } {
  const answer = `TOKEN-${createHash("sha256").update(`distance-target-${seed}`).digest("hex").slice(0, 10).toUpperCase()}`;
  const tags = ["amber", "birch", "cobalt", "delta", "elm", "fjord", "granite", "harbor"];
  const rows: string[] = [];
  for (let row = 1; row <= TARGET_ROW_COUNT; row += 1) {
    const amount = String((row * 7919 + seed * 104729) % 10000).padStart(4, "0");
    const tag = tags[(row * 7 + seed) % tags.length];
    const note = `sample-${String((row * 37 + seed) % 997).padStart(3, "0")}-record-${row}`;
    const marker = row === TARGET_ROW ? `|answer=${answer}` : "";
    rows.push(`R${String(row).padStart(3, "0")}|amount=${amount}|tag=${tag}|note=${note}${marker}`);
  }
  return { text: `${rows.join("\n")}\n`, answer };
}

function createFillerFixture(seed: number, index: number): string {
  const rows: string[] = [];
  for (let row = 1; row <= FILLER_ROW_COUNT; row += 1) {
    const digest = createHash("sha256").update(`distance-filler-${seed}-${index}-${row}`).digest("hex").slice(0, 16);
    rows.push(`G${String(index).padStart(3, "0")}-${String(row).padStart(2, "0")}|checkpoint=${digest}|seed=${seed}|gap-record`);
  }
  return `${rows.join("\n")}\n`;
}

function taskFor(gap: number): string {
  const fillerNames = Array.from({ length: gap }, (_, index) => `filler-${String(index + 1).padStart(3, "0")}.txt`);
  const fillerInstruction = gap === 0
    ? "There are no filler reads in the gap."
    : `Then read these filler files exactly once each, in this order: ${fillerNames.join(", ")}. ` +
      "Wait for one result before making the next filler read. Do not reread target.txt during this gap.";
  return (
    "This is a distant-query memory test. Use only the read tool. " +
    "First read target.txt exactly once and keep the answer in context without replying yet. " +
    `${fillerInstruction} ` +
    "After the gap, read target.txt exactly once again, using the same path and arguments as the first target read. " +
    `After the second target result, find row R${String(TARGET_ROW).padStart(3, "0")} and return only ` +
    "the value after 'answer=' on that row, as one line with no quotes or punctuation. " +
    "Do not guess, skip a file, add a tool, or claim a read without making the call."
  );
}

function expectedSequence(gap: number): string[] {
  return [
    "target.txt",
    ...Array.from({ length: gap }, (_, index) => `filler-${String(index + 1).padStart(3, "0")}.txt`),
    "target.txt",
  ];
}

function piEntryPoint(): string {
  const npmRoot = process.env.PI_BENCH_NPM_ROOT ?? (process.platform === "win32"
    ? join(process.env.APPDATA ?? "", "npm", "node_modules")
    : execFileSync("npm", ["root", "-g"], { encoding: "utf8" }).trim());
  const candidate = join(npmRoot, "@earendil-works", "pi-coding-agent", "dist", "bundle", "cli.js");
  if (!existsSync(candidate)) throw new Error(`Pi CLI entry point not found: ${candidate}`);
  return candidate;
}

function parseEvents(stdout: string): {
  usage: { input: number; output: number; cacheRead: number; cacheWrite: number; requests: number };
  requestUsage: RequestUsage[];
  readCalls: ReadCall[];
  finalText: string;
} {
  const usage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, requests: 0 };
  const requestUsage: RequestUsage[] = [];
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
    const input = u.input ?? 0;
    const cacheRead = u.cacheRead ?? 0;
    const cacheWrite = u.cacheWrite ?? 0;
    const output = u.output ?? 0;
    const prompt = input + cacheRead + cacheWrite;
    usage.requests += 1;
    usage.input += input;
    usage.output += output;
    usage.cacheRead += cacheRead;
    usage.cacheWrite += cacheWrite;
    requestUsage.push({
      request: usage.requests,
      inputTokens: input,
      cacheReadTokens: cacheRead,
      cacheWriteTokens: cacheWrite,
      promptTokensProcessed: prompt,
      outputTokens: output,
      totalTokensProcessed: prompt + output,
    });
  }

  return { usage, requestUsage, readCalls, finalText };
}

function normalizedReadPath(call: ReadCall, workspace: string): string | null {
  if (call.toolName !== "read") return null;
  const path = call.args.path;
  if (typeof path !== "string" || call.args.offset !== undefined || call.args.limit !== undefined) return null;
  return basename(resolve(workspace, path)).toLowerCase();
}

function checkProtocol(calls: ReadCall[], workspace: string, gap: number): {
  actual: string[];
  compliant: boolean;
  targetCount: number;
  fillerCount: number;
} {
  const actual = calls.map((call) => normalizedReadPath(call, workspace) ?? `${call.toolName}:invalid`);
  const expected = expectedSequence(gap);
  const compliant = actual.length === expected.length && actual.every((value, index) => value === expected[index]);
  const targetCount = actual.filter((value) => value === "target.txt").length;
  const fillerCount = actual.filter((value) => /^filler-\d{3}\.txt$/.test(value)).length;
  return { actual, compliant, targetCount, fillerCount };
}

function readPluginMetrics(path: string): {
  duplicateOccurrences: number;
  expiredOccurrences: number;
  estimatedTokensSuppressed: number;
  suppressedCharacters: number;
} {
  try {
    const lines = readFileSync(path, "utf8").split(/\r?\n/).filter(Boolean);
    return lines.reduce(
      (sum, line) => {
        const stats = JSON.parse(line) as {
          duplicateOccurrences?: number;
          expiredOccurrences?: number;
          estimatedTokensSuppressed?: number;
          suppressedCharacters?: number;
        };
        sum.duplicateOccurrences += stats.duplicateOccurrences ?? 0;
        sum.expiredOccurrences += stats.expiredOccurrences ?? 0;
        sum.estimatedTokensSuppressed += stats.estimatedTokensSuppressed ?? 0;
        sum.suppressedCharacters += stats.suppressedCharacters ?? 0;
        return sum;
      },
      { duplicateOccurrences: 0, expiredOccurrences: 0, estimatedTokensSuppressed: 0, suppressedCharacters: 0 },
    );
  } catch {
    return { duplicateOccurrences: 0, expiredOccurrences: 0, estimatedTokensSuppressed: 0, suppressedCharacters: 0 };
  }
}

function runOne(
  arm: Arm,
  seed: number,
  gap: number,
  maxGap: number,
  workspace: string,
  expectedAnswer: string,
): RunResult {
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
  args.push("--", taskFor(gap));

  const started = Date.now();
  const result = spawnSync(process.execPath, [piEntryPoint(), ...args], {
    cwd: workspace,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    timeout: 8 * 60 * 1000,
    env: {
      ...process.env,
      PI_SKIP_VERSION_CHECK: "1",
      ...(arm === "dedupe" ? {
        PI_RESULT_CACHE_STATS_FILE: metricsPath,
        PI_RESULT_CACHE_MAX_GAP: String(maxGap),
      } : {}),
    },
  });
  const wallSeconds = (Date.now() - started) / 1000;

  if (result.error) throw result.error;
  const parsed = parseEvents(result.stdout ?? "");
  const metrics = arm === "dedupe"
    ? readPluginMetrics(metricsPath)
    : { duplicateOccurrences: 0, expiredOccurrences: 0, estimatedTokensSuppressed: 0, suppressedCharacters: 0 };
  const protocol = checkProtocol(parsed.readCalls, workspace, gap);
  const promptTokensProcessed = parsed.usage.input + parsed.usage.cacheRead + parsed.usage.cacheWrite;
  const answer = parsed.finalText.trim();
  const error = result.status === 0 ? undefined : `pi exited ${result.status}; final answer or usage may be incomplete`;

  return {
    arm,
    seed,
    gap,
    model: MODEL,
    targetFile: "target.txt",
    fillerFiles: gap,
    inputTokens: parsed.usage.input,
    cacheReadTokens: parsed.usage.cacheRead,
    cacheWriteTokens: parsed.usage.cacheWrite,
    promptTokensProcessed,
    outputTokens: parsed.usage.output,
    totalTokensProcessed: promptTokensProcessed + parsed.usage.output,
    assistantRequests: parsed.usage.requests,
    wallSeconds,
    readCalls: parsed.readCalls.length,
    targetReadCalls: protocol.targetCount,
    fillerReadCalls: protocol.fillerCount,
    actualReadSequence: protocol.actual,
    expectedReadSequence: expectedSequence(gap),
    protocolCompliant: protocol.compliant,
    expectedAnswer,
    answer,
    answerCorrect: answer === expectedAnswer,
    duplicateOccurrencesSuppressed: metrics.duplicateOccurrences,
    expiredOccurrences: metrics.expiredOccurrences,
    estimatedTokensSuppressed: metrics.estimatedTokensSuppressed,
    suppressedCharacters: metrics.suppressedCharacters,
    requestUsage: parsed.requestUsage,
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
  const gaps = parseGaps();
  const seeds = parsePositiveIntFlag("--seeds", 3);
  const seedStart = parsePositiveIntFlag("--seed-start", 1);
  const maxGap = parseNonNegativeIntFlag("--max-gap", DEFAULT_MAX_INTERVENING_TOOL_RESULTS);
  const resultPath = parseResultPath();
  const appendResults = process.argv.includes("--append");
  if (!existsSync(EXTENSION)) throw new Error(`Extension not found: ${EXTENSION}`);

  console.log(`Pi distant-query A/B benchmark: gaps=${gaps.join(",")} seeds=${seeds} max-intervening-tool-results=${maxGap}`);
  console.log(`model=${MODEL}; target=${TARGET_ROW_COUNT} rows; gap files=${FILLER_ROW_COUNT} rows each`);

  const results: RunResult[] = [];
  for (const gap of gaps) {
    for (let offset = 0; offset < seeds; offset += 1) {
      const seed = seedStart + offset;
      const target = createTargetFixture(seed);
      const workspace = mkdtempSync(join(tmpdir(), `pi-result-cache-distance-${gap}-${seed}-`));
      writeFileSync(join(workspace, "target.txt"), target.text, "utf8");
      for (let index = 1; index <= gap; index += 1) {
        writeFileSync(join(workspace, `filler-${String(index).padStart(3, "0")}.txt`), createFillerFixture(seed, index), "utf8");
      }
      const order: Arm[] = (seed + gap) % 2 === 0 ? ["dedupe", "baseline"] : ["baseline", "dedupe"];

      try {
        for (const arm of order) {
          process.stdout.write(`[gap ${gap} seed ${seed}] ${arm} ... `);
          const run = runOne(arm, seed, gap, maxGap, workspace, target.answer);
          results.push(run);
          console.log(
            `${run.wallSeconds.toFixed(1)}s | prompt ${run.promptTokensProcessed} | requests ${run.assistantRequests} | ` +
            `target ${run.targetReadCalls}/2 | filler ${run.fillerReadCalls}/${gap} | ` +
            `${run.answerCorrect ? "correct" : "wrong"} | ${run.protocolCompliant ? "protocol" : "protocol-drift"}` +
            (arm === "dedupe" ? ` | suppressed≈${run.estimatedTokensSuppressed} tokens | expired=${run.expiredOccurrences}` : "") +
            (run.error ? ` | ${run.error}` : ""),
          );
        }
      } catch (error) {
        console.log(`ERROR ${(error as Error).message}`);
        for (const arm of order) {
          if (!results.some((run) => run.seed === seed && run.gap === gap && run.arm === arm)) {
            results.push({
              arm,
              seed,
              gap,
              model: MODEL,
              targetFile: "target.txt",
              fillerFiles: gap,
              inputTokens: 0,
              cacheReadTokens: 0,
              cacheWriteTokens: 0,
              promptTokensProcessed: 0,
              outputTokens: 0,
              totalTokensProcessed: 0,
              assistantRequests: 0,
              wallSeconds: 0,
              readCalls: 0,
              targetReadCalls: 0,
              fillerReadCalls: 0,
              actualReadSequence: [],
              expectedReadSequence: expectedSequence(gap),
              protocolCompliant: false,
              expectedAnswer: target.answer,
              answer: "",
              answerCorrect: false,
              duplicateOccurrencesSuppressed: 0,
              expiredOccurrences: 0,
              estimatedTokensSuppressed: 0,
              suppressedCharacters: 0,
              requestUsage: [],
              error: (error as Error).message,
            });
          }
        }
      } finally {
        safeRemoveWorkspace(workspace);
      }
    }
  }

  mkdirSync(dirname(resultPath), { recursive: true });
  let combinedResults = results;
  if (appendResults && existsSync(resultPath)) {
    const previous = JSON.parse(readFileSync(resultPath, "utf8")) as { results?: RunResult[] };
    const keys = new Set(results.map((run) => `${run.gap}:${run.seed}:${run.arm}`));
    combinedResults = [
      ...(previous.results ?? []).filter((run) => !keys.has(`${run.gap}:${run.seed}:${run.arm}`)),
      ...results,
    ];
  }
  combinedResults.sort((a, b) => a.gap - b.gap || a.seed - b.seed || a.arm.localeCompare(b.arm));
  const report = {
    generatedAt: new Date().toISOString(),
    model: MODEL,
    piVersion: "0.87.1",
    repetitionsPerCondition: seeds,
    gaps,
    maxInterveningToolResults: maxGap,
    fixture: { targetRows: TARGET_ROW_COUNT, targetRow: TARGET_ROW, fillerRows: FILLER_ROW_COUNT },
    task: "Read target.txt, read distinct filler files during an intervening gap, then read target.txt again and return its target token.",
    arms: ["baseline (extension disabled)", "dedupe (request-local context deduplication)"],
    results: combinedResults,
  };
  writeFileSync(resultPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");

  console.log(`\nSaved aggregate results to ${resultPath}`);
  for (const run of report.results) {
    console.log(
      `${run.arm.padEnd(8)} gap=${run.gap} seed=${run.seed} prompt=${run.promptTokensProcessed} ` +
      `requests=${run.assistantRequests} correct=${run.answerCorrect} protocol=${run.protocolCompliant} ` +
      `target=${run.targetReadCalls} filler=${run.fillerReadCalls} hits=${run.duplicateOccurrencesSuppressed} expired=${run.expiredOccurrences}`,
    );
  }
}

main().catch((error) => {
  console.error(`distant-query benchmark failed: ${(error as Error).message}`);
  process.exitCode = 1;
});
