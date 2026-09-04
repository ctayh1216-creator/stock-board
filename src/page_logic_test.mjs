// 종목 발굴 페이지 순수 로직 테스트.
// discover_template.html 의 단일 <script> 블록을 추출해 node 에서 실행하면
// (window 가 없으므로) globalThis.__DISCOVER_PURE__ 에 순수 함수만 노출된다.
// 검증 항목: null 뒤로 보내는 양방향 정렬, 검색어+섹터 필터, 전략별 강조색 매핑.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "..", "discover_template.html"), "utf8");

const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("FAIL: <script> block not found in template"); process.exit(1); }

new Function(m[1])();
const P = globalThis.__DISCOVER_PURE__;
if (!P) { console.error("FAIL: __DISCOVER_PURE__ not exposed"); process.exit(1); }

let pass = 0, fail = 0;
function eq(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { pass++; console.log("ok  " + name); }
  else { fail++; console.error("FAIL " + name + "\n  got  " + g + "\n  want " + w); }
}

/* ---------------- strategy accent mapping ---------------- */
eq("accent momentum -> --s1", P.stratAccent("momentum"), "--s1");
eq("accent value_quality -> --s2", P.stratAccent("value_quality"), "--s2");
eq("accent earnings -> --s3", P.stratAccent("earnings"), "--s3");
eq("accent oversold -> --s4", P.stratAccent("oversold"), "--s4");
eq("accent unknown -> --s5 fallback", P.stratAccent("mystery"), "--s5");

/* ---------------- sorting: nulls last in BOTH directions ---------------- */
const rows = [
  { ticker: "AAA", v: 3 },
  { ticker: "BBB", v: null },
  { ticker: "CCC", v: 1 },
  { ticker: "DDD", v: undefined },
  { ticker: "EEE", v: 2 },
  { ticker: "FFF", v: NaN },
];
const getV = r => r.v;
eq("numeric asc, nulls last",
  P.sortRows(rows, getV, "asc").map(r => r.ticker).slice(0, 3), ["CCC", "EEE", "AAA"]);
eq("numeric asc, tail is all-null",
  P.sortRows(rows, getV, "asc").slice(3).every(r => !P.has(r.v)), true);
eq("numeric desc, nulls last",
  P.sortRows(rows, getV, "desc").map(r => r.ticker).slice(0, 3), ["AAA", "EEE", "CCC"]);
eq("numeric desc, tail is all-null",
  P.sortRows(rows, getV, "desc").slice(3).every(r => !P.has(r.v)), true);
eq("sortRows does not mutate input",
  rows.map(r => r.ticker), ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]);

const srows = [
  { ticker: "NVDA", s: "2026-09-17" },
  { ticker: "AAPL", s: null },
  { ticker: "MSFT", s: "2026-08-30" },
];
eq("string asc (dates), null last",
  P.sortRows(srows, r => r.s, "asc").map(r => r.ticker), ["MSFT", "NVDA", "AAPL"]);
eq("string desc (dates), null last",
  P.sortRows(srows, r => r.s, "desc").map(r => r.ticker), ["NVDA", "MSFT", "AAPL"]);

eq("cmpVals both null -> 0", P.cmpVals(null, undefined, "asc"), 0);
eq("cmpVals NaN treated as null (last on desc)", P.cmpVals(NaN, 5, "desc"), 1);

/* ---------------- filter: search + sector ---------------- */
const uni = [
  { ticker: "NVDA", name: "NVIDIA",            sector: "Information Technology" },
  { ticker: "AVGO", name: "Broadcom",          sector: "Information Technology" },
  { ticker: "LLY",  name: "Eli Lilly",         sector: "Health Care" },
  { ticker: "XOM",  name: "Exxon Mobil",       sector: "Energy" },
  { ticker: "NVR",  name: "NVR, Inc.",         sector: "Consumer Discretionary" },
];
eq("filter: empty query + empty sector -> all",
  P.filterRows(uni, "", "").length, 5);
eq("filter: ticker partial, case-insensitive",
  P.filterRows(uni, "nv", "").map(r => r.ticker), ["NVDA", "NVR"]);
eq("filter: name partial, case-insensitive",
  P.filterRows(uni, "broadCOM", "").map(r => r.ticker), ["AVGO"]);
eq("filter: sector exact",
  P.filterRows(uni, "", "Information Technology").map(r => r.ticker), ["NVDA", "AVGO"]);
eq("filter: search AND sector combined",
  P.filterRows(uni, "nv", "Information Technology").map(r => r.ticker), ["NVDA"]);
eq("filter: whitespace-only query -> all",
  P.filterRows(uni, "   ", "").length, 5);
eq("filter: no match -> empty",
  P.filterRows(uni, "zzz", "Energy").length, 0);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
