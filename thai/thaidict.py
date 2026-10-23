"""Транскрипция тайских слов из Викисловаря (выгрузка kaikki.org), без догадок.

Викисловарь даёт Paiboon (bp/dt/g для непридыхательных, p/t/k для придыхательных).
Автор курса выбрал вид p/ph, t/th, k/kh: так русскому уху понятнее, что «kh» это «к» с выдохом.
Меняем только начальную согласную слога, гласные и значки тона остаются как в словаре.
"""
import json
import os
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.environ.get("THAI_DATA") or os.path.join(HERE, "data"))  # по умолчанию ./data рядом с кодом
KAIKKI = f"{DATA}/build/kaikki-thai.jsonl"
INDEX = f"{DATA}/build/dict_index.json"


def build_index():
    idx = {}
    for line in open(KAIKKI, encoding="utf-8"):
        d = json.loads(line)
        e = idx.setdefault(d["word"], {"p": [], "g": []})
        for s in d.get("sounds", []):
            if "Paiboon" in (s.get("raw_tags") or []) and s["roman"] not in e["p"]:
                e["p"].append(s["roman"])
        for s in d.get("senses", []):
            for g in s.get("glosses", [])[:1]:
                if len(e["g"]) < 4 and g not in e["g"]:
                    e["g"].append(g)
    idx = {w: e for w, e in idx.items() if e["p"]}
    json.dump(idx, open(INDEX, "w", encoding="utf-8"), ensure_ascii=False)
    return idx


def load_index():
    if not os.path.exists(INDEX):
        return build_index()
    return json.load(open(INDEX, encoding="utf-8"))


def _syl(s):
    for src, dst in (("bp", "p"), ("dt", "t"), ("g", "k")):
        if s.startswith(src):
            return dst + s[len(src):]
    if s.startswith("ch"):
        return s
    for c in "ptk":
        if s.startswith(c):
            return c + "h" + s[1:]
    return s


_DIPH = re.compile("([iuʉ])([\u0300\u0301\u0302\u030c]?)\\1a")


def convert(paiboon):
    """Paiboon -> наш вид: 'kɔ̀ɔp-kun' -> 'khɔ̀ɔp-khun', 'bpai' -> 'pai', 'kùuat' -> 'khùat'.

    Долгие дифтонги iia/uua/ʉʉa пишем ia/ua/ʉa: короткие почти не встречаются, а читается проще.
    """
    paiboon = unicodedata.normalize("NFC", _DIPH.sub(r"\1\2a", unicodedata.normalize("NFD", paiboon)))
    return " ".join("-".join(_syl(x) for x in w.split("-")) for w in paiboon.split(" "))


TONE_MARKS = {"̀": "low", "́": "high", "̂": "falling", "̌": "rising"}


def tone_of(syllable):
    """Тон слога по значку в транскрипции; без значка средний."""
    for ch in unicodedata.normalize("NFD", syllable):
        if ch in TONE_MARKS:
            return TONE_MARKS[ch]
    return "mid"


MARK = {"mid": "", "low": "\u0300", "falling": "\u0302", "high": "\u0301", "rising": "\u030c"}
VOWELS = set("aeiouɛɔəʉ")


def set_tone(syl, tone):
    s = "".join(c for c in unicodedata.normalize("NFD", syl) if c not in TONE_MARKS)
    for i, c in enumerate(s):
        if c in VOWELS:
            s = s[:i + 1] + MARK[tone] + s[i + 1:]
            break
    return unicodedata.normalize("NFC", s)


def tone_variants(tr):
    """Та же транскрипция с другим тоном одного слога: правдоподобные неверные варианты для чтения."""
    words, out = tr.split(" "), set()
    for wi, w in enumerate(words):
        syls = w.split("-")
        for si, syl in enumerate(syls):
            for t in MARK:
                if t != tone_of(syl):
                    new = "-".join(syls[:si] + [set_tone(syl, t)] + syls[si + 1:])
                    out.add(" ".join(words[:wi] + [new] + words[wi + 1:]))
    out.discard(tr)
    return sorted(out)
