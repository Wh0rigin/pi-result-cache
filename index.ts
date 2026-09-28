import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { deduplicateToolResults, type DeduplicationStats } from "./src/deduplicate.ts";

const COMMAND = "result-cache";

export default function resultCacheExtension(pi: ExtensionAPI) {
  let enabled = true;
  let contextRequests = 0;
  let duplicateOccurrences = 0;
  let suppressedCharacters = 0;
  let estimatedTokensSuppressed = 0;
  const metricsFile = process.env.PI_RESULT_CACHE_STATS_FILE;

  pi.on("context", (event) => {
    contextRequests += 1;
    if (!enabled) return;

    const result = deduplicateToolResults(event.messages);
    duplicateOccurrences += result.stats.duplicateOccurrences;
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
      const subcommand = args.trim().toLowerCase();
      if (subcommand === "on") enabled = true;
      else if (subcommand === "off") enabled = false;
      else if (subcommand === "reset") {
        contextRequests = 0;
        duplicateOccurrences = 0;
        suppressedCharacters = 0;
        estimatedTokensSuppressed = 0;
      }

      ctx.ui.notify(
        [
          `result-cache ${enabled ? "enabled" : "disabled"}`,
          `context requests: ${contextRequests}`,
          `duplicate results suppressed: ${duplicateOccurrences}`,
          `estimated input tokens suppressed: ${estimatedTokensSuppressed}`,
          `characters suppressed: ${suppressedCharacters}`,
          "Usage: /result-cache [on|off|reset|status]",
        ].join("\n"),
        "info",
      );
    },
  });
}
