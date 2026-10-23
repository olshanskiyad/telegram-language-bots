"""Прогон курса без Телеграма: жмёт кнопки как ученик и проверяет ограничения Телеграма.

  python3 simulate.py     двое учеников (мужская и женская речь): все уроки подряд, пауза урока,
                          все сценки «Разговора», повторение, тренажёр, прогресс, смена речи
Код выхода не 0, если нашлась ошибка. Нужны собранные course.json и course_f.json (python3 build.py --audio)
или тестовые: python3 tests/smoke.py гоняет оба скрипта на курсе, собранном с фиктивным словарём.
"""
import os
import random
import re
import sys
import tempfile

import engine as E

problems, missing, stats = [], set(), {"msgs": 0, "voices": 0, "questions": 0}


def check(outs, ctx):
    for o in outs:
        stats["msgs"] += 1
        plain = re.sub(r"<[^>]+>", "", o.text or "")
        if o.kind == "voice":
            stats["voices"] += 1
            if len(plain) > 1024:
                problems.append(f"{ctx}: подпись {len(plain)} > 1024")
            if not os.path.exists(o.path):
                missing.add(o.path)
        if len(plain) > 4096:
            problems.append(f"{ctx}: текст {len(plain)} > 4096")
        tags = re.findall(r"<(/?)(b|i)>", o.text or "")
        for t in ("b", "i"):
            if [x for x in tags if x[1] == t and not x[0]].__len__() != [x for x in tags if x[1] == t and x[0]].__len__():
                problems.append(f"{ctx}: незакрытый тег <{t}>: {o.text[:80]}")
        if re.search(r"<(?!/?(b|i)>)", o.text or ""):
            problems.append(f"{ctx}: неэкранированный «<»: {o.text[:80]}")
        for row in o.buttons or []:
            for label, data in row:
                if len(data.encode()) > 64:
                    problems.append(f"{ctx}: данные кнопки > 64 байт: {data}")
                if not label.strip():
                    problems.append(f"{ctx}: пустая кнопка")
        for bad in ("\u2014", "\u2013"):
            if bad in (o.text or "") or any(bad in lab for row in o.buttons or [] for lab, _ in row):
                problems.append(f"{ctx}: длинное тире: {o.text[:80]}")
    return outs


def buttons(outs):
    return [(d, o.kind == "voice") for o in outs for row in (o.buttons or []) for _, d in row]


def play(eng, outs, ctx, accuracy=0.85, max_steps=400):
    """Жмёт кнопки, пока поток не закончится. Возвращает последние выходы."""
    for _ in range(max_steps):
        check(outs, ctx)
        bs = buttons(outs)
        pick = None
        for d, v in bs:
            if d.startswith(("n:", "r:")):
                pick = (d, v)
        ans = [d for d, _ in bs if d.startswith("a:")]
        if ans:
            s = eng.session()
            step = s["steps"][s["i"]]
            k = step["ans"] if random.random() < accuracy else random.choice([i for i in range(len(ans)) if i != step["ans"]] or [0])
            stats["questions"] += 1
            pick = (f"a:{s['nonce']}:{k}", False)
        grades = [d for d, _ in bs if d.startswith("g:")]
        if grades:
            pick = (random.choices(grades, weights=[1, 2, 6])[0], True)
        for d, _ in bs:  # после ошибки в тоне иногда жмём «сравнить», это отдельное голосовое
            if d.startswith("cmp:") and random.random() < 0.5:
                stats["compare"] = stats.get("compare", 0) + 1
                check(eng.on_callback(d), f"{ctx} сравнить")
        if not pick:
            return outs
        outs = eng.on_callback(*pick)
    problems.append(f"{ctx}: поток не закончился за {max_steps} шагов")
    return outs


def run_course(u, ctx):
    """Весь курс одним учеником: пауза урока, все уроки, сценки, повторение, тренажёр."""
    outs = check(u.on_callback("ls"), f"{ctx} урок 1")
    outs = u.on_callback(buttons(outs)[0][0])
    check(u.on_menu("🎵 Тоны"), f"{ctx} тренажёр до уроков")
    outs = check(u.on_menu("▶ Урок"), f"{ctx} продолжение урока")
    if "Продолжаем урок 1" not in outs[0].text:
        problems.append(f"{ctx}: отложенный урок не продолжился")
    for n, lid in enumerate(u.order, 1):
        if n > 1:
            outs = u.on_callback("ls")
        last = play(u, outs, f"{ctx} урок {n} {lid}")
        if lid not in u.done():
            problems.append(f"{ctx}: урок {lid} не отметился пройденным: {last[-1].text[:100] if last else ''}")
    # алфавит вне основного пути, по желанию: /abc
    if any(lid in u.order for lid in u.abc) or u.next_lesson():
        problems.append(f"{ctx}: алфавит попал в основной путь или путь не пройден без него")
    for n, lid in enumerate(u.abc, 1):
        outs = u.alphabet()
        if f"Урок алфавита {n}." not in outs[0].text:
            problems.append(f"{ctx}: /abc открыл не урок алфавита {n}: {outs[0].text[:60]}")
        last = play(u, outs, f"{ctx} алфавит {n} {lid}")
        if lid not in u.done():
            problems.append(f"{ctx}: урок алфавита {lid} не отметился пройденным")
    if "пройдены" not in u.alphabet()[0].text or "Урок алфавита 3." not in u.alphabet(3)[0].text:
        problems.append(f"{ctx}: /abc после всего алфавита или /abc 3 не сработали")
    # разминка: карточки повторения в начале урока двигают повторение и не входят в счёт урока
    u.st.run("update reviews set due=? where uid=?", (u.today().isoformat(), u.uid))
    outs = u.on_callback("rl:s1_03")
    steps = u.session()["steps"]
    warm = [x["th"] for x in steps if x.get("rev")]
    state = lambda: dict(u.st.q("select th, box || due from reviews where uid=?", (u.uid,)))
    before = state()
    last = play(u, outs, f"{ctx} урок с разминкой")
    if "rv" not in [d for d, _ in buttons(last)]:
        problems.append(f"{ctx}: в конце урока нет кнопки повторения, хотя карточки ждут")
    after = state()
    total = u.st.q("select total from lessons_done where uid=? and id='s1_03'", (u.uid,))[0][0]
    if len(warm) != E.WARMUP or "разминка" not in outs[0].text or all(after[th] == before[th] for th in warm) \
            or total != sum(1 for x in steps if x["t"] == "q"):
        problems.append(f"{ctx}: разминка: карточек {len(warm)}, в счёте урока {total}")
    # сценки: шпаргалка, дорожка и урок-сценка; урок, начатый до сценки, не теряется
    check(u.on_callback("rl:s1_03"), f"{ctx} урок перед сценкой")
    for pid, P in u.scenes.items():
        check(u.on_callback(f"pl:{pid}"), f"{ctx} сценка {pid}")
        check(u.on_callback(f"pa:{pid}"), f"{ctx} сценка {pid} звук")
        steps = check(u.on_callback(f"tk:{pid}"), f"{ctx} сценка {pid} урок") and u.session()["steps"]
        if any(st["t"] == "q" and st["qt"] in ("tone", "same") for st in steps):
            problems.append(f"{ctx}: в сценке {pid} вопросы про тоны")
        seen = {st["th"] for st in steps if st["t"] == "item"}
        if seen != {x["th"] for x in P["seq"]}:
            problems.append(f"{ctx}: в сценке {pid} показаны не все фразы")
        play(u, u.render(), f"{ctx} сценка {pid}")
        if pid not in u.done():
            problems.append(f"{ctx}: сценка {pid} не отметилась пройденной")
    if not u.get("paused"):
        problems.append(f"{ctx}: урок, начатый до сценок, потерялся")
    check(u.scenes_menu(), f"{ctx} сценки")
    outs = check(u.on_menu(E.OLD_PLACES), f"{ctx} старая кнопка мест")
    if not outs[0].menu or "pl:hello" not in str(outs[-1].buttons):
        problems.append(f"{ctx}: старая кнопка «Я сейчас в» не ведёт в «Разговор»")
    # то, что только слышишь от тайцев, в повторении всегда на слух
    hear = [th for th in u.items if u.items[th].get("hear")]
    u.st.run("update reviews set due='2999-01-01' where uid=?", (u.uid,))
    u.st.run(f"update reviews set due=? where uid=? and th in ({','.join('?' * len(hear))})",
             (u.today().isoformat(), u.uid, *hear))
    check(u.start_review(), f"{ctx} повторение услышанного")
    dirs = {st["dir"] for st in u.session()["steps"] if st["t"] == "card"}
    if not hear or dirs != {"audio"}:
        problems.append(f"{ctx}: услышанные фразы в повторении не на слух: {dirs}")
    # все карточки сегодня, прогон повторения
    u.st.run("update reviews set due=? where uid=?", (u.today().isoformat(), u.uid))
    play(u, check(u.on_menu("🔁 Повторение"), f"{ctx} повторение"), f"{ctx} повторение")
    play(u, check(u.on_menu("🎵 Тоны"), f"{ctx} тренажёр"), f"{ctx} тренажёр")


def main():
    random.seed(7)
    db = tempfile.mktemp(suffix=".db")
    eng = E.Engine(store=E.Store(db))
    # двое учеников: мужская речь и женская
    for uid, g in ((1, "m"), (2, "f")):
        u = eng.add_user(uid, uid)
        check(u.ask_gender(first=True), f"ученик {uid} вопрос о речи")
        check(u.on_callback(f"gv:{g}"), f"ученик {uid} выбор речи")
    a, b = eng.user(1), eng.user(2)
    first_b = [b.items[th]["th"] for th in b.lessons["s1_01"]["items"]]
    if not any("ค่ะ" in x for x in first_b) or any("ครับ" in x for x in first_b):
        problems.append(f"урок 1 женской речи: {first_b}")
    run_course(a, "м")
    if b.done() or b.st.q("select count(*) from reviews where uid=2")[0][0]:
        problems.append("прогресс первого ученика попал ко второму")
    run_course(b, "ж")
    for u, ctx in ((a, "м"), (b, "ж")):
        for f in (u.progress, u.lesson_list, u.reminder):
            print(ctx, re.sub(r"<[^>]+>", "", f()[0].text), "\n---")
    # смена речи: повторение переезжает на фразы другого варианта, ничего не теряется
    back = {f: m for m, f in eng.variants["f"]["c"]["twins"].items()}
    want = {back.get(r[0], r[0]) for r in b.st.q("select th from reviews where uid=2")}  # ฉัน и ผม из чтения сольются
    check(eng.user(2).on_callback("gv:m"), "смена речи на мужскую")
    rv = {r[0] for r in b.st.q("select th from reviews where uid=2")}
    lost = [th for th in rv if th not in eng.variants["m"]["items"]]
    if lost or rv != want:
        problems.append(f"смена речи: ждали {len(want)}, стало {len(rv)}, нет в мужском курсе {lost[:5]}")
    check(eng.user(2).on_callback("gv:f"), "смена речи обратно")
    lost = [r[0] for r in b.st.q("select th from reviews where uid=2") if r[0] not in eng.variants["f"]["items"]]
    if lost:
        problems.append(f"обратная смена речи: нет в женском курсе {lost[:5]}")
    b = eng.user(2)
    check(b.on_menu("🔁 Повторение"), "повторение после смены речи")
    check(a.on_callback("n:1"), "устаревшая кнопка")
    check(a.on_callback("anc"), "эталоны")
    check(a.on_callback("rl:s1_03"), "урок ещё раз")
    check(a.on_callback("cmp:00000000:low"), "сравнить без слова")
    for u, ctx in ((a, "м"), (b, "ж")):
        qt = u.st.q("select qt, count(*), sum(ok) from answers where uid=? group by qt", (u.uid,))
        print(ctx, "записанные ответы:", qt, "| точность:", u.tone_stats_line())
    kinds = {}
    for lid in a.order + a.abc:
        for st in a.start_lesson(lid) and a.session()["steps"]:
            if st["t"] == "q":
                kinds[st["qt"]] = kinds.get(st["qt"], 0) + 1
    print("виды вопросов по курсу:", kinds)
    # неверные варианты из темы урока
    a.start_lesson("s2_07_2")
    own = {a.items[th]["ru"] for th in a.lesson_pool(a.lessons["s2_07_2"])}
    off = [q["opts"] for q in a.session()["steps"] if q["t"] == "q" and q["qt"] == "meaning" and set(q["opts"]) != own]
    if off:
        problems.append(f"s2_07_2: варианты не из урока {off[:2]}")
    # сбой связи: шаг сменился, а сообщение не дошло; старая кнопка позже RESEND_AFTER присылает текущий шаг
    s = a.session()
    old = s["nonce"]
    a.advance(s)
    quick = a.on_callback(f"n:{old}", False)
    s = a.session()
    s["at"] -= E.RESEND_AFTER + 1
    a.save(s)
    late = a.on_callback(f"n:{old}", False)
    if [o.kind for o in quick] != ["toast"] or not [o for o in late if o.kind != "toast"]:
        problems.append(f"старая кнопка после потерянного шага: сразу {[o.kind for o in quick]}, позже {[o.kind for o in late]}")
    print(f"нажатий «сравнить»: {stats.get('compare', 0)}")
    for u, ctx in ((a, "м"), (b, "ж")):
        print(f"{ctx}: уроков пройдено {len(u.done() & set(u.order))}/{len(u.order)}, "
              f"алфавита {len(u.done() & set(u.abc))}/{len(u.abc)}, сценок {len(u.done() & set(u.scenes))}/{len(u.scenes)}, "
              f"в повторении {u.st.q('select count(*) from reviews where uid=?', (u.uid,))[0][0]}")
    # знакомство новичка: цель -> что знает -> проверка на слух -> план и сразу первый урок
    for k, (goal, lvl, right, want_skip, want_les) in enumerate([("trip", 0, True, 0, "s1_01"), ("live", 1, True, 1, "s2_01"),
                                                               ("talk", 1, False, 0, "s1_01"), ("trip", 2, True, 13, "s2_07"),
                                                               ("live", 2, False, 0, "s1_01")]):
        uid = 700 + k
        eng.add_user(uid, uid)
        u = eng.user(uid)
        if "ob:goal:trip" not in str(u.on_callback("gv:f")[-1].buttons):
            problems.append("после выбора речи новичку не задан вопрос о цели")
        eng.user(uid).on_callback(f"ob:goal:{goal}")
        outs = eng.user(uid).on_callback(f"ob:lvl:{lvl}")
        while (s := (u := eng.user(uid)).session()) and s["mode"] == "check":
            q = s["steps"][s["i"]]
            outs = u.on_answer(s["nonce"], q["ans"] if right else (q["ans"] + 1) % len(q["opts"]))
        u = eng.user(uid)
        s4 = [x for x in u.order if x.startswith("s4_")]
        if not any("План под" in (o.text or "") for o in outs) or (u.session() or {}).get("lesson") != want_les \
                or len(u.get("skip") or []) != want_skip or not u.lessons[s4[0]]["title"].startswith(E.GOALS[goal][2]):
            problems.append(f"знакомство {goal}/{lvl}/{right}: урок {(u.session() or {}).get('lesson')}, пропуск {u.get('skip')}")
    rep = "\n".join(o.text for o in eng.users_report())
    if "Ученики: 7" not in rep or rep.count("Пройдено уроков: 69 из 69") != 2 or "Новички за 30 дней: пришли" not in rep:
        problems.append(f"/users: {rep[:300]}")
    print(f"сообщений {stats['msgs']}, голосовых {stats['voices']}, вопросов {stats['questions']}")
    print(f"нет файлов озвучки: {len(missing)}")
    os.remove(db)
    if problems:
        print("ПРОБЛЕМЫ:\n  " + "\n  ".join(problems[:40]))
        sys.exit(1)
    print("ок")


if __name__ == "__main__":
    main()
