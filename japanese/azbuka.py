"""Азбука с нуля: хирагана, катакана и первые фразы из content/abc.txt.

Сборка разбирает файл и проверяет: в словах только пройденные знаки, каждый из 46 знаков хираганы и катаканы
и 25 звонких по разу, у фраз чтение русскими буквами, ответ ситуации есть среди фраз урока. Чтение слов
русскими буквами (система Поливанова, долгие гласные удвоены) пишет cyr(). Неверные варианты чтения
строятся заменой знака на похожий по форме или звуку, картинки знаков рисуются учебным шрифтом Klee One.
Бот берёт готовое из bank.json["abc"].
"""
import hashlib
import os
import random
import re
import unicodedata

import jdict
import jpnum

HIRA_ROWS = [("あいうえお", "а и у э о"), ("かきくけこ", "ка ки ку кэ ко"), ("さしすせそ", "са си су сэ со"),
             ("たちつてと", "та ти цу тэ то"), ("なにぬねの", "на ни ну нэ но"), ("はひふへほ", "ха хи фу хэ хо"),
             ("まみむめも", "ма ми му мэ мо"), ("やゆよ", "я ю ё"), ("らりるれろ", "ра ри ру рэ ро"), ("わを", "ва о"),
             ("ん", "н"), ("がぎぐげご", "га ги гу гэ го"), ("ざじずぜぞ", "дза дзи дзу дзэ дзо"),
             ("だぢづでど", "да дзи дзу дэ до"), ("ばびぶべぼ", "ба би бу бэ бо"), ("ぱぴぷぺぽ", "па пи пу пэ по")]
BASIC = "".join(r for r, _ in HIRA_ROWS[:11])
VOICED_ROWS = "".join(r for r, _ in HIRA_ROWS[11:])


def kata(s):
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def hira(s):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def is_kata(c):
    return "ァ" <= c <= "ヺ" or c == "ー"


CYR = {}
for _row, _rd in HIRA_ROWS:
    for _c, _r in zip(_row, _rd.split()):
        CYR[_c] = CYR[kata(_c)] = _r
CYR["ヴ"] = "ву"
SMALL_Y = {"ゃ": "я", "ゅ": "ю", "ょ": "ё", "ャ": "я", "ュ": "ю", "ョ": "ё"}
SMALL_V = {"ぁ": "а", "ぃ": "и", "ぅ": "у", "ぇ": "э", "ぉ": "о", "ァ": "а", "ィ": "и", "ゥ": "у", "ェ": "э", "ォ": "о"}
SMALL_BIG = dict(zip("ぁぃぅぇぉゃゅょっゎァィゥェォャュョッヮ", "あいうえおやゆよつわアイウエオヤユヨツワ"))
LONG = {"а": "а", "и": "и", "у": "у", "э": "э", "о": "о", "я": "а", "ю": "у", "ё": "о"}
VOWELS = "аиуэояюё"
SOUND = {"を": "お", "ヲ": "お", "ぢ": "じ", "ヂ": "じ", "づ": "ず", "ヅ": "ず"}  # одиночный знак: синтез того же звука

# Похожие по форме знаки: из них неверные варианты «как читается?» и «какой знак прозвучал?»
_SHAPE = ("あお あめ あの いり いこ うつ うら えん えそ おす かや きさ きち くへ くし けは けほ こに こい さち しつ しも "
          "すむ すお せや せも そろ そて たな たに ちら つう てそ とこ なよ ぬめ ぬね ねれ ねわ のめ はほ はよ ひい "
          "まも まよ みよ みゆ めあ ゆよ ゆぬ らう りい るろ るら れわ ろら をお んそ "
          "アマ アヤ イト ウワ ウフ エユ エコ オホ オネ キチ キモ クケ クタ クワ コユ コロ サセ サヤ シツ シン シミ "
          "スヌ スラ ソン ソリ ソツ チテ ツン ナメ ナチ ニコ ヌメ ネホ ノメ ノソ ハル ヒト フワ フラ フヲ ホオ マム "
          "ミシ メナ モキ ヤセ ユヨ ヨコ ラヲ リル ルレ ロコ ワク ヲワ")
SHAPE = {}
for _p in _SHAPE.split():
    SHAPE.setdefault(_p[0], []).append(_p[1])
    SHAPE.setdefault(_p[1], []).append(_p[0])
VOICED = dict(zip("かきくけこさしすせそたちつてとはひふへほカキクケコサシスセソタチツテトハヒフヘホ",
                  "がぎぐげござじずぜぞだぢづでどばびぶべぼガギグゲゴザジズゼゾダヂヅデドバビブベボ"))
VOICED.update({v: k for k, v in list(VOICED.items())})
VOICED.update(dict(zip("ぱぴぷぺぽパピプペポ", "ばびぶべぼバビブベボ")))
GEMINABLE = set("かきくけこさしすせそたちつてとぱぴぷぺぽカキクケコサシスセソタチツテトパピプペポ")
LENGTHEN = {**{c: "う" for c in "おこそとのほもよろごぞどぼぽ"}, **{c: "い" for c in "えけせてねへめれげぜでべぺ"},
            **{c: "ー" for c in kata("あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもらりるれろ"
                                    "がぎぐげござじずぜぞだでどばびぶべぼぱぴぷぺぽ")}}
LONG_O = {"おう": "お", "こう": "こ", "とう": "と", "ょう": "ょ", "ゅう": "ゅ", "あさ": "ああさ", "ばあ": "ば", "かあ": "か",
          "おお": "お", "えい": "え"}


def base(c):
    """Знак без точек и кружка, маленький как большой: が → か, ゃ → や."""
    c = SMALL_BIG.get(c, c)
    return unicodedata.normalize("NFD", c)[0]


def cyr(s):
    """Чтение русскими буквами по Поливанову: きって «киттэ», とうきょう «тоокёо», コーヒー «коохии»."""
    out, gem, i = [], False, 0
    while i < len(s):
        c = s[i]
        if c in " 、":
            out.append(" ")
            gem, i = False, i + 1
            continue
        if c in "っッ":
            gem, i = True, i + 1
            continue
        if c in "をヲ" and out and out[-1] != " ":  # を только частица: пишется отдельно, ほんを «хон о»
            out.append(" о")
            gem, i = False, i + 1
            continue
        if c == "ー":
            if out and out[-1] == "й":  # ナイーフ: тянется «и», а не «а»
                out[-1] = "и"
            last = next((x for x in reversed("".join(out)) if x in VOWELS), None)
            if not last:
                raise ValueError(f"{s}: ー без гласной")
            out.append(LONG[last])
            i += 1
            continue
        if c not in CYR:
            raise ValueError(f"{s}: нет чтения у «{c}»")
        r, nxt = CYR[c], s[i + 1] if i + 1 < len(s) else ""
        if nxt in SMALL_Y and r.endswith("и") and len(r) > 1:
            r, i = r[:-1] + SMALL_Y[nxt], i + 1
        elif nxt in SMALL_V:
            r, i = ("у" if c in "うウ" else r[:-1]) + SMALL_V[nxt], i + 1
        prev = out[-1] if out and out[-1] != " " else ""
        syl = nxt in ("ん", "ン") or (nxt in ("い", "イ") and s[i + 2:i + 3] not in ("ん", "ン")) or \
            s[:i].endswith(("じゅう", "ひゃく"))  # いん, いい, いち в числах отдельным слогом: «кайсяин», «мо ии», «дзюуити»
        if c in "いイ" and prev and prev[-1] in "аэоуяюё" and not syl:
            r = "й"
        elif c in "うウ" and prev and prev[-1] in "оё":
            r = "о"
        if gem and r[0] not in VOWELS:
            r = r[0] + r
        gem = False
        out.append(r)
        i += 1
    for k, r in enumerate(out):
        if r == "н" and k + 1 < len(out):
            nx = out[k + 1][:1]
            out[k] = "м" if nx in ("м", "б", "п") else "нъ" if nx and nx in VOWELS else "н"
    return "".join(out)


PARTICLE = {"は": "ва", "へ": "э", "を": "о"}
TAIL = {"でしたか": " дэсита ка", "でした": " дэсита", "ですか": " дэсу ка", "です": " дэсу",  # связка отдельным словом
        "ましたか": "масита ка", "ませんか": "масэн ка", "ますか": "масу ка"}  # か вопроса отдельно
_TOK = r"(?:\d{1,2}(?=じ))?[぀-ヿ一-鿿々～〜]+"  # японский кусок; часы цифрой вместе с じ: 7じに
FRAG = re.compile(rf"{_TOK}(?:[ ·]{_TOK})*[。？！]?")


def _word(w, phrase):
    """Одно слово каной. В фразе (phrase) は и へ на конце слова частицы; ては и では внутри слова тоже: てはいけません
    «тэ ва икэмасэн», ではありません «дэ ва аримасэн»; です и か вопроса отдельно."""
    if phrase and w in PARTICLE:
        return PARTICLE[w]
    if w[-1] in "はへ" and len(w) > 1 and (phrase or w.endswith(("にちは", "ばんは", "では"))):
        return _word(w[:-1], False) + " " + PARTICLE[w[-1]]
    parts = re.split(r"(?<=[てで])は(?=.)", w)
    if len(parts) > 1:
        return " ва ".join(_word(x, False) for x in parts)
    end = next((t for t in TAIL if w.endswith(t) and len(w) > len(t)), None)
    return cyr(w[:-len(end)]) + TAIL[end] if end else cyr(w)


def ru_read(kana, phrase=False):
    """Чтение для карточки (よっか и ようか без русских букв не различить).
    Слово или фраза с пробелами между словами; в фразе は и へ на конце слова частицы «ва» и «э», です отдельно:
    わたしは がくせいです «ватаси ва гакусэй дэсу». Часы цифрой читаются (9じ «кудзи»), части через · по частям.
    Сплошную фразу со знаками препинания не читаем: частицу в ней не отличить. Что не читается, пустая строка."""
    if "·" in (kana or ""):
        parts = [ru_read(p, phrase) for p in kana.split("·")]
        return "·".join(parts) if all(parts) else ""
    kana = re.sub(r"(\d{1,2})じ", lambda m: jpnum.time(int(m[1]), 0) if 0 < int(m[1]) <= 24 else m[0], kana or "")
    kana = re.sub(r"[。？！](?=\S)", " ", kana)  # два предложения подряд: いいですね。たべましょう。
    words = [w for w in re.split(r"[ 　、，]+", kana) if w]
    if len(words) == 1 and re.search(r"[。？！?!]", words[0]):
        return ""
    phrase = phrase or len(words) > 1
    if len(words) > 1 and any(re.search(r".(?<![おてで])[はへ](?!ん|ち|っ).", w) for w in words):
        return ""  # は или へ внутри куска фразы: частица это или часть слова, не понять (ごはん, おはよう, はち можно)
    out = []
    try:
        for w in words:
            w = re.sub(r"[。？！?!「」（）()…～〜・]", "", w)
            if w:
                out.append(_word(w, phrase))
    except ValueError:
        return ""
    return " ".join(out)


def annotate(text):
    """Пояснения (правила грамматики, вступления дат): после японского чтение в квадратных скобках, перевод в тексте
    остаётся в «»: としょかんで べんきょうします [тосёкан дэ бэнкёо симасу] «занимаюсь в библиотеке». Каждый кусок
    один раз за текст; одиночные は, へ, を частицы. Кандзи без каны и то, что не читается, без скобок."""
    seen, text = set(), text or ""

    def one(m):
        f = m.group(0)
        core = f.rstrip("。？！")
        if core in seen or not re.search(r"[぀-ゟ゠-ヿ]", core):
            return f
        seen.add(core)
        rd = ru_read(core, phrase=True)
        after = re.match(r"\s*(?:читается\s*)?[«\[(]([^»\])]*)", text[m.end():])  # уже есть: きって «киттэ»
        if not rd or after and re.sub(r"[^а-я]", "", after[1].lower().replace("ё", "е"))[:3] == \
                re.sub(r"[^а-я]", "", rd.replace("ё", "е"))[:3]:
            return f
        return f"{f} [{rd}]"
    return FRAG.sub(one, text)


def sound(ch):
    """Что озвучивать для одиночного знака: катакану голос читает по хирагане, を как お."""
    return SOUND.get(ch) or hira(ch)


# ---------- разбор файла
def parse(path):
    lessons, cur = [], None
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        line = line.rstrip("\n")
        if not line.strip() or line.startswith("#"):
            continue
        if line.startswith("== "):
            lid, title = [x.strip() for x in line[3:].split("|", 1)]
            cur = {"id": lid, "kind": lid[0], "title": title, "intro": [], "k": [], "r": [], "c": [], "w": [], "p": [], "s": []}
            lessons.append(cur)
            continue
        m = re.match(r"([krcwps]): (.*)", line)
        if not m:
            cur["intro"].append(line)
            continue
        cur[m[1]].append((n, [x.strip() for x in m[2].split("|")]))
    return lessons


def word_of(field, ru):
    jp, _, own = field.partition("=")
    jp = jp.strip()
    return {"jp": jp, "cyr": own.strip() or cyr(jp), "ru": ru}


# ---------- неверные варианты слова
def variants(word):
    out = set()
    for i, c in enumerate(word):
        for d in SHAPE.get(c, []) + ([VOICED[c]] if c in VOICED else []):
            out.add(word[:i] + d + word[i + 1:])
        row = next((r for r, _ in HIRA_ROWS if hira(c) in r), "")
        for d in row:  # тот же ряд: か → こ
            d = kata(d) if is_kata(c) else d
            if d != c:
                out.add(word[:i] + d + word[i + 1:])
        if c in "っッー":
            out.add(word[:i] + word[i + 1:])
        if c in SMALL_BIG and c not in "っッ":
            out.add(word[:i] + SMALL_BIG[c] + word[i + 1:])
        if i > 0 and c in GEMINABLE and word[i - 1] not in "っッんンー ":
            out.add(word[:i] + ("ッ" if is_kata(c) else "っ") + word[i:])
        if c in LENGTHEN and (i + 1 == len(word) or word[i + 1] not in "うーいゃゅょャュョ"):
            out.add(word[:i + 1] + LENGTHEN[c] + word[i + 1:])
    for k, v in LONG_O.items():
        if k in word:
            out.add(word.replace(k, v, 1))
    return {v for v in out if plausible(v, word)}


BAD = {"ちんぽ", "ちんこ", "まんこ", "うんこ", "うんち", "ちんちん", "きんたま", "おっぱい", "くそ"}  # и в составе слова
BAD_RU = {"сука", "суки", "суку", "хуй", "пидор", "мудак", "жопа", "блядь", "бля", "срака", "говно", "кака"}  # отдельным словом


def plausible(v, word):
    """Вариант, который мог бы быть словом: не с ん и не с маленького знака, っ перед согласным, を только частицей,
    без грубых слов ни по-японски, ни в чтении русскими буквами."""
    if not v or v[0] in "んンっッーゃゅょャュョ" or v[-1] in "っッ" or any(b in v for b in BAD):
        return False
    if BAD_RU & set(safe_cyr(v).split()):
        return False
    if any(c in "をヲ" for c in v) and not any(c in "をヲ" for c in word):
        return False
    return all(v[i + 1] in GEMINABLE for i, c in enumerate(v[:-1]) if c in "っッ")


def safe_cyr(s):
    try:
        return cyr(s)
    except ValueError:
        return ""


def build(path, check, errors, report):
    """Уроки азбуки для bank.json. check(где, текст) ловит длинные тире."""
    raw = parse(path)
    learned = set()          # знаки, пройденные к этому уроку (хирагана и катакана)
    daku = small = False     # правила звонких и маленьких знаков пройдены
    seen_k, seen_words, lessons, kana_ix = [], {}, [], {}
    all_phr = []
    for li, L in enumerate(raw):
        kind, where = L["kind"], f"азбука {L['id']}"
        new = [f[0] for _, f in L["k"]] + [c for _, f in L["r"] for c in f[0]]
        learned |= set(new)
        daku = daku or bool(L["r"])
        small = small or bool(L["c"])
        rnd = random.Random(L["id"])
        for c in new:
            if c in kana_ix:
                errors.append(f"{where}: знак {c} уже был в уроке {kana_ix[c]['les'] + 1}")
            kana_ix[c] = {"cyr": CYR.get(c, "?"), "les": li, "say": sound(c)}
        seen_k += new

        def ok_word(w):
            for c in w:
                if c == " " or (c == "ー" and kind == "k"):
                    continue
                if c in "っッ" or c in SMALL_BIG:  # маленькие знаки после урока о них
                    if not small or (c not in "っッ" and SMALL_BIG[c] not in learned):
                        return False
                    continue
                b = base(c)
                if b != c and not (daku and b in learned):  # звонкие после урока о точках и кружке
                    return False
                if b == c and c not in learned:
                    return False
            return True

        out = {"id": L["id"], "kind": kind, "title": L["title"], "intro": "\n\n".join(L["intro"]), "cards": [],
               "kana": new, "words": [], "phrases": [], "sits": []}
        for t in [L["title"]] + L["intro"]:
            check(where, t)

        def add_word(n, field, ru, card):
            if not ru:
                errors.append(f"{where}, строка {n}: у слова нет перевода")
                return None
            try:
                w = word_of(field, ru)
            except ValueError as e:
                errors.append(f"{where}, строка {n}: {e}")
                return None
            if kind in "hk" and not ok_word(w["jp"]):
                errors.append(f"{where}, строка {n}: в слове {w['jp']} непройденный знак")
            if w["jp"] in seen_words:
                errors.append(f"{where}, строка {n}: слово {w['jp']} уже было в уроке {seen_words[w['jp']]}")
            seen_words[w["jp"]] = L["id"]
            check(where, ru)
            cands = [v for v in variants(w["jp"]) if ok_word(v) and safe_cyr(v) not in ("", w["cyr"])]
            rnd.shuffle(cands)
            wrong, got = [], {w["cyr"]}
            for v in cands:
                if cyr(v) not in got:
                    got.add(cyr(v))
                    wrong.append({"jp": v, "cyr": cyr(v)})
                if len(wrong) == 3:
                    break
            if len(wrong) < 3:
                errors.append(f"{where}: мало неверных вариантов для {w['jp']}: {wrong}")
            w.update(card=card, wrong=wrong)
            out["words"].append(w)
            report.append(f"  азбука {L['id']}: {w['jp']} «{w['cyr']}» {ru} | неверные: " +
                          ", ".join(f"{x['jp']} «{x['cyr']}»" for x in wrong))
            return w

        if kind == "h":
            for n, f in L["k"]:
                if len(f) != 5:
                    errors.append(f"{where}, строка {n}: у знака хираганы нужно 5 полей")
                    continue
                ch, emo, hint, wj, wru = f
                check(where, hint)
                w = add_word(n, wj, wru, True)
                out["cards"].append({"type": "kana", "ch": ch, "cyr": CYR[ch], "emo": emo, "hint": hint, "word": w,
                                     "say": [sound(ch)] + ([w["jp"]] if w else [])})
        if kind == "k":
            items = []
            for n, f in L["k"]:
                if len(f) != 3:
                    errors.append(f"{where}, строка {n}: у знака катаканы нужно 3 поля")
                    continue
                check(where, f[2])
                items.append({"ch": f[0], "cyr": CYR[f[0]], "emo": f[1], "hint": f[2], "pair": hira(f[0])})
            for g in range(0, len(items), 5):
                grp = items[g:g + 5]
                out["cards"].append({"type": "group", "items": grp, "say": [sound(x["ch"]) for x in grp]})
        for n, f in L["r"]:
            row, emo, hint, wj, wru = f
            check(where, hint)
            w = add_word(n, wj, wru, True)
            out["cards"].append({"type": "row", "ch": row, "cyrs": [CYR[c] for c in row], "emo": emo, "hint": hint,
                                 "word": w, "say": [sound(c) for c in row] + ([w["jp"]] if w else [])})
        for n, f in L["c"]:
            title, emo, text, wj, wru = f
            check(where, text)
            w = add_word(n, wj, wru, True)
            out["cards"].append({"type": "rule", "title": title, "emo": emo, "text": text, "word": w,
                                 "say": [w["jp"]] if w else []})
        for n, f in L["w"]:
            add_word(n, f[0], f[1] if len(f) > 1 else "", False)
        for n, f in L["p"]:
            if len(f) not in (3, 4):
                errors.append(f"{where}, строка {n}: фраза не «японский | чтение | перевод | заметка»")
                continue
            jp, rd, ru, note = f + [""] * (4 - len(f))
            if re.search(r"[а-яёА-ЯЁa-zA-Z]", jp.replace("?", "")) or not re.fullmatch(r"[а-яёА-ЯЁ ,?!-]+", rd):
                errors.append(f"{where}, строка {n}: японский и чтение перепутаны или смешаны")
            for t in (ru, note, rd):
                check(where, t)
            p = {"jp": jp, "cyr": rd, "ru": ru, "note": note}
            out["phrases"].append(p)
            all_phr.append(p)
        for n, f in L["s"]:
            if len(f) != 2 or f[1] not in [p["jp"] for p in out["phrases"]]:
                errors.append(f"{where}, строка {n}: ответ ситуации не из фраз урока")
                continue
            check(where, f[0])
            out["sits"].append({"text": f[0], "jp": f[1]})
        if kind == "p" and (len(out["phrases"]) < 4 or not out["sits"]):
            errors.append(f"{where}: мало фраз или нет ситуаций")
        if kind in "hk" and len(out["words"]) < 4:
            errors.append(f"{where}: мало слов для чтения")
        lessons.append(out)
    want = set(BASIC) | set(VOICED_ROWS) | set(kata(BASIC))
    if set(seen_k) != want:
        errors.append(f"азбука: не хватает знаков {''.join(sorted(want - set(seen_k)))}, лишние "
                      f"{''.join(sorted(set(seen_k) - want))}")
    for c, x in kana_ix.items():  # похожие: пара без точек или с точками (か/が, は/ば/ぱ), потом по форме
        voiced = [d for d in kana_ix if d != c and (base(d) == base(c))]
        x["conf"] = voiced + [d for d in SHAPE.get(c, []) if d in kana_ix and d not in voiced]
    ru = [p["ru"] for p in all_phr]
    if len(set(ru)) != len(ru):
        errors.append("азбука: у фраз повторяются переводы")
    report.append(f"  азбука: уроков {len(lessons)}, знаков {len(kana_ix)}, слов {sum(len(x['words']) for x in lessons)}, "
                  f"фраз {len(all_phr)}")
    return {"lessons": lessons, "kana": kana_ix}


# ---------- картинки
# Учебный шрифт Klee One (форма как от руки, SIL OFL 1.1) скачивает fetch_data.py. Пути можно задать в окружении.
FONT = os.environ.get("JP_FONT") or f"{jdict.BUILD}/fonts/KleeOne-SemiBold.ttf"
RU_FONT = os.environ.get("JP_RU_FONT") or "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"  # любой шрифт с кириллицей
INK, RED, GREY, PAPER = (40, 40, 60), (178, 58, 58), (150, 150, 160), (255, 253, 247)


def img_name(kind, *parts):
    return f"abc_{kind}_{hashlib.sha1('|'.join(parts).encode()).hexdigest()[:12]}.png"


def _center(d, text, font, cx, top, fill):
    bb = d.textbbox((0, 0), text, font=font)
    d.text((cx - (bb[2] - bb[0]) / 2 - bb[0], top - bb[1]), text, font=font, fill=fill)


def draw(path, cols, big):
    """cols: [(знак, чтение, пара)]: один знак крупно или ряд до пяти знаков, под каждым чтение."""
    from PIL import Image, ImageDraw, ImageFont
    W, H = 640, 480
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    if len(cols) == 1 and big:
        ch, rd, pair = cols[0]
        size = 300 if len(ch) == 1 else min(300, int(560 / len(ch)))
        f = ImageFont.truetype(FONT, size)
        bb = d.textbbox((0, 0), ch, font=f)
        d.text(((W - (bb[2] - bb[0])) / 2 - bb[0], (H - 100 - (bb[3] - bb[1])) / 2 - bb[1]), ch, font=f, fill=INK)
        _center(d, rd, ImageFont.truetype(RU_FONT, 46), W / 2, H - 92, RED)
    else:
        n = len(cols)
        step = W / n
        f = ImageFont.truetype(FONT, min(150, int(step * 0.85)))
        g = ImageFont.truetype(RU_FONT, 34 if n > 3 else 40)
        p = ImageFont.truetype(FONT, 58)
        for k, (ch, rd, pair) in enumerate(cols):
            cx = step * (k + 0.5)
            _center(d, ch, f, cx, 70, INK)
            _center(d, rd, g, cx, 265, RED)
            if pair:
                _center(d, pair, p, cx, 340, GREY)
    tmp = path + ".tmp.png"
    im.save(tmp)
    os.replace(tmp, path)


def images(abc_bank, out_dir):
    """Картинки карточек; имя файла от содержимого, готовые не перерисовываем. Пишет имя в карточку."""
    os.makedirs(out_dir, exist_ok=True)
    made = 0
    for L in abc_bank["lessons"]:
        for c in L["cards"]:
            if c["type"] == "kana":
                cols, big = [(c["ch"], c["cyr"], "")], True
            elif c["type"] == "group":
                cols, big = [(x["ch"], x["cyr"], x["pair"]) for x in c["items"]], False
                for x in c["items"]:  # в повторении знак один, крупно
                    x["img"] = img_name("kana", x["ch"], x["cyr"])
                    if not os.path.exists(f"{out_dir}/{x['img']}"):
                        draw(f"{out_dir}/{x['img']}", [(x["ch"], x["cyr"], "")], True)
                        made += 1
            elif c["type"] == "row":
                cols, big = list(zip(c["ch"], c["cyrs"], [""] * len(c["ch"]))), False
            elif c["type"] == "rule" and c["word"]:
                cols, big = [(c["word"]["jp"], c["word"]["cyr"], "")], True
            else:
                continue
            name = img_name(c["type"], repr(cols))
            c["img"] = name
            if not os.path.exists(f"{out_dir}/{name}"):
                draw(f"{out_dir}/{name}", cols, big)
                made += 1
    return made


def kanji_name(ch):
    return f"kanji_{ord(ch):x}.png"


def kanji_images(chars, out_dir):
    """Кандзи крупно, один знак на картинку: в тексте Телеграма черты не разглядеть. Готовые не перерисовываем."""
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(out_dir, exist_ok=True)
    W, H, made = 640, 480, 0
    f = ImageFont.truetype(FONT, 440)
    for ch in chars:
        path = f"{out_dir}/{kanji_name(ch)}"
        if os.path.exists(path):
            continue
        im = Image.new("RGB", (W, H), PAPER)
        d = ImageDraw.Draw(im)
        bb = d.textbbox((0, 0), ch, font=f)
        d.text(((W - (bb[2] - bb[0])) / 2 - bb[0], (H - (bb[3] - bb[1])) / 2 - bb[1]), ch, font=f, fill=INK)
        im.save(path + ".tmp.png")
        os.replace(path + ".tmp.png", path)
        made += 1
    return made


def audio_texts(abc_bank):
    """Всё, что звучит в азбуке, обычной скоростью (медленные сборка добавит сама)."""
    texts = set()
    for L in abc_bank["lessons"]:
        for c in L["cards"]:
            texts |= set(c["say"])
        for w in L["words"]:
            texts.add(w["jp"])
        texts |= {p["jp"] for p in L["phrases"]}
    texts |= {sound(c) for c in abc_bank["kana"]}
    return sorted(t for t in texts if t.strip())
