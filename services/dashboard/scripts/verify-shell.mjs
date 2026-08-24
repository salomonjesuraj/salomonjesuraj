import { readFileSync } from "node:fs";
import { join } from "node:path";

const html = readFileSync(join(process.cwd(), "public", "index.html"), "utf8");
const rail = readFileSync(join(process.cwd(), "public", "js", "rail-v2.js"), "utf8");
const mode = readFileSync(join(process.cwd(), "public", "js", "dashboard-mode.js"), "utf8");

const failures = [];

function expect(condition, message) {
  if (!condition) failures.push(message);
}

function count(pattern) {
  return (html.match(pattern) || []).length;
}

expect(
  html.includes("saved === 'classic' ? 'classic' : 'new'"),
  "index.html anti-FOUC dashboard mode must default to New unless Classic is saved."
);
expect(
  mode.includes("return 'new';"),
  "dashboard-mode.js initialMode() must default to New."
);
expect(count(/id="breakoutRadarV2"/g) === 1, "breakoutRadarV2 must be mounted exactly once.");
expect(
  html.indexOf('id="breakoutRadarV2"') < html.indexOf('id="scannerV2"'),
  "Breakout Radar must render before the F&O Screener in the New primary view."
);
expect(
  rail.includes("if (key === 'breakout-radar')"),
  "Rail Breakout Radar action must return to the primary view instead of opening a duplicate pane."
);

const requiredIds = [
  "newShell",
  "dashboardModeSwitch",
  "primaryViewV2",
  "paneViewV2",
  "railV2",
  "cockpitV2",
  "watchStripV2",
  "breakoutRadarV2",
  "scannerV2",
  "optionCockpitBodyV2",
  "scannerInsightPanelV2",
  "integrityPanelV2",
];

for (const id of requiredIds) {
  expect(count(new RegExp(`id="${id}"`, "g")) === 1, `${id} must exist exactly once.`);
}

const paneKeys = new Set([...html.matchAll(/data-pane-v2="([^"]+)"/g)].map((match) => match[1]));
const railKeys = [...rail.matchAll(/key: '([^']+)'/g)].map((match) => match[1]);
const specialRailKeys = new Set(["breakout-radar"]);

for (const key of railKeys) {
  if (specialRailKeys.has(key)) continue;
  expect(paneKeys.has(key), `Rail key '${key}' must have a matching data-pane-v2 pane.`);
}

if (failures.length > 0) {
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("Dashboard shell wiring check passed.");
