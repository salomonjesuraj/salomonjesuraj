import { readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { spawnSync } from "node:child_process";

const root = join(process.cwd(), "public", "js");
const files = [];

function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      if (name === "vendor") continue;
      walk(path);
      continue;
    }
    if (name.endsWith(".js")) files.push(path);
  }
}

walk(root);

const failures = [];
for (const file of files) {
  const result = spawnSync(process.execPath, ["--check", file], {
    encoding: "utf8",
  });
  if (result.status !== 0) {
    failures.push({
      file: relative(process.cwd(), file),
      output: `${result.stdout || ""}${result.stderr || ""}`.trim(),
    });
  }
}

if (failures.length > 0) {
  for (const failure of failures) {
    console.error(`\n${failure.file}\n${failure.output}`);
  }
  process.exit(1);
}

console.log(`Checked ${files.length} dashboard JS files.`);
