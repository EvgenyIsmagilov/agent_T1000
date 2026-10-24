"""Fingerprint scan for personal persona text (persona-guard.py)."""
import re
import unicodedata
from pathlib import Path

MIN_LINE_LEN = 30
UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
BULLET = re.compile(r"^[-*+]\s+")
# Кодировки, в которых персона может лежать в чужом файле.
ENCODINGS = ("utf-8", "cp1251", "utf-16-le", "utf-16-be")


class ScanError(Exception):
  pass


def normalise(text: str) -> str:
  # Только буквы и цифры в NFKC и casefold: переносы строк, пробелы, тире,
  # кавычки склейки литералов и NFD больше не прячут совпадение.
  text = UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
  text = unicodedata.normalize("NFKC", text).casefold()
  return "".join(c for c in text if c.isalnum())


def read_lines(path: Path) -> list[str]:
  try:
    raw = path.read_bytes()
  except OSError as e:
    raise ScanError(f"cannot read {path.name}: {e.strerror}")
  return raw.decode("utf-8", errors="replace").split("\n")


def load_fingerprints(root: Path) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
  # (номер строки в файле, нормализованный отпечаток): маркеры и строки персоны.
  # Пустой набор значит, что защита молча отключена, поэтому это ошибка.
  stripped = [BULLET.sub("", l.strip()) for l in read_lines(root / ".claude" / "persona.md")]
  lines = [(i, normalise(l)) for i, l in enumerate(stripped, 1) if len(l) >= MIN_LINE_LEN]
  markers = [(i, normalise(l)) for i, l in enumerate(read_lines(root / ".claude" / "persona-markers.txt"), 1)]
  lines = [x for x in lines if x[1]]
  markers = [x for x in markers if x[1]]
  if not lines:
    raise ScanError("persona.md has no usable fingerprint lines")
  if not markers:
    raise ScanError("persona-markers.txt has no usable markers")
  return markers, lines


def find_hits(fingerprints, raw: bytes) -> list[tuple[str, int]]:
  # Список (kind, номер строки отпечатка); текст совпадения не возвращается.
  markers, lines = fingerprints
  norms = {normalise(raw.decode(enc, errors="replace")) for enc in ENCODINGS}
  hits = []
  for kind, items in (("marker", markers), ("persona-line", lines)):
    for n, fp in items:
      if any(fp in t for t in norms):
        hits.append((kind, n))
  return hits
