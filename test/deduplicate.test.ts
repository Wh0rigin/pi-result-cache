import assert from "node:assert/strict";
import test from "node:test";
import { deduplicateToolResults } from "../src/deduplicate.ts";

function toolResult(
  toolName: string,
  toolCallId: string,
  text: string,
  extras: Record<string, unknown> = {},
) {
  return {
    role: "toolResult",
    toolName,
    toolCallId,
    isError: false,
    content: [{ type: "text", text }],
    ...extras,
  };
}

test("keeps the first read verbatim and replaces exact duplicates with a pointer", () => {
  const originalText = "R001 | repeated fixture body\n".repeat(24);
  const input = [toolResult("read", "call-a", originalText), toolResult("read", "call-b", originalText)];
  const before = structuredClone(input);

  const result = deduplicateToolResults(input);

  assert.deepEqual(input, before, "request-local processing must not mutate the session messages");
  assert.equal(result.stats.duplicateOccurrences, 1);
  assert.equal(result.messages[0], input[0], "the first occurrence stays untouched");
  const replacement = result.messages[1].content[0].text;
  assert.match(replacement, /Exact duplicate of an earlier read result/);
  assert.match(replacement, /call call-a/);
  assert.ok(result.stats.estimatedTokensSuppressed > 0);
});

test("deduplicates grep output, but keeps unrelated tools and short results", () => {
  const longText = "match in src/file.ts: a long repeated search output\n".repeat(12);
  const result = deduplicateToolResults([
    toolResult("grep", "g1", longText),
    toolResult("grep", "g2", longText),
    toolResult("bash", "b1", longText),
    toolResult("read", "r1", "short"),
  ]);

  assert.equal(result.stats.duplicateOccurrences, 1);
  assert.equal(result.messages[1].content[0].text.startsWith("[pi-result-cache]"), true);
  assert.equal(result.messages[2].content[0].text, longText);
  assert.equal(result.messages[3].content[0].text, "short");
});

test("does not collapse failed or mixed image results", () => {
  const same = "large output line\n".repeat(30);
  const result = deduplicateToolResults([
    toolResult("read", "err1", same, { isError: true }),
    toolResult("read", "err2", same, { isError: true }),
    {
      role: "toolResult",
      toolName: "read",
      toolCallId: "img1",
      isError: false,
      content: [{ type: "text", text: same }, { type: "image", data: "abc", mimeType: "image/png" }],
    },
    {
      role: "toolResult",
      toolName: "read",
      toolCallId: "img2",
      isError: false,
      content: [{ type: "text", text: same }, { type: "image", data: "abc", mimeType: "image/png" }],
    },
  ]);

  assert.equal(result.stats.duplicateOccurrences, 0);
  assert.equal(result.messages[0].content[0].text, same);
  assert.equal(result.messages[3].content[0].text, same);
});

test("only exact byte-identical output from the same tool matches", () => {
  const body = "same payload\n".repeat(30);
  const result = deduplicateToolResults([
    toolResult("read", "r1", body),
    toolResult("read", "r2", `${body}different`),
    toolResult("grep", "g1", body),
  ]);

  assert.equal(result.stats.duplicateOccurrences, 0);
  assert.equal(result.messages[1].content[0].text, `${body}different`);
  assert.equal(result.messages[2].content[0].text, body);
});
