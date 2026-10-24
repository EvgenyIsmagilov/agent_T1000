#!/usr/bin/env python3
"""PreToolUse guardrail for Bash: lets the read-only `reviewer` subagent run
only commands that cannot modify the working tree or files.

`reviewer` has no Edit/Write, but its Bash is unrestricted. `git stash`,
`git checkout -- .`, `sed -i`, a bare `>` — any of these can destroy the
uncommitted diff the reviewer is supposed to be judging, with nothing left
to restore it from. This hook is the only thing stopping that.

FAIL-CLOSED, unlike `config-protection.py`: a command this hook cannot
parse, or a command/flag it does not explicitly recognize, is denied.
`config-protection` fails open because blocking a legitimate edit on an
unhandled edge case is the worse outcome there. For the reviewer the
asymmetry runs the other way: telling it to rephrase a denied command
costs nothing; a diff destroyed by a command that slipped through because
parsing gave up costs the whole review.

Parsing uses `shlex` in POSIX mode with `punctuation_chars` so quoting is
respected (`rg "a|b"`, `grep 'x;y'` stay single arguments) while shell
operators (`&&`, `||`, `;`, `|`, redirections, newlines) come back as their
own tokens. Newlines are pulled out of `shlex.whitespace` and treated as a
segment separator like `;` — left as plain whitespace they would let a
denied command on one line hide as an argument to an allowed command on
the next (`ls\nrm -rf /` must not parse as `ls` called with args `rm -rf /`).

Heredocs and here-strings (`<<`, `<<-`, `<<<`) are denied outright rather
than parsed: three review rounds found that a body-skipper built on shlex
tokens misjudges where the shell actually ends the body — matching the
end against a token rather than a raw line let commands after the real
body slip through unseen, and an earlier `<<` in the same command shifted
every later heredoc's position out of sync. A multi-line `python3 -c
'...'` covers the same use case without that risk, so — like `sed`/`awk`/
etc. below — the surface is removed instead of guarded.

Known gap: `python -c` can write files — `python3 -c
"open('f','w').write(...)"` looks identical to any other `-c` invocation
from here, and this hook does not evaluate Python. That gap is intentional
and is covered by `reviewer.md`'s prose rules, not by this hook. Overall
this is a guard against a careless agent overreaching, not a security
boundary against a hostile one — it only ever sees the command string,
never what a shell would actually do with it.

`sed`, `awk`, `sort`, `uniq`, `find`, and `file` are deliberately absent
from the allowlist rather than guarded by flag denylists. Two review
rounds kept turning up new write/exec spellings for them (`sort -uo`,
`sort --out=`, `uniq - out`, `sed -n '1wout'`, `sed -n -e1wout`, GNU
`s///e`, gawk `-iinplace`, `-d`, `-l rwarray`, `-f prog.awk`, `file -C`...),
and `python -c` is allowed anyway and can already do anything those tools
could — so denylisting their flags bought nothing but more spellings to
miss. They now fall through to the default "unknown command" deny below.
"""
import fnmatch
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path.home() / ".claude" / "logs" / "reviewer-bash-guard.jsonl"

# Простые команды: разрешены с любыми аргументами, ничего в них не пишет файлы.
SIMPLE_COMMANDS = {
    "cd", "pwd", "ls", "cat", "head", "tail", "wc", "nl",
    "cut", "tr", "diff", "jq", "echo", "printf", "stat", "shasum",
    "basename", "dirname", "which", "true", "grep",
}

# Всегда запрещены — независимо от флагов. "." (dot-source) и "source"
# проверяются отдельно, так как "." совпадает с обычным аргументом-путём.
ALWAYS_DENIED = {
    "rm", "mv", "cp", "tee", "touch", "mkdir", "chmod", "ln", "dd",
    "truncate", "xargs", "env", "eval", "exec", "sh", "bash", "zsh",
}

GIT_ALLOWED_SUBCOMMANDS = {
    "diff", "log", "show", "rev-parse", "blame", "status",
    "ls-files", "merge-base", "grep", "cat-file",
}

# Единственное разрешённое вхождение "${" — буквальный префикс "${TMPDIR}"
# в цели редиректа. zsh-флаги параметрического раскрытия вроде "${(e)...}"
# вычисляют своё содержимое как команду, а не просто подставляют переменную.
TMPDIR_LITERAL_PREFIX = "${TMPDIR}"

_OPERATOR_CHARS = set("&|;()<>\n")


def _new_lexer(command: str) -> shlex.shlex:
    """Общая настройка shlex — и для обычной токенизации, и для поиска
    настоящих (некавыченных) heredoc-операторов в исходной строке
    (см. find_raw_heredoc_operator)."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="&|;()<>\n")
    lexer.whitespace = " \t\r"  # перевод строки — НЕ пробел, а отдельный разделитель сегментов
    lexer.whitespace_split = True
    lexer.commenters = ""  # '#' не должен незаметно "съедать" часть команды
    return lexer


def tokenize(command: str) -> list[str]:
    """Токенизация с учётом кавычек: `punctuation_chars` даёт отдельные токены
    операторам (&&, ||, ;, |, <, >, ...), но не трогает содержимое кавычек,
    поэтому `rg "a|b"` и `grep 'x;y'` не разваливаются на части."""
    return list(_new_lexer(command))


def find_raw_heredoc_operator(command: str) -> str | None:
    """Первый по порядку появления токен "<<" или "<<<", который не удалось
    ОДНОЗНАЧНО опознать как кавыченный/экранированный декой, — то есть
    настоящий heredoc/here-string оператор шелла, — или None, если такого
    нет. Разобранный токен неотличим от кавыченного аргумента с тем же
    текстом (`rg '<<' src` тоже даёт токен "<<"), поэтому смотрим на сырой
    срез строки в позиции токена: декой — только если там нашлась кавычка
    или обратный слэш (единственное, что может превратить исходные символы
    в этот токен, кроме самих операторных символов); во всех остальных
    случаях, включая ошибку вычисления границ, токен считается оператором
    (fail-closed) — см. reviewer-bash-guard-noheredoc MEDIUM: сравнение по
    точному равенству вместо этого решало "не оператор" при любом промахе
    границ, что превращало промах в ALLOW.

    Для настоящего операторного токена (набран состоянием "c" в shlex —
    только из символов punctuation_chars) это в принципе не может дать
    ложного срабатывания: кавычки и обратный слэш не входят в
    punctuation_chars, так что попадание любого из них в состояние "c"
    сразу обрывает токен раньше — то есть его точный срез не может их
    содержать. Начало/конец среза считаем через instream.tell(): shlex
    дочитывает на 1 символ вперёд, чтобы решить, продолжается ли токен, —
    и не возвращает его обратно в поток, даже если отбрасывает как пробел;
    единственный случай без лишнего чтения — настоящий конец строки, и это
    видно по lexer.state (None только тогда, а не по tell() < len(command),
    которое путает конец строки с "опережающий символ оказался последним")."""
    lexer = _new_lexer(command)
    while True:
        tok = lexer.get_token()
        if tok is None:
            return None
        if tok in ("<<", "<<<"):
            end = lexer.instream.tell()
            # lexer.state есть в реальном shlex.shlex, но отсутствует в тайпшеде;
            # getattr — чтобы не подавлять проверку типов на весь модуль ради одного атрибута.
            if getattr(lexer, "state", None) is not None:
                end -= 1
            start = end - len(tok)
            span = command[start:end] if start >= 0 else ""
            if "'" in span or '"' in span or "\\" in span:
                continue  # похоже на кавычки/экранирование — декой, не оператор
            return tok


def is_separator(token: str) -> bool:
    """';', '&&', '||', '|' и любая последовательность из одних переводов строки."""
    return token in (";", "&&", "||", "|") or (bool(token) and set(token) == {"\n"})


def is_operator_token(token: str) -> bool:
    """Токен целиком из служебных символов, не распознанный явно выше —
    отказ по умолчанию для '(', ')', '<(', '>(', одиночного '&', 'ls;;pwd' и т.п.
    "<<"/"<<<" сюда не входят: find_raw_heredoc_operator в evaluate уже
    проверил каждый такой токен и отклонил его, если в его точном сыром
    срезе НЕ нашлось кавычки/обратного слэша — а токен, набранный из чистых
    операторных символов (состояние "c" в shlex), в принципе не может их
    содержать (см. докстринг find_raw_heredoc_operator). Значит, "<<"/"<<<",
    дошедший сюда, точно кавыченный декой, а не оператор."""
    return bool(token) and token not in ("<<", "<<<") and set(token) <= _OPERATOR_CHARS


def has_substitution(token: str) -> bool:
    """Ищем `$(` и обратную кавычку как подстроку токена, а не как отдельный
    токен `$`/`` ` ``. posix-разбор shlex снимает кавычки до того, как мы видим
    токен, поэтому `"$(git stash)"` в двойных кавычках и `x`touch evil`` без
    пробела превращаются в обычные токены без ведущего `$`/`` ` ``, и старая
    проверка по первому символу токена их пропускала. Отличить после разбора
    "исполнится" (без кавычек, в двойных кавычках) от "буквально" (в одинарных)
    уже нельзя — поэтому запрещаем обе формы всегда, вслепую."""
    return "`" in token or "$(" in token


def strip_tmpdir_prefix(target: str) -> str:
    """Часть цели редиректа после буквального разрешённого префикса
    "${TMPDIR}"/"$TMPDIR", если он есть ЦЕЛИКОМ (у "$TMPDIR" без скобок сразу
    за ним должны идти "/" или конец строки — иначе это не TMPDIR, а другая,
    необъявленная переменная: "$TMPDIRz" ниже читается как "$TMPDIR"+"z" по
    тексту, а в реальном шелле — как пустая подстановка "$TMPDIRz" перед
    "z"). Иначе — вся цель как есть. Общая часть для проверок "$"/фигурных
    скобок/глобов за пределами этого префикса."""
    if target.startswith(TMPDIR_LITERAL_PREFIX):
        return target[len(TMPDIR_LITERAL_PREFIX):]
    if target == "$TMPDIR" or target.startswith("$TMPDIR/"):
        return target[len("$TMPDIR"):]
    return target


def redirect_target_dollar_ok(target: str) -> bool:
    """"$" в цели редиректа разрешён только внутри одного из двух буквальных
    префиксов целиком (см. strip_tmpdir_prefix) — не как другая переменная,
    не второй раз, не как zsh-флаги вроде "${(e)...}" после него. Без этой
    проверки наивная текстовая замена в is_review_tmp_target доверяла бы
    "$TMPDIRz/../x" как будто это "$TMPDIR"+"z/../x", хотя реальный шелл
    читает "$TMPDIRz" как несуществующую переменную (пустую строку)."""
    return "$" not in strip_tmpdir_prefix(target)


def is_review_tmp_target(target: str) -> bool:
    """Цель редиректа разрешена, только если её basename — review-*.md,
    а сама она (после раскрытия буквального $TMPDIR/${TMPDIR} и приведения
    через realpath) лежит внутри realpath($TMPDIR). realpath разворачивает
    и симлинки (macOS: /tmp -> /private/tmp), и все "..", так что отдельной
    проверки на ".." не нужно — попытка выйти из TMPDIR перестаёт совпадать
    с префиксом после разрешения."""
    tmpdir = os.environ.get("TMPDIR")
    if not tmpdir:
        return False
    expanded = target.replace("${TMPDIR}", tmpdir).replace("$TMPDIR", tmpdir)
    if not fnmatch.fnmatchcase(os.path.basename(expanded), "review-*.md"):
        return False
    tmp_real = os.path.realpath(tmpdir)
    target_real = os.path.realpath(expanded)
    return target_real == tmp_real or target_real.startswith(tmp_real + os.sep)


def redirect_allowed(fd: str | None, op: str, target: str) -> bool:
    if op == "<":
        return True  # вход из файла ничем не ограничен — это чтение, не запись
    if op == ">" and target == "/dev/null":
        return True
    if fd == "2":
        return False  # для fd 2 разрешены только 2>/dev/null и 2>&1 (обработан отдельно)
    return is_review_tmp_target(target)


def check_rg(args: list[str]) -> tuple[bool, str]:
    if any(a == "--pre" or a.startswith("--pre=") for a in args):
        return False, "rg --pre запрещён ревьюеру: запускает внешний процесс-фильтр"
    return True, ""


def check_git(args: list[str]) -> tuple[bool, str]:
    rest = args
    if rest[:1] == ["-C"]:
        if len(rest) < 2:
            return False, "git -C указан без каталога"
        rest = rest[2:]
    if not rest:
        return False, "git без подкоманды запрещён ревьюеру"
    subcommand, subargs = rest[0], rest[1:]
    if subcommand not in GIT_ALLOWED_SUBCOMMANDS:
        return False, f"подкоманда git «{subcommand}» запрещена ревьюеру: может изменить репозиторий"
    if any(a == "--output" or a.startswith("--output=") for a in args):
        return False, "git ... --output запрещён ревьюеру: пишет файл в обход read-only ревью"
    if subcommand == "grep" and any(a in ("-O", "--open-files-in-pager") for a in subargs):
        return False, "git grep -O/--open-files-in-pager запрещён ревьюеру: открывает внешний редактор"
    return True, ""


def check_python(name: str, args: list[str]) -> tuple[bool, str]:
    if len(args) == 2 and args[0] == "-c":
        return True, ""
    return False, (
        f"{name} разрешён ревьюеру только как «-c \"...\"»: heredoc и чтение "
        "скрипта со stdin («-») запрещены, используйте многострочную строку в кавычках"
    )


def check_pytest(args: list[str]) -> tuple[bool, str]:
    if any(a == "-k" for a in args) or any("::" in a for a in args):
        return True, ""
    return False, "pytest без -k или node id запрещён ревьюеру: слишком широкий прогон"


def check_uv(args: list[str]) -> tuple[bool, str]:
    if len(args) >= 2 and args[0] == "run" and args[1] in ("python", "python3"):
        return check_python(args[1], args[2:])
    if len(args) >= 2 and args[0] == "run" and args[1] == "pytest":
        return check_pytest(args[2:])
    return False, "ревьюеру разрешены только «uv run python[3]» и «uv run pytest»"


def check_command_tokens(tokens: list[str]) -> tuple[bool, str]:
    if not tokens:
        return True, ""  # пустой сегмент (например, между двумя ';') — no-op

    name, args = tokens[0], tokens[1:]

    if name == "." or name in ALWAYS_DENIED:
        return False, f"команда «{name}» запрещена ревьюеру: может изменить рабочее дерево"
    if name == "source":
        return False, "source запрещён ревьюеру: исполняет произвольный скрипт в текущей оболочке"
    if name in SIMPLE_COMMANDS:
        return True, ""
    if name == "rg":
        return check_rg(args)
    if name == "git":
        return check_git(args)
    if name in ("python", "python3"):
        return check_python(name, args)
    if name == "pytest":
        return check_pytest(args)
    if name == "uv":
        return check_uv(args)

    return False, f"команда «{name}» не входит в список разрешённых ревьюеру"


def check_segment(tokens: list[str]) -> tuple[bool, str]:
    """Вытаскивает и проверяет редиректы, попутно отказывая на любую
    подстановку/скобки/фон, и передаёт очищенные токены на проверку команды."""
    clean: list[str] = []
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]

        if has_substitution(tok):
            return False, "подстановка команд ($(...) или обратные кавычки) запрещена ревьюеру"

        if "${" in tok:
            return False, "параметрическое раскрытие «${...}» запрещено ревьюеру: некоторые флаги (например zsh «${(e)...}») исполняют команду"

        if tok in ("<", ">", ">>"):
            if i + 1 >= n:
                return False, f"редирект «{tok}» без цели"
            target = tokens[i + 1]
            if has_substitution(target):
                return False, "подстановка команд ($(...) или обратные кавычки) запрещена ревьюеру"
            if not redirect_target_dollar_ok(target):
                return False, "«$» в цели редиректа запрещён ревьюеру, кроме буквального «$TMPDIR»/«${TMPDIR}» целиком в начале пути"
            # то, что идёт после разрешённого префикса TMPDIR (если он есть), —
            # общая часть цели для проверок фигурных скобок и глобов ниже.
            rest_of_target = strip_tmpdir_prefix(target)
            if "{" in rest_of_target or "}" in rest_of_target:
                return False, "фигурные скобки «{}» в цели редиректа запрещены ревьюеру: brace expansion шелла может увести путь за пределы TMPDIR"
            if any(ch in rest_of_target for ch in ("*", "?", "[")):
                return False, "глоб-символы «*»/«?»/«[» в цели редиректа запрещены ревьюеру: shell-глоббинг может подставить неожиданный путь"
            # голая цифра прямо перед редиректом — это номер файлового дескриптора
            # (2>...), а не отдельный аргумент команды; при разрешённом редиректе
            # её нужно убрать из аргументов, а не оставлять как лишнее слово.
            fd = clean[-1] if clean and clean[-1] in ("1", "2") else None
            if not redirect_allowed(fd, tok, target):
                return False, f"редирект «{tok} {target}» запрещён ревьюеру"
            if fd is not None:
                clean.pop()
            i += 2
            continue

        if tok == ">&":
            if clean and clean[-1] == "2" and i + 1 < n and tokens[i + 1] == "1":
                clean.pop()
                i += 2
                continue
            return False, "редирект файловых дескрипторов запрещён ревьюеру, кроме 2>&1"

        if tok == "&>":
            if i + 1 < n and tokens[i + 1] == "/dev/null":
                i += 2
                continue
            return False, "ревьюеру разрешён только редирект &>/dev/null"

        if is_operator_token(tok):
            return False, f"служебный символ «{tok}» не поддерживается и запрещён ревьюеру"

        clean.append(tok)
        i += 1

    return check_command_tokens(clean)


def evaluate(command: str) -> tuple[bool, str]:
    """Разбивает команду на сегменты по &&, ||, ;, |, переводам строки (вне кавычек)
    и требует, чтобы каждый прошёл проверку. Heredoc/here-string проверяется
    первым и на всей команде целиком: он не сегмент и не тело, которое можно
    аккуратно пропустить (см. find_raw_heredoc_operator и докстринг модуля)."""
    try:
        heredoc_op = find_raw_heredoc_operator(command)
        if heredoc_op is not None:
            return False, (
                f"heredoc/here-string «{heredoc_op}» запрещён ревьюеру: перепишите как "
                "«python3 -c '...'» с многострочной строкой в кавычках"
            )
        tokens = tokenize(command)
    except Exception as error:
        return False, f"команда не разобрана как shell-выражение (незакрытая кавычка?): {error}"

    pending: list[str] = []
    for tok in tokens:
        if is_separator(tok):
            ok, reason = check_segment(pending)
            if not ok:
                return False, reason
            pending = []
            continue
        pending.append(tok)

    return check_segment(pending)


def log_event(command: str | None, decision: str, reason: str | None) -> None:
    """Каждое решение — allow и deny. Логирование никогда не должно быть причиной отказа."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as handle:
            handle.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "command": command,
                "decision": decision,
                "reason": reason,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def respond(decision: str, reason: str | None = None) -> None:
    payload = {"hookEventName": "PreToolUse", "permissionDecision": decision}
    if reason:
        payload["permissionDecisionReason"] = reason
    print(json.dumps({"hookSpecificOutput": payload}))
    sys.exit(0)


def allow(command: str | None) -> None:
    log_event(command, "allow", None)
    respond("allow")


def deny(command: str | None, reason: str) -> None:
    log_event(command, "deny", reason)
    respond("deny", reason)


def main() -> None:
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        deny(command if isinstance(command, str) else None, "команда не найдена или пуста")
        return

    ok, reason = evaluate(command)
    if ok:
        allow(command)
    else:
        deny(command, reason)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Fail-closed: в отличие от config-protection, любая ошибка разбора —
        # это отказ, а не разрешение. Переформулировать команду дёшево,
        # потерять diff, который проверяет ревьюер, — нет.
        deny(None, f"хук не смог разобрать вход — отказ по умолчанию: {error!r}")
