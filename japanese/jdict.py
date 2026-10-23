"""Японские словари: JMdict (слова, чтения, русские и английские значения) и KANJIDIC2 (кандзи).

Чтения и значения берём только отсюда, не из головы. Индексы строятся один раз из выгрузок
EDRDG (CC BY-SA 4.0) и лежат рядом в build/.
"""
import functools
import json
import os
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.environ.get("JP_DATA") or os.path.join(HERE, "data"))  # по умолчанию ./data рядом с кодом
BUILD = f"{DATA}/build"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
BAD_INF = {"irregular okurigana usage", "word containing irregular kanji usage", "word containing out-dated kanji",
           "word containing out-dated kanji or kanji usage", "rarely used kanji form", "search-only kanji form",
           "io", "iK", "oK", "rK", "sK"}


def build_words():
    """expression -> [{r: чтения, ru: [...], en: [...], pri: bool}] для каждой письменной формы и каждого чтения."""
    idx = {}
    for _, e in ET.iterparse(f"{BUILD}/JMdict", events=("end",)):
        if e.tag != "entry":
            continue
        kebs = [k.findtext("keb") for k in e.findall("k_ele")]
        # пометки форм: io неправильная окуригана, iK/oK/rK/sK неправильная, устаревшая, редкая, только для поиска
        bad = [k.findtext("keb") for k in e.findall("k_ele") if {x.text for x in k.findall("ke_inf")} & BAD_INF]
        rebs = [r.findtext("reb") for r in e.findall("r_ele")]
        pri = any(x.text in ("news1", "ichi1", "spec1", "spec2", "gai1") for x in e.iter() if x.tag in ("ke_pri", "re_pri"))
        ru, en = [], []
        for s in e.findall("sense"):
            for g in s.findall("gloss"):
                lang = g.get(XML_LANG, "eng")
                if lang == "rus" and len(ru) < 4:
                    ru.append(g.text)
                elif lang == "eng" and len(en) < 4:
                    en.append(g.text)
        rec = {"r": rebs, "k": kebs, "bad": bad, "ru": ru, "en": en, "pri": pri}
        for form in set(kebs + rebs):
            idx.setdefault(form, []).append(rec)
        e.clear()
    json.dump(idx, open(f"{BUILD}/words_index.json", "w", encoding="utf-8"), ensure_ascii=False)
    return idx


def build_kanji():
    idx = {}
    root = ET.parse(f"{BUILD}/kanjidic2.xml").getroot()
    for c in root.findall("character"):
        ch = c.findtext("literal")
        rm = c.find("reading_meaning")
        on, kun, en = [], [], []
        if rm is not None:
            for g in rm.findall("rmgroup"):
                on += [r.text for r in g.findall("reading") if r.get("r_type") == "ja_on"]
                kun += [r.text for r in g.findall("reading") if r.get("r_type") == "ja_kun"]
                en += [m.text for m in g.findall("meaning") if m.get("m_lang") is None]
        misc = c.find("misc")
        idx[ch] = {"on": on, "kun": kun, "en": en[:5],
                   "grade": int(misc.findtext("grade") or 0), "freq": int(misc.findtext("freq") or 9999),
                   "strokes": int(misc.findtext("stroke_count") or 0)}
    json.dump(idx, open(f"{BUILD}/kanji_index.json", "w", encoding="utf-8"), ensure_ascii=False)
    return idx


def load(name, builder):
    p = f"{BUILD}/{name}.json"
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else builder()


@functools.cache
def words():
    return load("words_index", build_words)


@functools.cache
def kanji():
    return load("kanji_index", build_kanji)


def lookup(expr, reading=None):
    """Статьи словаря для записи; если дано чтение, только статьи с этим чтением."""
    recs = words().get(expr, [])
    return [r for r in recs if reading is None or reading in r["r"]]
