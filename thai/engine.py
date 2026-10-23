"""Логика курса без Телеграма: уроки, вопросы, повторение, прогресс.

Движок возвращает список Out, бот их только отрисовывает. Поэтому весь путь ученика
проверяется в консоли без токена (simulate.py).

Учеников несколько, прогресс по id Телеграма. Базовый движок держит оба варианта курса
(мужская речь ученика в course.json, женская в course_f.json); user(uid) даёт копию движка
с прогрессом и вариантом одного ученика.

Меню «💬 Разговор»: сценки из жизни (talk.yaml) и места (places.yaml). У сценки шпаргалка и урок
на пять минут: фразы порциями, свою сказать вслух, чужую понять на слух, диалог по ролям.

Слух на тоны тренируется так: новые слова порциями по 3 с вопросом сразу после порции;
«одинаковый тон или разный?» раньше, чем «какой тон?»; четыре «диктора» (два голоса и их
сдвиги по высоте); опорное слово на каждый тон и «сравнить» после ошибки; тренажёр
чаще даёт тоны, которые ученик путает.
"""
import copy
import datetime as dt
import hashlib
import html
import json
import os
import random
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import thaidict
import tts

DATA = thaidict.DATA

def _owner_id():
    """OWNER_ID из окружения: Telegram id владельца. Не задан: владелец первый, кто написал боту."""
    v = os.environ.get("OWNER_ID", "").strip()
    if v and not v.isdigit():
        raise SystemExit("OWNER_ID должен быть числом: Telegram id владельца (его показывает, например, @userinfobot)")
    return int(v) if v else None


OWNER_ID = _owner_id()
TZ = ZoneInfo(os.environ.get("THAI_TZ") or "UTC")  # часовой пояс отметок времени в истории
INTERVALS = [0, 1, 2, 4, 7, 15, 30]  # дни до следующего показа по ступеням
MAX_BOX, LEARNED_BOX = 6, 4
REVIEW_LIMIT = 20
QUIZ_LIMIT = 8        # итоговая проверка урока
CHUNK = 3             # новых слов в порции
WARMUP = 5            # карточек повторения в начале урока: повторение идёт само, без отдельной кнопки
RESEND_AFTER = 20     # с, нажатие на прошлый шаг позже этого присылает текущий шаг снова
REPEAT_BELOW = 0.8    # ниже этой доли верных ответов предлагаем пройти урок ещё раз
TONES = ["mid", "low", "falling", "high", "rising"]
TONE_RU = {"mid": "средний", "low": "низкий", "falling": "нисходящий", "high": "высокий", "rising": "восходящий"}
TONE_MARK = {"mid": "a", "low": "à", "falling": "â", "high": "á", "rising": "ǎ"}
TONE_ARROW = {"mid": "→", "low": "↘", "falling": "⤵", "high": "↗", "rising": "⤴"}
ANCHORS = {"mid": "มา", "low": "ใหม่", "falling": "ไม่", "high": "ม้า", "rising": "หมา"}  # опорные слова тонов
VOICES = ["m", "f", "m+", "f-"]  # мужской, женский, мужской выше, женский ниже
CLS_RU = {"m": "средний", "h": "высокий", "l": "низкий"}
MENU = [["▶ Урок", "💬 Разговор"], ["🔁 Повторение", "🎵 Тоны"], ["📊 Прогресс", "✍️ Написать"]]
MENU_V = 2  # номер раскладки: у кого старая, получит новую вместе с ближайшим итогом (2 = ✍️ Написать)
FB_WAIT = 1800  # ✍️ Написать: сообщение ждём полчаса
FB_MORE = 120  # и ещё две минуты после него: скриншот или голосовое вдогонку уходят без нового ответа
OLD_PLACES = "📍 Я сейчас в…"  # старая кнопка меню: у кого-то она ещё может быть на клавиатуре
ZONE_RU = {3: "по Москве", 7: "по Таиланду"}
SCHEMA = """
create table if not exists users (uid integer primary key, chat integer, created text, g text,
    tz integer not null default 3, remind text not null default '09:00', reminded text,
    role text not null default 'member');
create table if not exists kv (uid integer, key text, value text, primary key (uid, key));
create table if not exists lessons_done (uid integer, id text, done_at text, score int, total int, primary key (uid, id));
create table if not exists reviews (uid integer, th text, box int not null, due text not null, primary key (uid, th));
create table if not exists activity (uid integer, day text, primary key (uid, day));
create table if not exists tgfiles (path text primary key, file_id text);
create table if not exists answers (uid integer, ts text, qt text, th text, right text, chosen text, ok int);
create table if not exists studytime (uid integer, day text, sec integer, primary key (uid, day));
create table if not exists feedback (id integer primary key, uid integer, ts text, kind text, text text,
    file_id text, ctx text, chat integer, mid integer)
"""
GOALS = {"trip": ("✈️", "поездку", "Поездки"), "live": ("🏠", "жизнь в Таиланде", "Жизнь"),
         "talk": ("💬", "общение с тайцами", "Общение")}  # цель новичка: эмодзи, «план под …», группа тем s4
IDLE = 300  # пауза между нажатиями дольше 5 минут не считается временем занятий


@dataclass
class Out:
    kind: str              # text | voice | edit | toast | fb (сообщение ученика владельцу, id в text)
    text: str = ""
    path: str = ""         # voice: файл озвучки
    buttons: list = None   # [[(подпись, данные), ...], ...]
    target: str = ""       # edit: "text" | "caption" | "markup"
    toast: str = ""
    menu: bool = False     # прикрепить нижнее меню к этому сообщению


def esc(s):
    return html.escape(s or "", quote=False)


def plural(n, one, few, many):
    n10, n100 = n % 10, n % 100
    return one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many


def mix_after_skip(order, skip):
    """Порядок для того, кто на проверке показал основы: пропущенные уроки первыми (они отмечены),
    дальше фраза и тон по очереди, чтобы не было недели одних тонов; темы s4 на месте."""
    base = lambda x: x[:-2] if x.endswith("_2") else x
    groups = {}
    for x in order:
        groups.setdefault(base(x), []).append(x)
    s12 = [b for b in groups if b.startswith(("s1_", "s2_"))]
    left = lambda pre: [groups[b] for b in s12 if b.startswith(pre) and b not in skip]
    s1, s2 = left("s1_"), left("s2_")
    mix = [g for k in range(max(len(s1), len(s2))) for g in s2[k:k + 1] + s1[k:k + 1]]
    return [x for b in s12 if b in skip for x in groups[b]] + [x for g in mix for x in g] + \
        [x for x in order if base(x) not in s12]


def dur(sec):
    h, m = divmod(round(sec / 60), 60)
    return " ".join(([f"{h} ч"] if h else []) + ([f"{m} мин"] if m else [])) or "меньше минуты"


def hid(s):
    return hashlib.sha1(s.encode()).hexdigest()[:8]


def alts(v):
    """Смыслы варианта для сравнения: «Здесь» и «здесь (буду есть тут)» одно, у «можно; могу» два смысла."""
    v = re.sub(r"\s*\([^)]*\)", "", v.lower())
    return {re.sub(r"[.,!?«»]", "", a).strip() for a in re.split(r"[;/]", v)} - {""}


def shape(v):
    """Вид варианта на кнопке: сколько слов (у транскрипции слогов), с заглавной ли, вопрос ли, есть ли ครับ/ค่ะ."""
    core = re.sub(r"\s*\([^)]*\)", "", v).strip()
    words = len([w for w in re.split(r"[\s\-/;,.!?]+", core) if w])
    return min(words, 5), core[:1].isupper(), core.endswith("?"), core.endswith(("khráp", "khâ", "khá"))


def unlike(a, b):
    """Насколько варианты различаются видом: 0-1 похожи, от 4 ответ выделяется среди неверных."""
    return abs(a[0] - b[0]) + 2 * sum(x != y for x, y in zip(a[1:], b[1:]))


def tone_label(t):
    return f"{TONE_MARK[t]} {TONE_ARROW[t]} {TONE_RU[t]}"


class Store:
    def __init__(self, path=None):
        self.db = sqlite3.connect(path or os.environ.get("THAI_DB") or f"{DATA}/bot.db")
        self.db.executescript(SCHEMA)

    def q(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def run(self, sql, args=()):
        self.db.execute(sql, args)
        self.db.commit()


def load_course(path):
    c = json.load(open(path, encoding="utf-8"))
    # короткий ключ кнопки 🐢 -> (текст озвучки, голос)
    sounds = {hid(it["say"] + it["voice"]): (it["say"], it["voice"]) for it in c["items"].values()}
    for L in c["lessons"].values():
        for x in L["letters"]:
            sounds[hid(x["say"] + "m")] = (x["say"], "m")
    return {"c": c, "items": c["items"], "lessons": c["lessons"], "order": c["order"], "abc": c.get("abc", []),
            "scenes": {p["id"]: p for p in c["scenes"]}, "by_hid": {hid(th): th for th in c["items"]}, "sounds": sounds}


class Engine:
    def __init__(self, course_path=f"{DATA}/course.json", store=None):
        if not os.path.exists(course_path):
            raise SystemExit(f"Нет файла {course_path}: сначала python fetch_data.py, потом python build.py (см. README)")
        self.st = store or Store()
        if OWNER_ID:  # явный владелец главнее записи в базе: роли подгоняем под него
            self.st.run("update users set role = case when uid=? then 'owner' else 'member' end", (OWNER_ID,))
        fem = course_path.replace(".json", "_f.json")
        self.variants = {"m": load_course(course_path)}
        self.variants["f"] = load_course(fem) if os.path.exists(fem) else self.variants["m"]
        self.__dict__.update(self.variants["m"])
        self.uid = None

    # ---------- ученик
    def user(self, uid):
        """Движок одного ученика: его прогресс и его вариант курса (мужская или женская речь)."""
        e = copy.copy(self)
        e.uid = uid
        u = e.profile()
        e.__dict__.update(self.variants.get(u and u["g"], self.variants["m"]))
        e.__dict__.pop("_mv", None)
        skip = set(e.get("skip") or []) if u else set()
        if any(x.startswith("s2_") for x in skip):  # основы пропущены: оставшиеся тоны дальше вперемешку с фразами
            e.order = mix_after_skip(e.order, skip)
        goal = e.get("goal") if u else None
        if goal in GOALS:  # темы под цель идут первыми среди тем, в поездке сначала сценки-места
            mine = lambda x: e.lessons[x]["title"].startswith(GOALS[goal][2] + ":")
            s4 = [x for x in e.order if x.startswith("s4_")]
            e.order = [x for x in e.order if not x.startswith("s4_")] + [x for x in s4 if mine(x)] + \
                [x for x in s4 if not mine(x)]
            if goal == "trip":
                e.scenes = dict(sorted(e.scenes.items(), key=lambda kv: kv[1]["kind"] != "place"))
        return e

    def add_user(self, uid, chat, tz=3):
        """Новый ученик. В день знакомства утреннее напоминание не шлём."""
        day = dt.datetime.now(dt.timezone(dt.timedelta(hours=tz))).date().isoformat()
        self.st.run("insert or ignore into users(uid, chat, created, tz, reminded, role) values(?,?,?,?,?,?)",
                    (uid, chat, day, tz, day, self.role_for(uid)))
        e = self.user(uid)
        e.set("menu_v", MENU_V)  # новичку меню и так придёт свежее
        return e

    def owner(self):
        """Владелец: при заданном OWNER_ID он (как только написал боту), иначе первый, кто написал боту."""
        if OWNER_ID:
            return OWNER_ID if self.st.q("select 1 from users where uid=?", (OWNER_ID,)) else None
        r = self.st.q("select uid from users where role='owner'")
        return r[0][0] if r else None

    def role_for(self, uid):
        """Роль нового ученика: владелец это OWNER_ID, а без него самый первый ученик."""
        return "owner" if (uid == OWNER_ID if OWNER_ID else self.owner() is None) else "member"

    def touch(self):
        """Время занятий: паузы между нажатиями до IDLE складываются по дням ученика."""
        now, last = dt.datetime.utcnow().replace(microsecond=0), self.get("seen")
        self.set("seen", now.isoformat())
        gap = (now - dt.datetime.fromisoformat(last)).total_seconds() if last else 0
        if 0 < gap <= IDLE:
            self.st.run("insert into studytime values(?,?,?) on conflict(uid, day) do update set sec = sec + excluded.sec",
                        (self.uid, self.today().isoformat(), int(gap)))

    def profile(self):
        r = self.st.q("select chat, g, tz, remind, reminded from users where uid=?", (self.uid,))
        return dict(zip(("chat", "g", "tz", "remind", "reminded"), r[0])) if r else None

    def get(self, key, default=None):
        r = self.st.q("select value from kv where uid=? and key=?", (self.uid, key))
        return json.loads(r[0][0]) if r else default

    def set(self, key, value):
        self.st.run("insert into kv values(?,?,?) on conflict(uid, key) do update set value=excluded.value",
                    (self.uid, key, json.dumps(value, ensure_ascii=False)))

    def now(self):
        u = self.profile()
        return dt.datetime.now(dt.timezone(dt.timedelta(hours=u["tz"] if u else 3)))

    def today(self):
        return self.now().date()

    def zone(self):
        tz = self.profile()["tz"]
        return f"{ZONE_RU[tz]} (UTC{tz:+d})" if tz in ZONE_RU else f"по UTC{tz:+d}"

    def ask_gender(self, first=False):
        txt = ("Привет! Это курс тайского с нуля. Сначала один вопрос.\n\n" if first else "") + \
            ("В тайском мужчины и женщины говорят немного по-разному. Вежливое окончание фразы у мужчин "
             "ครับ (khráp), у женщин ค่ะ (khâ). «Я» тоже разное: ผม (phǒm) и ฉัน (chǎn).\n\n"
             "Какую речь учим? Под неё подстроятся фразы и голос в уроках.")
        return [Out("text", txt, buttons=[[("👨 Мужскую: ครับ", "gv:m"), ("👩 Женскую: ค่ะ", "gv:f")]])]

    def set_gender(self, g):
        """Выбор речи. При смене повторение переезжает на фразы другого варианта, начатый урок сбрасывается."""
        old = self.profile()["g"]
        if g not in self.variants or g == old:
            return [Out("toast", toast="Уже выбрано")]
        self.st.run("update users set g=? where uid=?", (g, self.uid))
        e = self.user(self.uid)
        done = Out("edit", target="markup", buttons=None, toast="Выбрано")
        if not old:
            return [done] + e.ask_goal()
        twins = self.variants["f"]["c"].get("twins", {})
        twins = twins if g == "f" else {b: a for a, b in twins.items()}
        for th, box, due in self.st.q("select th, box, due from reviews where uid=?", (self.uid,)):
            if th in twins:
                self.st.run("delete from reviews where uid=? and th=?", (self.uid, th))
                self.st.run("insert or replace into reviews values(?,?,?,?)", (self.uid, twins[th], box, due))
        e.set("session", None)
        e.set("paused", None)
        return [done, Out("text", "Готово: в уроках и повторении теперь " +
                          ("ค่ะ (khâ), คะ (khá) и ฉัน (chǎn), женский голос." if g == "f" else "ครับ (khráp) и ผม (phǒm), мужской голос."), menu=True)]

    # ---------- знакомство новичка: цель, что уже знает, три фразы на слух, план и сразу первый урок
    def ask_goal(self):
        return [Out("text", "Для чего тебе тайский? Под это подстрою порядок тем и сценки.",
                    buttons=[[("✈️ Поездка", "ob:goal:trip")], [("🏠 Живу в Таиланде", "ob:goal:live")],
                             [("💬 Общаться с тайцами", "ob:goal:talk")]])]

    def on_goal(self, goal):
        if goal not in GOALS:
            return [Out("toast", toast="Кнопка устарела")]
        self.set("goal", goal)
        return [Out("edit", target="markup", buttons=None, toast="Запомнил"),
                Out("text", "Что уже знаешь по-тайски?",
                    buttons=[[("🌱 Ничего", "ob:lvl:0")], [("🗣 Пару фраз: здороваюсь, благодарю", "ob:lvl:1")],
                             [("💬 Объясняюсь немного", "ob:lvl:2")]])]

    def on_level(self, lvl):
        e = self.user(self.uid)  # порядок тем уже под цель
        return [Out("edit", target="markup", buttons=None)] + (e.start_check(1, lvl) if lvl in (1, 2) else e.plan())

    def check_lessons(self, stage):
        base = ["s1_01"] if stage == 1 else [f"s2_0{i}" for i in range(1, 7)]
        return [x for x in self.order if x in base or x[:-2] in base]  # вместе со вторыми частями

    def start_check(self, stage, lvl):
        """Три фразы на слух, выбрать перевод. Все верно: эти уроки пропускаем. Уроки тонов не пропускаем никогда."""
        pool = [th for lid in self.check_lessons(stage) for th in self.lessons[lid]["items"] if th not in ("ครับ", "ค่ะ")]
        near = {th: 1 for lid in self.check_lessons(2) for th in self.lessons[lid]["items"]} | {th: 0 for th in pool}
        qs = [self.q_meaning(th, near) for th in random.sample(pool, 3)]
        head = ("Проверим за три фразы: слушай и выбирай перевод. Все три верно, первый урок пропустим."
                if stage == 1 else "✅ Эти знаешь. Ещё три посложнее: все верно, пропустим основы до кафе, "
                                   "а уроки тонов останутся.")
        return [Out("text", head)] + self.begin("check", qs + [{"t": "end"}], stage=stage, lvl=lvl)

    def check_next(self, s):
        self.save(None)
        ok = s["total"] and s["score"] == s["total"]
        if ok:
            self.set("skip", sorted(set(self.get("skip") or []) | set(self.check_lessons(s["stage"]))))
        if ok and s["stage"] == 1 and s["lvl"] == 2:
            return self.start_check(2, 2)
        note = ("✅ Все верно: основы до кафе пропускаем, уроки тонов оставляем." if ok and s["stage"] == 2 else
                "✅ Все верно: «Первые слова» пропускаем." if ok else
                "Начнём с первого урока: там как раз эти фразы." if s["stage"] == 1 else
                "«Первые слова» пропускаем, остальное по порядку.")
        return [Out("text", note)] + self.user(self.uid).plan()  # порядок заново: с учётом пропусков

    def plan(self):
        """План новичка и сразу первый урок: первая неделя, темы под цель, сценки, напоминание."""
        goal, u = self.get("goal"), self.profile()
        em, what, group = GOALS.get(goal, ("📚", "старт", ""))
        d = self.done() | set(self.get("skip") or [])
        week = list(dict.fromkeys(self.lessons[x]["title"].split(", часть")[0] for x in self.order if x not in d))[:4]
        topics = list(dict.fromkeys(self.lessons[x]["title"].split(":")[1].split(", часть")[0].strip()
                                    for x in self.order if group and self.lessons[x]["title"].startswith(group + ":")))
        scenes = [P["title"] for P in list(self.scenes.values())[:3]]
        when = (f"Напоминание каждое утро в {u['remind']} {self.zone()}: /time меняет время, /tz часовой пояс."
                if u["remind"] != "off" else "Напоминание выключено, включить: /time 09:00.")
        txt = (f"{em} <b>План под {what}</b>\n\n"
               "▶ Урок 5-10 минут: голос, повторяешь вслух, потом вопросы кнопками. Новых слов до шести, "
               "в начале урока разминка из повторения.\n"
               f"📅 Первая неделя, если по уроку в день: {', '.join(week)}.\n"
               + (f"{em} Потом темы под {what}: {', '.join(topics)}.\n" if topics else "")
               + f"💬 Разговор: сценки к месту, например {', '.join(scenes)}.\n\n{when} Список уроков: /lessons.")
        return [Out("text", txt, menu=True)] + self.start_lesson()

    # ---------- справки
    def done(self):
        return {r[0] for r in self.st.q("select id from lessons_done where uid=?", (self.uid,))}

    def next_lesson(self):
        d = self.done() | set(self.get("skip") or [])  # уроки, которые новичок показал на проверке
        return next((x for x in self.order if x not in d), None)

    def next_abc(self):
        d = self.done()
        return next((x for x in self.abc if x not in d), None)

    def no(self, lid):
        """Номер в основном пути; алфавит по желанию считается отдельно: «урок алфавита 2»."""
        return self.order.index(lid) + 1 if lid in self.order else f"алфавита {self.abc.index(lid) + 1}"

    def lesson_pool(self, L):
        """Все тайские элементы урока, которые уходят в повторение."""
        pool = [x for p in L["pairs"] for x in p] + L["items"] + L["read"]
        pool += [d["item"] for d in L["dialog"] if d["who"] == "me"]
        return [th for th in dict.fromkeys(pool) if self.items[th]["ru"]]

    def known(self):
        d = self.done()
        return list(dict.fromkeys(th for lid in self.order + self.abc if lid in d for th in self.lesson_pool(self.lessons[lid])))

    def audio(self, th, slow=False):
        it = self.items[th]
        return tts.path_for(it["say"], tts.MALE if it["voice"] == "m" else tts.FEMALE, tts.SLOW if slow else tts.NORMAL)

    def vpath(self, th, var):
        """Слово голосом одного из четырёх «дикторов»; нет такой записи, берём основную."""
        base = tts.path_for(self.items[th]["say"], tts.FEMALE if var[0] == "f" else tts.MALE, tts.NORMAL)
        p = tts.variant_path(base, "hi" if var == "m+" else "lo") if len(var) > 1 else base
        return p if os.path.exists(p) else self.audio(th)

    def voices(self, th):
        """Доступные «дикторы» слова. Пока точность на слух ниже 80%, только основной голос:
        новичку смена голоса мешает, ухо цепляется за высоту голоса, а не за мелодию тона
        («одинаковый» при разных голосах легко перепутать)."""
        if not self.multi_voice():
            return [self.items[th]["voice"]]
        return [v for v in VOICES if self.vpath(th, v) != self.audio(th) or v == self.items[th]["voice"]] or ["m"]

    def multi_voice(self):
        if not hasattr(self, "_mv"):
            rows = self.st.q("select ok from answers where uid=? and qt in ('tone','same') order by ts desc limit 20",
                             (self.uid,))
            self._mv = len(rows) >= 20 and sum(r[0] for r in rows) / len(rows) >= 0.8
        return self._mv

    def slow_key(self, th):
        it = self.items[th]
        return f"s:{hid(it['say'] + it['voice'])}"

    def meaning(self, th):
        it = self.items[th]
        return f"{it['pic']} {it['ru']}" if it.get("pic") else it["ru"]

    def how(self, th):
        """«Как сказать: «поели?»» без второго вопросительного знака."""
        m = self.meaning(th)
        return f"Как сказать: «{esc(m)}»" + ("" if m.endswith(("?", "!")) else "?")

    def caption(self, th, ru=None):
        it = self.items[th]
        s = f"<b>{esc(it['th'])}</b>\n{esc(it['tr'])}"
        if it["tone"]:
            s += f"  {TONE_ARROW[it['tone']]}"
        s += f"\n{esc(ru or self.meaning(th))}"
        if it.get("note"):
            s += f"\n<i>{esc(it['note'])}</i>"
        if it.get("mn"):
            s += f"\n<i>Запомнить: {esc(it['mn'])}</i>"
        return s

    def line(self, th):
        it = self.items[th]
        m = self.meaning(th)
        return f"<b>{esc(it['th'])}</b>  {esc(it['tr'])}" + (f"  {esc(m)}" if m else "")

    # ---------- сессия
    def session(self):
        return self.get("session")

    def save(self, s):
        self.set("session", s)

    def unfinished_lesson(self):
        s = self.session()
        return s if s and s["mode"] == "lesson" and s["i"] < len(s["steps"]) - 1 else None

    def begin(self, mode, steps, **extra):
        if mode != "lesson" and self.unfinished_lesson():
            self.set("paused", self.session())
        if mode == "lesson":
            self.set("paused", None)
        s = {"mode": mode, "steps": steps, "i": 0, "score": 0, "total": 0, "nonce": random.randint(1, 10 ** 6),
             "at": time.time(), **extra}
        self.save(s)
        return self.render()

    def advance(self, s):
        s["i"] += 1
        s["nonce"] += 1
        s["at"] = time.time()
        self.save(s)
        return self.render()

    def stale(self, s, toast):
        """Нажатие на прошлый шаг. Если шаг сменился давно, новое сообщение, видимо, не дошло (сбой связи
        с Телеграмом: оценка засчиталась, а следующая карточка потерялась): присылаем текущий шаг снова."""
        if not s or s["steps"][min(s["i"], len(s["steps"]) - 1)]["t"] == "end" or time.time() - s.get("at", 0) < RESEND_AFTER:
            return [Out("toast", toast=toast)]
        s["at"] = time.time()
        self.save(s)
        return [Out("toast", toast=toast)] + self.render()

    # ---------- урок
    def start_lesson(self, lid=None):
        lid = lid or self.next_lesson()
        if not lid:
            return [Out("text", "Все уроки курса пройдены. Повторение и тренажёры остаются с тобой каждый день.")]
        L = self.lessons[lid]
        tone_mode = "tone" in L["quiz"]
        own, other, shown = self.lesson_pool(L), self.sibling(lid), [x for p in L["pairs"] for x in p]
        near = self.ranks(own, shown, other)
        warm = self.due()[:WARMUP]
        steps = [{"t": "intro", "warm": len(warm)}] + [self.card(th, rev=True) for th in warm]
        # пары: слушаем оба слова, «одинаковый тон или разный?», потом «какое из двух?»
        for a, b in L["pairs"]:
            steps += [{"t": "item", "th": a}, {"t": "item", "th": b}]
            ta, tb = self.items[a]["tone"], self.items[b]["tone"]
            if ta and tb and ta != tb:
                pair_qs = [self.q_same(a, b), self.q_same(random.choice([a, b]), None, near)]
                random.shuffle(pair_qs)
                steps += pair_qs
            steps.append(self.q_pair(a, b))
        # новые слова и буквы порциями по 3, сразу после порции вопрос по каждому
        units = [("item", th) for th in L["items"]] + [("letter", i) for i in range(len(L["letters"]))]
        for k in range(0, len(units), CHUNK):
            chunk = units[k:k + CHUNK]
            steps += [{"t": "item", "th": x} if u == "item" else {"t": "letter", "i": x} for u, x in chunk]
            shown += [x for u, x in chunk if u == "item"]
            near = self.ranks(own, shown, other)
            recall = [self.q_recall(u, x, L, tone_mode, near) for u, x in chunk]
            random.shuffle(recall)
            steps += [q for q in recall if q]
        steps += [{"t": "item", "th": x} for x in L["read"]]
        steps += [{"t": "dlg", "who": d["who"], "th": d["item"], "ru": d["ru"]} for d in L["dialog"]]
        steps += self.make_quiz(L, self.ranks(own, own, other))
        steps.append({"t": "end"})
        return self.begin("lesson", steps, lesson=lid, again=[])

    def continue_or_next(self):
        s = self.unfinished_lesson() or self.get("paused")
        if s:
            self.save(s)
            self.set("paused", None)
            return [Out("text", f"Продолжаем урок {self.no(s['lesson'])}.")] + self.render()
        return self.start_lesson()

    # ---------- вопросы
    def distractors(self, correct, value, near, n=3):
        """Неверные варианты, чтобы ответ не угадывался по теме или виду.
        near: слово -> ступень (self.ranks), сначала слова этого урока. Внутри ступени сначала похожие видом:
        слово к слову, фраза к фразе, вопрос к вопросу. Тот же смысл другими словами не берём."""
        want, used, out = shape(correct), alts(correct), []
        key = lambda th: (near.get(th, 4), min(2, unlike(shape(value(th)), want) // 2), random.random())
        for th in sorted((x for x in self.items if self.items[x]["ru"] and value(x)), key=key):
            a = alts(value(th))
            if a & used:
                continue
            used |= a
            out.append(value(th))
            if len(out) == n:
                break
        return out

    def sibling(self, lid):
        """Слова другой части того же урока: та же тема."""
        other = lid[:-2] if lid.endswith("_2") else lid + "_2"
        return self.lesson_pool(self.lessons[other]) if other in self.lessons else []

    def ranks(self, own, shown, other=()):
        """Ступени для distractors: 0 слова этого урока или сценки, уже показанные; 1 пройденная часть того же
        урока; 2 слова темы, ещё не показанные; 3 пройденное раньше. Остальные слова курса последними."""
        known = self.known()
        seen = set(known) | set(shown)
        return {th: 3 for th in known} | {th: 1 if th in seen else 2 for th in other} | \
            {th: 0 if th in seen else 2 for th in own}

    def options(self, correct, wrong):
        opts = [correct] + wrong
        random.shuffle(opts)
        return opts, opts.index(correct)

    def q_meaning(self, th, near):
        o, a = self.options(self.items[th]["ru"], self.distractors(self.items[th]["ru"], lambda x: self.items[x]["ru"], near))
        return {"t": "q", "qt": "meaning", "th": th, "opts": o, "ans": a}

    def q_choose(self, th, near):
        o, a = self.options(self.items[th]["tr"], self.distractors(self.items[th]["tr"], lambda x: self.items[x]["tr"], near))
        return {"t": "q", "qt": "choose", "th": th, "opts": o, "ans": a}

    def q_pic(self, th, near):
        pic = self.items[th].get("pic")
        wrong = self.distractors(pic, lambda x: self.items[x].get("pic"), near)
        if not pic or len(wrong) < 3:
            return None
        o, a = self.options(pic, wrong)
        return {"t": "q", "qt": "pic", "th": th, "opts": o, "ans": a}

    def q_tone(self, th, allowed=TONES):
        t = self.items[th]["tone"]
        opts = [x for x in TONES if x in allowed or x == t]
        return {"t": "q", "qt": "tone", "th": th, "opts": opts, "ans": opts.index(t), "v": random.choice(self.voices(th))}

    def q_same(self, a, b, pool=()):
        """Два слова подряд: тон одинаковый или разный? b=None: пример «одинакового»,
        другое слово того же тона из pool (с одним голосом тот же файл дважды ничему не учит)."""
        if b is None:
            mates = [w for w in pool if w != a and self.items[w]["tone"] == self.items[a]["tone"]]
            b = random.choice(mates) if mates and not self.multi_voice() else a
        va, vb = random.choice(self.voices(a)), random.choice(self.voices(b))
        if a == b and len(self.voices(a)) > 1:
            vb = random.choice([v for v in self.voices(a) if v != va])
        same = self.items[a]["tone"] == self.items[b]["tone"]
        return {"t": "q", "qt": "same", "a": a, "b": b, "va": va, "vb": vb,
                "opts": ["одинаковый", "разный"], "ans": 0 if same else 1}

    def q_pair(self, a, b):
        th = random.choice([a, b])
        return {"t": "q", "qt": "pair", "th": th, "opts": [a, b], "ans": [a, b].index(th)}

    def q_letter(self, L, i):
        sounds = list(dict.fromkeys(x["sound"] for lid in self.abc for x in self.lessons[lid]["letters"]))
        x = L["letters"][i]
        o, a = self.options(x["sound"], random.sample([s for s in sounds if s != x["sound"]], 3))
        return {"t": "q", "qt": "letter", "i": i, "opts": o, "ans": a}

    def q_read(self, th):
        tr = self.items[th]["tr"]
        o, a = self.options(tr, random.sample(thaidict.tone_variants(tr), 3))
        return {"t": "q", "qt": "read", "th": th, "opts": o, "ans": a}

    def q_recall(self, unit, x, L, tone_mode, near):
        if unit == "letter":
            return self.q_letter(L, x)
        if not self.items[x]["ru"]:
            return None
        if tone_mode and self.items[x]["tone"]:
            return self.q_tone(x, L["tones"])
        return self.q_pic(x, near) or self.q_meaning(x, near)

    def same_questions(self, words, n):
        """Пары слов для «одинаковый или разный?»: поровну одинаковых и разных тонов, если получится."""
        words = [w for w in words if self.items[w]["tone"]]
        pairs = [(a, b) for a in words for b in words if a < b]
        same = [p for p in pairs if self.items[p[0]]["tone"] == self.items[p[1]]["tone"]]
        diff = [p for p in pairs if p not in same]
        pick = random.sample(diff, min(len(diff), (n + 1) // 2)) + random.sample(same, min(len(same), n // 2))
        return [self.q_same(a, b) for a, b in pick]

    def make_quiz(self, L, near):
        pool = self.lesson_pool(L)
        if L["kind"] == "review_tones":
            tp = [th for th in self.known() if self.items[th]["tone"]]
            qs = [self.q_tone(th) for th in random.sample(tp, min(10, len(tp)))] + self.same_questions(tp, 3)
            random.shuffle(qs)
            return qs
        qs = []
        for qt in L["quiz"]:
            if qt == "meaning":
                qs += [(self.q_pic(th, near) if random.random() < 0.5 else None) or self.q_meaning(th, near) for th in pool]
            elif qt == "choose":
                qs += [self.q_choose(th, near) for th in pool]
            elif qt == "tone":
                qs += [self.q_tone(th, L["tones"]) for th in pool if self.items[th]["tone"]]
                qs += self.same_questions(pool, 3)
            elif qt == "pair":
                qs += [self.q_pair(a, b) for a, b in L["pairs"]]
            elif qt == "letter":
                qs += [self.q_letter(L, i) for i in range(len(L["letters"]))]
            elif qt == "read":
                qs += [self.q_read(th) for th in L["read"]]
        random.shuffle(qs)
        return qs[:QUIZ_LIMIT]

    # ---------- отрисовка текущего шага
    def render(self):
        s = self.session()
        step = s["steps"][s["i"]]
        n = s["nonce"]
        nxt = ("Дальше ▶", f"n:{n}")
        t = step["t"]
        if t == "intro" and s["mode"] == "scene":
            P = self.scenes[s["scene"]]
            k = len(P["seq"])
            return [Out("text", f"<b>{esc(P['title'])}</b>\n\n" + (f"{esc(P['intro'])}\n\n" if P["intro"] else "")
                        + f"<i>{k} {plural(k, 'фраза', 'фразы', 'фраз')}. Каждую повтори вслух 2-3 раза вслед за голосом, "
                          "копируй мелодию целиком.</i>", buttons=[[("Начать ▶", f"n:{n}")]])]
        if t == "intro":
            L = self.lessons[s["lesson"]]
            k = step.get("warm")
            warm = f"\n\n<i>Сначала разминка: {k} {plural(k, 'слово', 'слова', 'слов')} из повторения, потом новое.</i>" if k else ""
            text = f"<b>Урок {self.no(L['id'])}. {esc(L['title'])}</b>\n\n{esc(L['intro'])}{warm}"
            if L.get("intro_voice") and L["items"]:  # урок 1: первым делом звук, а не теория
                th = L["items"][0]
                return [Out("voice", text, self.audio(th), [[("🐢 Медленно", self.slow_key(th)), ("Начать ▶", f"n:{n}")]])]
            return [Out("text", text, buttons=[[("Начать ▶", f"n:{n}")]])]
        if t == "item":
            head = {"me": "🗣 Твоя фраза:\n", "t": "👂 Тебе скажут:\n"}.get(step.get("who"), "")
            return [Out("voice", head + self.caption(step["th"]), self.audio(step["th"]),
                        [[("🐢 Медленно", self.slow_key(step["th"])), nxt]])]
        if t == "letter":
            x = self.lessons[s["lesson"]]["letters"][step["i"]]
            txt = (f"<b>{esc(x['ch'])}</b>   {esc(x['tr'])}   «{esc(x['name_ru'])}»\n"
                   f"Звук в начале слога: {esc(x['sound'] or 'нет')} ({esc(x['sound_ru'])})\n"
                   + (f"В конце слога: {esc(x['final'])}\n" if x["final"] else "")
                   + f"Класс: {CLS_RU[x['cls']]}" + (f"\n<i>{esc(x['note'])}</i>" if x["note"] else ""))
            return [Out("voice", txt, tts.path_for(x["say"], tts.MALE),
                        [[("🐢 Медленно", f"s:{hid(x['say'] + 'm')}"), nxt]])]
        if t == "dlg":
            if step["who"] == "t":
                return [Out("voice", "🇹🇭 " + self.caption(step["th"], step["ru"]), self.audio(step["th"]),
                            [[("🐢 Медленно", self.slow_key(step["th"])), nxt]])]
            return [Out("text", f"Твоя реплика: «{esc(step['ru'])}»\nСкажи вслух по-тайски, потом проверь себя.",
                        buttons=[[("Показать ответ", f"r:{n}")]])]
        if t == "q":
            return [self.render_q(step, n)]
        if t == "card":
            it = self.items[step["th"]]
            if step["dir"] == "ru":
                return [Out("text", f"{self.how(step['th'])}\nСкажи вслух, потом проверь.",
                            buttons=[[("Показать ответ", f"r:{n}")]])]
            return [Out("voice", "Что это значит? Вспомни, потом проверь.", self.audio(step["th"]),
                        [[("Показать ответ", f"r:{n}")]])]
        if t == "end":
            return self.menu_news() + self.finish(s)
        raise ValueError(t)

    def render_q(self, step, n):
        qt = step["qt"]
        rows = lambda labels: [[(lab, f"a:{n}:{k}")] for k, lab in enumerate(labels)]
        if qt == "meaning":
            return Out("voice", "Что это значит?", self.audio(step["th"]),
                       rows(step["opts"]) + [[("🐢 Медленно", self.slow_key(step["th"]))]])
        if qt == "pic":
            btn = [[(step["opts"][0], f"a:{n}:0"), (step["opts"][1], f"a:{n}:1")],
                   [(step["opts"][2], f"a:{n}:2"), (step["opts"][3], f"a:{n}:3")]]
            return Out("voice", "Что это? Послушай и выбери картинку.", self.audio(step["th"]),
                       btn + [[("🐢 Медленно", self.slow_key(step["th"]))]])
        if qt == "choose":
            return Out("text", self.how(step["th"]), buttons=rows(step["opts"]))
        if qt == "tone":
            return Out("voice", "Какой тон? Веди рукой по мелодии, пока слушаешь.", self.vpath(step["th"], step.get("v", "m")),
                       rows([tone_label(x) for x in step["opts"]])
                       + [[("🐢 Медленно", self.slow_key(step["th"])), ("🎧 Эталоны", "anc")]])
        if qt == "same":
            return Out("voice", "Тон одинаковый или разный?\n<i>Два слова подряд, голоса могут отличаться: слушай мелодию.</i>",
                       tts.concat([self.vpath(step["a"], step["va"]), self.vpath(step["b"], step["vb"])]),
                       [[(step["opts"][0], f"a:{n}:0"), (step["opts"][1], f"a:{n}:1")]])
        if qt == "pair":
            labels = [f"{self.items[x]['tr']}  {self.meaning(x)}" for x in step["opts"]]
            return Out("voice", "Какое из двух слов?", self.audio(step["th"]),
                       rows(labels) + [[("🐢 Медленно", self.slow_key(step["th"]))]])
        if qt == "letter":
            x = self.lessons[self.session()["lesson"]]["letters"][step["i"]]
            return Out("text", f"<b>{esc(x['ch'])}</b>\nКакой звук даёт буква в начале слога?",
                       buttons=rows([o or "без звука" for o in step["opts"]]))
        if qt == "read":
            return Out("text", f"Прочитай: <b>{esc(step['th'])}</b>\nВыбери верную транскрипцию.", buttons=rows(step["opts"]))
        raise ValueError(qt)

    # ---------- нажатия
    def on_next(self, nonce, is_voice):
        s = self.session()
        if not s or s["nonce"] != nonce:
            return self.stale(s, "Этот шаг уже пройден")
        step = s["steps"][s["i"]]
        keep = [[("🐢 Медленно", self.slow_key(step["th"]))]] if step.get("th") and is_voice else None
        return [Out("edit", target="markup", buttons=keep)] + self.advance(s)

    def feedback(self, step, ok, k):
        """Текст правки вопроса после ответа и кнопки, которые на нём остаются."""
        qt = step["qt"]
        if qt == "letter":
            x = self.lessons[self.session()["lesson"]]["letters"][step["i"]]
            right = f"<b>{esc(x['ch'])}</b> даёт {esc(x['sound'] or 'без звука')} ({esc(x['sound_ru'])})"
            return f"<b>{esc(x['ch'])}</b>", right, "text", None
        if qt == "same":
            a, b = step["a"], step["b"]
            verdict = "одинаковый" if step["ans"] == 0 else "разный"
            right = (f"тон {verdict}:\n{self.line(a)}  {TONE_ARROW[self.items[a]['tone']]} {TONE_RU[self.items[a]['tone']]}"
                     + ("" if a == b else f"\n{self.line(b)}  {TONE_ARROW[self.items[b]['tone']]} {TONE_RU[self.items[b]['tone']]}"))
            return "Тон одинаковый или разный?", right, "caption", None
        th = step["th"]
        it = self.items[th]
        right = self.line(th)
        keep = [[("🐢 Медленно", self.slow_key(th))]]
        if qt == "tone":
            right += f"\nтон: {tone_label(it['tone'])}"
            if not ok:
                keep = [[("🎧 Сравнить с эталоном", f"cmp:{hid(th)}:{step['opts'][k]}"), ("🐢 Медленно", self.slow_key(th))]]
        head = {"meaning": "Что это значит?", "tone": "Какой тон?", "pair": "Какое из двух слов?", "pic": "Что это?",
                "choose": self.how(th), "read": f"Прочитай: <b>{esc(it['th'])}</b>"}[qt]
        target = "caption" if qt in ("meaning", "tone", "pair", "pic") else "text"
        return head, right, target, keep if target == "caption" else None

    def on_answer(self, nonce, k):
        s = self.session()
        if not s or s["nonce"] != nonce:
            return self.stale(s, "Этот вопрос уже позади")
        step = s["steps"][s["i"]]
        ok = k == step["ans"]
        s["total"] += 1
        s["score"] += ok
        self.__dict__.pop("_mv", None)
        if step["qt"] == "tone":
            self.log_answer("tone", step["th"], step["opts"][step["ans"]], step["opts"][k], ok)
        elif step["qt"] == "same":
            self.log_answer("same", f"{step['a']}|{step['b']}", step["opts"][step["ans"]], step["opts"][k], ok)
        head, right, target, keep = self.feedback(step, ok, k)
        fb = f"{head}\n\n✅ {right}" if ok else f"{head}\n\n❌ Правильно: {right}"
        edit = Out("edit", fb, target=target, buttons=keep, toast="✅ Верно" if ok else "❌ Неверно")
        hear = [Out("voice", "", self.audio(step["th"]))] if step["qt"] in ("read", "choose") else []
        return [edit] + hear + self.advance(s)

    def log_answer(self, qt, th, right, chosen, ok):
        self.st.run("insert into answers values(?,?,?,?,?,?,?)",
                    (self.uid, dt.datetime.now(TZ).isoformat(timespec="seconds"), qt, th, right, chosen, int(ok)))

    def on_reveal(self, nonce):
        s = self.session()
        if not s or s["nonce"] != nonce:
            return self.stale(s, "Уже открыто")
        step = s["steps"][s["i"]]
        th, n = step["th"], s["nonce"]
        if step["t"] == "dlg":
            return [Out("edit", target="markup", buttons=None),
                    Out("voice", self.caption(th, step["ru"]), self.audio(th),
                        [[("🐢 Медленно", self.slow_key(th)), ("Дальше ▶", f"n:{n}")]])]
        grades = [[("Не помню", f"g:{n}:0"), ("С трудом", f"g:{n}:1"), ("Помню", f"g:{n}:2")]]
        if step["dir"] == "ru":
            return [Out("edit", target="markup", buttons=None),
                    Out("voice", self.caption(th), self.audio(th), grades + [[("🐢 Медленно", self.slow_key(th))]])]
        return [Out("edit", self.caption(th), target="caption", buttons=grades + [[("🐢 Медленно", self.slow_key(th))]])]

    def on_slow(self, key):
        if key not in self.sounds:
            return [Out("toast", toast="Звук не найден")]
        say, voice = self.sounds[key]
        return [Out("voice", "🐢", tts.path_for(say, tts.MALE if voice == "m" else tts.FEMALE, tts.SLOW))]

    def anchors(self):
        rows = "\n".join(f"{TONE_ARROW[t]} {TONE_RU[t]}: {self.line(ANCHORS[t])}" for t in TONES)
        return [Out("voice", f"<b>Эталоны тонов</b>, по порядку:\n{rows}",
                    tts.concat([self.audio(ANCHORS[t]) for t in TONES]))]

    def compare(self, key, chosen):
        th = self.by_hid.get(key)
        if not th or chosen not in TONES or not self.items[th]["tone"]:
            return [Out("toast", toast="Нечего сравнивать")]
        t = self.items[th]["tone"]
        a, c = ANCHORS[t], ANCHORS[chosen]
        txt = (f"{self.line(th)}\nЭто {TONE_RU[t]} {TONE_ARROW[t]}, как {self.line(a)}.\n"
               f"Твой ответ: {TONE_RU[chosen]} {TONE_ARROW[chosen]}, как {self.line(c)}.\n"
               "<i>Порядок: слово, эталон его тона, снова слово, эталон твоего ответа.</i>")
        return [Out("voice", txt, tts.concat([self.audio(th), self.audio(a), self.audio(th), self.audio(c)]))]

    # ---------- конец урока
    def finish(self, s):
        if s["mode"] == "check":
            return self.check_next(s)
        if s["mode"] == "review":
            return self.finish_review(s)
        if s["mode"] == "scene":
            return self.finish_scene(s)
        if s["mode"] == "trainer":
            self.mark_activity()
            self.save(None)
            return [Out("text", f"Тренажёр тонов: {s['score']} из {s['total']} верно.\n{self.tone_stats_line()}",
                        buttons=[[("🎵 Ещё раз", "tr"), ("▶ Урок", "ls")]])]
        L = self.lessons[s["lesson"]]
        self.st.run("insert or replace into lessons_done values(?,?,?,?,?)",
                    (self.uid, L["id"], dt.datetime.now(TZ).isoformat(timespec="minutes"), s["score"], s["total"]))
        tomorrow = (self.today() + dt.timedelta(days=1)).isoformat()
        for th in self.lesson_pool(L):
            self.st.run("insert or ignore into reviews values(?,?,?,?)", (self.uid, th, 1, tomorrow))
        self.mark_activity()
        self.save(None)
        txt = f"<b>Урок {self.no(L['id'])} пройден.</b>"
        weak = s["total"] and s["score"] / s["total"] < REPEAT_BELOW
        if s["total"]:
            txt += f" Верно {s['score']} из {s['total']}."
        if weak:
            txt += "\nЭто меньше 80%: лучше пройти урок ещё раз, пока всё свежее. Второй раз всегда легче."
        if self.lesson_pool(L):
            txt += "\nНовые слова ушли в повторение, первое уже завтра."
        due = len(self.due())
        if due:
            txt += f"\nЖдут повторения: {due}, за раз до {REVIEW_LIMIT}."
        m = L.get("mission")
        if m:
            txt += f"\n\n<b>Задание на сегодня:</b> {esc(m['text'])}"
            if m["item"]:
                txt += f"\n{self.line(m['item'])}"
        btn = [[("🔁 Пройти ещё раз", f"rl:{L['id']}")]] if weak else []
        na = self.next_abc() if L["id"] in self.abc else None
        if na:
            btn.append([("🔤 Дальше алфавит", f"rl:{na}")])
        row = ([("🔁 Повторение", "rv")] if due else []) + ([("▶ Следующий урок", "ls")] if self.next_lesson() else [])
        if row:
            btn.append(row)
        return [Out("text", txt, buttons=btn or None)]

    def mark_activity(self):
        self.st.run("insert or ignore into activity values(?,?)", (self.uid, self.today().isoformat()))

    def streak(self):
        days = {r[0] for r in self.st.q("select day from activity where uid=?", (self.uid,))}
        d = self.today()
        if d.isoformat() not in days:
            d -= dt.timedelta(days=1)
        n = 0
        while d.isoformat() in days:
            n += 1
            d -= dt.timedelta(days=1)
        return n

    # ---------- повторение
    def due(self):
        return [r[0] for r in self.st.q("select th from reviews where uid=? and due<=? order by due, box",
                                        (self.uid, self.today().isoformat())) if r[0] in self.items]

    def start_review(self):
        d = self.due()[:REVIEW_LIMIT]
        if not d:
            nxt = self.st.q("select min(due) from reviews where uid=?", (self.uid,))[0][0]
            tail = f" Следующее повторение: {dt.date.fromisoformat(nxt).strftime('%d.%m')}." if nxt else ""
            return [Out("text", "На сегодня повторять нечего." + tail, buttons=[[("▶ Урок", "ls")]])]
        steps = [self.card(th) for th in d]
        steps.append({"t": "end"})
        return [Out("text", f"Повторение: {len(d)} {plural(len(d), 'карточка', 'карточки', 'карточек')}. "
                            "Вспоминай вслух, потом открывай ответ и честно оценивай.")] + \
            self.begin("review", steps, again=[])

    def card(self, th, **extra):
        return {"t": "card", "th": th, "dir": "audio" if self.items[th].get("hear") else random.choice(["ru", "audio"]), **extra}

    def on_grade(self, nonce, g):
        s = self.session()
        if not s or s["nonce"] != nonce:
            return self.stale(s, "Уже оценено")
        step = s["steps"][s["i"]]
        th = step["th"]
        if s["mode"] == "review" or step.get("rev"):  # в уроке-сценке фраза только знакомится, в повторение уходит в конце
            box = self.st.q("select box from reviews where uid=? and th=?", (self.uid, th))
            box = box[0][0] if box else 0
            box = 0 if g == 0 else box if g == 1 else min(MAX_BOX, box + 1)
            self.st.run("insert or replace into reviews values(?,?,?,?)",
                        (self.uid, th, box, (self.today() + dt.timedelta(days=INTERVALS[box])).isoformat()))
        if not step.get("rev"):  # разминка в начале урока не входит в его счёт
            s["total"] += 1
            s["score"] += g > 0
        if g == 0 and th not in s["again"]:
            s["again"].append(th)
            s["steps"].insert(len(s["steps"]) - 1, {"t": "card", "th": th, "dir": step["dir"], "rev": step.get("rev")})
        return [Out("edit", target="markup", buttons=[[("🐢 Медленно", self.slow_key(th))]],
                    toast={0: "Вернётся в конце", 1: "Ок, повторим скоро", 2: "Отлично"}[g])] + self.advance(s)

    def finish_review(self, s):
        self.mark_activity()
        self.save(None)
        left = len(self.due())
        txt = f"Повторение готово: {s['total']} {plural(s['total'], 'карточка', 'карточки', 'карточек')}, вспомнились {s['score']}."
        txt += f"\nНа сегодня осталось ещё {left}." if left else "\nНа сегодня всё."
        return [Out("text", txt, buttons=[([("🔁 Ещё повторение", "rv")] if left else []) + [("▶ Урок", "ls")]])]

    # ---------- тренажёр тонов: чаще то, что путаешь
    def tone_acc(self):
        rows = self.st.q("select right, ok from answers where uid=? and qt='tone' order by ts desc limit 400", (self.uid,))
        acc = {}
        for t in TONES:
            xs = [ok for r, ok in rows if r == t][:30]
            acc[t] = (sum(xs) / len(xs), len(xs)) if xs else (None, 0)
        return acc

    def tone_stats_line(self):
        acc = [(t, a, n) for t, (a, n) in self.tone_acc().items() if n >= 5]
        if not acc:
            return ""
        return "Точность на слух: " + " · ".join(f"{TONE_RU[t]} {round(a * 100)}%" for t, a, n in acc)

    def start_trainer(self):
        tp = [th for th in self.known() if self.items[th]["tone"]]
        if len(tp) < 4:
            return [Out("text", "Тренажёр откроется после первых уроков про тоны.", buttons=[[("▶ Урок", "ls")]])]
        acc = self.tone_acc()
        weight = {th: 1 + 3 * (1 - (acc[self.items[th]["tone"]][0] if acc[self.items[th]["tone"]][0] is not None else 0.5))
                  for th in tp}
        pick = []
        pool = list(tp)
        while pool and len(pick) < 8:
            th = random.choices(pool, weights=[weight[x] for x in pool])[0]
            pick.append(th)
            pool.remove(th)
        qs = [self.q_tone(th) for th in pick]
        # самая частая путаница: «одинаковый или разный?» на словах этих двух тонов
        conf = Counter((r, c) for r, c in self.st.q("select right, chosen from answers where uid=? and qt='tone' and ok=0",
                                                         (self.uid,)))
        if conf:
            (r, c), _ = conf.most_common(1)[0]
            wr = [th for th in tp if self.items[th]["tone"] == r]
            wc = [th for th in tp if self.items[th]["tone"] == c]
            if wr and wc:
                qs.append(self.q_same(random.choice(wr), random.choice(wc)))
        qs += self.same_questions(tp, 2)
        random.shuffle(qs)
        intro = "Тренажёр тонов: слушай, веди рукой по мелодии, выбирай."
        if not self.multi_voice():
            intro += "\nПока все задания одним голосом. Другие голоса добавлю, когда верных ответов станет 80%."
        if conf:
            intro += f"\nЧаще всего ты путаешь {TONE_RU[r]} и {TONE_RU[c]}, их будет больше."
        return [Out("text", intro)] + self.begin("trainer", qs + [{"t": "end"}])

    # ---------- разговор: сценки из жизни и места
    def next_scene(self):
        d = self.done()
        return next((x for x in self.scenes if x not in d), None)

    def scenes_menu(self):
        d = self.done()
        mark = lambda P: ("✅ " if P["id"] in d else "") + P["title"]
        talk = [P for P in self.scenes.values() if P["kind"] == "talk"]
        places = [P for P in self.scenes.values() if P["kind"] == "place"]
        rows = [[(mark(P), f"pl:{P['id']}")] for P in talk]
        rows += [[(mark(P), f"pl:{P['id']}") for P in places[k:k + 2]] for k in range(0, len(places), 2)]
        txt = ("<b>💬 Разговор</b>\nСценки из жизни: сверху общение с людьми, ниже места. В каждой шпаргалка с голосом "
               "перед выходом и «🎓 Выучить», урок минут на пять: слушаешь, говоришь вслух, разыгрываешь диалог. "
               "Потом фразы возвращаются в повторении.\n<i>Тоны тут не зубрим: фразу учишь целиком, как мелодию.</i>")
        return [Out("text", txt, buttons=rows)]

    def scene_card(self, pid):
        P = self.scenes[pid]
        txt = f"<b>{esc(P['title'])}</b>"
        if P["say"]:
            txt += "\n\n<b>Скажи:</b>\n" + "\n".join(f"• {self.line(th)}" for th in P["say"])
        if P["hear"]:
            txt += "\n\n<b>Тебе могут сказать:</b>\n" + "\n".join(f"• {self.line(th)}" for th in P["hear"])
        learn = "🎓 Пройти ещё раз" if pid in self.done() else "🎓 Выучить"
        return [Out("text", txt, buttons=[[(learn, f"tk:{pid}"), ("🔊 Послушать всё", f"pa:{pid}")]])]

    def scene_audio(self, pid):
        return [Out("voice", self.scenes[pid]["title"], f"{DATA}/audio/{self.scenes[pid]['track']}")]

    def start_scene(self, pid):
        """Урок-сценка: фразы порциями по 3, после порции вспомнить каждую (свою сказать вслух, чужую
        понять на слух), диалог по ролям, в конце ещё круг вперемешку. Тонов не спрашиваем."""
        P = self.scenes[pid]
        topic = [x["th"] for x in P["seq"]]
        check = lambda x, near: {"t": "card", "th": x["th"], "dir": "ru"} if x["who"] == "me" else self.q_meaning(x["th"], near)
        steps = [{"t": "intro"}]
        for k in range(0, len(P["seq"]), CHUNK):
            chunk = P["seq"][k:k + CHUNK]
            steps += [{"t": "item", "th": x["th"], "who": x["who"]} for x in chunk]
            near = self.ranks(topic, topic[:k + CHUNK])
            recall = [check(x, near) for x in chunk]
            random.shuffle(recall)
            steps += recall
        steps += [{"t": "dlg", "who": d["who"], "th": d["item"], "ru": d["ru"]} for d in P["dialog"]]
        near = self.ranks(topic, topic)
        final = [check(x, near) for x in P["seq"]]
        random.shuffle(final)
        steps += final[:QUIZ_LIMIT] + [{"t": "end"}]
        return self.begin("scene", steps, scene=pid, again=[])

    def finish_scene(self, s):
        P = self.scenes[s["scene"]]
        self.st.run("insert or replace into lessons_done values(?,?,?,?,?)",
                    (self.uid, P["id"], dt.datetime.now(TZ).isoformat(timespec="minutes"), s["score"], s["total"]))
        tomorrow = (self.today() + dt.timedelta(days=1)).isoformat()
        for th in dict.fromkeys([x["th"] for x in P["seq"]] + [d["item"] for d in P["dialog"] if d["who"] == "me"]):
            self.st.run("insert or ignore into reviews values(?,?,?,?)", (self.uid, th, 1, tomorrow))
        self.mark_activity()
        self.save(None)
        weak = s["total"] and s["score"] / s["total"] < REPEAT_BELOW
        txt = f"<b>Сценка пройдена:</b> {esc(P['title'])}"
        if s["total"]:
            txt += f"\nВерно {s['score']} из {s['total']}."
        if weak:
            txt += " Это меньше 80%: пройди сценку ещё раз, второй раз фразы ложатся легче."
        txt += "\nФразы ушли в повторение, первое уже завтра. А крепче всего запомнится то, что скажешь живому человеку."
        m = P.get("mission")
        if m:
            txt += f"\n\n<b>Задание:</b> {esc(m['text'])}" + (f"\n{self.line(m['item'])}" if m["item"] else "")
        btn = [[("🎓 Пройти ещё раз", f"tk:{P['id']}")]] if weak else []
        btn.append([("💬 Сценки", "sc"), ("▶ Урок", "ls")])
        return [Out("text", txt, buttons=btn)]

    # ---------- прогресс и списки
    def progress(self):
        d = self.done()
        n_rev = self.st.q("select count(*) from reviews where uid=?", (self.uid,))[0][0]
        n_sc = len(d & set(self.scenes))
        n_ok = self.st.q("select count(*) from reviews where uid=? and box>=?", (self.uid, LEARNED_BOX))[0][0]
        nl = self.next_lesson()
        rt = self.profile()["remind"]
        k = len(set(self.get("skip") or []) - d)
        txt = (f"📊 <b>Прогресс</b>\nУроков пройдено: {len(d & set(self.order))} из {len(self.order)}"
               + (f", пропущено после проверки: {k}" if k else "") + "\n"
               + (f"Алфавит по желанию: {len(d & set(self.abc))} из {len(self.abc)}, /abc\n" if d & set(self.abc) else "")
               + f"Сценок для разговора: {n_sc} из {len(self.scenes)}\n"
               f"Слов в повторении: {n_rev}, из них закреплено: {n_ok}\n"
               f"Сегодня на повторение: {len(self.due())}\nСерия: {self.streak()} дн.\n")
        stats = self.tone_stats_line()
        if stats:
            txt += stats + "\n"
        txt += f"Следующий урок: {self.no(nl)}. {esc(self.lessons[nl]['title'])}\n" if nl else "Все уроки пройдены.\n"
        txt += f"Напоминание: {rt} {self.zone()}" if rt != "off" else "Напоминание выключено"
        return [Out("text", txt)]

    def funnel(self):
        """Новички за последние 30 дней: сколько дошло до каждого шага."""
        cutoff = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        new = [u for u, c, r in self.st.q("select uid, created, role from users") if c >= cutoff and r != "owner"]
        if not new:
            return ""
        one = lambda sql, u: bool(self.st.q(sql, (u, u) if sql.count("?") == 2 else (u,)))
        days = lambda u: len(self.st.q("select day from activity where uid=? union select day from studytime "
                                       "where uid=? and sec > 0", (u, u)))
        return (f"\n🧭 Новички за 30 дней: пришли {len(new)}, "
                f"выбрали речь {sum(one('select 1 from users where uid=? and g is not null', u) for u in new)}, "
                f"прошли урок {sum(one('select 1 from lessons_done where uid=? limit 1', u) for u in new)}, "
                f"занимались 2 дня и больше {sum(days(u) >= 2 for u in new)}")

    def users_report(self, names=None):
        """Итоги учеников для владельца (/users). names: {uid: имя} бот берёт из Телеграма в момент запроса,
        в базе имён нет."""
        rows, active = [], 0
        for uid, created, role in self.st.q("select uid, created, role from users"):
            u = self.user(uid)
            t0 = u.today()
            days = sorted(r[0] for r in self.st.q("select day from activity where uid=? union "
                                                  "select day from studytime where uid=? and sec > 0", (uid, uid)))
            lines = [f"👤 <b>{esc((names or {}).get(uid) or str(uid))}</b>" + (" (владелец)" if role == "owner" else "")]
            if days:
                n = (t0 - dt.date.fromisoformat(days[-1])).days
                active += n < 7
                last = "сегодня" if n <= 0 else "вчера" if n == 1 else \
                    f"{days[-1][8:10]}.{days[-1][5:7]}, {n} {plural(n, 'день', 'дня', 'дней')} назад"
                lines.append(f"📅 С {created[8:10]}.{created[5:7]}: дней с занятиями {len(days)}, последний раз {last}, "
                             f"серия {u.streak()} дн.")
            else:
                lines.append(f"📅 С {created[8:10]}.{created[5:7]}, занятий пока не было")
            spent = self.st.q("select day, sec from studytime where uid=? and sec > 0", (uid,))
            total = sum(x for _, x in spent)
            week = sum(x for d, x in spent if d > (t0 - dt.timedelta(days=7)).isoformat())
            if total >= 60:
                lines.append(f"⏱ Время ≈ {dur(total)}" + (f", за 7 дней {dur(week)}" if week < total else ""))
            d = u.done()
            score, out_of = self.st.q("select coalesce(sum(score), 0), coalesce(sum(total), 0) from lessons_done "
                                      "where uid=?", (uid,))[0]
            lines.append(f"✅ Пройдено уроков: {len(d & set(u.order))} из {len(u.order)}"
                         + (f", алфавит {len(d & set(u.abc))} из {len(u.abc)}" if d & set(u.abc) else "")
                         + (f", сценок {len(d & set(u.scenes))} из {len(u.scenes)}" if d & set(u.scenes) else "")
                         + (f"; в уроках верно {round(100 * score / out_of)}%" if out_of else ""))
            s = u.unfinished_lesson()
            if s:
                L = u.lessons[s["lesson"]]
                lines.append(f"⏸ Не закончен урок {u.no(L['id'])} «{esc(L['title'])}», шаг {s['i'] + 1} из {len(s['steps'])}")
            nl = u.next_lesson()
            if not s or s["lesson"] != nl:
                lines.append(f"▶️ Дальше: урок {u.no(nl)} «{esc(u.lessons[nl]['title'])}»" if nl else "▶️ Все уроки пройдены")
            n_rev, n_ok = self.st.q("select count(*), coalesce(sum(box >= ?), 0) from reviews where uid=?", (LEARNED_BOX, uid))[0]
            lines.append(f"🔁 Слов в повторении {n_rev}, закреплено {n_ok}, ждут {len(u.due())}" if n_rev else
                         "🔁 Слов в повторении пока нет")
            if u.tone_stats_line():
                lines.append("🎵 " + u.tone_stats_line())
            if u.get("goal") in GOALS:
                k = len(set(u.get("skip") or []) - d)
                lines.append(f"🧭 Цель: {GOALS[u.get('goal')][1]}" + (f", после проверки пропущено уроков {k}" if k else ""))
            p = u.profile()
            lines.append("⚙️ Речь " + {"m": "мужская", "f": "женская"}.get(p["g"], "не выбрана") + ", " +
                         (f"напоминание {p['remind']} {u.zone()}" if p["remind"] != "off" else "напоминание выключено"))
            rows.append((days[-1] if days else "", created, "\n".join(lines)))
        if not rows:
            return [Out("text", "Пока никого.")]
        msgs = [f"👥 <b>Ученики: {len(rows)}</b>, за последние 7 дней занимались {active}" + self.funnel()]
        for *_, block in sorted(rows, reverse=True):  # сначала те, кто занимался недавно
            if len(msgs[-1]) + len(block) > 3800:  # в одном сообщении Телеграма до 4096 знаков
                msgs.append(block)
            else:
                msgs[-1] += "\n\n" + block
        return [Out("text", m) for m in msgs]

    def lesson_list(self):
        d = self.done()
        skip = set(self.get("skip") or [])
        rows = [f"{'✅' if lid in d else '⏭' if lid in skip else '▫️'} {i}. {esc(self.lessons[lid]['title'])}"
                for i, lid in enumerate(self.order, 1)]
        return [Out("text", "<b>Уроки</b>\n" + "\n".join(rows) + "\n\nОткрыть любой: /go номер, например /go 3\n\n"
                            f"🔤 Алфавит, чтение тайских букв, по желанию: пройдено {len(d & set(self.abc))} из {len(self.abc)}. "
                            "Следующий урок алфавита: /abc, любой: /abc номер")]

    def alphabet(self, n=None):
        """Алфавит вне основного пути: /abc следующий урок, /abc 3 любой."""
        if n is not None and not 1 <= n <= len(self.abc):
            return [Out("text", f"Уроки алфавита с 1 по {len(self.abc)}.")]
        lid = self.abc[n - 1] if n else self.next_abc()
        if not lid:
            return [Out("text", f"Все {len(self.abc)} уроков алфавита пройдены. Открыть любой снова: /abc номер.")]
        return self.start_lesson(lid)

    def go(self, n):
        if not 1 <= n <= len(self.order):
            return [Out("text", f"Уроки с 1 по {len(self.order)}.")]
        return self.start_lesson(self.order[n - 1])

    # ---------- утреннее напоминание
    def reminder(self, hour=9):
        nl = self.next_lesson()
        due = len(self.due())
        txt = ("Доброе утро!" if hour < 12 else "Добрый день!" if hour < 18 else "Добрый вечер!") + "\n"
        txt += f"Сегодня урок {self.no(nl)}: «{esc(self.lessons[nl]['title'])}»." if nl else "Все уроки пройдены, остаётся повторение."
        ns = self.next_scene()
        if ns:
            txt += f"\nСценка для разговора: {esc(self.scenes[ns]['title'])}"
        if due:
            txt += f"\nНа повторение: {due}."
        m = None  # задание из последнего пройденного урока или сценки
        for (x,) in self.st.q("select id from lessons_done where uid=? order by done_at desc", (self.uid,)):
            m = (self.lessons.get(x) or self.scenes.get(x) or {}).get("mission")
            if m:
                break
        if m:
            txt += f"\n\n<b>Задание дня:</b> {esc(m['text'])}" + (f"\n{self.line(m['item'])}" if m["item"] else "")
        btn = [("▶ Урок", "ls")] + ([("💬 Сценка", f"tk:{ns}")] if ns else []) + ([("🔁 Повторение", "rv")] if due else [])
        return [Out("text", txt, buttons=[btn])]

    def welcome(self):
        u = self.profile()
        when = (f"Каждое утро в {u['remind']} {self.zone()} пришлю урок. Время меняется командой /time, "
                "часовой пояс /tz" if u["remind"] != "off" else "Напоминание выключено, включить: /time 09:00")
        return [Out("text",
                    "<b>Как устроен курс</b>\n\n"
                    "▶ <b>Урок</b>: 10-15 минут. Объяснение, голосовые, задания кнопками.\n"
                    "🔁 <b>Повторение</b>: пройденные слова возвращаются по интервалам, чтобы не забылись.\n"
                    "💬 <b>Разговор</b>: сценки из жизни, от «куда идёшь?» до такси. Шпаргалка перед выходом "
                    "и урок на пять минут.\n"
                    "🎵 <b>Тоны</b>: тренажёр тонов на слух.\n"
                    "✍️ <b>Написать</b>: вопрос или пожелание автору бота.\n\n"
                    f"{when}, список уроков /lessons.",
                    menu=True),
                Out("text", "Начнём?", buttons=[[("▶ Первый урок", "ls")]])]

    # ---------- ✍️ Написать: вопросы и пожелания автору
    def menu_news(self):
        """Раскладка меню сменилась: новая клавиатура приходит с ближайшим итогом урока, повторения или сценки."""
        if self.get("menu_v") == MENU_V:
            return []
        self.set("menu_v", MENU_V)
        return [Out("text", "Внизу новая кнопка ✍️ <b>Написать</b>: вопрос, пожелание или что неудобно. "
                            "Я передам автору бота.", menu=True)]

    def where(self):
        """Где был ученик, когда написал: урок или сценка и фраза шага. Чтобы отзыв было к чему привязать."""
        s = self.session()
        if not s:
            return ""
        place = {"review": "повторение", "trainer": "тренажёр тонов", "check": "проверка на входе"}.get(s["mode"], s["mode"])
        if s["mode"] == "lesson" and s.get("lesson") in self.lessons:
            place = f"урок {self.no(s['lesson'])} «{self.lessons[s['lesson']]['title']}»"
        elif s["mode"] == "scene" and s.get("scene") in self.scenes:
            place = f"сценка «{self.scenes[s['scene']]['title']}»"
        step = s["steps"][min(s["i"], len(s["steps"]) - 1)]
        th = step.get("th")
        tail = "закончено" if step["t"] == "end" else \
            f"{th} {self.items[th]['tr']} ({self.meaning(th)})" if th in self.items else \
            {"intro": "вступление", "dlg": "диалог", "letter": "буква", "q": "вопрос", "card": "карточка"}.get(step["t"], step["t"])
        return f"{place} · {tail}"

    def live(self):
        s = self.session()
        return bool(s) and s["steps"][min(s["i"], len(s["steps"]) - 1)]["t"] != "end"

    def ask_feedback(self):
        self.set("fb_wait", {"ts": dt.datetime.utcnow().isoformat(), "ctx": self.where(), "n": 0})
        return [Out("text", "✍️ Напиши одним сообщением вопрос, пожелание или что неудобно. Можно голосовым или "
                            "скриншотом. Я передам автору бота.", buttons=[[("Отмена", "fb:no")]])]

    def save_feedback(self, m, ctx):
        """В базу текст, файл в Телеграме и где был ученик; имени нет. Out «fb» бот превращает в сообщение владельцу."""
        cur = self.st.db.execute("insert into feedback (uid, ts, kind, text, file_id, ctx, chat, mid) values (?,?,?,?,?,?,?,?)",
                                 (self.uid, dt.datetime.utcnow().isoformat(timespec="seconds"), m["kind"], m.get("text", ""),
                                  m.get("file_id"), ctx, m.get("chat"), m.get("mid")))
        self.st.db.commit()
        self.set("fb_wait", {"ts": dt.datetime.utcnow().isoformat(), "ctx": ctx, "n": 1})
        return Out("fb", str(cur.lastrowid))

    def fb_thanks(self):
        txt = "Записал 👌" if self.uid == self.owner() else "Спасибо! Передал автору 🙏"
        return [Out("text", f"{txt} Можно дописать или прислать скриншот следом.",
                    buttons=[[("▶ Продолжить", "fb:back")]] if self.live() else None)]

    def take_feedback(self, m):
        """Сообщение после ✍️ Написать. None, если ученик ничего не собирался писать."""
        w = self.get("fb_wait")
        if not w:
            return None
        if (dt.datetime.utcnow() - dt.datetime.fromisoformat(w["ts"])).total_seconds() > (FB_MORE if w["n"] else FB_WAIT):
            self.set("fb_wait", None)
            return None
        out = self.save_feedback(m, w["ctx"])
        return [out] if w["n"] else [out] + self.fb_thanks()

    def fallback(self, m=None):
        """Текст вместо кнопки, голосовое или картинка без ✍️: предлагаем передать автору."""
        if not m:
            return [Out("text", "Не понял. Кнопки меню внизу, список уроков: /lessons", menu=True)]
        if m.get("group") and m["group"] == self.get("fb_group"):
            return []  # альбом: предлагаем один раз
        self.set("fb_group", m.get("group"))
        self.set("fb_draft", {**m, "ctx": self.where()})
        return [Out("text", "Я понимаю только кнопки меню 🙂 Если это вопрос или пожелание, передам автору бота. "
                            "Кнопок не видно: /menu, список уроков: /lessons.", buttons=[[("✍️ Передать автору", "fb:send")]])]

    def on_fb(self, what):
        if what == "send":
            d = self.get("fb_draft")
            if not d:
                return [Out("toast", toast="Уже передано 🙂")]
            self.set("fb_draft", None)
            return [Out("edit", target="markup", buttons=None), self.save_feedback(d, d.get("ctx", ""))] + self.fb_thanks()
        self.set("fb_wait", None)
        self.set("fb_draft", None)  # ученик не стал передавать текст: не храним его
        if what == "back" and self.live():
            return [Out("edit", target="markup", buttons=None)] + self.render()
        return [Out("edit", target="markup", buttons=None), Out("toast", toast="Хорошо 👌")]

    # ---------- разбор нажатий: общий для бота и тестового прогона
    def on_callback(self, data, is_voice=False):
        kind, _, rest = data.partition(":")
        if kind != "fb" and self.get("fb_wait"):  # ученик нажал другое: ✍️ больше не ждём
            self.set("fb_wait", None)
        try:
            if kind == "fb":
                return self.on_fb(rest)
            if kind == "n":
                return self.on_next(int(rest), is_voice)
            if kind == "a":
                nonce, k = rest.split(":")
                return self.on_answer(int(nonce), int(k))
            if kind == "r":
                return self.on_reveal(int(rest))
            if kind == "g":
                nonce, g = rest.split(":")
                return self.on_grade(int(nonce), int(g))
            if kind == "s":
                return self.on_slow(rest)
            if kind == "cmp":
                key, chosen = rest.split(":")
                return self.compare(key, chosen)
            if kind == "rl" and rest in self.lessons:
                return self.start_lesson(rest)
            if kind == "pl" and rest in self.scenes:
                return self.scene_card(rest)
            if kind == "pa" and rest in self.scenes:
                return self.scene_audio(rest)
            if kind == "tk" and rest in self.scenes:
                return self.start_scene(rest)
            if kind == "gv":
                return self.set_gender(rest)
            if kind == "ob":
                what, _, val = rest.partition(":")
                return self.on_goal(val) if what == "goal" else self.on_level(int(val))
        except (ValueError, IndexError):
            pass
        actions = {"ls": self.continue_or_next, "rv": self.start_review, "tr": self.start_trainer, "anc": self.anchors,
                   "sc": self.scenes_menu}
        if data in actions:
            return actions[data]()
        return [Out("toast", toast="Кнопка устарела")]

    def on_menu(self, text):
        if text == "✍️ Написать":
            return self.ask_feedback()
        actions = {"▶ Урок": self.continue_or_next, "🔁 Повторение": self.start_review,
                   "💬 Разговор": self.scenes_menu, "🎵 Тоны": self.start_trainer, "📊 Прогресс": self.progress}
        if (text in actions or text == OLD_PLACES) and self.get("fb_wait"):
            self.set("fb_wait", None)
        if text == OLD_PLACES:
            return [Out("text", "Меню обновилось: шпаргалки мест теперь в «💬 Разговор», и каждую можно выучить.",
                        menu=True)] + self.scenes_menu()
        return actions[text]() if text in actions else None
