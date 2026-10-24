// Порт report-contract валидатора из ~/.claude/hooks/log-subagent.py
// (ROLE_CONTRACTS, validate_report, validate_line_report и вспомогательные
// функции). Должен меняться синхронно с ним — семантика (какие причины
// когда срабатывают, как читаются заголовки внутри code fence/цитат)
// обязана совпадать, иначе golden-фикстуры Python и тест
// test-report-contracts.mjs разъедутся.
//
// Живёт в lib/, а не в plugins/: OpenCode сканирует plugins/ на предмет
// файлов-плагинов и трактует их экспорты как функции плагина при старте —
// ROLE_CONTRACTS/validateReport/verdictStatus не должны туда попасть.
// claude-hooks.js импортирует этот модуль; default export самого плагина
// обязан оставаться функцией, которую грузит OpenCode.

const HEADING_RE = /^ {0,3}###\s+(.+?)\s*$/;
const LINE_LABEL_RE = /^(verdict|checked|result):\s*(.*)$/i;

// Тот же список ролей и полей, что и ROLE_CONTRACTS в log-subagent.py.
export const ROLE_CONTRACTS = {
  "web-researcher": {
    heading: "result",
    statuses: ["DONE", "PARTIAL", "BLOCKED"],
    required: ["result", "answer", "sources"],
  },
  "docs-researcher": {
    heading: "result",
    statuses: ["DONE", "PARTIAL", "BLOCKED"],
    required: ["result", "answer", "findings", "recommended action", "sources"],
  },
  "code-writer-t1": {
    heading: "result",
    statuses: ["DONE", "PARTIAL", "BLOCKED"],
    required: ["result", "changes", "verification"],
  },
  "code-writer-t2": {
    heading: "result",
    statuses: ["DONE", "PARTIAL", "BLOCKED"],
    required: ["result", "changes", "verification"],
  },
  "code-writer-t3": {
    heading: "result",
    statuses: ["DONE", "PARTIAL", "BLOCKED"],
    required: ["result", "changes", "verification"],
  },
  "bug-investigator": {
    heading: "result",
    statuses: ["CONFIRMED", "PROBABLE", "INCONCLUSIVE", "BLOCKED"],
    required: ["result", "root cause", "evidence", "fix direction", "verification"],
  },
  "test-runner": {
    heading: "result",
    statuses: ["PASS", "FAIL", "PARTIAL", "BLOCKED"],
    required: ["result", "checks", "next action"],
    conditional: { FAIL: ["failures"], PARTIAL: ["failures"] },
  },
  reviewer: {
    heading: "verdict",
    statuses: ["APPROVE", "REQUEST_CHANGES", "BLOCKED"],
    required: ["verdict", "findings", "final assessment"],
  },
  planner: {
    heading: "result",
    statuses: ["DONE", "PARTIAL", "BLOCKED"],
    required: ["result", "approach", "slices", "risks", "acceptance criteria"],
  },
  // Не `### heading`, а три подписанные строки, первая — `Verdict: <статус> ...`
  // без текста перед ней. См. ~/.claude/agents/finding-verifier.md.
  "finding-verifier": {
    style: "line",
    statuses: ["HOLDS", "REFUTED", "UNDETERMINED"],
    required: ["checked", "result"],
  },
};

function structuralText(line) {
  const stripped = line.replace(/^[ \t]*/, "");
  const prefix = line.slice(0, line.length - stripped.length);
  if (prefix.includes("\t") || prefix.length > 3) return null;
  return line.slice(prefix.length);
}

function fenceMarker(line) {
  const stripped = structuralText(line);
  if (!stripped || !"`~".includes(stripped[0])) return null;
  const marker = stripped[0];
  let length = 0;
  while (stripped[length] === marker) length++;
  return length >= 3 ? { marker, length } : null;
}

function nextFence(line, fence) {
  if (fence === null) {
    const opener = fenceMarker(line);
    return [opener, opener !== null];
  }
  const stripped = structuralText(line);
  if (stripped === null) return [fence, false];
  let run = 0;
  while (stripped[run] === fence.marker) run++;
  if (run >= fence.length && !stripped.slice(run).trim()) return [null, true];
  return [fence, false];
}

// Python's str.splitlines() breaks on more than \r\n/\r/\n -- also \v, \f,
// the FS/GS/RS control codes (\x1c-\x1e), NEL (\x85), and the Unicode
// line/paragraph separators U+2028/U+2029. Matching that set keeps line
// numbering identical to the hook this module ports. Built with RegExp(...)
// from an escaped string rather than a /.../ literal: U+2028/U+2029 are
// themselves JS source line terminators, so a raw one embedded inside a
// regex literal would break the literal instead of matching inside it.
const LINE_BREAK_RE = new RegExp("\\r\\n|[\\n\\r\\v\\f\\x1c-\\x1e\\x85\\u2028\\u2029]");

function splitLines(report) {
  return report.length ? report.split(LINE_BREAK_RE) : [];
}

function visibleLines(report) {
  let fence = null;
  const lines = [];
  splitLines(report).forEach((line, index) => {
    const trimmedStart = line.replace(/^\s*/, "");
    const [next, boundary] = nextFence(line, fence);
    fence = next;
    if (boundary || fence !== null) return;
    if (trimmedStart.startsWith(">")) return;
    lines.push([index, line]);
  });
  return lines;
}

function sections(report) {
  const found = {};
  let current = null;
  let fence = null;
  for (const line of splitLines(report)) {
    const stripped = line.replace(/^\s*/, "");
    const [next, boundary] = nextFence(line, fence);
    fence = next;
    if (boundary) continue;
    if (fence !== null) {
      if (current && stripped) found[current] = true;
      continue;
    }
    if (stripped.startsWith(">")) {
      if (current && stripped) found[current] = true;
      continue;
    }
    const match = HEADING_RE.exec(line);
    if (match) {
      current = match[1].trim().toLowerCase().replace(/:$/, "");
      found[current] = false;
      continue;
    }
    if (current && stripped) found[current] = true;
  }
  return found;
}

function validateLineReport(contract, report) {
  const lines = visibleLines(report);
  const reasons = [];
  const meaningful = lines.filter(([, line]) => line.trim());
  if (!meaningful.length) return [null, ["no_visible_report"]];

  const statuses = contract.statuses.join("|");
  let verdict = null;
  let start = null;
  const foundLabels = {};
  meaningful.forEach(([, line], position) => {
    const text = structuralText(line) || "";
    const match = LINE_LABEL_RE.exec(text);
    if (!match) return;
    const label = match[1].toLowerCase();
    foundLabels[label] = Boolean(match[2].trim());
    if (label === "verdict" && start === null) {
      const statusMatch = new RegExp(`^(${statuses})\\b`, "i").exec(match[2]);
      if (statusMatch) {
        start = position;
        verdict = statusMatch[1].toUpperCase();
      }
    }
  });

  if (start === null) reasons.push("missing_verdict");
  else if (start > 0) reasons.push("preamble_before_verdict");

  for (const label of contract.required) {
    if (!(label in foundLabels)) reasons.push("missing_section:" + label);
    else if (!foundLabels[label]) reasons.push("empty_section:" + label);
  }

  return [verdict, reasons.slice(0, 8)];
}

function reportMetadataStatus(reportPresent, report) {
  if (!reportPresent || report === null || report === undefined || typeof report !== "string") {
    return "unavailable";
  }
  if (!report.trim()) return "empty";
  return "present";
}

/**
 * Порт validate_report(agent_type, report_present, report) из log-subagent.py.
 * Возвращает { verdict, metadata, status, reasons } — те же четыре значения,
 * что и Python-версия, просто как объект вместо кортежа.
 */
export function validateReport(agentType, reportPresent, report) {
  const metadata = reportMetadataStatus(reportPresent, report);
  // Bracket access on a plain object literal resolves inherited
  // Object.prototype names too ("constructor", "toString",
  // "hasOwnProperty", "__proto__" ...), which Python's dict.get never does.
  // Without the ownProperty guard those agent-type strings would return a
  // truthy but wrong "contract" (e.g. the constructor function itself) and
  // blow up below instead of falling through to "exempt" like every other
  // unknown role.
  const contract = Object.prototype.hasOwnProperty.call(ROLE_CONTRACTS, agentType)
    ? ROLE_CONTRACTS[agentType]
    : undefined;
  if (!contract) return { verdict: null, metadata, status: "exempt", reasons: [] };

  if (metadata === "unavailable") {
    let reason;
    if (!reportPresent) reason = "report_missing";
    else if (report === null || report === undefined) reason = "report_null";
    else reason = "report_nonstring";
    return { verdict: null, metadata, status: "unavailable", reasons: [reason] };
  }
  if (metadata === "empty") {
    return { verdict: null, metadata, status: "invalid", reasons: ["report_empty"] };
  }

  if (contract.style === "line") {
    const [verdict, reasons] = validateLineReport(contract, report);
    return { verdict, metadata, status: reasons.length ? "invalid" : "valid", reasons };
  }

  const lines = visibleLines(report);
  const reasons = [];
  if (!lines.length) return { verdict: null, metadata, status: "invalid", reasons: ["no_visible_report"] };
  const meaningful = lines.filter(([, line]) => line.trim());

  const heading = contract.heading;
  let start = null;
  meaningful.forEach(([, line], position) => {
    if (start !== null) return;
    const match = HEADING_RE.exec(line);
    if (match && match[1].trim().toLowerCase().replace(/:$/, "") === heading) start = position;
  });

  let verdict = null;
  if (start === null) {
    reasons.push("missing_" + heading);
  } else {
    if (start > 0) reasons.push("preamble_before_" + heading);
    if (meaningful.length > start + 1) {
      const statusLine = structuralText(meaningful[start + 1][1]) || "";
      const statuses = contract.statuses.join("|");
      const match = new RegExp("^`?(" + statuses + ")`?(?=\\s|—|-|$)").exec(statusLine);
      if (match) verdict = match[1];
    }
    if (verdict === null) reasons.push("missing_or_invalid_status");
  }

  const found = sections(report);
  const required = [...contract.required];
  if (verdict) required.push(...((contract.conditional || {})[verdict] || []));
  for (const label of required) {
    if (!(label in found)) reasons.push("missing_section:" + label.replace(/ /g, "_"));
    else if (!found[label]) reasons.push("empty_section:" + label.replace(/ /g, "_"));
  }

  return {
    verdict,
    metadata,
    status: reasons.length ? "invalid" : "valid",
    reasons: reasons.slice(0, 8),
  };
}

/**
 * Порт verdict_status(contract_status, verdict) из log-subagent.py.
 */
export function verdictStatus(contractStatus, verdict) {
  if (verdict !== null && verdict !== undefined) return "captured";
  if (contractStatus === "exempt") return "not_applicable";
  return "lost";
}
