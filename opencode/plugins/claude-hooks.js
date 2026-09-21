import { appendFile, access, mkdir, readFile, writeFile } from "node:fs/promises";
import { createReadStream } from "node:fs";
import { homedir } from "node:os";
import { basename, join, resolve, sep } from "node:path";

const DEFAULT_LINE_LIMIT = 350;
const LOG_DIR = join(homedir(), ".claude", "logs");
const READ_LOG = "file-read-guardrail.jsonl";
// Собственный файл, отдельно от log-subagent.py (Claude Code) — раньше оба
// хука писали в subagent-runs.jsonl с одинаковым "schema": 4, но с несовместимой
// формой записи (camelCase vs snake_case, разный набор полей). Один и тот же
// номер схемы на два разных формата ломает любой скрипт, который её читает.
const SUBAGENT_LOG = "subagent-runs-opencode.jsonl";
const CONFIG_PROTECTION_LOG = "config-protection-opencode.jsonl";
const DOCS_DRIFT_LOG = "docs-drift-reminders-opencode.jsonl";
const MCP_FAILURES_LOG = "mcp-failures-opencode.jsonl";
const MCP_HEALTH_STATE = join(LOG_DIR, "mcp-health-state-opencode.json");

const READ_TARGETED_PARAMS = ["offset", "limit", "pages"];

const SKIP_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".pdf", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".mp3", ".mp4", ".mov", ".avi", ".wav", ".exe", ".dll", ".so", ".dylib", ".bin", ".woff", ".woff2", ".ttf", ".otf", ".class", ".jar", ".pyc"]);

// The repository the Trino MCP scope guard confines itself to. Point
// OPENCODE_TRINO_GUARDRAIL_PROJECT_ROOT at your own checkout; with nothing
// set the guard treats every session as out of scope and denies Trino calls
// everywhere (same convention as claude/hooks/trino-guardrail.py).
const PROJECT_ROOT = resolve(process.env.OPENCODE_TRINO_GUARDRAIL_PROJECT_ROOT || "/nonexistent");
const TRINO_TOOL_RE = /^mcp__[^_]+__(?:execute_query|explain_query)$/;

const SKILL_NAME = "task-orchestration";
// Read-only research agents in this OpenCode agent set — exempt from the
// orchestration gate because they don't delegate further work themselves.
const EXEMPT_AGENTS = new Set(["web-researcher-fast", "docs-researcher-deep"]);

const VERDICT_RE = /(DONE|PARTIAL|BLOCKED|PASS|FAIL|CONFIRMED|PROBABLE|INCONCLUSIVE|APPROVE|REQUEST_CHANGES)/;

// Порт config-protection.py: файлы, где весь смысл файла — настройка проверки,
// правка целиком запрещена (создание с нуля разрешено — там ещё нечего ослаблять).
const CONFIG_DEDICATED = new Set([
  "ruff.toml", ".ruff.toml", "mypy.ini", ".mypy.ini", ".flake8", "pytest.ini",
  ".pylintrc", "pylintrc", ".isort.cfg", ".bandit", "bandit.yaml",
  ".pre-commit-config.yaml", ".pre-commit-config.yml",
  ".sqlfluff",
  ".shellcheckrc", ".yamllint", ".yamllint.yaml", ".yamllint.yml",
  ".markdownlint.json", ".markdownlint.yaml", ".markdownlintrc", ".editorconfig",
  ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml",
  "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts",
  "biome.json", "biome.jsonc",
  ".prettierrc", ".prettierrc.json", ".prettierrc.js", ".prettierrc.yml",
  ".stylelintrc", ".stylelintrc.json",
]);
// Файлы, где настройки проверок соседствуют с зависимостями/метаданными —
// сравниваются только секции линтеров/тайпчекеров до и после правки.
const CONFIG_MIXED = new Set(["pyproject.toml", "setup.cfg", "tox.ini"]);
const SECTION_HEADER_RE = /^[ \t]*\[([^\]\n]+)\][ \t]*$/gm;
const TOOL_SECTION_RE = /^(?:tool[.:])?(ruff|mypy|flake8|pytest|coverage|black|isort|pylint|pycodestyle|pydocstyle|bandit|sqlfluff)\b/i;

// Порт docs-drift-reminder.py. task-orchestration и code-tier-assessment живут
// как общие Claude-скиллы (auto-discovery из ~/.claude/skills) кроме
// task-orchestration, у которого есть отдельный OpenCode-override — оба пути
// стоит напоминать. orchestration-architecture.md — общий кросс-инструментный
// файл, не дублируется под OpenCode.
const ORCH_FILES = new Set([
  join(homedir(), ".claude", "skills", "task-orchestration", "SKILL.md"),
  join(homedir(), ".config", "opencode", "skills", "task-orchestration", "SKILL.md"),
  join(homedir(), ".claude", "skills", "code-tier-assessment", "SKILL.md"),
  resolve(join(homedir(), ".config", "opencode", "plugins", "claude-hooks.js")),
  join(homedir(), ".config", "opencode", "tools", "agent-stats.py"),
]);
const AGENTS_DIR = join(homedir(), ".config", "opencode", "agent");
const ORCH_DOC_PATH = join(homedir(), ".claude", "docs", "orchestration-architecture.md");

// Порт mcp-health-check.py. OpenCode не даёт отдельного «tool failed» события
// (в отличие от Claude Code PostToolUseFailure) — tool.execute.after срабатывает
// и на успех, и на провал, так что провал распознаётся эвристикой по тексту
// результата. Не проверено вживую: список паттернов ниже может давать
// ложноотрицательные срабатывания на нестандартных текстах ошибок MCP.
const RESET_AFTER_MS = 30 * 60 * 1000;
const MCP_FAILURE_CLASSES = [
  ["auth", /\b401\b|unauthori[sz]ed|auth(?:entication)?\s+(?:failed|expired|invalid)|expired token/i],
  ["forbidden", /\b403\b|forbidden|permission denied/i],
  ["rate_limit", /\b429\b|rate.?limit|too many requests/i],
  ["unavailable", /\b(?:500|502|503)\b|service unavailable|overloaded|temporarily unavailable|internal server error/i],
  ["transport", /ECONNREFUSED|ENOTFOUND|EAI_AGAIN|timed? ?out|socket hang up|connection (?:failed|lost|reset|closed)/i],
];
const MCP_ADVICE = {
  auth: "учётные данные для него отклонены или истекли. Повтор не поможет — переавторизация не в руках модели: скажи пользователю и продолжай без этого сервера, либо остановись, если он необходим.",
  forbidden: "учётные данные приняты, но конкретный вызов не разрешён. Повтор того же вызова снова не сработает — нужен другой вызов или более широкий доступ.",
  rate_limit: "сервер ограничивает частоту запросов. Подожди перед следующим вызовом или объедини оставшуюся работу.",
  unavailable: "падает сам сервер, а не запрос. Немедленный повтор не поможет.",
  transport: "соединение не удержалось. Один повтор оправдан, второй провал подряд значит сервер лёг.",
};
const MCP_SECRETS = [
  [/(bearer\s+)[A-Za-z0-9._\-~+/]{8,}/gi, "$1***"],
  [/((?:token|secret|password|passwd|api[_-]?key|access[_-]?key|authorization)"?\s*[:=]\s*"?)[^\s"',;&]{4,}/gi, "$1***"],
  [/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, "$1***:***@"],
  [/\b(eyJ[A-Za-z0-9_-]{6,}\.)[A-Za-z0-9._-]{8,}/g, "$1***"],
];
const MCP_EXCERPT_LIMIT = 300;

const loadedSkills = new Set();

// callID (вызов tool "task") -> метаданные вызова + какой child-сессии он соответствует.
// Один "task"-вызов синхронный: до его before/after укладывается ровно один прогон
// субагента, поэтому child-сессия, созданная в этом окне с parentID == вызывающей
// сессии, — это она и есть. При нескольких параллельных вызовах от одного родителя
// сопоставление идёт по порядку создания (FIFO) — лучшее, что можно сделать без
// прямого id child-сессии в результате tool-вызова.
const pendingTaskCalls = new Map();
// child sessionID -> messageID -> { tokens/cost/model за это сообщение }
const childSessionMessages = new Map();

async function countLines(path) {
  let total = 0;
  for await (const chunk of createReadStream(path)) {
    total += chunk.toString("binary").split("\n").length - 1;
  }
  return total;
}

async function logEvent(file, extra) {
  try {
    await mkdir(LOG_DIR, { recursive: true });
    await appendFile(join(LOG_DIR, file), JSON.stringify({ timestamp: new Date().toISOString(), ...extra }) + "\n");
  } catch {
    /* никогда не ломать работу из-за логирования */
  }
}

function isInsideProject(cwd) {
  if (!cwd) return false;
  try {
    const c = resolve(cwd);
    return c === PROJECT_ROOT || c.startsWith(PROJECT_ROOT + sep);
  } catch {
    return false;
  }
}

function recordSkillLoad(args, metadata) {
  const name = (args && (args.name || args.skill)) || (metadata && metadata.name) || "";
  if (name) loadedSkills.add(String(name).split(":").pop());
}

function extractVerdict(text) {
  if (!text) return undefined;
  const headingIdx = text.search(/#{1,4}\s*(result|verdict)/i);
  const window = headingIdx >= 0 ? text.slice(headingIdx, headingIdx + 200) : text.slice(0, 200);
  const m = window.match(VERDICT_RE);
  return m ? m[1] : undefined;
}

function attachChildSession(session) {
  if (!session || !session.parentID) return;
  let best = null;
  for (const [callID, call] of pendingTaskCalls) {
    if (call.childSessionID) continue;
    if (call.sessionID !== session.parentID) continue;
    if (!best || call.startedAt < best.call.startedAt) best = { callID, call };
  }
  if (best) {
    best.call.childSessionID = session.id;
    if (!childSessionMessages.has(session.id)) childSessionMessages.set(session.id, new Map());
  }
}

function recordAssistantMessage(message) {
  if (!message || message.role !== "assistant") return;
  const bucket = childSessionMessages.get(message.sessionID);
  if (!bucket) return;
  bucket.set(message.id, {
    input: message.tokens?.input || 0,
    output: message.tokens?.output || 0,
    reasoning: message.tokens?.reasoning || 0,
    cacheRead: message.tokens?.cache?.read || 0,
    cacheWrite: message.tokens?.cache?.write || 0,
    cost: message.cost || 0,
    model: message.modelID,
    provider: message.providerID,
  });
}

function summarizeChildSession(childSessionID) {
  const bucket = childSessionID ? childSessionMessages.get(childSessionID) : undefined;
  if (!bucket || bucket.size === 0) return {};
  const totals = { turns: bucket.size, input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, cost: 0 };
  let last;
  for (const m of bucket.values()) {
    totals.input_tokens += m.input;
    totals.output_tokens += m.output;
    totals.reasoning_tokens += m.reasoning;
    totals.cache_read_tokens += m.cacheRead;
    totals.cache_write_tokens += m.cacheWrite;
    totals.cost += m.cost;
    last = m;
  }
  totals.cost = Math.round(totals.cost * 1e6) / 1e6;
  totals.model = last?.model;
  totals.provider = last?.provider;
  return totals;
}

async function pathExists(path) {
  try {
    await access(path);
    return true;
  } catch (err) {
    // EACCES/EPERM всё ещё значит «файл есть» — не даём гарду проскочить из-за прав.
    return Boolean(err) && err.code !== "ENOENT";
  }
}

function configSections(text) {
  const headers = [];
  let m;
  SECTION_HEADER_RE.lastIndex = 0;
  while ((m = SECTION_HEADER_RE.exec(text))) headers.push({ name: m[1].trim(), start: m.index, headerEnd: m.index + m[0].length });
  const found = {};
  for (let i = 0; i < headers.length; i++) {
    const h = headers[i];
    if (!TOOL_SECTION_RE.test(h.name)) continue;
    const end = i + 1 < headers.length ? headers[i + 1].start : text.length;
    found[h.name] = text.slice(h.headerEnd, end).trim();
  }
  return found;
}

function applyConfigEdit(current, tool, args) {
  if (tool === "write") return typeof args.content === "string" ? args.content : null;
  const oldStr = args.oldString;
  const newStr = args.newString;
  if (typeof oldStr !== "string" || typeof newStr !== "string" || !current.includes(oldStr)) return null;
  return args.replaceAll ? current.split(oldStr).join(newStr) : current.replace(oldStr, newStr);
}

async function handleConfigProtection(tool, args) {
  if (process.env.OPENCODE_CONFIG_PROTECTION === "0") return;
  const filePath = args.filePath;
  if (!filePath) return;
  const base = basename(filePath).toLowerCase();
  const dedicated = CONFIG_DEDICATED.has(base);
  const mixed = !dedicated && CONFIG_MIXED.has(base);
  if (!dedicated && !mixed) return;
  if (!(await pathExists(filePath))) return; // bootstrap в проекте без конфига — не ослабление

  if (dedicated) {
    const reason = `Правка ${base} заблокирована. Этот файл целиком настраивает проверки, и менять его, когда проверка не проходит, — значит ослаблять проверку, а не чинить код. Почини код. Если менять конфиг и есть сама задача — спроси пользователя; временно снять гард можно через OPENCODE_CONFIG_PROTECTION=0.`;
    await logEvent(CONFIG_PROTECTION_LOG, { file_path: filePath, decision: "deny", reason });
    throw new Error(reason);
  }

  let current;
  try {
    current = await readFile(filePath, "utf8");
  } catch {
    return; // не смогли прочитать — не судим
  }
  const updated = applyConfigEdit(current, tool, args);
  if (updated == null) return;
  const before = configSections(current);
  const after = configSections(updated);
  const changed = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter((name) => before[name] !== after[name])
    .sort();
  if (!changed.length) return;

  const reason = `Правка ${base} меняет настройки проверок: ${changed.join(", ")}. Ослаблять их, чтобы проверка прошла, нельзя — почини код. Остальные секции этого файла (зависимости, метаданные) гард не трогает. Если правка конфига и есть задача — спроси пользователя; временно снять гард можно через OPENCODE_CONFIG_PROTECTION=0.`;
  await logEvent(CONFIG_PROTECTION_LOG, { file_path: filePath, decision: "deny", sections: changed, reason });
  throw new Error(reason);
}

async function wasReminded(sessionID, filePath) {
  try {
    const text = await readFile(join(LOG_DIR, DOCS_DRIFT_LOG), "utf8");
    for (const line of text.split("\n")) {
      if (!line) continue;
      try {
        const rec = JSON.parse(line);
        if (rec.session_id === sessionID && rec.file_path === filePath) return true;
      } catch {
        /* пропускаем битую строку */
      }
    }
  } catch {
    /* лога ещё нет — значит не напоминали */
  }
  return false;
}

async function handleDocsDrift(input, output) {
  const filePath = (input.args || {}).filePath;
  if (!filePath) return;
  const resolved = resolve(filePath);
  const isAgentFile = resolved.startsWith(AGENTS_DIR + sep) && resolved.endsWith(".md");
  if (!ORCH_FILES.has(resolved) && !isAgentFile) return;
  if (await wasReminded(input.sessionID, resolved)) return;
  await logEvent(DOCS_DRIFT_LOG, { session_id: input.sessionID, file_path: resolved });
  const note = `Ты изменил файл оркестрации (${resolved}). Если изменение существенное (новая роль или тир, другая механика эскалации, другой формат брифа или лога) — обнови ${ORCH_DOC_PATH}. Если это мелкая правка, ничего не делай.`;
  if (output && typeof output.output === "string") output.output = `${output.output}\n\n[docs-drift] ${note}`;
}

function mcpServerOf(toolName) {
  if (!toolName || !toolName.startsWith("mcp__")) return null;
  const parts = toolName.slice(5).split("__");
  return parts.length >= 2 && parts[0] ? parts[0] : null;
}

function classifyMcpFailure(text) {
  for (const [name, re] of MCP_FAILURE_CLASSES) if (re.test(text)) return name;
  return null;
}

function maskMcpText(text) {
  let out = text;
  for (const [re, repl] of MCP_SECRETS) out = out.replace(re, repl);
  return out;
}

async function loadMcpState() {
  try {
    const parsed = JSON.parse(await readFile(MCP_HEALTH_STATE, "utf8"));
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

async function saveMcpState(state) {
  try {
    await mkdir(LOG_DIR, { recursive: true });
    await writeFile(MCP_HEALTH_STATE, JSON.stringify(state, null, 1));
  } catch {
    /* состояние не критично — теряем счётчик, не рушим прогон */
  }
}

async function handleMcpHealth(input, output) {
  if (process.env.OPENCODE_MCP_HEALTH === "0") return;
  const server = mcpServerOf(input.tool);
  if (!server) return;
  const rawText = String((output || {}).output || "");
  const failureClass = classifyMcpFailure(rawText);
  if (!failureClass) return; // нет распознанного паттерна сбоя — считаем успехом

  const excerpt = maskMcpText(rawText.replace(/\s+/g, " ")).slice(0, MCP_EXCERPT_LIMIT);
  const now = Date.now();
  const state = await loadMcpState();
  const previous = state[server] || {};
  const fresh = previous.lastSeen && now - previous.lastSeen <= RESET_AFTER_MS && previous.class === failureClass;
  const count = fresh ? (previous.count || 0) + 1 : 1;
  state[server] = { class: failureClass, count, lastSeen: now, firstSeen: fresh ? previous.firstSeen : now };
  await saveMcpState(state);

  await logEvent(MCP_FAILURES_LOG, {
    schema: 1,
    sessionID: input.sessionID,
    server,
    tool: input.tool,
    class: failureClass,
    consecutive: count,
    excerpt,
  });

  const repeat = count > 1 ? ` Это ${count}-й подряд сбой этого сервера.` : "";
  const note = `MCP-сервер \`${server}\` упал (${failureClass}): ${MCP_ADVICE[failureClass]}${repeat} Записано в ~/.claude/logs/${MCP_FAILURES_LOG}.`;
  if (output && typeof output.output === "string") output.output = `${output.output}\n\n[mcp-health] ${note}`;
}

export const ClaudeHooks = async ({ directory }) => {
  return {
    event: async ({ event }) => {
      try {
        if (event.type === "session.created") attachChildSession(event.properties.info);
        else if (event.type === "message.updated") recordAssistantMessage(event.properties.info);
      } catch {
        /* фоновая телеметрия не должна ронять сессию */
      }
    },

    "tool.execute.after": async (input, output) => {
      const tool = input.tool;

      if (tool === "skill") {
        recordSkillLoad(input.args, (output || {}).metadata);
        return;
      }

      if (tool === "edit" || tool === "write") {
        try {
          await handleDocsDrift(input, output);
        } catch {
          /* напоминание никогда не должно ломать прогон */
        }
        return;
      }

      if (tool !== "task") {
        try {
          await handleMcpHealth(input, output);
        } catch {
          /* наблюдаемость никогда не должна ломать прогон */
        }
        return;
      }

      const call = pendingTaskCalls.get(input.callID) || {};
      const args = input.args || call.args || {};
      const text = String(args.prompt || args.description || "");
      const slice = text.match(/^[ \t]*(?:[-*] )?`?slice_id`?[ \t]*[:=][ \t]*`?([A-Za-z0-9._-]{1,64})/im);
      const tier = text.match(/^[ \t]*(?:[-*] )?`?tier`?[ \t]*[:=][ \t]*`?(T[123])/im);
      const failure = text.match(/^[ \t]*(?:[-*] )?`?previous_failure_class`?[ \t]*[:=][ \t]*`?(executor|spec|env|slicing)/im);
      const usage = summarizeChildSession(call.childSessionID);

      await logEvent(SUBAGENT_LOG, {
        schema: 1,
        hookEvent: "tool.execute.after",
        sessionID: input.sessionID,
        childSessionID: call.childSessionID || undefined,
        tool: input.tool,
        subagentType: args.subagent_type || args.subagentType || undefined,
        description: args.description || undefined,
        sliceId: slice ? slice[1] : undefined,
        tier: tier ? tier[1] : undefined,
        previousFailureClass: failure ? failure[1] : undefined,
        verdict: extractVerdict((output || {}).output),
        duration_s: call.startedAt ? Math.round((Date.now() - call.startedAt) / 100) / 10 : undefined,
        ...usage,
      });

      pendingTaskCalls.delete(input.callID);
      if (call.childSessionID) childSessionMessages.delete(call.childSessionID);
    },

    "tool.execute.before": async (input, output) => {
      const tool = input.tool;
      const args = output.args || input.args || {};

      if (tool === "read") {
        const filePath = args.filePath;
        if (!filePath || READ_TARGETED_PARAMS.some((p) => args[p] !== undefined)) return;
        const ext = filePath.slice(filePath.lastIndexOf(".")).toLowerCase();
        if (SKIP_EXTENSIONS.has(ext)) return;
        try {
          await access(filePath);
          const lines = await countLines(filePath);
          const threshold = Number(process.env.OPENCODE_READ_LINE_LIMIT || DEFAULT_LINE_LIMIT);
          if (lines > threshold) {
            const reason = `Файл ${filePath} — ${lines} строк, порог ${threshold}. Не читай целиком: делегируй чтение/анализ сабагенту (tool "task", subagentType=Explore или general-purpose) и забери выжимку, а не сырое содержимое.`;
            await logEvent(READ_LOG, { file_path: filePath, lines, threshold, decision: "deny", reason });
            throw new Error(reason);
          }
        } catch (err) {
          if (err instanceof Error && err.message.startsWith("Файл ")) throw err;
          await logEvent(READ_LOG, { file_path: filePath, decision: "allow-on-error", reason: err instanceof Error ? err.message : String(err) });
          return;
        }
        return;
      }

      if (tool === "edit" || tool === "write") {
        await handleConfigProtection(tool, args);
        return;
      }

      if (TRINO_TOOL_RE.test(tool) && !isInsideProject(directory)) {
        throw new Error(
          "Trino MCP ограничен проектом airflow-dags и его воркtree-ами. Эта сессия работает в другом проекте — обращение к складу данных здесь не предусмотрено."
        );
      }

      if (tool === "task") {
        const subagentType = args.subagent_type || args.subagentType || "general-purpose";
        // Регистрируем вызов для логирования только после гейта — если он бросит
        // исключение, tool.execute.after для этого callID никогда не наступит,
        // и запись осталась бы в pendingTaskCalls навсегда.
        if (!EXEMPT_AGENTS.has(subagentType) && process.env.OPENCODE_ORCHESTRATION_GATE !== "0" && !loadedSkills.has(SKILL_NAME)) {
          throw new Error(
            `Делегирование в subagentType=${subagentType} без загруженной доктрины. Сначала загрузи навык ${SKILL_NAME}: вызови tool "skill" с именем "${SKILL_NAME}", прочитай его и повтори вызов task. Гейт не распространяется на поиск и справки: ${[...EXEMPT_AGENTS].join(", ")}.`
          );
        }
        pendingTaskCalls.set(input.callID, { sessionID: input.sessionID, args, startedAt: Date.now(), childSessionID: null });
      }
    },
  };
};

export default ClaudeHooks;
