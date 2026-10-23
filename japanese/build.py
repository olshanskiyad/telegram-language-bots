"""Сборка японской базы: bank.json для теста уровня, урока «два чтения кандзи», дат и слуха.

Чтения только из JMdict/KANJIDIC2 и правил jpnum (сверены со статьями JMdict).
Русские значения слов из JMdict; русские значения кандзи теста написаны вручную и сверяются
глазами с английскими значениями KANJIDIC2 в отчёте.

Сначала python3 fetch_data.py: он скачивает словари, списки JLPT, Tatoeba и шрифт в JP_DATA/build (см. README).

  python3 build.py           проверка и сборка, отчёт в build/report.txt (нужно около 1 ГБ памяти)
  python3 build.py --audio   плюс озвучка и записи носителей (Tatoeba, по одной с паузой)
  python3 build.py --audio-only   только озвучка готового bank.json, без словарей в памяти

Азбука с нуля (content/abc.txt) собирается модулем azbuka: проверки, чтения, картинки знаков в DATA/img.
"""
import asyncio
import csv
import itertools
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request

import azbuka as A
import drills
import jdict as J
import jpnum as N
import tts

DATA = J.DATA
B = J.BUILD
OUT = f"{DATA}/bank.json"
REPORT = f"{B}/report.txt"
NATIVE_DIR = f"{DATA}/audio/native"
IMG = f"{DATA}/img"
BANNED = ("\u2014", "\u2013")
LEVELS = ["N5", "N4", "N3"]
errors, report = [], []
K = {}  # индекс KANJIDIC2: main() заполняет его при сборке (для озвучки не нужен), поэтому build.py импортируется без словарей
KANJI_RE = re.compile(r"[一-鿿々]")
KANA_RE = re.compile(r"^[぀-ヿー]+$")

# Русские значения кандзи теста: по 12 на уровень, только из списка уровня (сборка проверяет).
KANJI_RU = {
    "N5": {"日": "день; солнце", "人": "человек", "大": "большой", "見": "смотреть", "月": "луна; месяц",
           "学": "учёба", "山": "гора", "水": "вода", "食": "есть, еда", "車": "машина", "雨": "дождь", "書": "писать"},
    "N4": {"会": "встречаться", "新": "новый", "開": "открывать", "思": "думать", "家": "дом, семья", "朝": "утро",
           "春": "весна", "花": "цветок", "買": "покупать", "駅": "станция", "魚": "рыба", "歌": "песня"},
    "N3": {"頭": "голова", "光": "свет", "飛": "летать", "客": "гость", "港": "порт, гавань", "美": "красота",
           "若": "молодой", "残": "оставаться", "深": "глубокий", "声": "голос", "葉": "лист (растения)", "石": "камень"},
}

# Урок «два чтения»: пары слов с одним кандзи, чтение одиночного (кунное) и в составе (онное).
ONKUN = [("食", "食べる", "たべる", "食堂", "しょくどう"), ("大", "大きい", "おおきい", "大学", "だいがく"),
         ("山", "山", "やま", "富士山", "ふじさん"), ("水", "水", "みず", "水曜日", "すいようび"),
         ("生", "生まれる", "うまれる", "学生", "がくせい"), ("見", "見る", "みる", "見学", "けんがく"),
         ("人", "人", "ひと", "日本人", "にほんじん"), ("行", "行く", "いく", "旅行", "りょこう"),
         ("書", "書く", "かく", "辞書", "じしょ"), ("日", "日", "ひ", "毎日", "まいにち")]
ONKUN_EXCEPT = [("今日", "きょう"), ("一人", "ひとり"), ("大人", "おとな"), ("花火", "はなび")]

GRAMMAR = [
    ("N5", "わたし（　）がくせいです。", ["は", "を", "に", "で"], 0, "я студент"),
    ("N5", "まいにち コーヒー（　）のみます。", ["を", "に", "で", "へ"], 0, "каждый день пью кофе"),
    ("N5", "としょかん（　）べんきょうします。", ["で", "を", "に", "は"], 0, "занимаюсь в библиотеке"),
    ("N5", "きのう えいがを（　）。", ["みました", "みます", "みましょう", "みて"], 0, "вчера смотрел(а) фильм"),
    ("N4", "にほんへ いった（　）が あります。", ["こと", "もの", "ところ", "の"], 0, "бывал(а) в Японии"),
    ("N4", "あめが ふって（　）ので、かさを もって いきます。", ["いる", "ある", "おく", "みる"], 0,
     "идёт дождь, поэтому беру зонт"),
]

CONFUSE = {"シ": "ツ", "ツ": "シ", "ソ": "ン", "ン": "ソ", "ク": "ケ", "ケ": "ク", "ワ": "ウ", "ウ": "ワ", "ル": "レ",
           "レ": "ル", "ア": "マ", "マ": "ア", "コ": "ユ", "ユ": "コ", "チ": "テ", "テ": "チ", "ヌ": "ス", "ス": "ヌ",
           "さ": "ち", "ち": "さ", "き": "さ", "ぬ": "め", "め": "ぬ", "ね": "れ", "れ": "わ", "わ": "ね", "る": "ろ",
           "ろ": "る", "は": "ほ", "ほ": "は", "あ": "お", "お": "あ", "い": "り", "り": "い", "ゆ": "よ", "よ": "ゆ",
           "ま": "も", "も": "ま", "た": "な", "な": "た", "こ": "に", "に": "こ"}
SMALL = {"ゃ": "や", "ゅ": "ゆ", "ょ": "よ", "ャ": "ヤ", "ュ": "ユ", "ョ": "ヨ", "っ": "", "ッ": ""}


def check(where, text):
    if text and any(b in text for b in BANNED):
        errors.append(f"{where}: длинное тире")


def hira(s):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def clean_ru(glosses):
    """Первое короткое русское значение без помет, ссылок и нумерации."""
    for g in glosses:
        g = re.sub(r"^\d+[.)]\s*", "", g)
        while re.search(r"\([^()]*\)", g):
            g = re.sub(r"\([^()]*\)", "", g)
        g = re.sub(r"\{[^}]*\}", "", g).replace("[", "").replace("]", "")
        if re.search(r"[぀-ヿ一-鿿]", g):
            continue
        g = re.split(r"\s\d+\)", g)[0]
        g = re.sub(r"\s+,", ",", re.sub(r"\s+", " ", g.split(";")[0])).strip(" ,.:")
        if 1 < len(g) <= 40:
            return g
    return None


# Ручные правки: у JMdict на этих словах устаревший, многословный или не тот перевод (ручная вычитка).
RU_FIX = {"一万": "десять тысяч", "朝食": "завтрак", "特色": "особенность, отличительная черта", "一つ": "одна штука", "前": "перед, впереди", "上": "верх, наверху", "東": "восток",
          "西": "запад", "南": "юг", "金|かね": "деньги", "話": "рассказ, разговор", "元気": "бодрый, здоровый",
          "時間": "время; час", "見物": "осмотр достопримечательностей", "以下": "ниже, не больше",
          "半|はん": "половина", "地下": "под землёй, подземный", "目": "глаз", "料理": "кулинария; блюдо",
          "大体": "в общем, примерно", "田": "рисовое поле", "主人": "муж; хозяин", "不安": "тревога, беспокойство",
          "持つ": "держать, иметь", "多い": "многочисленный, много", "正月": "Новый год (праздник)",
          "立つ": "стоять", "女子": "девушка, девочка", "心": "сердце, душа", "別": "другой, отдельный",
          "品|しな": "товар, вещь", "白鳥": "лебедь", "台|だい": "подставка; счётное слово машин",
          "昼食": "обед", "牛": "корова, бык", "転ぶ": "падать, упасть", "海外": "за границей", "別れる": "расставаться",
          "中止": "отмена", "急行": "скорый поезд, экспресс", "試す": "пробовать", "運転手": "водитель", "空|そら": "небо", "少ない": "мало, немного",
          "起きる": "вставать, просыпаться", "早い": "ранний", "親切": "добрый, любезный", "中古": "подержанный, б/у",
          "悪い": "плохой", "兄弟": "братья и сёстры", "私": "я", "山": "гора", "富士山": "гора Фудзи", "辞書": "словарь", "見学": "экскурсия, осмотр",
          "大学": "университет", "事": "дело, вещь", "高校生": "старшеклассник", "大事": "важный", "通勤": "поездка на работу",
          "釣り": "рыбалка", "日付": "дата", "決まり": "правило", "見える": "быть видным", "待つ": "ждать",
          "また": "снова, ещё", "七日": "седьмое число", "アパート": "квартира, многоквартирный дом",
          "先": "впереди; раньше", "かける": "вешать; звонить", "便利": "удобный", "役に立つ": "быть полезным",
          "釣": "рыбалка", "関する": "касаться, относиться", "予防": "профилактика",
          "車": "машина, автомобиль", "荷物": "багаж, вещи", "国際": "международный",
          "議会": "парламент", "助ける": "помогать, спасать", "独特": "своеобразный"}


def overlap(a, b):
    wa = set(re.findall(r"[a-z]+", " ".join(a).lower())) - {"to", "a", "the", "of", "be", "an"}
    wb = set(re.findall(r"[a-z]+", " ".join(b).lower())) - {"to", "a", "the", "of", "be", "an"}
    return len(wa & wb)


def word_ru(expr, reading, en_hint=""):
    """Русское значение: статья JMdict, ближе всего совпадающая с английским значением из списка JLPT."""
    recs = [r for r in J.lookup(expr, reading) if expr not in r.get("bad", [])]  # 楽む это неправильная форма 楽しむ
    if en_hint:
        recs = sorted(recs, key=lambda r: -overlap([en_hint], r["en"]))
    for rec in recs:
        ru = RU_FIX.get(f"{expr}|{reading}") or RU_FIX.get(expr) or clean_ru(rec["ru"])
        if ru:
            return ru, rec
    return None, None


def vocab_rows(level):
    return list(csv.DictReader(open(f"{B}/vocab_n{level[1]}.csv", encoding="utf-8")))


def level_kanji(level):
    return [l.strip() for l in open(f"{B}/kanji_n{level[1]}.csv", encoding="utf-8")][1:]


def kanji_upto(level):
    out = set()
    for lv in LEVELS[:LEVELS.index(level) + 1]:
        out |= set(level_kanji(lv))
    return out


VOICED = dict(zip("かきくけこさしすせそたちつてとはひふへほカキクケコサシスセソタチツテトハヒフヘホ",
                  "がぎぐげござじずぜぞだぢづでどばびぶべぼガギグゲゴザジズゼゾダヂヅデドバビブベボ"))
VOICED.update({v: k for k, v in VOICED.items()})
VOICED.update(dict(zip("ぱぴぷぺぽパピプペポ", "ばびぶべぼバビブベボ")))
LONG_O = {"おう": "お", "こう": "こ", "そう": "そ", "とう": "と", "ょう": "ょ", "ゅう": "ゅ", "あさ": "ああさ",
          "ばあ": "ば", "じい": "じ", "おお": "お", "えい": "え"}


GEMINABLE = set("かきくけこさしすせそたちつてとぱぴぷぺぽカキクケコサシスセソタチツテトパピプペポ")
LENGTHEN = {**{c: "う" for c in "おこそとのほもよろごぞどぼぽ"}, **{c: "い" for c in "えけせてねへめれげぜでべぺ"},
            **{c: "ー" for c in "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモラリルレロ"
                                "ガギグゲゴザジズゼゾダデドバビブベボパピプペポ"}}


def kana_distractors(word, rnd):
    """Похожие неверные написания: путаемые знаки, звонкость, ー, маленькие っ/ゃ, долгие гласные."""
    out = set()
    for _ in range(300):
        w = list(word)
        i = rnd.randrange(len(w))
        c = w[i]
        kind = rnd.randrange(7)
        if kind == 0 and c in CONFUSE:
            w[i] = CONFUSE[c]
        elif kind == 1 and c in VOICED:
            w[i] = VOICED[c]
        elif kind == 2 and c in SMALL:
            w[i] = SMALL[c]
        elif kind == 3 and c == "ー":
            w[i] = ""
        elif kind == 3 and re.match(r"[ァ-ヶ]", c) and c not in "ンッ" and i == len(w) - 1:
            w[i] = c + "ー"
        elif kind == 5 and 0 < i and c in GEMINABLE and w[i - 1] not in "っッんンー":  # きて → きって
            w[i] = ("ッ" if "ァ" <= c <= "ヶ" else "っ") + c
        elif kind == 6 and c in LENGTHEN and (i == len(w) - 1 or w[i + 1] not in "うーいゃゅょャュョ"):  # こ → こう
            w[i] = c + LENGTHEN[c]
        elif kind == 4:
            cand = word
            for k, v in LONG_O.items():
                if k in word and rnd.random() < 0.5:
                    cand = word.replace(k, v, 1)
                    break
            if cand != word:
                out.add(cand)
            continue
        else:
            continue
        cand = "".join(w)
        if cand and cand != word:
            out.add(cand)
        if len(out) >= 3:
            break
    return sorted(out)[:3]


def reading_candidates(ch):
    k = K.get(ch, {})
    cands = [hira(r) for r in k.get("on", [])] + [r.split(".")[0].replace("-", "") for r in k.get("kun", [])]
    return [c for c in dict.fromkeys(cands) if c]


def reading_distractors(expr, reading, rnd):
    """Неверные чтения, которые путают онное и кунное: чтения каждого кандзи из KANJIDIC2 вперемешку."""
    parts = [reading_candidates(c)[:4] if KANJI_RE.match(c) else [c] for c in expr]
    valid = {r for rec in J.lookup(expr) for r in rec["r"]}  # у 外 верны и ほか, и そと
    combos = {"".join(p) for p in itertools.product(*parts)} - {reading} - valid
    combos = [c for c in combos if len(c) <= len(reading) + 3]
    rnd.shuffle(combos)
    return combos[:3]


def build_vocab():
    pools = {}
    for lv in LEVELS:
        cands = []
        for r in vocab_rows(lv):
            e, k = r["expression"], r["reading"]
            if re.search(r"[〜～・\s（(]", e) or len(k) > 7:
                continue
            ru, rec = word_ru(e, k, r["meaning"])
            if ru and rec["pri"]:
                cands.append({"jp": e, "kana": k, "ru": ru})
        random.Random(lv).shuffle(cands)
        pools[lv] = cands[:12]
        for x in pools[lv]:
            report.append(f"  слово {lv}: {x['jp']} {x['kana']} = {x['ru']}")
    return pools


def build_kana():
    rnd = random.Random(7)
    hi, ka = [], []
    for r in vocab_rows("N5"):
        e, k = r["expression"], r["reading"]
        if e == k and KANA_RE.match(e) and 3 <= len(e) <= 6:
            ru, rec = word_ru(e, k, r["meaning"])
            if ru:
                (ka if re.search(r"[ァ-ヶ]", e) else hi).append({"jp": e, "kana": k, "ru": ru})
    rnd.shuffle(hi)
    rnd.shuffle(ka)
    pool = hi[:6] + ka[:6]
    for x in pool:
        x["wrong"] = kana_distractors(x["jp"], rnd)
        if len(x["wrong"]) < 3:
            errors.append(f"кана: мало похожих вариантов для {x['jp']}: {x['wrong']}")
        report.append(f"  кана: {x['jp']} = {x['ru']} | неверные: {x['wrong']}")
    return pool


def build_gcourse():
    """content/grammar.txt: уроки грамматики. Проверяем форму: у пропуска один ответ и три разных неверных,
    у перевода четыре разных варианта, в русском нет длинных тире."""
    lessons, cur = [], None
    for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "content/grammar.txt"), encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("#") or not line.strip():
            continue
        if line.startswith("== "):
            n, lv, title = [x.strip() for x in line[3:].split("|")]
            cur = {"n": int(n), "lvl": lv, "title": title, "rule": [], "ex": [], "q": [], "tr": []}
            lessons.append(cur)
            continue
        kind, sep, rest = line.partition(": ")
        parts = [x.strip() for x in rest.split("|")]
        where = f"грамматика {cur['n']}: {line[:40]}"
        if kind == "ex" and sep:
            if len(parts) != 2:
                errors.append(f"{where}: пример не «японский | русский»")
                continue
            cur["ex"].append({"jp": parts[0], "ru": parts[1]})
        elif kind == "q" and sep:
            m = re.fullmatch(r"([^（）]*)（([^（）]+)）([^（）]*)", parts[0]) if len(parts) == 3 else None
            wrong = [w.strip() for w in parts[1].split(" / ")] if m else []
            if not m or len(set(wrong)) != 3 or m[2] in wrong:
                errors.append(f"{where}: пропуск без одного ответа или не три разных неверных")
                continue
            cur["q"].append({"blank": f"{m[1]}（　）{m[3]}", "full": m[1] + m[2] + m[3], "ans": m[2], "wrong": wrong,
                             "ru": parts[2]})
        elif kind == "tr" and sep:
            if len(parts) != 5 or len(set(parts[1:])) != 4:
                errors.append(f"{where}: перевод без четырёх разных вариантов")
                continue
            cur["tr"].append({"ru": parts[0], "opts": parts[1:]})
        else:
            cur["rule"].append(line)
    for i, g in enumerate(lessons, 1):
        if g["n"] != i or len(g["ex"]) < 3 or len(g["q"]) < 5 or not g["tr"] or not g["rule"]:
            errors.append(f"грамматика {g['n']}: номер не по порядку или мало примеров, пропусков, переводов")
        for t in [g["title"]] + g["rule"] + [x["ru"] for x in g["ex"] + g["q"] + g["tr"]]:
            check(f"грамматика {g['n']}", t)
    report.extend(f"  грамматика {g['n']}: {g['title']} | примеров {len(g['ex'])}, пропусков {len(g['q'])}, "
                  f"переводов {len(g['tr'])}" for g in lessons)
    return lessons


def build_kana_ear(course):
    """Кана на слух: слова уроков хираганой (озвучка уже есть) и слова катаканой из списков JLPT.
    Неверные варианты: долгота, маленькие っ/ゃ, звонкость, похожие знаки катаканы."""
    rnd, items, seen = random.Random("ear"), [], set()
    for x in course["kanji"].values():
        for e in x["ex"]:
            if e["kana"] not in seen and 2 <= len(e["kana"]) <= 7:
                seen.add(e["kana"])
                items.append({"kana": e["kana"], "jp": e["jp"], "ru": e["ru"]})
    for lv in LEVELS:
        for r in vocab_rows(lv):
            e = r["expression"].split(";")[0].strip().strip("～〜")
            if re.fullmatch(r"[ァ-ヶー]{2,7}", e) and e not in seen:
                ru, rec = word_ru(e, r["reading"].split(";")[0].strip(), r["meaning"])
                if ru:
                    seen.add(e)
                    items.append({"kana": e, "jp": e, "ru": ru})
    for x in items:
        x["wrong"] = kana_distractors(x["kana"], rnd)
    return [x for x in items if len(x["wrong"]) == 3]


def build_kanji_mean():
    pools = {}
    for lv, d in KANJI_RU.items():
        lk = set(level_kanji(lv))
        pools[lv] = []
        for ch, ru in d.items():
            check(f"кандзи {ch}", ru)
            if ch not in lk:
                errors.append(f"кандзи {ch} не из списка {lv}")
            k = K[ch]
            pools[lv].append({"ch": ch, "ru": ru, "on": k["on"], "kun": k["kun"]})
            report.append(f"  кандзи {lv}: {ch} = {ru} | KANJIDIC: {', '.join(k['en'][:4])}")
    return pools


def build_kanji_read():
    pools = {}
    for lv in LEVELS:
        allowed = kanji_upto(lv)
        own = set(level_kanji(lv))
        kun, on = [], []
        rnd = random.Random("r" + lv)
        for r in vocab_rows(lv):
            e, k = r["expression"], r["reading"]
            ks = KANJI_RE.findall(e)
            if not ks or not set(ks) <= allowed or not (set(ks) & own) or re.search(r"[〜～・\s（(]", e):
                continue
            ru, rec = word_ru(e, k, r["meaning"])
            if not ru:
                continue
            item = {"jp": e, "kana": k, "ru": ru}
            if len(ks) == 1 and len(e) <= 4:
                kun.append(item)
            elif len(ks) >= 2 and len(ks) == len(e):
                on.append(item)
        rnd.shuffle(kun)
        rnd.shuffle(on)
        pool = []
        for group in (kun, on):
            got = 0
            for x in group:
                x["wrong"] = reading_distractors(x["jp"], x["kana"], rnd)
                if len(x["wrong"]) >= 3:
                    x["kind"] = "kun" if group is kun else "on"
                    pool.append(x)
                    report.append(f"  чтение {lv}: {x['jp']} {x['kana']} = {x['ru']} | неверные: {x['wrong']}")
                    got += 1
                if got == 6:
                    break
        pools[lv] = pool
        if len(pool) < 8:
            errors.append(f"чтения {lv}: мало слов ({len(pool)})")
    return pools


def build_onkun():
    pairs = []
    for ch, kw, kr, ow, orr in ONKUN:
        for w, r in ((kw, kr), (ow, orr)):
            ru, rec = word_ru(w, r)
            if not rec:
                errors.append(f"два чтения: {w} {r} нет в JMdict")
        kru, _ = word_ru(kw, kr)
        oru, _ = word_ru(ow, orr)
        pairs.append({"ch": ch, "kun": {"jp": kw, "kana": kr, "ru": kru}, "on": {"jp": ow, "kana": orr, "ru": oru},
                      "wrong_kun": reading_distractors(kw, kr, random.Random(kw)),
                      "wrong_on": reading_distractors(ow, orr, random.Random(ow))})
        report.append(f"  два чтения {ch}: {kw} {kr} ({kru}) / {ow} {orr} ({oru})")
    # у 水 и 山 своих чтений на три неверных не хватает (без добора был один вариант ответа):
    # добираем чтениями слов того же вида из других пар, потом похожими написаниями
    for p in pairs:
        for w in ("kun", "on"):
            x, rnd = p[w], random.Random(p[w]["jp"] + w)
            valid = {r for rec in J.lookup(x["jp"]) for r in rec["r"]}
            sib = [q[w]["kana"] for q in pairs if q is not p and abs(len(q[w]["kana"]) - len(x["kana"])) <= 1]
            rnd.shuffle(sib)
            extra = [r for r in sib + kana_distractors(x["kana"], rnd)
                     if r != x["kana"] and r not in valid and r not in p[f"wrong_{w}"]]
            p[f"wrong_{w}"] = (p[f"wrong_{w}"] + list(dict.fromkeys(extra)))[:3]
            if len(p[f"wrong_{w}"]) < 3:
                errors.append(f"два чтения: мало неверных чтений для {x['jp']}: {p[f'wrong_{w}']}")
    exc = []
    for w, r in ONKUN_EXCEPT:
        ru, rec = word_ru(w, r)
        if not rec:
            errors.append(f"исключение {w} {r} нет в JMdict")
        exc.append({"jp": w, "kana": r, "ru": ru})
    return {"pairs": pairs, "except": exc}


# ---------- курс кандзи: уроки по 5 кандзи, у каждого слово с кунным и слово с онным чтением
COURSE_LEVELS = ["N5", "N4"]
LESSON = 5
KRAD_SUB = {"亻": "化", "𠆢": "个", "丷": "并", "刂": "刈", "𠂉": "乞", "氵": "汁", "扌": "扎", "艹": "艾", "辶": "込",
            "阝": "阡", "⺌": "尚", "灬": "杰", "耂": "老", "礻": "礼", "衤": "初", "疒": "疔", "忄": "忙", "犭": "犯"}
HAN_RE = re.compile(r"[⺀-⿟㐀-鿿豈-﫿\U00020000-\U0002FFFF]")


def kanji_content():
    """content/kanji.txt: кандзи|значение|эмодзи|подсказка[|слова-примеры вручную: «一つ ひとつ; 一月 いちがつ»]."""
    rows = {}
    for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "content/kanji.txt"), encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        ch, ru, emo, hook, *ex = line.rstrip("\n").split("|")
        check(f"кандзи {ch}", ru + hook)
        rows[ch] = {"ru": ru, "emo": emo, "hook": hook,
                    "manual": [tuple(w.split()) for w in ex[0].split(";")] if ex and ex[0].strip() else []}
    return rows


def krad():
    out = {}
    for line in open(f"{B}/kradfile", "rb").read().decode("euc-jp", errors="replace").splitlines():
        if " : " in line and not line.startswith("#"):
            a, b = line.split(" : ")
            out[a.strip()] = set(b.split())
    return out


def kun_stems(ch):
    return [r.replace("-", "").partition(".")[0] for r in K[ch]["kun"]]


def on_forms(ch):
    """Онные чтения хираганой и их изменения в составе слова: がく → がっ, ほん → ぼん, ぽん."""
    out = set()
    for r in K[ch]["on"]:
        h = hira(r)
        out.add(h)
        if h[0] in VOICED:
            out.add(VOICED[h[0]] + h[1:])
        if h[0] in "はひふへほ":
            out.add("ぱぴぷぺぽ"["はひふへほ".index(h[0])] + h[1:])
        if len(h) > 1 and h[-1] in "くつちき":
            out.add(h[:-1] + "っ")
    return out


def kun_word(ch, expr, reading):
    """Кандзи один или с окончанием хираганой, чтение начинается с кунной основы: 山 やま, 食べる たべる."""
    m = re.fullmatch(rf"(お|ご)?{ch}([ぁ-ゖ]*)", expr)
    if not m or not reading.startswith(m[1] or "") or not reading.endswith(m[2]):
        return False
    core = reading[len(m[1] or ""):len(reading) - len(m[2])]
    return bool(core) and core in kun_stems(ch)


def word_kind(ch, expr, reading):
    """Какое чтение у кандзи в слове по правилу урока: один кандзи с онным чтением (本 ほん) тоже онное."""
    if expr == ch and reading in {hira(r) for r in K[ch]["on"]}:
        return "on"
    if kun_word(ch, expr, reading):
        return "kun"
    return "on" if len(expr) > 1 and on_word(expr, reading) else None


def on_word(expr, reading):
    """Только кандзи, и чтение складывается из онных чтений каждого: 学校 がっこう = がく + こう."""
    if not all(KANJI_RE.match(c) for c in expr) or "々" in expr:
        return False

    def go(i, pos):
        if i == len(expr):
            return pos == len(reading)
        return any(reading.startswith(f, pos) and go(i + 1, pos + len(f)) for f in on_forms(expr[i]))
    return go(0, 0)


def build_course():
    content, kr = kanji_content(), krad()
    order = [ch for lv in COURSE_LEVELS for ch in level_kanji(lv)]
    pos = {ch: i for i, ch in enumerate(order)}
    for ch in order:
        if ch not in content:
            errors.append(f"курс: нет значения для {ch}")
    for ch, c in content.items():
        if ch not in pos:
            errors.append(f"курс: {ch} не из списков {COURSE_LEVELS}")
        if "похож" not in c["hook"].lower():
            for part in HAN_RE.findall(c["hook"]):
                if part != ch and KRAD_SUB.get(part, part) not in kr.get(ch, set()) and \
                        not (part == "阝" and "邦" in kr.get(ch, set())):
                    errors.append(f"курс: в подсказке {ch} часть {part}, а KRADFILE даёт {sorted(kr.get(ch, []))}")
    rus = [c["ru"] for c in content.values()]
    for ru in sorted({x for x in rus if rus.count(x) > 1}):
        errors.append(f"курс: значение «{ru}» у двух кандзи")
    # кандидаты: слова JLPT N5-N3 и частые слова JMdict, все кандзи которых есть в курсе
    cands = {}
    seen = set()

    def add(expr, reading, en, lvl):
        if (expr, reading) in seen or re.search(r"[〜～・\s（(]", expr) or len(reading) > 8:
            return
        ks = KANJI_RE.findall(expr)
        if not ks or not all(k in pos for k in ks):
            return
        seen.add((expr, reading))
        for ch in set(ks):
            kind = word_kind(ch, expr, reading)
            if kind and (kind == "on" or lvl < 3):  # кунные слова только из списков JLPT: 午 うま никому не нужно
                cands.setdefault(ch, []).append((expr, reading, en, lvl, kind))
    for lvl, lv in enumerate(LEVELS):
        for r in vocab_rows(lv):
            for expr in r["expression"].split(";"):  # «川; 河», «～円»
                for reading in r["reading"].split(";"):  # «いく; ゆく»
                    add(expr.strip().strip("～〜"), reading.strip().strip("～〜"), r["meaning"], lvl)
    for expr, recs in J.words().items():  # частые слова JMdict, только если в списках JLPT онного слова нет
        for rec in recs:
            if rec["pri"] and expr in rec["k"] and expr not in rec["bad"] and len(expr) <= 3:
                for reading in rec["r"][:1]:
                    add(expr, reading, "; ".join(rec["en"]), 3)
    lessons, kanji, rnd = [], {}, random.Random("course")
    for lv in COURSE_LEVELS:
        chars = level_kanji(lv)
        groups = [chars[i:i + LESSON] for i in range(0, len(chars), LESSON)]
        if len(groups) > 1 and len(groups[-1]) < 3:
            tail = groups.pop()
            groups[-1] = groups[-1] + tail
        for g in groups:
            lessons.append({"n": len(lessons) + 1, "lvl": lv, "kanji": g})
    for les in lessons:
        last = max(pos[ch] for ch in les["kanji"])
        used = set()
        for ch in les["kanji"]:
            stems = {w[:4] for w in re.findall(r"[а-яё]{3,}", content[ch]["ru"])}

            def score(c):
                expr, reading, en, lvl, kind = c
                later = sum(pos[k] > last for k in set(KANJI_RE.findall(expr)))
                ru = word_ru(expr, reading, en)[0] or ""
                return (lvl, not any(st in ru for st in stems), later, (expr, reading) in used, len(expr) > 2, len(reading))
            ex = []
            for expr, reading in content[ch]["manual"]:
                reading, _, forced = reading.partition("=")  # «木曜日 もくようび=on»: 木 онное, хотя 日 здесь кунное
                kind = "exc" if expr[0] == "!" else forced or word_kind(ch, expr, reading)  # «!夕方»: исключение
                expr = expr.lstrip("!")
                ru, rec = word_ru(expr, reading)
                if not kind or not rec:
                    errors.append(f"курс: пример {expr} {reading} для {ch} не по правилу или нет в JMdict")
                    continue
                ex.append({"jp": expr, "kana": reading, "ru": ru, "kind": kind})
            has_kun = any(c[4] == "kun" for c in cands.get(ch, []))
            for kind in ("kun", "on", "on") if not content[ch]["manual"] else ():  # ручной список окончательный
                if len(ex) >= 2 or kind == "on" and has_kun and any(e["kind"] == "on" for e in ex):
                    continue
                for c in sorted((c for c in cands.get(ch, []) if c[4] == kind), key=score):
                    if any((e["jp"], e["kana"]) == c[:2] for e in ex):
                        continue
                    ru, rec = word_ru(c[0], c[1], c[2])
                    if ru:
                        ex.append({"jp": c[0], "kana": c[1], "ru": ru, "kind": kind})
                        used.add((c[0], c[1]))
                        break
            if not ex:
                errors.append(f"курс: нет слов-примеров для {ch}")
            k = K[ch]
            kanji[ch] = {**{k: v for k, v in content[ch].items() if k != "manual"}, "lvl": les["lvl"], "lesson": les["n"], "ex": ex[:2],
                         "on": [r for r in k["on"][:2]], "kun": [r.replace("-", "") for r in k["kun"] if "-" not in r[:1]][:2]}
    # неверные чтения: смесь онных и кунных чтений тех же кандзи, добор из соседних слов урока
    for les in lessons:
        sib = [e["kana"] for ch in les["kanji"] for e in kanji[ch]["ex"]]
        for ch in les["kanji"]:
            for e in kanji[ch]["ex"]:
                valid = {r for rec in J.lookup(e["jp"]) for r in rec["r"]}
                wrong = reading_distractors(e["jp"], e["kana"], rnd)
                extra = list(dict.fromkeys(x for x in sib if x != e["kana"] and x not in valid and x not in wrong))
                rnd.shuffle(extra)  # без повторов: 洋服 пример и у 洋, и у 服, у 駅員 было ようふく дважды
                e["wrong"] = (wrong + extra)[:3]
                if len(set(e["wrong"])) < 3:
                    errors.append(f"курс: мало неверных чтений для {e['jp']}")
    for les in lessons:
        for ch in les["kanji"]:
            x = kanji[ch]
            report.append(f"  урок {les['n']} {ch} {x['ru']}: " + " / ".join(
                f"{e['jp']} {e['kana']} {e['ru']} ({e['kind']}; неверные {','.join(e['wrong'])})" for e in x["ex"]))
    return {"lessons": lessons, "kanji": kanji}


def kanji_num(n):
    d = "〇一二三四五六七八九"
    if n < 10:
        return d[n]
    t, u = divmod(n, 10)
    return ("" if t == 1 else d[t]) + "十" + (d[u] if u else "")


def verify_numbers():
    """Каждое правило jpnum сверяем со статьёй JMdict, если такая есть."""
    checks = [(kanji_num(d) + "日", N.day(d)) for d in range(1, 32)]
    checks += [(kanji_num(m) + "月", N.month(m)) for m in range(1, 13)]
    checks += [(kanji_num(h) + "時", N.HOUR[h]) for h in range(1, 13)]
    checks += [(kanji_num(m) + "分", N.minute(m)) for m in range(1, 11)]
    checks += [(p + "百", N.number(v)) for p, v in (("三", 300), ("六", 600), ("八", 800))]
    checks += [(p + "千", N.number(v)) for p, v in (("三", 3000), ("八", 8000))]
    checks += [(w, r) for w, r, _ in N.WEEKDAY]
    seen = 0
    for w, r in checks:
        recs = J.words().get(w, [])
        if recs:
            seen += 1
            readings = {x for rec in recs for x in rec["r"]}
            if r not in readings:
                errors.append(f"даты/числа: {w} по правилу {r}, в JMdict {sorted(readings)}")
    report.append(f"  даты и числа: сверено со словарём {seen} из {len(checks)}")


def valid_readings():
    def rd(w):
        return sorted({x for rec in J.words().get(w, []) for x in rec["r"]})
    return {"m": {m: sorted(set(rd(kanji_num(m) + "月")) | {N.month(m)}) for m in range(1, 13)},
            "d": {d: sorted(set(rd(kanji_num(d) + "日")) | {N.day(d)}) for d in range(1, 32)},
            "h": {h: sorted(set(rd(kanji_num(h) + "時")) | {N.HOUR[h]}) for h in range(1, 13)}}




def build_listen():
    jpn, rus = {}, {}
    for line in open(f"{B}/jpn_sentences.tsv", encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3:
            jpn[p[0]] = p[2]
    for line in open(f"{B}/rus_sentences.tsv", encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3:
            rus[p[0]] = p[2]
    links = {}
    for line in open(f"{B}/jpn-rus_links.tsv", encoding="utf-8"):
        a, b = line.split()[:2]
        if a in jpn and b in rus and a not in links:
            links[a] = rus[b]
    out = []
    for row in csv.reader(open(f"{B}/sentences_with_audio.csv", encoding="utf-8"), delimiter="\t"):
        if len(row) < 4 or row[0] not in links or row[3] not in ("CC BY-NC 4.0", "CC BY 4.0"):
            continue
        jp, ru = jpn[row[0]], links[row[0]]
        if 4 <= len(jp) <= 22 and len(ru) <= 60 and not any(b in ru for b in BANNED):
            out.append({"sid": row[0], "jp": jp, "ru": ru, "audio_id": row[1], "speaker": row[2], "license": row[3],
                        "lvl": 1 if len(jp) <= 9 else 2 if len(jp) <= 15 else 3})
    uniq = {}
    for x in out:
        uniq.setdefault(x["jp"], x)
    out = sorted(uniq.values(), key=lambda x: (x["lvl"], x["sid"]))
    rnd = random.Random(11)
    by_lvl = {l: [x for x in out if x["lvl"] == l] for l in (1, 2, 3)}
    pick = []
    for l, n in ((1, 60), (2, 60), (3, 40)):
        rnd.shuffle(by_lvl[l])
        tom = [x for x in by_lvl[l] if "トム" in x["jp"]][:n // 4]  # у Tatoeba каждая третья фраза про Тома
        pick += tom + [x for x in by_lvl[l] if "トム" not in x["jp"]][:n - len(tom)]
    report.append(f"  слух: подходящих фраз {len(out)}, взято {len(pick)}, дикторов {len({x['speaker'] for x in pick})}")
    for x in pick[:12]:
        report.append(f"    {x['jp']} = {x['ru']} ({x['speaker']})")
    listen_kana(pick)
    return pick


def listen_kana(items):
    """Чтение фраз носителей каной из транскрипций Tatoeba: «[何|なん]の[話|はなし]？» -> «なんのはなし？».
    Файл: downloads.tatoeba.org/exports/per_language/jpn/jpn_transcriptions.tsv.bz2 (CC BY 2.0 FR, как и фразы)."""
    want = {str(x["sid"]): x for x in items}
    for line in open(f"{B}/jpn_transcriptions.tsv", encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        if f[0] in want and "[" in f[-1] or f[0] in want and not re.search(r"[一-鿿々]", want[f[0]]["jp"]):
            want[f[0]]["kana"] = re.sub(r"\[([^|\]]+)\|([^\]]+)\]", lambda m: m[2].replace("|", ""), f[-1])
    miss = [x["jp"] for x in items if "kana" not in x]
    report.append(f"  слух: чтение каной есть у {len(items) - len(miss)} из {len(items)}" + (f", нет: {miss[:5]}" if miss else ""))


def kanji_chars(bank):
    """Кандзи, которые бот показывает по одному: уроки и тест."""
    return sorted(set(bank["course"]["kanji"]) | {x["ch"] for lv in bank["kanji_mean"].values() for x in lv})


def main():
    if "--audio-only" in sys.argv:  # озвучить уже собранный bank.json, словари в память не грузим
        bank = json.load(open(OUT, encoding="utf-8"))
        A.images(bank["abc"], IMG)
        A.kanji_images(kanji_chars(bank), IMG)
        asyncio.run(make_audio(bank))
        course_audio(bank)
        return
    K.update(J.kanji())
    random.seed(1)
    bank = {"vocab": build_vocab(), "kana": build_kana(), "kanji_mean": build_kanji_mean(),
            "kanji_read": build_kanji_read(), "onkun": build_onkun(), "listen": build_listen(),
            "grammar": [{"lv": lv, "q": q, "opts": o, "ans": a, "ru": ru} for lv, q, o, a, ru in GRAMMAR],
            "valid": valid_readings(), "numbers": NUMBERS,
            "course": (course := build_course()), "kana_ear": build_kana_ear(course), "gcourse": build_gcourse(),
            "abc": A.build(os.path.join(os.path.dirname(os.path.abspath(__file__)), "content/abc.txt"), check, errors, report)}
    verify_numbers()
    for lv, items in bank["vocab"].items():
        if len(items) < 8:
            errors.append(f"слова {lv}: мало ({len(items)})")
    for x in bank["listen"]:
        check(f"слух {x['sid']}", x["ru"])
    os.makedirs(B, exist_ok=True)
    open(REPORT, "w", encoding="utf-8").write("\n".join(report) + "\n")
    if errors:
        print("ОШИБКИ СБОРКИ:\n  " + "\n  ".join(errors))
        sys.exit(1)
    print(f"картинки азбуки: новых {A.images(bank['abc'], IMG)}, кандзи: новых {A.kanji_images(kanji_chars(bank), IMG)}")
    tmp = OUT + ".tmp"
    json.dump(bank, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print(f"ок: слов {sum(map(len, bank['vocab'].values()))}, каны {len(bank['kana'])}, "
          f"кандзи {sum(map(len, bank['kanji_mean'].values()))}, чтений {sum(map(len, bank['kanji_read'].values()))}, "
          f"пар «два чтения» {len(bank['onkun']['pairs'])}, фраз на слух {len(bank['listen'])}, "
          f"уроков кандзи {len(bank['course']['lessons'])}, кана на слух {len(bank['kana_ear'])}, "
          f"уроков грамматики {len(bank['gcourse'])}, уроков азбуки {len(bank['abc']['lessons'])}")
    if "--audio" in sys.argv:
        asyncio.run(make_audio(bank))
        course_audio(bank)
        download_native(bank)


def course_audio(bank):
    """Карточка кандзи: оба слова-примера одной записью; карточка азбуки: знак и слово одной записью."""
    for x in bank["course"]["kanji"].values():
        paths = [tts.path_for(e["kana"], tts.FEMALE, tts.NORMAL) for e in x["ex"]]
        if len(paths) > 1 and all(os.path.exists(p) for p in paths):
            tts.concat(paths)
    for L in bank.get("abc", {}).get("lessons", []):
        for c in L["cards"]:
            paths = [tts.path_for(t, tts.FEMALE, tts.NORMAL) for t in c["say"]]
            if len(paths) > 1 and all(os.path.exists(p) for p in paths):
                tts.concat(paths)


def audio_jobs(bank):
    """(текст, голос, скорость). Японские слова озвучиваем по кане: так чтение гарантированно верное."""
    jobs = set()
    words = [x for lv in bank["vocab"].values() for x in lv] + bank["kana"] + \
        [x for lv in bank["kanji_read"].values() for x in lv]
    for p in bank["onkun"]["pairs"]:
        words += [p["kun"], p["on"]]
    words += bank["onkun"]["except"]
    words += [e for x in bank["course"]["kanji"].values() for e in x["ex"]] + bank["kana_ear"]
    for x in words:
        jobs.add((x["kana"], tts.FEMALE, tts.NORMAL))
    for v in (tts.FEMALE, tts.MALE):
        for m in range(1, 13):
            for d in range(1, 32):
                if d <= (29 if m == 2 else 30 if m in (4, 6, 9, 11) else 31):
                    jobs.add((N.date_reading(m, d), v, tts.NORMAL))
        for _, r, _ in N.WEEKDAY:
            jobs.add((r, v, tts.NORMAL))
        for h in range(1, 13):
            for mi in (0, 5, 10, 15, 20, 30, 45):
                jobs.add((N.time(h, mi), v, tts.NORMAL))
        for n in NUMBERS:
            jobs.add((N.number(n), v, tts.NORMAL))
    for c in drills.cheat():  # 📋 шпаргалка: каждое слово отдельно, медленно
        for _, w in c["words"]:
            jobs.add((w, tts.FEMALE, tts.SLOW))
    for L in drills.course(bank["valid"]):  # 📅 даты по шагам: голос каждой карточки и каждого числа в строке
        for x in L["items"]:
            for p in x.get("parts") or [x]:
                jobs.add((p["kana"], tts.FEMALE, tts.NORMAL))
    for g in bank.get("gcourse", []):  # грамматика: примеры, фразы с пропуском целиком, верные переводы
        for t in [x["jp"] for x in g["ex"]] + [x["full"] for x in g["q"]] + [x["opts"][0] for x in g["tr"]]:
            jobs.add((t, tts.FEMALE, tts.NORMAL))
    for x in bank["listen"]:
        jobs.add((x["jp"], tts.FEMALE, tts.NORMAL))
    for t in A.audio_texts(bank.get("abc", {"lessons": [], "kana": {}})):  # азбука: знаки, слова, фразы
        jobs.add((t, tts.FEMALE, tts.NORMAL))
    jobs |= {(t, v, tts.SLOW) for t, v, r in jobs if r == tts.NORMAL}  # кнопка 🐢 под каждым голосовым
    return sorted(jobs)


NUMBERS = list(range(0, 21)) + list(range(30, 101, 10)) + [h * 100 for h in range(2, 10)] + \
    [t * 1000 for t in range(1, 10)] + [10000, 110, 280, 350, 480, 680, 750, 980, 1200, 1500, 2800, 3600,
                                        4500, 6800, 8800, 12000, 25000, 38000]


async def make_audio(bank):
    todo = [j for j in audio_jobs(bank) if not os.path.exists(tts.path_for(*j))]
    print(f"озвучка: делаю {len(todo)}", flush=True)
    failed = 0
    for i, (t, v, r) in enumerate(todo, 1):
        try:
            await tts.ensure(t, v, r, retries=5)
        except Exception:
            failed += 1
        if i % 100 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        await asyncio.sleep(0.25)
    print(f"озвучка готова, сбоев {failed}", flush=True)


def download_native(bank):
    """Записи носителей с Tatoeba: по одной, с паузой, иначе сайт отвечает 429."""
    os.makedirs(NATIVE_DIR, exist_ok=True)
    got = failed = 0
    for x in bank["listen"]:
        path = f"{NATIVE_DIR}/{x['audio_id']}.mp3"
        if os.path.exists(path):
            continue
        for attempt in range(3):
            try:
                req = urllib.request.Request(f"https://tatoeba.org/en/audio/download/{x['audio_id']}",
                                             headers={"User-Agent": "jp-bot-build/1.0 (learning bot build)"})
                data = urllib.request.urlopen(req, timeout=30).read()
                if len(data) > 1000:
                    open(path, "wb").write(data)
                    got += 1
                    break
            except Exception:
                time.sleep(20 * (attempt + 1))
        else:
            failed += 1
        time.sleep(4)
    print(f"записи носителей: скачано {got}, сбоев {failed}", flush=True)


if __name__ == "__main__":
    main()
