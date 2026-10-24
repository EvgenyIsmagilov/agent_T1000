#!/usr/bin/env node
// Прогоняет golden-фикстуры из .claude/tools/fixtures/reports/ (от корня репозитория) через JS-порт
// валидатора (../lib/report-contracts.mjs) и сверяет исход/причину с тем,
// что ожидает test_agent_reporting.py для тех же файлов. Держит JS и Python
// валидацию в одном контракте — расхождение здесь означает, что порт отстал
// от log-subagent.py.
//
// Использование: node ~/.config/opencode/tools/test-report-contracts.mjs
// Ничего не пишет в ~/.claude/logs — только читает фикстуры.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { ROLE_CONTRACTS, validateReport } from "../lib/report-contracts.mjs";

const FIXTURES_DIR = fileURLToPath(new URL("../../../.claude/tools/fixtures/reports", import.meta.url));

// Зеркало EXTRA_MALFORMED_REASONS в test_agent_reporting.py.
const EXTRA_MALFORMED_REASONS = {
  "code-writer-t1.malformed-missing-section.md": "missing_section:verification",
  "web-researcher.malformed-bad-status.md": "missing_or_invalid_status",
  "bug-investigator.malformed-empty-section.md": "empty_section:evidence",
  "test-runner.malformed-fail-no-failures.md": "missing_section:failures",
  "finding-verifier.malformed-bad-label.md": "missing_verdict",
};

let failures = 0;

function check(label, condition, detail) {
  if (!condition) {
    failures++;
    console.error(`FAIL: ${label}${detail ? " — " + detail : ""}`);
  }
}

const files = new Set(readdirSync(FIXTURES_DIR));

for (const [role, contract] of Object.entries(ROLE_CONTRACTS)) {
  const validPath = join(FIXTURES_DIR, `${role}.valid.md`);
  const malformedPath = join(FIXTURES_DIR, `${role}.malformed.md`);
  check(`${role}: valid fixture exists`, files.has(`${role}.valid.md`));
  check(`${role}: malformed fixture exists`, files.has(`${role}.malformed.md`));
  if (!files.has(`${role}.valid.md`) || !files.has(`${role}.malformed.md`)) continue;

  const validResult = validateReport(role, true, readFileSync(validPath, "utf8"));
  check(`${role}: valid fixture passes`, validResult.status === "valid", JSON.stringify(validResult.reasons));

  const malformedResult = validateReport(role, true, readFileSync(malformedPath, "utf8"));
  check(`${role}: malformed fixture fails`, malformedResult.status === "invalid");
  const preambleReason = "preamble_before_" + (contract.heading || "verdict");
  check(
    `${role}: malformed fixture has ${preambleReason}`,
    malformedResult.reasons.includes(preambleReason),
    JSON.stringify(malformedResult.reasons)
  );
}

for (const [filename, expectedReason] of Object.entries(EXTRA_MALFORMED_REASONS)) {
  const role = filename.split(".", 1)[0];
  const path = join(FIXTURES_DIR, filename);
  check(`${filename}: fixture exists`, files.has(filename));
  if (!files.has(filename)) continue;
  const result = validateReport(role, true, readFileSync(path, "utf8"));
  check(`${filename}: fails`, result.status === "invalid");
  check(
    `${filename}: has reason ${expectedReason}`,
    result.reasons.includes(expectedReason),
    JSON.stringify(result.reasons)
  );
}

// Regression cases from code review, not fixture-derived — see
// report-contracts.mjs for what each guards against.

// Object.prototype names must resolve like any other unknown role (exempt,
// no throw), the same as Python's dict.get on ROLE_CONTRACTS.
for (const name of ["constructor", "toString", "hasOwnProperty", "__proto__"]) {
  let result;
  let threw = false;
  try {
    result = validateReport(name, true, "### Result\nDONE\nblah");
  } catch {
    threw = true;
  }
  check(`${name}: does not throw`, !threw);
  if (!threw) {
    check(`${name}: exempt`, result.status === "exempt");
    check(`${name}: verdict null`, result.verdict === null);
  }
}

// splitLines must break on the same control characters as Python's
// str.splitlines(), not just \r\n/\r/\n — otherwise a report using \x0b or
// \x0c as a line break would see its headings fused onto one line and fail
// contract validation the Python hook would call valid. Expectation below
// was computed once by running the real Python validate_report on this
// exact string (see the task's fix-direction note).
{
  const report = "### Result\nDONE — summary\x0b### Changes\ncontent\x0c### Verification\nverified";
  const result = validateReport("code-writer-t2", true, report);
  check(
    "splitLines: \\x0b/\\x0c line breaks parse like Python splitlines()",
    result.status === "valid" && result.verdict === "DONE",
    JSON.stringify(result)
  );
}

if (failures) {
  console.error(`\n${failures} failure(s).`);
  process.exit(1);
} else {
  console.log(`OK — all fixtures match expected Python outcomes.`);
}
