#!/usr/bin/env python3
"""Морфологическая обработка семантического ядра для SEO (русский язык).

Зачем: русский флективный — один смысл порождает десятки словоформ
("купить телефон", "куплю телефоны", "покупка телефона"). Чтобы чистить,
дедуплицировать и группировать ядро, фразы приводят к леммам.

Подкоманды:
  lemmatize  — каждое слово фразы → его лемма (начальная форма)
  normalize  — фраза → нормализованный ключ: отсортированные значимые
               леммы без предлогов/союзов/частиц (для группировки и дедупа)
  dedup      — схлопнуть фразы-дубли, отличающиеся только словоформой
  cluster    — сгруппировать фразы по общим леммам (быстрый оффлайн-препроцесс;
               коммерческую кластеризацию по пересечению ТОП выдачи он не заменяет)

Бэкенды:
  pymorphy3 (по умолчанию) — оффлайн, быстрый, словарный.
  pymystem3 (--mystem)     — обёртка над Yandex MyStem: контекстное снятие
                             омонимии (при первом запуске качает бинарник).

Вход: путь к файлу первым аргументом ИЛИ stdin. Одна фраза на строку.
Пустые строки и дубли строк игнорируются.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

# Служебные части речи, которые выкидываем из значимого ядра.
# pymorphy3 POS: https://pymorphy2.readthedocs.io/en/stable/user/grammemes.html
_PYMORPHY_SERVICE_POS = {"PREP", "CONJ", "PRCL", "INTJ"}
# MyStem помечает часть речи первой граммемой разбора.
_MYSTEM_SERVICE_POS = {"PR", "CONJ", "PART", "INTJ"}
# Страховочный список служебных слов на случай, если бэкенд не дал часть речи.
_STOPWORDS = {
    "и", "в", "во", "на", "с", "со", "по", "для", "от", "до", "к", "ко",
    "у", "о", "об", "обо", "за", "из", "же", "ли", "бы", "а", "но", "или",
    "что", "как", "это", "не", "ни", "при", "под", "над", "без", "про",
}


def read_phrases(path: str | None) -> list[str]:
    """Читает фразы из файла или stdin, сохраняя порядок и убирая дубли строк."""
    stream = open(path, encoding="utf-8") if path else sys.stdin
    try:
        seen: set[str] = set()
        phrases: list[str] = []
        for line in stream:
            phrase = line.strip()
            if phrase and phrase not in seen:
                seen.add(phrase)
                phrases.append(phrase)
        return phrases
    finally:
        if path:
            stream.close()


def tokenize(phrase: str) -> list[str]:
    """Разбивает фразу на слова (буквы и цифры), приводит к нижнему регистру."""
    tokens: list[str] = []
    current: list[str] = []
    for ch in phrase.lower():
        if ch.isalnum():
            current.append(ch)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


class Analyzer:
    """Единый интерфейс над pymorphy3 / pymystem3: слово → (лемма, служебное?)."""

    def __init__(self, use_mystem: bool = False) -> None:
        self.use_mystem = use_mystem
        if use_mystem:
            try:
                from pymystem3 import Mystem
            except ImportError:
                _die("pymystem3 не установлен. Установи: pip install pymystem3")
            self._mystem = Mystem()
        else:
            try:
                import pymorphy3
            except ImportError:
                _die(
                    "pymorphy3 не установлен.\n"
                    "  Установи:  pip install pymorphy3\n"
                    "  Для украинского добавь:  pip install pymorphy3-dicts-uk"
                )
            self._morph = pymorphy3.MorphAnalyzer()

    def analyze_word(self, word: str) -> tuple[str, bool]:
        """Возвращает (лемма, is_service) для одиночного слова (pymorphy3)."""
        parse = self._morph.parse(word)[0]
        pos = parse.tag.POS
        is_service = pos in _PYMORPHY_SERVICE_POS or word in _STOPWORDS
        return parse.normal_form, is_service

    def analyze_phrase(self, phrase: str) -> list[tuple[str, bool]]:
        """Возвращает [(лемма, is_service), ...] для всех слов фразы."""
        if not self.use_mystem:
            return [self.analyze_word(w) for w in tokenize(phrase)]
        return self._analyze_phrase_mystem(phrase)

    def _analyze_phrase_mystem(self, phrase: str) -> list[tuple[str, bool]]:
        result: list[tuple[str, bool]] = []
        for item in self._mystem.analyze(phrase):
            analysis = item.get("analysis")
            if not analysis:
                continue
            best = analysis[0]
            lemma = best.get("lex", item["text"]).strip().lower()
            gr = best.get("gr", "")
            head_pos = gr.replace("=", ",").split(",")[0] if gr else ""
            is_service = head_pos in _MYSTEM_SERVICE_POS or lemma in _STOPWORDS
            if lemma:
                result.append((lemma, is_service))
        return result


def significant_lemmas(analyzer: Analyzer, phrase: str) -> list[str]:
    """Значимые леммы фразы: не служебные и длиннее одного символа."""
    return [
        lemma
        for lemma, is_service in analyzer.analyze_phrase(phrase)
        if not is_service and len(lemma) > 1
    ]


def normalize_key(analyzer: Analyzer, phrase: str) -> str:
    """Нормализованный ключ фразы: уникальные значимые леммы, отсортированы."""
    return " ".join(sorted(set(significant_lemmas(analyzer, phrase))))


def cmd_lemmatize(args: argparse.Namespace, analyzer: Analyzer) -> None:
    for phrase in read_phrases(args.input):
        lemmas = [lemma for lemma, _ in analyzer.analyze_phrase(phrase)]
        print(f"{phrase}\t{' '.join(lemmas)}")


def cmd_normalize(args: argparse.Namespace, analyzer: Analyzer) -> None:
    for phrase in read_phrases(args.input):
        print(f"{phrase}\t{normalize_key(analyzer, phrase)}")


def cmd_dedup(args: argparse.Namespace, analyzer: Analyzer) -> None:
    """Оставляет по одной фразе на каждый нормализованный ключ (первую по порядку)."""
    groups: dict[str, list[str]] = defaultdict(list)
    for phrase in read_phrases(args.input):
        groups[normalize_key(analyzer, phrase)].append(phrase)
    kept = removed = 0
    for phrases in groups.values():
        print(phrases[0])
        kept += 1
        removed += len(phrases) - 1
    print(f"# оставлено {kept}, схлопнуто дублей {removed}", file=sys.stderr)


def cmd_cluster(args: argparse.Namespace, analyzer: Analyzer) -> None:
    """Группирует фразы через связные компоненты по числу общих лемм.

    Порог --min-common задаёт, сколько лемм должны совпасть, чтобы две фразы
    попали в один кластер. Транзитивно (A~B, B~C → все вместе).
    Сложность O(n^2) — для ядра до нескольких тысяч фраз это нормально.
    """
    phrases = read_phrases(args.input)
    if len(phrases) > 5000:
        print(
            f"# внимание: {len(phrases)} фраз, O(n^2) кластеризация будет медленной; "
            "для больших ядер режь на регионы/направления",
            file=sys.stderr,
        )
    lemma_sets = [set(significant_lemmas(analyzer, p)) for p in phrases]

    parent = list(range(len(phrases)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(phrases)):
        for j in range(i + 1, len(phrases)):
            if len(lemma_sets[i] & lemma_sets[j]) >= args.min_common:
                parent[find(i)] = find(j)

    clusters: dict[int, list[int]] = defaultdict(list)
    for idx in range(len(phrases)):
        clusters[find(idx)].append(idx)

    ordered = sorted(clusters.values(), key=len, reverse=True)
    for n, members in enumerate(ordered, 1):
        core = Counter()
        for idx in members:
            core.update(lemma_sets[idx])
        top = ", ".join(lemma for lemma, _ in core.most_common(3)) or "—"
        print(f"## Кластер {n} ({len(members)}) — ядро: {top}")
        for idx in members:
            print(f"  {phrases[idx]}")
        print()


def _die(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Морфология семантического ядра (RU) для SEO.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Примеры:\n"
        "  python semcore.py normalize keys.txt\n"
        "  cat keys.txt | python semcore.py dedup\n"
        "  python semcore.py cluster keys.txt --min-common 2 --mystem",
    )
    parser.add_argument("--mystem", action="store_true", help="использовать Yandex MyStem вместо pymorphy3")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, helptext in (
        ("lemmatize", cmd_lemmatize, "слова фразы → леммы"),
        ("normalize", cmd_normalize, "фраза → ключ из значимых лемм"),
        ("dedup", cmd_dedup, "схлопнуть дубли-словоформы"),
        ("cluster", cmd_cluster, "сгруппировать по общим леммам"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("input", nargs="?", help="файл с фразами (иначе stdin)")
        if name == "cluster":
            p.add_argument("--min-common", type=int, default=2, help="сколько общих лемм нужно для склейки (по умолчанию 2)")
        p.set_defaults(func=func)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    analyzer = Analyzer(use_mystem=args.mystem)
    args.func(args, analyzer)


if __name__ == "__main__":
    main()
