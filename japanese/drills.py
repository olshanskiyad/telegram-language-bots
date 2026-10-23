"""Задания на даты, время, дни недели и числа: генерируются по правилам jpnum, бесконечно.

Неверные варианты строятся из типичных ошибок (よんがつ вместо しがつ, はちにち вместо ようか,
перепутанные よっか и ようか, ふつか и はつか) и никогда не совпадают с чтением, которое
JMdict считает допустимым (bank["valid"]). Спорные разговорные формы (ななじ, じゅうななにち)
в неверные не берём.
"""
import random

import azbuka
import jpnum as N

DAYS_IN = {2: 29, 4: 30, 6: 30, 9: 30, 11: 30}
ACCEPTABLE = {"ななじ", "じゅうななにち", "にじゅうななにち", "じゅうきゅうにち", "にじゅうきゅうにち", "しちふん"}
DAY_PAIR = {4: 8, 8: 4, 2: 20, 20: 2, 14: 24, 24: 14, 1: 7, 7: 1, 3: 6, 6: 3, 5: 9, 9: 5, 10: 20}
MONTH_NEAR = {4: [7, 9], 7: [4, 1], 9: [4, 7], 1: [7, 11], 11: [1, 12], 12: [2, 11], 2: [12, 3], 3: [6, 8],
              6: [3, 8], 8: [6, 4], 10: [1, 11], 5: [4, 6]}
HOUR_WRONG = {4: ["よんじ"], 9: ["きゅうじ"], 7: ["いちじ"]}


def fb(jp, kana, ru=""):
    """Ответ: написание → чтение каной, ниже чтение русскими буквами и перевод."""
    rd = azbuka.ru_read(kana)
    rest = ", ".join(x for x in (f"«{rd}»" if rd else "", ru) if x)
    return f"<b>{jp}</b> → <b>{kana}</b>" + (f"\n{rest}" if rest else "")


def month_wrong(m, valid):
    wrong = {4: ["よんがつ"], 9: ["きゅうがつ"], 7: ["なながつ"]}.get(m, [])
    return [w for w in wrong if w not in valid["m"][str(m)] and w not in ACCEPTABLE]


def day_wrong(d, valid):
    out = []
    if d in N.DAY_SPECIAL:
        out.append(N.number(d) + "にち")                       # よっか -> よんにち
    if d in DAY_PAIR:
        out.append(N.day(DAY_PAIR[d]))                        # よっか <-> ようか
    out += [N.day(x) for x in (d + 1, d - 1, d + 10, d - 10) if 1 <= x <= 31]
    ok = set(valid["d"][str(d)]) | ACCEPTABLE
    return [w for w in dict.fromkeys(out) if w not in ok and w != N.day(d)]


def pick3(correct, pool, rnd, extra=()):
    """Верный ответ и три неверных; не хватает типичных ошибок, добираем из extra."""
    pool = [p for p in dict.fromkeys(pool) if p != correct]
    rnd.shuffle(pool)
    rest = [p for p in dict.fromkeys(extra) if p != correct and p not in pool]
    rnd.shuffle(rest)
    opts = [correct] + (pool + rest)[:3]
    rnd.shuffle(opts)
    return opts, opts.index(correct)


def rand_date(rnd):
    m = rnd.randint(1, 12)
    tricky = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14, 20, 24]
    d = rnd.choice(tricky) if rnd.random() < 0.6 else rnd.randint(1, DAYS_IN.get(m, 31))
    return m, min(d, DAYS_IN.get(m, 31))


def date_listen(rnd, valid):
    m, d = rand_date(rnd)
    cands = [(m, DAY_PAIR.get(d, d + 1))] + [(mm, d) for mm in MONTH_NEAR[m]] + \
            [(m, x) for x in (d + 10, d - 10, d + 1, d - 1) if 1 <= x <= DAYS_IN.get(m, 31)]
    cands = [(mm, dd) for mm, dd in cands if 1 <= dd <= DAYS_IN.get(mm, 31)]
    extra = [N.date_written(m, x) for x in range(1, DAYS_IN.get(m, 31) + 1)]
    opts, ans = pick3(N.date_written(m, d), [N.date_written(*c) for c in cands], rnd, extra)
    return {"qt": "date_listen", "say": N.date_reading(m, d), "text": "Какую дату назвали?", "opts": opts, "ans": ans,
            "fb": fb(N.date_written(m, d), N.date_reading(m, d), N.date_ru(m, d))}


def date_read(rnd, valid):
    m, d = rand_date(rnd)
    right = N.date_reading(m, d)
    wrong = [f"{w} {N.day(d)}" for w in month_wrong(m, valid)] + [f"{N.month(m)} {w}" for w in day_wrong(d, valid)]
    extra = [f"{N.month(m)} {N.day(x)}" for x in range(1, 29) if N.day(x) not in valid["d"][str(d)]]
    opts, ans = pick3(right, wrong, rnd, extra)
    return {"qt": "date_read", "say": right, "text": f"<b>{N.date_written(m, d)}</b>\nКак читается?", "opts": opts,
            "ans": ans, "fb": fb(N.date_written(m, d), right, N.date_ru(m, d)), "after": True}


def date_ru(rnd, valid):
    m, d = rand_date(rnd)
    cands = [(m, DAY_PAIR.get(d, d + 1))] + [(mm, d) for mm in MONTH_NEAR[m]] + [(d, m)] if d <= 12 else \
        [(m, DAY_PAIR.get(d, d + 1))] + [(mm, d) for mm in MONTH_NEAR[m]]
    cands = [(mm, dd) for mm, dd in cands if 1 <= mm <= 12 and 1 <= dd <= DAYS_IN.get(mm, 31)]
    extra = [N.date_written(m, x) for x in range(1, DAYS_IN.get(m, 31) + 1)]
    opts, ans = pick3(N.date_written(m, d), [N.date_written(*c) for c in cands], rnd, extra)
    return {"qt": "date_ru", "say": N.date_reading(m, d), "text": f"Как написать: «{N.date_ru(m, d)}»?", "opts": opts,
            "ans": ans, "fb": fb(N.date_written(m, d), N.date_reading(m, d)), "after": True}


def weekday(rnd, valid):
    i = rnd.randrange(7)
    jp, kana, ru = N.WEEKDAY[i]
    if rnd.random() < 0.5:
        opts, ans = pick3(ru, [w[2] for w in N.WEEKDAY], rnd)
        return {"qt": "weekday", "say": kana, "text": f"<b>{jp}</b>\nКакой это день?", "opts": opts, "ans": ans,
                "fb": fb(jp, kana, ru), "after": True}
    opts, ans = pick3(jp, [w[0] for w in N.WEEKDAY], rnd)
    return {"qt": "weekday_listen", "say": kana, "text": "Какой день недели назвали?", "opts": opts, "ans": ans,
            "fb": fb(jp, kana, ru)}


def time_listen(rnd, valid):
    h, mi = rnd.choice([4, 7, 9, 1, 2, 3, 5, 6, 8, 10, 11, 12]), rnd.choice([0, 5, 10, 15, 20, 30, 45])
    near_h = {4: [7, 9], 7: [4, 1], 9: [4, 7], 1: [7, 11]}.get(h, [h % 12 + 1, (h - 2) % 12 + 1])
    fmt = lambda a, b: f"{a}:{b:02d}"
    cands = [fmt(x, mi) for x in near_h] + [fmt(h, x) for x in (0, 15, 30, 45, 5, 10) if x != mi]
    opts, ans = pick3(fmt(h, mi), cands, rnd, [fmt(x, mi) for x in range(1, 13)])
    return {"qt": "time_listen", "say": N.time(h, mi), "text": "Сколько времени назвали?", "opts": opts, "ans": ans,
            "fb": fb(f"{h}時{f'{mi}分' if mi and mi != 30 else '半' if mi == 30 else ''}", N.time(h, mi), fmt(h, mi))}


def num_listen(rnd, valid, numbers):
    n = rnd.choice([x for x in numbers if 11 <= x < 10000])  # まん в уроках дат не проходят
    s = str(n)
    cands = [n * 10, n // 10, int(s[::-1]) if s[::-1][0] != "0" else n + 1, n + 100 if n >= 100 else n + 10,
             n - 100 if n > 200 else n + 1]
    cands = [c for c in cands if c > 0 and c != n]
    opts, ans = pick3(f"{n:,}".replace(",", " "), [f"{c:,}".replace(",", " ") for c in cands], rnd,
                      [f"{c:,}".replace(",", " ") for c in numbers if c != n])
    return {"qt": "num_listen", "say": N.number(n), "text": "Какое число назвали?", "opts": opts, "ans": ans,
            "fb": fb(f"{n:,}".replace(",", " "), N.number(n))}


def time_read(rnd, valid):
    h = rnd.choice([4, 7, 9, 1, 2, 3, 6, 8, 10])
    right = N.HOUR[h]
    wrong = HOUR_WRONG.get(h, []) + [N.HOUR[x] for x in (h + 1, h - 1, h + 3) if 1 <= x <= 12]
    wrong = [w for w in wrong if w not in valid["h"][str(h)] and w not in ACCEPTABLE]
    extra = [N.HOUR[x] for x in range(1, 13) if N.HOUR[x] not in valid["h"][str(h)]]
    opts, ans = pick3(right, wrong, rnd, extra)
    return {"qt": "time_read", "say": right, "text": f"<b>{h}時</b>\nКак читается?", "opts": opts, "ans": ans,
            "fb": fb(f"{h}時", right), "after": True}


KINDS = {"date_listen": date_listen, "date_read": date_read, "date_ru": date_ru, "weekday": weekday,
         "time_listen": time_listen, "time_read": time_read}


def make(kind, rnd, bank):
    if kind == "num_listen":
        return num_listen(rnd, bank["valid"], bank["numbers"])
    return KINDS[kind](rnd, bank["valid"])


MONTH_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь",
             "декабрь"]
WEEK_NOTE = ["月 луна (и месяц)", "火 огонь", "水 вода", "木 дерево", "金 металл, золото", "土 земля", "日 солнце (и день)"]


def course(valid):
    """📅 Даты по шагам (всё сразу на слух не запомнить; удобнее «число, написание,
    произношение, перевод», потом месяц, год, числа, часы, минуты, секунды). В уроке 5-7 карточек, у каждой свой
    голос; вопросы по пройденному. kind: какие ответы похожи; near: похожие написания для «что прозвучало?»;
    wrong: неверные чтения для «как читается?» (не из допустимых по JMdict); cyr: чтение, где азбука ошибается."""
    def item(kind, jp, kana, ru, note="", near=(), wrong=(), cyr=None):
        return {"kind": kind, "jp": jp, "kana": kana, "ru": ru, "note": note, "near": list(near), "wrong": list(wrong),
                "cyr": cyr}

    def day(d, note="", cyr=None):
        near = [f"{x}日" for x in (DAY_PAIR.get(d), d + 10, d - 10, d + 1, d - 1) if x and 1 <= x <= 31]
        return item("day", f"{d}日", N.day(d), f"{d}-е число", note, near, day_wrong(d, valid), cyr)

    def month(m, note="", cyr=None):
        return item("month", f"{m}月", N.month(m), MONTH_NOM[m - 1], note, [f"{x}月" for x in MONTH_NEAR[m]],
                    month_wrong(m, valid) + [N.month(x) for x in MONTH_NEAR[m]], cyr)

    def hour(h, ru, note=""):
        near = {1: [7, 11], 4: [7, 9], 7: [4, 1], 9: [4, 7], 10: [1, 11]}[h]
        wrong = HOUR_WRONG.get(h, []) + [N.HOUR[x] for x in (h - 1, h + 1, h + 3) if 1 <= x <= 12]
        ok = set(valid["h"][str(h)]) | ACCEPTABLE
        return item("hour", f"{h}時", N.HOUR[h], ru, note, [f"{x}時" for x in near], [w for w in wrong if w not in ok])

    def num(n, ru, note="", near=(), wrong=()):
        return item("num", str(n), N.number(n), ru, note, [str(x) for x in near], wrong)

    def minute(m, ru, note="", near=(), wrong=()):
        return item("min", f"{m}分", N.minute(m), ru, note, [f"{x}分" for x in near], wrong)

    def row(nums, ru, note):
        parts = [num(n, w) for n, w in zip(nums, ru.split(", "))]
        return {"kind": "row", "jp": " · ".join(str(n) for n in nums), "kana": " · ".join(p["kana"] for p in parts),
                "ru": ru, "note": note, "parts": parts}

    return [
        {"title": "Числа месяца с 1-го по 5-е", "intro": "Первые десять чисел месяца по-японски особые: их запоминают как "
         "отдельные слова. Сегодня первые пять. На каждой карточке одно число: как пишется, как читается и что значит. Голос "
         "можно переслушать, 🐢 повторит медленно.",
         "items": [day(1, "Особое слово, на обычный счёт не похоже."), day(2), day(3, "っ это короткая пауза перед «к»."),
                   day(4, "Тоже с паузой перед «к»."), day(5)]},
        {"title": "Числа месяца с 6-го по 10-е", "intro": "Ещё пять особых чисел. После них будет проще: с 11-го "
         "почти всё по правилу.",
         "items": [day(6), day(7), day(8, "Похоже на 4日 よっか. Здесь долгое «ёо», а в よっか пауза перед «к»."),
                   day(9), day(10, "Долгое «оо».")]},
        {"title": "Числа до 100", "intro": "Обычный счёт. На нём строятся числа месяца с 11-го, месяцы, годы и время. "
         "Десятки: число + じゅう, 20 にじゅう. Число собирается по частям: 56 = ご·じゅう·ろく, «пять, десять, шесть».",
         "items": [row([1, 2, 3, 4, 5], "один, два, три, четыре, пять", "4 бывает и し, но в числах обычно よん: 40 よんじゅう."),
                   row([6, 7, 8, 9, 10], "шесть, семь, восемь, девять, десять",
                       "7 бывает и しち, 9 бывает и く: так будет в месяцах и часах. В числах обычно なな и きゅう."),
                   num(20, "двадцать", "Два и десять: に + じゅう.", [2, 12, 200], ["にひゃく", "じゅうに"]),
                   num(56, "пятьдесят шесть", "Пять, десять, шесть: ご + じゅう + ろく.", [65, 46, 506],
                       ["ろくじゅうご", "ごひゃくろく", "ごじゅうはち"]),
                   num(99, "девяносто девять", "Девять, десять, девять.", [90, 9, 909], ["きゅうひゃくきゅう", "きゅうじゅう", "じゅうきゅう"])]},
        {"title": "Числа месяца с 11-го", "intro": "С 11-го числа работает правило: число + にち «нити». 12日 じゅうににち, 15日 "
         "じゅうごにち, 30日 さんじゅうにち. Особые только 14, 20 и 24. А в 17, 19, 27 и 29 семь и девять звучат как しち и く.",
         "items": [day(11, "По правилу: じゅういち + にち.", cyr="дзюуитинити"),
                   day(14, "Как 4日 よっか, только с じゅう впереди."), day(17, "Семь здесь しち."),
                   day(19, "Девять здесь く."), day(20, "Особое слово. Не путай с 2日 ふつか."),
                   day(24, "Как 4日: にじゅう + よっか.")]},
        {"title": "Месяцы", "intro": "Месяц = число + がつ «гацу»: 1月 いちがつ, 2月 にがつ, 3月 さんがつ и так до "
         "декабря. Ловушки только в апреле, июле и сентябре. В дате сначала месяц, потом число.",
         "items": [month(1), month(4, "Четыре здесь し, а не よん."), month(7, "Семь здесь しち, а не なな."),
                   month(9, "Девять здесь く, а не きゅう."), month(11, "По правилу: じゅういち + がつ.", cyr="дзюуитигацу"),
                   item("date", "10月1日", N.date_reading(10, 1), "1 октября", "Сначала месяц, потом число.",
                        ["1月10日", "10月2日", "10月7日"], ["じゅうがつ ふつか", "じゅういちがつ ついたち", "いちがつ とおか"])]},
        {"title": "Дни недели", "intro": "День недели = знак + ようび «ёоби». Знаки с понедельника по воскресенье: "
         "луна, огонь, вода, дерево, металл, земля, солнце.",
         "items": [item("week", jp, kana, ru, note, (), [w[1] for w in N.WEEKDAY if w[1] != kana])
                   for (jp, kana, ru), note in zip(N.WEEKDAY, WEEK_NOTE)]},
        {"title": "Сотни, тысячи и год", "intro": "100 ひゃく, 1000 せん. В сотнях после 3, 6 и 8 звук меняется: 300 "
         "さんびゃく, 600 ろっぴゃく, 800 はっぴゃく. В тысячах после 3 и 8: 3000 さんぜん, 8000 はっせん. "
         "Год = число + ねん, только 4 здесь よ: 2024年 "
         "にせんにじゅうよねん.",
         "items": [num(100, "сто", "Просто ひゃく, без いち.", [1000, 10, 200], ["いちひゃく"]),
                   num(300, "триста", "Не ひゃく, а びゃく.", [3000, 30, 600], ["さんひゃく", "さんぴゃく"]),
                   num(600, "шестьсот", "ろく + ひゃく = ろっぴゃく.", [6000, 60, 300], ["ろくひゃく", "ろっひゃく"]),
                   num(800, "восемьсот", "はち + ひゃく = はっぴゃく.", [8000, 80, 300], ["はちひゃく", "はっひゃく"]),
                   num(3000, "три тысячи", "Не せん, а ぜん.", [300, 8000, 30], ["さんせん"]),
                   item("year", "2026年", N.year(2026), "2026 год", "Число целиком + ねん.",
                        ["2016年", "2062年", "2006年"])]},
        {"title": "Часы", "intro": "Час = число + じ «дзи»: 1時 いちじ, 2時 にじ, 3時 さんじ. Ловушки в 4, 7 и 9 "
         "часов: よじ, しちじ, くじ. Половина: 半 はん, 7時半 = 7:30.",
         "items": [hour(1, "1 час"), hour(4, "4 часа", "Четыре здесь よ, а не よん."),
                   hour(7, "7 часов", "Семь здесь しち, как в июле."), hour(9, "9 часов", "Девять здесь く, как в сентябре."),
                   hour(10, "10 часов"),
                   item("time", "7時半", N.time(7, 30), "7:30, половина восьмого", "半 はん: половина.",
                        ["7時", "4時半", "1時半"])]},
        {"title": "Минуты и секунды", "intro": "Минута = число + ふん или ぷん. После 1, 3, 4, 6, 8 и 10 звучит "
         "ぷん «пун»: いっぷん, さんぷん, よんぷん, ろっぷん, はっぷん, じゅっぷん. После остальных ふん «фун»: ごふん. "
         "Секунда = число + びょう «бёо», без ловушек.",
         "items": [minute(1, "1 минута", "いち + ぷん = いっぷん.", [10, 7, 11], ["いちふん", "いちぷん"]),
                   minute(3, "3 минуты", "После 3 звучит ぷん.", [30, 13, 8]),
                   minute(5, "5 минут", "После 5 звучит ふん.", [15, 50, 9], ["ごぷん"]),
                   minute(10, "10 минут", "じゅう + ぷん = じゅっぷん.", [1, 100, 20]),
                   minute(15, "15 минут", "Как 5: じゅうご + ふん.", [50, 5, 25], ["じゅうごぷん"]),
                   item("sec", "30秒", N.number(30) + "びょう", "30 секунд", "Секунды без ловушек: число + びょう.",
                        ["30分", "13秒", "3秒"])]},
    ]


def cheat():
    """📋 Шпаргалка построчно: у каждой строки свой голос, слова медленно и с паузой (одной записью не разобрать)."""
    row = lambda xs, f: [(x, f(x)) for x in xs]
    return [
        {"title": "Шпаргалка: числа месяца", "prefix": "", "words": row(range(1, 6), N.day), "tail": ""},
        {"title": "", "prefix": "", "words": row(range(6, 11), N.day),
         "tail": "Дальше число + にち: 11 じゅういちにち, 12 じゅうににち."},
        {"title": "Ловушки", "prefix": "Числа: ", "words": row([14, 17, 19, 20, 24], N.day), "tail": ""},
        {"title": "", "prefix": "Месяцы: ", "words": row([4, 7, 9], N.month), "tail": ""},
        {"title": "", "prefix": "Часы: ", "words": row([4, 7, 9], N.HOUR.get) + [("7時半", N.time(7, 30))], "tail": ""},
        {"title": "", "prefix": "Минуты: ", "words": row([1, 3, 5, 10], N.minute), "tail": ""},
    ]
