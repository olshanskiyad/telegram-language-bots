"""Прогон японского бота без Телеграма, несколько учеников сразу. Код выхода не 0 при проблеме.

  python3 simulate.py
Нужны собранные данные (python3 build.py --audio) или тестовые: python3 tests/smoke.py гоняет оба скрипта
на тестовых данных без словарей.
"""
import json
import os
import random
import re
import sys
import tempfile

import engine as E

problems, missing, stats = [], set(), {"msgs": 0, "voices": 0, "answers": 0}
slow_keys = {}  # кнопки 🐢, которые видел ученик: ключ -> где


def check(outs, ctx):
    for o in outs or []:
        stats["msgs"] += 1
        plain = re.sub(r"<[^>]+>", "", o.text or "")
        if o.kind == "photo":
            stats["photos"] = stats.get("photos", 0) + 1
            if not os.path.exists(o.path):
                missing.add(o.path)
        if o.kind == "voice":
            stats["voices"] += 1
            if len(plain) > 1024:
                problems.append(f"{ctx}: подпись {len(plain)} > 1024")
            if not os.path.exists(o.path):
                missing.add(o.path)
            if not plain.strip():  # «дублировать текстом всегда»: голосовое без подписи не шлём
                problems.append(f"{ctx}: голосовое без текста")
            has_slow = any(d.startswith("sl:") for row in o.buttons or [] for _, d in row)
            if not has_slow and not plain.startswith("🐢") and "тест" not in ctx and ctx != "шпаргалка":
                problems.append(f"{ctx}: голосовое без 🐢: {plain[:60]}")
        if len(plain) > 4096:
            problems.append(f"{ctx}: текст {len(plain)} > 4096")
        for t in ("b", "i"):
            if len(re.findall(f"<{t}>", o.text or "")) != len(re.findall(f"</{t}>", o.text or "")):
                problems.append(f"{ctx}: незакрытый <{t}>: {o.text[:80]}")
        if re.search(r"<(?!/?(b|i)>)", o.text or ""):
            problems.append(f"{ctx}: неэкранированный «<»: {o.text[:80]}")
        for row in o.buttons or []:
            for label, data in row:
                if data.startswith("sl:"):
                    slow_keys.setdefault(data, ctx)
                if len(data.encode()) > 64:
                    problems.append(f"{ctx}: данные кнопки > 64 байт: {data}")
                if not label.strip():
                    problems.append(f"{ctx}: пустая кнопка")
        if any(b in (o.text or "") for b in ("\u2014", "\u2013")):
            problems.append(f"{ctx}: длинное тире: {o.text[:80]}")
    return outs


def buttons(outs):
    return [d for o in outs for row in (o.buttons or []) for _, d in row]


def step_once(eng, uid, outs, acc):
    s = eng.session(uid)
    if not s:
        return None
    st = s["steps"][s["i"]]
    bs = buttons(outs)
    if st["t"] == "q":
        need = 2 if st.get("qt") in ("onkun_kind", "c_kind") else 4  # «кунное или онное» вдвоём, остальное вчетвером
        if len(set(st["opts"])) < need:
            problems.append(f"вопрос {st.get('qt')}: вариантов {len(set(st['opts']))}: {re.sub('<[^>]+>', '', st['text'])[:30]} {st['opts']}")
        k = st["ans"] if random.random() < acc else random.choice([i for i in range(len(st["opts"])) if i != st["ans"]] + [E.IDK])
        stats["answers"] += 1
        return eng.on_callback(uid, f"a:{s['nonce']}:{k}")
    if st["t"] == "card":
        out = eng.on_callback(uid, f"r:{s['nonce']}")
        check(out, "карточка")
        return eng.on_callback(uid, f"g:{s['nonce']}:{random.choice([0, 1, 2, 2])}")
    nxt = [d for d in bs if d.startswith("n:")]
    return eng.on_callback(uid, nxt[-1] if nxt else f"n:{s['nonce']}")


def play(eng, uid, outs, ctx, acc=0.7, limit=400):
    for _ in range(limit):
        check(outs, ctx)
        if not eng.session(uid):
            return outs
        outs = step_once(eng, uid, outs, acc)
    problems.append(f"{ctx}: не закончилось за {limit} шагов")
    return outs


def main():
    random.seed(3)
    eng = E.Engine(store=E.Store(tempfile.mktemp(suffix=".db")), rnd=random.Random(3))
    OWNER, LEARNER = 1, 2
    eng.add_user(OWNER, 11, "owner", "владелец")
    eng.add_user(LEARNER, 22, "member", "")
    check(eng.welcome(LEARNER), "приветствие")
    # два теста одновременно, у каждого свой
    a = check(eng.on_menu(LEARNER, "📝 Тест"), "тест ученика")
    o = check(eng.on_menu(OWNER, "📝 Тест"), "тест владельца")
    for _ in range(6):
        a = step_once(eng, LEARNER, a, 0.8)
        o = step_once(eng, OWNER, o, 0.3)
    if eng.session(LEARNER)["i"] == eng.session(OWNER)["i"] and eng.session(LEARNER)["steps"] == eng.session(OWNER)["steps"]:
        problems.append("сессии учеников не разделены")
    # тест на паузе ради тренажёра и продолжение
    d = check(eng.on_menu(LEARNER, "📅 Даты и числа"), "даты посреди теста")
    play(eng, LEARNER, d, "даты")
    r = check(eng.on_menu(LEARNER, "📝 Тест"), "возврат к тесту")
    if "Продолжаем" not in r[0].text:
        problems.append("тест не продолжился после тренажёра")
    play(eng, LEARNER, r, "тест ученика", acc=0.75)
    play(eng, OWNER, o, "тест владельца", acc=0.3)
    ts, res = eng.last_result(LEARNER)
    if not res:
        problems.append("результат теста не сохранён")
    else:
        print("ИТОГ ТЕСТА (ученик):\n  " + "\n  ".join(res["lines"]))
    print("разделы теста:", {k: len(v) for k, v in res["res"].items()} if res else None)
    # уроки: сначала вводный «два чтения», потом урок кандзи с места, которое выбрал тест
    ci0 = eng.get(LEARNER, "course_i")
    print("урок по итогам теста у ученика:", ci0)
    first = check(eng.on_menu(LEARNER, "📚 Урок"), "вводный урок")
    if "вводный" not in first[0].text:
        problems.append("перед уроками кандзи не показан вводный урок")
    play(eng, LEARNER, first, "вводный урок")
    play(eng, LEARNER, check(eng.on_menu(LEARNER, "📚 Урок"), "урок кандзи"), "урок кандзи", acc=0.6)
    if eng.get(LEARNER, "course_i") != ci0 + 1:
        problems.append("урок не продвинул курс")
    play(eng, LEARNER, check(eng.on_callback(LEARNER, f"ls:{ci0}"), "урок ещё раз"), "урок ещё раз")
    if eng.get(LEARNER, "course_i") != ci0 + 1:
        problems.append("повтор пройденного урока сдвинул курс")
    g1 = check(eng.on_menu(LEARNER, "📚 Урок"), "грамматика по очереди")
    if eng.session(LEARNER)["mode"] != "grammar":
        problems.append("после урока кандзи 📚 Урок дал не грамматику")
    play(eng, LEARNER, g1, "урок грамматики", acc=0.7)
    if eng.get(LEARNER, "gram_i") != 1:
        problems.append("урок грамматики не продвинул курс грамматики")
    play(eng, LEARNER, check(eng.on_callback(LEARNER, "lg:0"), "грамматика ещё раз"), "грамматика ещё раз")
    l2 = check(eng.on_menu(LEARNER, "📚 Урок"), "следующий урок")
    for _ in range(4):
        l2 = step_once(eng, LEARNER, l2, 0.9)
    play(eng, LEARNER, check(eng.on_menu(LEARNER, "📅 Даты и числа"), "даты посреди урока"), "даты посреди урока")
    r = check(eng.on_menu(LEARNER, "📚 Урок"), "возврат к уроку")
    if "Продолжаем" not in r[0].text:
        problems.append("урок не продолжился после тренажёра")
    play(eng, LEARNER, r, "следующий урок")
    if not eng.st.q("select count(*) from reviews where uid=? and kana=''", (LEARNER,))[0][0]:
        problems.append("кандзи урока не попали в повторение")
    # каждый урок курса: подписи, кнопки, озвучка
    ALL = 3
    eng.add_user(ALL, 33, "member", "")
    eng.set(ALL, "onkun_done", True)
    qn = []
    for i in range(len(eng.lessons())):
        o = check(eng.on_callback(ALL, f"ls:{i}"), f"урок {i + 1}")
        qn.append(sum(1 for st in eng.session(ALL)["steps"] if st["t"] == "q"))
        play(eng, ALL, o, f"урок {i + 1}", acc=0.85)
    print(f"уроков пройдено: {len(qn)}, вопросов в уроке {min(qn)}-{max(qn)}")
    gq = []
    for i in range(len(eng.gcourse())):
        o = check(eng.on_callback(ALL, f"lg:{i}"), f"грамматика {i + 1}")
        gq.append(sum(1 for st in eng.session(ALL)["steps"] if st["t"] == "q"))
        play(eng, ALL, o, f"грамматика {i + 1}", acc=0.8)
    print(f"уроков грамматики пройдено: {len(gq)}, вопросов в уроке {min(gq)}-{max(gq)}")
    # азбука с нуля: выбор старта, 24 урока подряд, четыре кнопки, потом всё меню и грамматика первой
    NEW = 6
    eng.add_user(NEW, 66, "member", "")
    w = check(eng.welcome(NEW), "приветствие новичка")
    if not eng.need_choice(NEW) or "tr:start" not in buttons(w):
        problems.append("новичку не предложен выбор старта")
    if "Что ты уже знаешь" not in (check(eng.on_menu(NEW, "📚 Урок"), "урок до выбора")[0].text or "") or eng.session(NEW):
        problems.append("до выбора старта урок начался")
    if "tr:check" not in buttons(w) or "tr:test" in buttons(w):
        problems.append("на входе нет лесенки уровня или остался длинный тест")
    # лесенка уровня: N верных ступеней -> место в курсе, на ступень два вопроса
    for k, (good, want) in enumerate([(0, "abc"), (1, "kata"), (2, "n5"), (4, "top")]):
        u = 900 + k
        eng.add_user(u, u, "member", "")
        o = check(eng.on_callback(u, "tr:check"), f"лесенка {good}")
        n_q = 0
        while (s := eng.session(u)) and s["mode"] == "place":
            q = s["steps"][s["i"]]
            o = check(eng.on_answer(u, s["nonce"], q["ans"] if E.PLACE.index(s["stage"]) < good else E.IDK), f"лесенка {good}")
            n_q += 1
        if eng.get(u, "placed") != want or n_q != 2 * min(good + 1, len(E.PLACE)) or not eng.session(u):
            problems.append(f"лесенка {good}: место {eng.get(u, 'placed')}, вопросов {n_q}, урок {bool(eng.session(u))}")
    o = check(eng.on_callback(NEW, "tr:start"), "с нуля")
    if not o[0].menu or eng.menu_for(NEW) != E.MENU_START or eng.session(NEW)["mode"] != "abc":
        problems.append("«с нуля» не дал четыре кнопки и первый урок азбуки")
    check(eng.on_menu(NEW, "🔤 Азбука"), "азбука пустая")
    aq, kinds = [], {}
    for i in range(len(eng.abc())):
        if i:
            o = check(eng.on_menu(NEW, "📚 Урок"), f"азбука {i + 1}")
        st = eng.session(NEW)["steps"]
        if eng.session(NEW)["mode"] != "abc" or eng.session(NEW)["ai"] != i:
            problems.append(f"урок азбуки {i + 1} не по порядку")
            break
        aq.append(sum(1 for x in st if x["t"] == "q"))
        for x in st:
            if x["t"] == "q":
                kinds[x["qt"]] = kinds.get(x["qt"], 0) + 1
                if len(x["opts"]) != 4 or len(set(x["opts"])) != 4:
                    problems.append(f"азбука {i + 1}: вариантов не 4 разных: {x['text'][:30]} {x['opts']}")
        if i == 3:  # посреди урока ушла в повторение и вернулась
            for _ in range(5):
                o = step_once(eng, NEW, o, 0.9)
            eng.st.run("update reviews set due=? where uid=?", (eng.today(NEW).isoformat(), NEW))
            play(eng, NEW, check(eng.on_menu(NEW, "🔁 Повторение"), "повторение азбуки"), "повторение азбуки")
            o = check(eng.on_menu(NEW, "📚 Урок"), "возврат к азбуке")
            if "Продолжаем" not in o[0].text:
                problems.append("урок азбуки не продолжился после повторения")
        o = play(eng, NEW, o, f"азбука {i + 1}", acc=0.8)
        if eng.get(NEW, "abc_i") != i + 1:
            problems.append(f"урок азбуки {i + 1} не продвинул азбуку")
        if i == 4:
            t = check(eng.on_menu(NEW, "🔤 Азбука"), "таблица азбуки")[0].text
            p = check(eng.progress(NEW), "прогресс новичка")[0].text
            r = check(eng.reminder(NEW), "напоминание новичку")[0].text
            if "Твоя азбука" not in t or "あ" not in t or "Азбука и первые фразы" not in p or "🌱 урок 6" not in r:
                problems.append("таблица, прогресс или напоминание азбуки не те")
            if "кандзи" in p or "📅" in r:
                problems.append("новичку в азбуке показаны кандзи или тренажёры")
            play(eng, NEW, check(eng.on_callback(NEW, "la:0"), "азбука 1 ещё раз"), "азбука 1 ещё раз")
            if eng.get(NEW, "abc_i") != 5:
                problems.append("повтор урока азбуки сдвинул азбуку")
    print(f"уроков азбуки: {len(aq)}, вопросов в уроке {min(aq)}-{max(aq)}, виды: {kinds}")
    if eng.get(NEW, "track") != "full" or eng.menu_for(NEW) != E.MENU or not any(x.menu for x in o or []):
        problems.append("после азбуки не открылось всё меню")
    g = check(eng.on_menu(NEW, "📚 Урок"), "после азбуки")
    if eng.session(NEW)["mode"] != "grammar":
        problems.append("после азбуки первым уроком не грамматика")
    play(eng, NEW, g, "грамматика после азбуки")
    if eng.session(NEW) or check(eng.on_menu(NEW, "📚 Урок"), "потом вводный")[0].text.find("вводный") < 0:
        problems.append("после грамматики нет вводного урока о чтениях кандзи")
    play(eng, NEW, eng.render(NEW), "вводный после азбуки")
    kana_cards = eng.st.q("select count(*) from reviews where uid=? and key like 'abc|%'", (NEW,))[0][0]
    phr_cards = eng.st.q("select count(*) from reviews where uid=? and key like 'phr|%'", (NEW,))[0][0]
    print(f"в повторении у новичка: знаков и рядов {kana_cards}, фраз {phr_cards}")
    eng.st.run("update reviews set due=? where uid=?", (eng.today(NEW).isoformat(), NEW))
    play(eng, NEW, check(eng.on_menu(NEW, "🔁 Повторение"), "повторение после азбуки"), "повторение после азбуки")
    # «знаю азбуку» и тест прямо с выбора; тест с незнакомой каной предлагает азбуку
    FULL, T = 7, 8
    for uid in (FULL, T):
        eng.add_user(uid, uid * 11, "member", "")
    o = check(eng.on_callback(FULL, "tr:full"), "знаю азбуку")
    if eng.menu_for(FULL) != E.MENU or "m:lesson" not in buttons(o):
        problems.append("«знаю азбуку» не дал всё меню")
    t = check(eng.on_callback(T, "tr:test"), "тест с выбора")
    offered = False
    for _ in range(40):
        s_ = eng.session(T)
        if not s_ or s_["mode"] != "test":
            break
        if s_["steps"][s_["i"]].get("alt"):
            offered = True
            t = check(eng.on_callback(T, "tr:start"), "азбука из теста")
            break
        t = step_once(eng, T, t, 0.0)
    if not offered or eng.session(T)["mode"] != "abc" or not eng.get(T, "paused_test"):
        problems.append("тест с незнакомой каной не предложил азбуку или потерял тест")
    play(eng, T, t, "азбука из теста")
    r = check(eng.on_menu(T, "📝 Тест"), "тест после азбуки")
    if "Продолжаем" not in r[0].text:
        problems.append("тест не продолжился после урока азбуки")
    # старый ученик со слабой каной в тесте: в напоминании предложена азбука
    OLD = 9
    eng.add_user(OLD, 99, "member", "")
    eng.st.run("insert into results values(?,?,?)", (OLD, "2000-01-01T00:00", json.dumps({"res": {"kana": [[0, 0]] * 6}, "lines": []})))
    eng.set(OLD, "course_i", 0)
    if "tr:start" not in buttons(check(eng.reminder(OLD), "напоминание со слабой каной")):
        problems.append("старому ученику со слабой каной не предложена азбука")
    # место в курсе по тесту
    P = 4
    eng.add_user(P, 44, "member", "")
    n4 = next(i for i, x in enumerate(eng.lessons()) if x["lvl"] == "N4")
    for k, (res_p, want) in enumerate([({"kmean": [[0, 1]] * 4, "kread": [[0, 1], [0, 0], [0, 0], [0, 1]]}, (0, False)),
                                       ({"kmean": [[0, 1]] * 4 + [[1, 0]] * 4, "kread": [[0, 1]] * 4 + [[1, 1]] * 4}, (n4, False)),
                                       ({"kmean": [[0, 1]] * 4 + [[1, 1]] * 4 + [[2, 0]] * 4,
                                         "kread": [[0, 1]] * 4 + [[1, 1]] * 3 + [[1, 0]] + [[2, 0]] * 4}, (n4, True))]):
        eng.st.run("insert into results values(?,?,?)", (P, f"2000-01-01T00:0{k}", json.dumps({"res": res_p, "lines": []})))
        if eng.placement(P) != want:
            problems.append(f"место в курсе {eng.placement(P)} вместо {want} для {res_p}")
    k1 = check(eng.on_menu(LEARNER, "🔤 Кана"), "кана на слух")
    if "Кана на слух" not in k1[0].text:
        problems.append("кана на слух без вступления в первый раз")
    play(eng, LEARNER, k1, "кана на слух")
    if "Кана на слух" in (check(eng.on_menu(LEARNER, "🔤 Кана"), "кана 2")[0].text or ""):
        problems.append("вступление каны показано повторно")
    play(eng, LEARNER, eng.render(LEARNER), "кана 2")
    # 📅 даты по шагам: уроки по порядку, у каждой карточки свой голос и 🐢, после уроков тренажёр;
    # «это я знаю» на первом уроке сразу даёт тренажёр
    DL, SK = 21, 22
    for u in (DL, SK):
        eng.add_user(u, u * 10, "member", "")
    for i in range(len(eng.dc)):
        o = check(eng.on_menu(DL, "📅 Даты"), f"даты, урок {i + 1}")
        s = eng.session(DL)
        if not s or s["mode"] != "dl" or s["di"] != i:
            problems.append(f"даты: урок {i + 1} не по порядку")
            break
        cards = [st for st in s["steps"] if st["t"] == "msg" and st.get("slow")]
        if len(cards) != len(eng.dc[i]["items"]) or not all(st.get("path") and os.path.exists(st["path"]) for st in cards):
            problems.append(f"даты, урок {i + 1}: карточка без голоса")
        if i == 0:  # снова 📅 посреди урока: тот же шаг, а не урок сначала
            o = step_once(eng, DL, step_once(eng, DL, o, 1.0), 1.0)
            at = eng.session(DL)["i"]
            o = check(eng.on_menu(DL, "📅 Даты"), "даты: кнопка посреди урока")
            if "Продолжаем" not in o[0].text or eng.session(DL)["i"] != at:
                problems.append("📅 посреди урока дат начал урок заново")
        play(eng, DL, o, f"даты, урок {i + 1}", acc=0.8)
        if eng.get(DL, "dl_i") != i + 1:
            problems.append(f"даты, урок {i + 1} не продвинул уроки дат")
    o = check(eng.on_menu(DL, "📅 Даты"), "тренажёр после уроков дат")
    if (eng.session(DL) or {}).get("mode") != "dates":
        problems.append("после уроков дат не тренажёр")
    play(eng, DL, o, "тренажёр дат")
    check(eng.on_menu(SK, "📅 Даты"), "даты, первый урок")
    check(eng.on_callback(SK, "dl:skip"), "даты: сразу тренажёр")
    if (eng.session(SK) or {}).get("mode") != "dates" or eng.get(SK, "paused_dl"):
        problems.append("«это я знаю» не дал тренажёр")
    for name, fn in (("слух", "🎧 Слух"), ("даты 2", "📅 Даты"), ("слух по старой кнопке", "🎧 Слушать"),
                     ("повторение", "🔁 Повторение")):
        eng.st.run("update reviews set due=?", (eng.today(LEARNER).isoformat(),))
        play(eng, LEARNER, check(eng.on_menu(LEARNER, fn), name), name)
    # разминка: перед уроком до 5 карточек повторения, результат урока не трогают, забытая возвращается
    # в конце урока без шапки; в итоге урока «Ждут повторения» и кнопка 🔁, в конце повторения «ещё» и урок
    WU = 9100
    eng.add_user(WU, WU, "member", "")
    eng.set(WU, "track", "full")
    eng.set(WU, "onkun_done", True)
    for k in range(8):
        eng.add_review(WU, f"w{k}", f"かな{k}", f"слово {k}")
    eng.st.run("update reviews set due=? where uid=?", (eng.today(WU).isoformat(), WU))
    o = check(eng.start_grammar(WU, 0), "урок с разминкой")
    s = eng.session(WU)
    warm = [st for st in s["steps"] if st.get("rev")]
    if len(warm) != E.WARMUP or "Разминка" not in (o[-1].text or ""):
        problems.append(f"разминка: карточек {len(warm)}, начало «{(o[-1].text or '')[:40]}»")
    eng.on_callback(WU, f"r:{s['nonce']}")
    eng.on_callback(WU, f"g:{s['nonce']}:0")  # первую забыли
    s = eng.session(WU)
    back = [st for st in s["steps"][s["i"]:] if st.get("rev") and st["key"] == warm[0]["key"]]
    if len(back) != 1 or back[0].get("head") or s["steps"][-2]["key"] != warm[0]["key"]:
        problems.append("забытая карточка разминки не вернулась в конце урока или вернулась с шапкой")
    random.seed(5)
    o = play(eng, WU, o, "урок с разминкой", acc=1.0)
    end = next((x for x in reversed(o) if x.text and "пройден" in x.text), None)
    qs = sum(1 for st in eng.grammar_steps(0) if st["t"] == "q")
    if not end or f"верно {qs} из {qs}" not in end.text or "Ждут повторения" not in end.text or \
            "m:review" not in buttons([end]):
        problems.append(f"итог урока после разминки: {re.sub('<[^>]+>', '', end.text if end else '')[:160]}")
    o = check(eng.start_review(WU), "повторение после урока")
    o = play(eng, WU, o, "повторение после урока")
    if not any("m:lesson" in buttons([x]) for x in o):
        problems.append("в конце повторения нет кнопки урока")
    # неверные варианты (как в тайском): «что значит» из кандзи этого урока, на слух вопрос к вопросу
    C = eng.b["course"]["kanji"]
    for i, les in enumerate(eng.lessons()):
        own = {C[c]["ru"] for c in les["kanji"]}
        off = [q["opts"] for q in eng.lesson_steps(i) if q.get("qt") == "c_mean" and not set(q["opts"]) <= own]
        if off:
            problems.append(f"урок кандзи {i + 1}: варианты не из урока {off[0]}")
    eng.start_listen(WU)
    for q in eng.session(WU)["steps"]:
        if q.get("qt") == "listen" and len({o.rstrip().endswith("?") for o in q["opts"]}) > 1 and \
                sum(o.rstrip().endswith("?") for o in q["opts"]) == 1 and q["opts"][q["ans"]].rstrip().endswith("?"):
            problems.append(f"на слух ответ единственный вопрос: {q['opts']}")
    # сбой связи: шаг сменился, а сообщение не дошло; старая кнопка позже RESEND_AFTER присылает текущий шаг
    s = eng.session(WU)
    old = s["nonce"]
    eng.advance(WU, s)
    quick = eng.on_callback(WU, f"n:{old}")
    s = eng.session(WU)
    s["at"] -= E.RESEND_AFTER + 1
    eng.set(WU, "session", s)
    late = eng.on_callback(WU, f"n:{old}")
    if [o.kind for o in quick] != ["toast"] or not [o for o in late if o.kind != "toast"]:
        problems.append(f"старая кнопка после потерянного шага: сразу {[o.kind for o in quick]}, позже {[o.kind for o in late]}")
    eng.set(WU, "session", None)
    cheat = check(eng.cheat(), "шпаргалка")  # медленно по словам, у каждой строки свой голос
    if len(cheat) != len(E.drills.cheat()) or any(o.kind != "voice" for o in cheat):
        problems.append("шпаргалка не голосом по строкам")
    for f in (lambda: eng.progress(LEARNER), lambda: eng.reminder(LEARNER)):
        out = check(f(), "справка")
        print("---\n" + re.sub(r"<[^>]+>", "", out[0].text)[:700])
    rep = "\n\n".join(o.text for o in check(eng.users_report(), "итоги учеников"))
    print("---\n" + re.sub(r"<[^>]+>", "", rep))
    for want in (f"(кандзи {len(eng.lessons())}, грамматика {len(eng.gcourse())})", "кандзи все пройдены",
                 f"азбука {len(eng.abc())} из {len(eng.abc())}", "Тренажёры: даты", f"даты {len(eng.dc)} из {len(eng.dc)}"):
        if want not in rep:
            problems.append(f"итоги учеников без «{want}»")
    check(eng.on_callback(LEARNER, "n:1"), "устаревшая кнопка")
    check(eng.on_callback(LEARNER, "sl:0000000000"), "медленно без фразы")
    # каждая кнопка 🐢, которую видели: голосовое с медленной записью (пока озвучка не готова, вежливый отказ)
    got = {"голос": 0, "готовится": 0}
    for key, ctx in slow_keys.items():
        out = check(eng.on_callback(LEARNER, key), f"🐢 {ctx}")
        if out[0].kind == "voice" and os.path.exists(out[0].path):
            got["голос"] += 1
        elif "готовится" in (out[0].toast or ""):
            got["готовится"] += 1
        else:
            problems.append(f"🐢 {ctx}: {out[0].kind} {out[0].toast or out[0].path}")
    print(f"кнопок 🐢: {len(slow_keys)}, {got}")
    n_rev = eng.st.q("select count(*) from reviews where uid=?", (LEARNER,))[0][0]
    print(f"\nсообщений {stats['msgs']}, голосовых {stats['voices']}, картинок {stats.get('photos', 0)}, "
          f"ответов {stats['answers']}, карточек у ученика {n_rev}")
    print(f"нет файлов озвучки: {len(missing)}", sorted(missing)[:3])
    if problems:
        print("ПРОБЛЕМЫ:\n  " + "\n  ".join(problems[:30]))
        sys.exit(1)
    print("ок")


if __name__ == "__main__":
    main()
