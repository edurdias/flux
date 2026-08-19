// Runs the console's pure text helpers and prints their results as JSON, so
// a Python test can compare them against the server-side implementations
// they are supposed to mirror. Invoked by test_web_text.py; skipped when
// node is not installed.
import { byteSize, formatBytes, sliceBytes, truncate } from "../../../../flux/agents/web/text.js";

const cases = JSON.parse(process.argv[2]);
const results = cases.map(({ op, text, limit }) => {
  if (op === "truncate") return truncate(text, limit);
  if (op === "sliceBytes") return sliceBytes(text, limit);
  if (op === "byteSize") return byteSize(text);
  if (op === "formatSize") return formatBytes(byteSize(text));
  throw new Error(`unknown op: ${op}`);
});
process.stdout.write(JSON.stringify(results));
