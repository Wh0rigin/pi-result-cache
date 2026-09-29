import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  DEFAULT_EXPIRE_AFTER_TOOL_RESULTS,
  deduplicateToolResults,
  type DeduplicationStats,
} from "./src/deduplicate.ts";

const COMMAND = "result-cache";

function expireAfterToolResultsFromEnv(): number {
  const configured = Number(process.env.PI_RESULT_CACHE_EXPIRE_AFTER);
  if (Number.isInteger(configured) && configured >= 1) return configured;

  // Keep the earlier max-gap variable working with its original `>` semantics.
  const legacyMaxGap = Number(process.env.PI_RESULT_CACHE_MAX_GAP);
  if (Number.isInteger(legacyMaxGap) && legacyMaxGap >= 0) return legacyMaxGap + 1;
  return DEFAULT_EXPIRE_AFTER_TOOL_RESULTS;
}

export default function resultCacheExtension(pi: ExtensionAPI) {
  let enabled = true;
  let contextRequests = 0;
  let duplicateOccurrences = 0;
  let expiredOccurrences = 0;
  let suppressedCharacters = 0;
  let estimatedTokensSuppressed = 0;
  const metricsFile = process.env.PI_RESULT_CACHE_STATS_FILE;
  let expireAfterToolResults = expireAfterToolResultsFromEnv();

  pi.on("context", (event) => {
    contextRequests += 1;
    if (!enabled) return;

    const result = deduplicateToolResults(event.messages, undefined, expireAfterToolResults);
    duplicateOccurrences += result.stats.duplicateOccurrences;
    expiredOccurrences += result.stats.expiredOccurrences;
    suppressedCharacters += result.stats.suppressedCharacters;
    estimatedTokensSuppressed += result.stats.estimatedTokensSuppressed;

    if (metricsFile) {
      try {
        // Only numeric counts are written; no prompt or tool output is persisted.
        appendFileSync(
          metricsFile,
          `${JSON.stringify({
            contextRequest: contextRequests,
            ...result.stats,
          } satisfies DeduplicationStats & { contextRequest: number })}\n`,
          "utf8",
        );
      } catch {
        // Metrics are optional and must never interrupt Pi's request path.
      }
    }

    return { messages: result.messages as typeof event.messages };
  });

  pi.registerCommand(COMMAND, {
    description: "Inspect or toggle repeated read/grep result suppression",
    handler: async (args, ctx) => {
      const [subcommand = "", rawValue] = args.trim().toLowerCase().split(/\s+/, 2);
      let configMessage: string | undefined;
      if (subcommand === "on") enabled = true;
      else if (subcommand === "off") enabled = false;
      else if (subcommand === "expire-after") {
        const configured = Number(rawValue);
        if (Number.isInteger(configured) && configured >= 1) expireAfterToolResults = configured;
        else configMessage = "expire-after requires a positive integer";
      } else if (subcommand === "reset") {
        contextRequests = 0;
        duplicateOccurrences = 0;
        expiredOccurrences = 0;
        suppressedCharacters = 0;
        estimatedTokensSuppressed = 0;
      }

      ctx.ui.notify(
        [
          ...(configMessage ? [configMessage] : []),
          `result-cache ${enabled ? "enabled" : "disabled"}`,
          `context requests: ${contextRequests}`,
          `duplicate results suppressed: ${duplicateOccurrences}`,
          `expire after ${expireAfterToolResults} intervening tool results; expired occurrences: ${expiredOccurrences}`,
          `estimated input tokens suppressed: ${estimatedTokensSuppressed}`,
          `characters suppressed: ${suppressedCharacters}`,
          "Usage: /result-cache [on|off|expire-after <n>|reset|status]",
        ].join("\n"),
        "info",
      );
    },
  });
}
