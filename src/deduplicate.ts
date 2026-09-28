import { createHash } from "node:crypto";

const ELIGIBLE_TOOLS = new Set(["read", "grep"]);

/** Avoid replacing small results where the reference would save little or nothing. */
export const DEFAULT_MIN_RESULT_CHARS = 256;

export interface DeduplicationStats {
  eligibleResults: number;
  uniqueResults: number;
  duplicateOccurrences: number;
  suppressedCharacters: number;
  estimatedTokensSuppressed: number;
}

export interface DeduplicationResult<T> {
  messages: T[];
  stats: DeduplicationStats;
}

interface TextBlock {
  type: "text";
  text: string;
  [key: string]: unknown;
}

interface FirstOccurrence {
  toolCallId: string;
  index: number;
}

/**
 * Replace exact, repeated read/grep payloads in a request-local Pi transcript.
 * The first result remains verbatim. Later copies become short pointers to it.
 * The input array and the persisted Pi session are never mutated.
 */
export function deduplicateToolResults<T>(
  messages: readonly T[],
  minResultChars = DEFAULT_MIN_RESULT_CHARS,
): DeduplicationResult<T> {
  const output = [...messages];
  const seen = new Map<string, FirstOccurrence>();
  const stats: DeduplicationStats = {
    eligibleResults: 0,
    uniqueResults: 0,
    duplicateOccurrences: 0,
    suppressedCharacters: 0,
    estimatedTokensSuppressed: 0,
  };

  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index] as unknown as Record<string, unknown>;
    if (
      message.role !== "toolResult" ||
      typeof message.toolName !== "string" ||
      !ELIGIBLE_TOOLS.has(message.toolName) ||
      message.isError === true
    ) {
      continue;
    }

    const content = message.content;
    if (!Array.isArray(content) || content.length === 0) continue;
    if (
      !content.every(
        (block): block is TextBlock =>
          block !== null &&
          typeof block === "object" &&
          (block as Record<string, unknown>).type === "text" &&
          typeof (block as Record<string, unknown>).text === "string",
      )
    ) {
      // Keep mixed text/image results intact; the text hash would not cover them.
      continue;
    }

    const textLength = content.reduce((sum, block) => sum + block.text.length, 0);
    if (textLength < minResultChars) continue;
    stats.eligibleResults += 1;

    const fingerprint = createHash("sha256")
      .update(message.toolName)
      .update("\0")
      .update(JSON.stringify(content))
      .digest("hex");
    const previous = seen.get(fingerprint);

    if (!previous) {
      seen.set(fingerprint, {
        toolCallId: typeof message.toolCallId === "string" ? message.toolCallId : "unknown",
        index,
      });
      stats.uniqueResults += 1;
      continue;
    }

    const marker =
      `[pi-result-cache] Exact duplicate of an earlier ${message.toolName} result ` +
      `(call ${previous.toolCallId}, transcript item ${previous.index + 1}, ` +
      `SHA-256 ${fingerprint.slice(0, 12)}). The original result is already present above.`;
    const markerContent = [{ type: "text", text: marker }];
    const saved = Math.max(0, textLength - marker.length);

    output[index] = {
      ...(messages[index] as object),
      content: markerContent,
    } as T;
    stats.duplicateOccurrences += 1;
    stats.suppressedCharacters += saved;
  }

  stats.estimatedTokensSuppressed = Math.floor(stats.suppressedCharacters / 4);
  return { messages: output, stats };
}
