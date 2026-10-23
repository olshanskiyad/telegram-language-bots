"""Сборка курса: content/*.yaml -> course.json.

Транскрипция каждого тайского слова берётся только из Викисловаря (thaidict).
Слова нет в словаре: сборка падает, пока слово не разбито точкой «·» или не дан явный tr.

Курс собирается дважды: course.json с мужской речью ученика (ครับ, ผม) и course_f.json с женской
(ค่ะ/คะ, ฉัน, женский голос). Женский вариант строится по правилам fem_th; где правил мало, строка
урока несёт fth= ftr= fru= fnote= fmn=, а урок блок «fem:» с заменами в тексте «было => стало».

  python3 build.py           проверка и сборка, отчёты в build/report.txt и build/report_f.txt
  python3 build.py --audio   плюс озвучка всего, чего ещё нет в кэше
"""
import asyncio
import json
import os
import re
import subprocess
import sys

import yaml

import thaidict
import tts

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = thaidict.DATA
OUT = f"{DATA}/course.json"
OUT_F = f"{DATA}/course_f.json"
REPORT = f"{DATA}/build/report.txt"
BANNED = ("\u2014", "\u2013")  # длинное и среднее тире в текстах курса запрещены
CLS_TONE = {"m": "", "l": "", "h": "̌"}  # слог имени буквы: средний у средних и низких, восходящий у высоких
SOUND_RU = {
    "k": "к", "kh": "к с выдохом", "ng": "нг, как в «гонг»", "j": "мягкое «чь» без выдоха",
    "ch": "ч с выдохом", "s": "с", "d": "д", "t": "т", "th": "т с выдохом", "n": "н", "b": "б",
    "p": "п", "ph": "п с выдохом", "f": "ф", "m": "м", "y": "й", "r": "р", "l": "л",
    "w": "у-образное «в», как англ. w", "h": "лёгкое «х», как англ. h", "": "без звука, держит гласную",
}

KEYS = ("tr", "say", "mn", "pic", "voice", "fth", "ftr", "fru", "fnote", "fmn")
Q_WORDS = {"ไหม", "มั้ย", "อะไร", "ไหน", "ที่ไหน", "เท่าไหร่", "ไง", "กี่", "หรือ", "เหรอ", "ใคร", "ทำไม", "เมื่อไหร่"}
CALL = {"พี่", "น้อง", "คุณ"}  # окликая («พี่คะ!»), женщины говорят คะ
NAME = {"ลีโอ": "อันนา", "Лео": "Анна"}  # имя-пример в «меня зовут…»

idx = thaidict.load_index()
errors, report = [], []
items = {}
audio_jobs = set()
FEM = False    # идёт сборка женского варианта
twins = {}     # мужская фраза -> женская: при смене варианта повторение переезжает
fem_log = []


def check_text(where, text):
    if text and any(b in text for b in BANNED):
        errors.append(f"{where}: длинное тире в тексте")
    # «ไม่ (mâi)» в объяснениях: транскрипция в скобках обязана совпасть со словарём
    for th, tr in re.findall(r"([\u0E00-\u0E7F]+)\s*\(([^)]+)\)", text or ""):
        e = idx.get(th)
        if e and len(th) > 1 and " " not in tr and tr not in [thaidict.convert(p) for p in e["p"]]:
            errors.append(f"{where}: в тексте «{th} ({tr})», а по словарю {[thaidict.convert(p) for p in e['p']]}")


TH_RUN = re.compile(r"[\u0E00-\u0E7F]+(?: [\u0E00-\u0E7F]+)*")


def annotate(text, where):
    """Транскрипция в скобках к каждому тайскому слову текста, где её нет (чтобы у каждого тайского слова было чтение).
    Слово урока берёт транскрипцию карточки, остальное по словарю: разбиение на меньше всего словарных слов."""
    def split(w):
        best = {0: []}
        for i in range(len(w)):
            for j in range(i + 1, len(w) + 1) if i in best else ():
                if (w[i:j] in idx or w[i:j] == "ๆ") and len(best.get(j, w)) > len(best[i]) + 1:
                    best[j] = best[i] + [w[i:j]]
        return best.get(len(w)) or list(w)

    def one(m):
        th, before, after = m.group(), m.string[:m.start()], m.string[m.end():]
        if re.match(r"\s*\(", after):
            return th
        tr = " ".join(items[w]["tr"] if w in items else transcribe_phrase(split(w), th, where) for w in th.split())
        return f"{th}, {tr}" if before.endswith("(") and after.startswith(")") else f"{th} ({tr})"
    return TH_RUN.sub(one, text or "")


def transcribe(th, where):
    """Транскрипция фразы. Слова разделены «·» или пробелом; «слово#2» выбирает второй вариант произношения.

    Сначала ищется самое длинное сочетание соседних слов, которое есть в словаре целиком:
    у составных статей своё живое произношение (น้ำเปล่า nám-plào, а не náam plàao).
    """
    return " ".join(transcribe_phrase([x for x in ph.split("·") if x], th, where) for ph in th.split())


def transcribe_phrase(segs, th, where):
    """Одна фраза без пробелов: сочетания ищутся только внутри неё, через пробел не склеиваем."""
    parts, i = [], 0
    while i < len(segs):
        seg = segs[i]
        if seg == "ๆ":
            parts[-1] = f"{parts[-1]}-{parts[-1]}"
            i += 1
            continue
        found = None
        for j in range(len(segs), i, -1):
            chunk = segs[i:j]
            if len(chunk) > 1 and any("#" in x or x == "ๆ" for x in chunk):
                continue
            word, _, var = "".join(chunk).partition("#")
            rep = word.endswith("ๆ")
            word = word.rstrip("ๆ")
            e = idx.get(word)
            if e:
                k = int(var) - 1 if var else 0
                if k >= len(e["p"]):
                    errors.append(f"{where}: у «{word}» нет варианта {var}")
                    k = 0
                t = thaidict.convert(e["p"][k])
                found = (j, f"{t}-{t}" if rep else t)
                report.append(f"    {word:<14} {t:<22} {' | '.join(e['g'])[:90]}")
                break
        if not found:
            errors.append(f"{where}: нет в словаре «{seg}» (из «{th}»): разбей точкой или дай tr")
            found = (i + 1, "?")
        i, t = found
        parts.append(t)
    return " ".join(parts)


def parse_line(line):
    """«th | ru | заметка | tr=... | say=... | mn=... | pic=...» -> словарь.

    Всё после перевода необязательно: заметка, tr= (своя транскрипция), say= (что озвучить),
    mn= (подсказка-сценка для запоминания), pic= (эмодзи вместо картинки), voice=m (реплику тайца
    говорит мужчина), fth= ftr= fru= fnote= fmn= (то же для женского варианта, если правил fem_th мало).
    """
    f = [x.strip() for x in line.split(" | ")]
    raw = {"th": f[0]}
    for x in f[1:]:
        k, _, v = x.partition("=")
        if k in KEYS and v:
            raw[k] = v
        elif "ru" not in raw:
            raw["ru"] = x
        else:
            raw["note"] = x
    raw.setdefault("ru", "")
    return raw


def key_of(th):
    return re.sub(r"#\d", "", th).replace("·", "")


def fem_th(th):
    """Фраза ученика в женской речи: ครับ -> ค่ะ, в вопросе, после นะ и в оклике คะ; ผม «я» -> ฉัน."""
    out = []
    for ph in th.split():
        segs = ph.split("·")
        q = (any(x.partition("#")[0] in Q_WORDS for x in segs) or (len(segs) == 2 and segs[0] in CALL)
             or (len(segs) > 2 and segs[-2:] == ["ยัง", "ครับ"]))  # «กินข้าวยัง?» «уже?», а «ยังครับ» ответ
        new = []
        for i, x in enumerate(segs):
            if x == "ครับ":
                x = "คะ" if q or (i and segs[i - 1] == "นะ") else "ค่ะ"
            new.append({"ผม": "ฉัน"}.get(x, NAME.get(x, x)))
        out.append("·".join(new))
    return " ".join(out)


def fem_raw(raw, where):
    """Строка ученика для женского варианта: тайский по правилам или из fth=, русский из fru= fnote= fmn=."""
    new = dict(raw)
    for k in ("ru", "note", "mn", "tr"):
        if raw.get("f" + k):
            new[k] = raw["f" + k]
    for a, b in NAME.items():
        new["ru"] = new["ru"].replace(a, b)
    th = raw.get("fth") or fem_th(raw["th"])
    if th != raw["th"]:
        new["th"], new["fem"] = th, True
        if raw.get("tr") and not raw.get("ftr"):
            errors.append(f"{where}: у «{raw['th']}» своя tr=, для женского варианта нужна ftr=")
        twins[key_of(raw["th"])] = key_of(th)
        fem_log.append(f"  [{where}] {raw['th']}  ->  {th} | {new['ru']}")
    elif new != raw:
        fem_log.append(f"  [{where}] {raw['th']}  (только перевод) | {new['ru']}")
    return new


def lines(block):
    return [x.strip() for x in (block or "").splitlines() if x.strip() and not x.strip().startswith("#")]


def add_item(raw, where, voice="m", me=True):
    """Элемент курса. Ключ: тайский текст без точек, по нему же живёт прогресс повторения.
    me: фраза ученика, в женском варианте курса она говорится женской речью и женским голосом."""
    if isinstance(raw, str):
        raw = parse_line(raw)
    if FEM and me:
        raw = fem_raw(raw, where)
        voice = "f" if raw.get("fem") else voice
    th = key_of(raw["th"])
    ru = raw.get("ru", "")
    for k in ("ru", "note", "mn"):
        check_text(f"{where} {th}", raw.get(k))
    if th in items:  # фраза уже в курсе: транскрипция и озвучка общие
        it = items[th]
        for k in ("ru", "note", "mn", "pic"):
            it[k] = it.get(k) or raw.get(k, "")
        return th
    report.append(f"  [{where}] {th} = {ru}")
    if raw.get("tr"):
        tr = raw["tr"]
        report.append(f"    OVERRIDE tr: {tr}")
    else:
        tr = transcribe(raw["th"], where)
    voice = raw.get("voice", voice)
    single = ("-" not in tr) and (" " not in tr)
    it = items.setdefault(th, {"th": th, "tr": tr, "ru": ru, "note": raw.get("note", ""), "voice": voice,
                               "tone": thaidict.tone_of(tr) if single else None,
                               "mn": raw.get("mn", ""), "pic": raw.get("pic", "")})
    if not it["ru"] and ru:
        it["ru"] = ru
    tts_text = raw.get("say", th)
    it["say"] = tts_text
    audio_jobs.add((tts_text, voice))
    return th


def build_letter(line, where):
    f = [x.strip() for x in line.split(" | ")]
    L = {"ch": f[0], "cls": f[1], "sound": "" if f[2] == "-" else f[2], "final": "" if f[3] == "-" else f[3],
         "name": f[4], "ru": f[5], "note": f[6] if len(f) > 6 else ""}
    ch = L["ch"]
    name_tr = transcribe(L["name"], where)
    syl = L["sound"] + "ɔ" + CLS_TONE[L["cls"]] + "ɔ" if L["sound"] else "ɔɔ"
    say = f"{ch}อ {L['name'].replace('·', '')}" if ch != "อ" else f"ออ {L['name'].replace('·', '')}"
    audio_jobs.add((say, "m"))
    for k in ("ru",):
        check_text(where, L.get(k))
    return {"ch": ch, "cls": L["cls"], "sound": L["sound"], "final": L.get("final", ""),
            "sound_ru": SOUND_RU[L["sound"]], "name_th": L["name"].replace("·", ""), "name_ru": L.get("ru", ""),
            "tr": f"{syl} {name_tr}", "say": say, "note": L.get("note", "")}


def fem_text(text, block, w):
    """«было => стало»: правки текста урока или сценки для женского варианта."""
    if FEM:
        for x in lines(block):
            old, _, new = x.partition(" => ")
            if not new or old not in text:
                errors.append(f"{w}: fem: в тексте нет «{old[:50]}»")
            text = text.replace(old, new)
        check_text(w, text)
    return text


def build_dialog(block, w):
    out = []
    for d in lines(block):
        who, _, rest = d.partition(": ")
        if who not in ("t", "me"):
            errors.append(f"{w}: строка диалога должна начинаться с «t: » или «me: »: {d}")
            continue
        raw = parse_line(rest)
        if FEM and who == "me":
            raw = fem_raw(raw, w)
        out.append({"who": who, "item": add_item(raw, w, voice="f" if who == "t" or FEM else "m", me=False),
                    "ru": raw["ru"]})  # перевод по контексту этой реплики
    return out


def build_mission(m, w):
    if not m:
        return None
    text, _, th = m.strip().partition(" | ")
    check_text(w, text)
    return {"text": annotate(text, w), "item": add_item(th, w) if th else None}


MAX_NEW = 6  # новых слов и фраз в уроке; длиннее делим на две части (решение автора курса)


def build_lessons(src):
    out = {}
    for L in src:
        lid = L["id"]
        w = lid
        for k in ("title", "intro"):
            check_text(w, L.get(k))
        if len(L.get("intro", "")) > 3500:
            errors.append(f"{w}: вступление длиннее 3500 знаков")
        intro = fem_text(L.get("intro", "").strip(), L.get("fem"), w)
        les = {"id": lid, "title": L["title"], "intro": intro, "kind": L.get("kind", "normal"),
               "intro_voice": bool(L.get("intro_voice")),  # вступление голосовым с первым словом урока
               "tones": L.get("tones", ["mid", "low", "falling", "high", "rising"]),
               "quiz": L.get("quiz", ["meaning", "choose"]),
               "items": [add_item(x, w) for x in lines(L.get("items"))],
               "pairs": [[add_item(a, w, me=False), add_item(b, w, me=False)]
                         for a, b in (x.split(" || ") for x in lines(L.get("pairs")))],
               "letters": [build_letter(x, w) for x in lines(L.get("letters"))],
               "read": [add_item(x, w, me=False) for x in lines(L.get("read"))],
               "chars": L.get("chars", ""),
               "dialog": build_dialog(L.get("dialog"), w),
               "mission": build_mission(L.get("mission"), w)}
        if les["kind"] != "letters":  # в алфавите буквы по одной, их не транскрибируем
            les["title"], les["intro"] = annotate(les["title"], w), annotate(intro, w)
            intro = les["intro"]
        n, k = len(les["items"]), L.get("split") or -(-len(les["items"]) // 2)
        if les["kind"] == "letters" or n <= MAX_NEW:
            out[lid] = les
            continue
        if not (2 <= k <= MAX_NEW and 2 <= n - k <= MAX_NEW):
            errors.append(f"{w}: новых {n}, split={k} даёт часть больше {MAX_NEW} или меньше 2")
        # первая часть со вступлением и парами, вторая с диалогом и заданием: в них нужны слова обеих
        out[lid] = dict(les, title=f"{les['title']}, часть 1", items=les["items"][:k], dialog=[], mission=None)
        out[lid + "_2"] = dict(les, id=lid + "_2", title=f"{les['title']}, часть 2", items=les["items"][k:], pairs=[],
                               intro=f"Вторая часть темы: ещё {n - k} новых {'слова и фразы' if n - k < 5 else 'слов и фраз'}. "
                                     f"Напомню главное из первой:\n\n{intro}")
    return out


def build_scenes(path, kind):
    """Сценки меню «💬 Разговор»: разговорные из talk.yaml (phrases по порядку, «t: » говорят тебе)
    и места из places.yaml (say, потом hear). У каждой шпаргалка, урок на 5 минут и дорожка «послушать всё»."""
    out = []
    if not os.path.exists(path):
        return out
    for P in yaml.safe_load(open(path, encoding="utf-8")):
        w = P["id"]
        for k in ("title", "intro"):
            check_text(w, P.get(k))
        src = [("t", x[3:]) if x.startswith("t: ") else ("me", x) for x in lines(P.get("phrases"))]
        src += [("me", x) for x in lines(P.get("say"))] + [("t", x) for x in lines(P.get("hear"))]
        seq = [{"who": who, "th": add_item(x, w) if who == "me" else add_item(x, w, voice="f", me=False)}
               for who, x in src]
        out.append({"id": w, "kind": kind, "title": annotate(P["title"], w),
                    "intro": annotate(fem_text((P.get("intro") or "").strip(), P.get("fem"), w), w), "seq": seq,
                    "say": [x["th"] for x in seq if x["who"] == "me"], "hear": [x["th"] for x in seq if x["who"] == "t"],
                    "dialog": build_dialog(P.get("dialog"), w), "mission": build_mission(P.get("mission"), w),
                    "track": f"place_{w}{'_f' if FEM else ''}.mp3"})
    return out


def check_alphabet(order, lessons):
    """В словах для чтения только буквы, которые уже пройдены."""
    known = set()
    for lid in order:
        L = lessons[lid]
        if L["kind"] != "letters":
            continue
        known |= set(L["chars"]) | {x["ch"] for x in L["letters"]}
        for th in L["read"]:
            extra = set(th) - known - {" ", "ๆ"}
            if extra:
                errors.append(f"{lid}: в слове «{th}» ещё не пройдены: {''.join(sorted(extra))}")


def check_fem(course):
    """В женском варианте у ученика не осталось мужской речи."""
    def bad(th, where):
        it = course["items"][th]
        if re.search("ครับ|ผม|ลีโอ", th) or "Лео" in it["ru"]:
            errors.append(f"{where}: в женском варианте мужская речь: {th} | {it['ru']}")
    for L in course["lessons"].values():
        for th in L["items"] + [d["item"] for d in L["dialog"] if d["who"] == "me"]:
            bad(th, L["id"])
        if L["mission"] and L["mission"]["item"]:
            bad(L["mission"]["item"], L["id"])
    for P in course["scenes"]:
        for th in P["say"] + [d["item"] for d in P["dialog"] if d["who"] == "me"]:
            bad(th, P["id"])
        if P["mission"] and P["mission"]["item"]:
            bad(P["mission"]["item"], P["id"])


def build():
    """Один вариант курса: при FEM с женской речью ученика."""
    global items
    items = {}
    lessons = {}
    for f in sorted(os.listdir(f"{HERE}/content")):
        if f.endswith(".yaml") and f[0] == "s":
            lessons.update(build_lessons(yaml.safe_load(open(f"{HERE}/content/{f}", encoding="utf-8"))))
    order = yaml.safe_load(open(f"{HERE}/content/order.yaml", encoding="utf-8"))
    order = [y for x in order for y in ([x, x + "_2"] if x + "_2" in lessons else [x])]  # части длинных уроков
    abc = sorted(x for x in lessons if lessons[x]["kind"] == "letters")  # алфавит по желанию, вне основного пути
    missing = [x for x in order if x not in lessons]
    unused = [x for x in lessons if x not in order and x not in abc]
    if missing:
        errors.append(f"order.yaml: нет уроков {missing}")
    if unused:
        errors.append(f"уроки не стоят в order.yaml: {unused}")
    check_alphabet(abc, lessons)
    from engine import ANCHORS
    for t, th in ANCHORS.items():
        if th not in items or items[th]["tone"] != t:
            errors.append(f"опорное слово тона {t} «{th}» не найдено в курсе или его тон другой")
    scenes = build_scenes(f"{HERE}/content/talk.yaml", "talk") + build_scenes(f"{HERE}/content/places.yaml", "place")
    ids = [P["id"] for P in scenes]
    dup = {x for x in ids if ids.count(x) > 1 or x in lessons}
    if dup:
        errors.append(f"сценки: повторяются id {dup}")
    # что только слышишь от тайцев, в повторении всегда на слух: «что это значит?»
    said = {th for L in lessons.values() for th in L["items"] + [d["item"] for d in L["dialog"] if d["who"] == "me"]}
    said |= {th for P in scenes for th in P["say"] + [d["item"] for d in P["dialog"] if d["who"] == "me"]}
    for P in scenes:
        for th in P["hear"]:
            if th not in said:
                items[th]["hear"] = True
    return {"order": order, "abc": abc, "lessons": lessons, "items": items, "scenes": scenes}


def main():
    global FEM
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    courses = {}
    for FEM, out, rep_path in ((False, OUT, REPORT), (True, OUT_F, REPORT.replace(".txt", "_f.txt"))):
        del report[:]
        courses[out] = build()
        head = ["Женская речь ученика: что поменялось", *fem_log, "", "Транскрипции:"] if FEM else []
        open(rep_path, "w", encoding="utf-8").write("\n".join(head + report) + "\n")
    courses[OUT_F]["twins"] = twins
    check_fem(courses[OUT_F])
    if errors:
        print("ОШИБКИ СБОРКИ:\n  " + "\n  ".join(errors))
        sys.exit(1)
    for out, course in courses.items():
        tmp = out + ".tmp"
        json.dump(course, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, out)
    m, f = courses[OUT], courses[OUT_F]
    print(f"ок: уроков {len(m['order'])} и алфавита {len(m['abc'])}, элементов {len(m['items'])} и в женском варианте {len(f['items'])} "
          f"(фраз в женской речи {len(twins)}), сценок {len(m['scenes'])}, озвучек нужно {len(audio_jobs) * 2}")
    if "--audio" in sys.argv:
        asyncio.run(make_audio(list(courses.values())))


async def make_audio(courses):
    jobs = sorted(audio_jobs)
    tonal = sorted({it["say"] for c in courses for it in c["items"].values() if it["tone"]})
    todo = [(t, v, r) for t, v in jobs for r in (tts.NORMAL, tts.SLOW)]
    todo += [(t, v, tts.NORMAL) for t in tonal for v in ("m", "f")]  # оба голоса для «разных дикторов»
    todo = [(t, v, r) for t, v, r in dict.fromkeys(todo)
            if not os.path.exists(tts.path_for(t, tts.MALE if v == "m" else tts.FEMALE, r))]
    print(f"озвучка: делаю {len(todo)}")
    failed = []
    for i, (t, v, r) in enumerate(todo, 1):
        voice = tts.MALE if v == "m" else tts.FEMALE
        try:
            await tts.ensure(t, voice, r, retries=8 if v == "f" else 4)
        except Exception as e:
            failed.append((t, v, r, str(e)[:60]))
        if i % 50 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        await asyncio.sleep(0.3)
    print(f"озвучка готова, сбоев {len(failed)}")
    for f in failed:
        print("  СБОЙ", f)
    made = 0
    for t in tonal:  # ещё два «диктора»: мужской выше, женский ниже, мелодия тона та же
        for voice, kind in ((tts.MALE, "hi"), (tts.FEMALE, "lo")):
            base = tts.path_for(t, voice, tts.NORMAL)
            if os.path.exists(base) and not os.path.exists(tts.variant_path(base, kind)):
                tts.make_variant(base, kind)
                made += 1
    print(f"вариантов высоты сделано: {made}")
    for c in courses:
        for P in c["scenes"]:
            place_track(c, P)


def place_track(course, P):
    """Одна дорожка «послушать всё» для шпаргалки сценки: фразы по порядку, у мест сначала что сказать."""
    silence = f"{DATA}/audio/_silence.mp3"
    if not os.path.exists(silence):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                        "-t", "0.9", "-c:a", "libmp3lame", "-b:a", "48k", silence], check=True)
    parts = []
    for x in P["seq"]:
        it = course["items"][x["th"]]
        p = tts.path_for(it["say"], tts.MALE if it["voice"] == "m" else tts.FEMALE, tts.NORMAL)
        if os.path.exists(p):
            parts += [p, silence]
    out = f"{DATA}/audio/{P['track']}"
    lst = out + ".txt"
    open(lst, "w").write("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["nice", "-n", "19", "ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c", "copy", out], check=True)
    os.remove(lst)


if __name__ == "__main__":
    main()
