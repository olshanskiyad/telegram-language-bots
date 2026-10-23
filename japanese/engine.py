"""Логика японского бота без Телеграма, для нескольких учеников.

Всё состояние хранится по user_id. Движок возвращает Out, бот только рисует,
поэтому весь путь ученика проверяется в консоли без токена (simulate.py).

Сейчас в боте: азбука с нуля (хирагана, катакана и первые фразы, 24 урока с картинкой и голосом у каждого
знака), тест уровня (7 частей, слова и кандзи адаптивно N5-N3), уроки кандзи N5-N4 (по 5 кандзи: значение,
подсказка, слово с кунным и слово с онным чтением; первый урок «два чтения»), уроки грамматики N5, тренажёр
дат и чисел, слух с записями носителей, кана на слух, повторение по интервалам.
Новичок выбирает старт: с нуля (азбука и простое меню из четырёх кнопок, после азбуки открывается всё),
«знаю азбуку» (грамматика и кандзи) или тест (он решает, с какого урока начать).
Доступ: любой, кто нажал /start, прогресс по id Телеграма. Владельцу (OWNER_ID, а если он не задан, первому,
кто нажал /start) доступна /users.
"""
import datetime as dt
import hashlib
import html
import json
import os
import random
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass

import azbuka
import drills
import tts

DATA = tts.DATA

def _owner_id():
    """OWNER_ID из окружения: Telegram id владельца. Не задан: владелец первый, кто написал боту."""
    v = os.environ.get("OWNER_ID", "").strip()
    if v and not v.isdigit():
        raise SystemExit("OWNER_ID должен быть числом: Telegram id владельца (его показывает, например, @userinfobot)")
    return int(v) if v else None


OWNER_ID = _owner_id()
INTERVALS = [0, 1, 2, 4, 7, 15, 30]
MAX_BOX, LEARNED_BOX = 6, 4
REVIEW_LIMIT = 20
WARMUP = 5  # карточек повторения перед уроком: повторение идёт само, без отдельной кнопки
RESEND_AFTER = 20  # с, нажатие на прошлый шаг позже этого присылает текущий шаг снова
LEVELS = ["N5", "N4", "N3"]
IDK = 9  # индекс ответа «не знаю»
MENU = [["📚 Урок", "🔁 Повторение"], ["📅 Даты", "🎧 Слух", "🔤 Кана"], ["📝 Тест", "📊 Прогресс", "✍️ Написать"]]
MENU_START = [["📚 Урок", "🔁 Повторение"], ["🔤 Азбука", "📊 Прогресс", "✍️ Написать"]]  # пока идёт азбука с нуля
LESSON_MODES = ("abc", "lesson", "grammar", "dl")  # ошибка в уроке возвращается в конце; dl: даты по шагам
IMG = f"{DATA}/img"
YES = ["✅ Верно!", "✅ Точно!", "✅ Да, так!", "✅ Отлично!"]
ABC_BUTTONS = ("Внизу пять кнопок:\n📚 <b>Урок</b>: следующий урок, 5-10 минут\n🔁 <b>Повторение</b>: то, что пора "
               "вспомнить, приходит само\n🔤 <b>Азбука</b>: таблица выученных знаков\n📊 <b>Прогресс</b>: сколько уже "
               "пройдено\n✍️ <b>Написать</b>: вопрос или пожелание автору бота\n\nТест, даты и слух откроются после азбуки.")
FULL_BUTTONS = ("Внизу все кнопки:\n📚 <b>Урок</b>: грамматика и кандзи по очереди, урок минут на десять\n🔁 <b>Повторение</b>: "
                "то, что пора вспомнить\n📅 <b>Даты</b>, 🎧 <b>Слух</b>, 🔤 <b>Кана</b>: тренажёры по 10 заданий\n"
                "📝 <b>Тест</b>: подберёт, с какого урока начать\n📊 <b>Прогресс</b>: что уже пройдено\n"
                "✍️ <b>Написать</b>: вопрос или пожелание автору бота")
PLACE = ("h", "k", "5", "4")  # лесенка уровня на входе: хирагана, катакана, кандзи N5, N4; по два вопроса
MENU_V = 4  # номер раскладки: у кого старая, получит новую вместе с ближайшим итогом урока или тренажёра
FB_WAIT = 1800  # ✍️ Написать: сообщение ждём полчаса
FB_MORE = 120  # и ещё две минуты после него: скриншот или голосовое вдогонку уходят без нового ответа
MODE_RU = {"lesson": "урок кандзи", "grammar": "урок грамматики", "abc": "азбука", "dl": "даты по шагам",
           "dates": "тренажёр дат", "listen": "слух", "kana": "кана на слух", "review": "повторение", "test": "тест",
           "place": "лесенка уровня", "onkun": "два чтения"}
IDLE = 300  # пауза между нажатиями дольше 5 минут: ученик отошёл, во время занятий её не считаем
KIND_RU = {"kun": "кунное", "on": "онное", "exc": "исключение"}
JP_RE = re.compile(r"[぀-ヿ一-鿿々]")
SECTIONS = [  # ключ, название, адаптивно по уровням, пояснение перед частью
    ("kana", "Кана", False, "Послушай слово и выбери, как оно пишется."),
    ("vocab", "Слова", True, "Слово звучит и написано каной. Выбери перевод. Не знаешь, жми «не знаю»: так тест точнее."),
    ("kmean", "Значения кандзи", True, "Что значит кандзи? Уровень растёт, пока отвечаешь уверенно."),
    ("kread", "Чтения кандзи", True, "Как читается слово? Среди вариантов есть ловушки с онным и кунным чтением."),
    ("dates", "Даты, время и числа", False, "Даты и время на слух и на письме."),
    ("listen", "На слух", False, "Фразы медленно, в обычном темпе и голосами живых носителей."),
    ("grammar", "Грамматика", False, "Выбери, что вставить вместо пропуска."),
]
DATE_KINDS = ["date_listen", "date_read", "date_ru", "weekday", "time_listen", "time_read", "num_listen"]
DATE_RU = {"date_listen": "даты на слух", "date_read": "как читается дата", "date_ru": "дата по-японски",
           "weekday": "дни недели", "weekday_listen": "дни недели на слух", "time_listen": "время на слух",
           "time_read": "как читается время", "num_listen": "числа на слух"}
SEC_RU = {"kana": "кана на слух", "vocab": "слова", "kmean": "значения кандзи", "kread": "чтения кандзи",
          "dates": "даты и время", "listen": "речь на слух", "grammar": "грамматика"}
SEC_TOOL = {"kana": "🔤 Кана", "vocab": "📚 уроки", "kmean": "📚 уроки кандзи", "kread": "📚 уроки кандзи",
            "dates": "📅 Даты", "listen": "🎧 Слух", "grammar": "📚 уроки грамматики"}


@dataclass
class Out:
    kind: str              # text | voice | edit | toast | fb (сообщение ученика владельцу, id в text)
    text: str = ""
    path: str = ""
    buttons: list = None   # [[(подпись, данные), ...], ...]
    target: str = ""       # edit: "text" | "caption" | "markup"
    toast: str = ""
    menu: bool = False


def esc(s):
    return html.escape(s or "", quote=False)


def hid(s):
    return hashlib.sha1(s.encode()).hexdigest()[:10]


def say_path(text, voice="f", rate=tts.NORMAL):
    return tts.path_for(text, tts.FEMALE if voice == "f" else tts.MALE, rate)


def kanji_pic(ch):
    """Кандзи крупной картинкой (готовит сборка). Нет файла, знак останется только в тексте."""
    name = azbuka.kanji_name(ch)
    return name if os.path.exists(f"{IMG}/{name}") else None


def plural(n, one, few, many):
    n10, n100 = n % 10, n % 100
    return one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many


def dur(sec):
    h, m = divmod(round(sec / 60), 60)
    return " ".join(([f"{h} ч"] if h else []) + ([f"{m} мин"] if m else [])) or "меньше минуты"


def numbered(step):
    """Варианты с японскими знаками: на кнопках шрифт мелкий, ゛ и ゜ не разглядеть."""
    return any(JP_RE.search(o) for o in step["opts"])


def q_body(step, chosen=None):
    """Текст вопроса; японские варианты списком с номерами и жирным, после ответа с отметками ✅ и ❌."""
    if not numbered(step):
        return step["text"]
    lines = []
    for k, o in enumerate(step["opts"]):
        mark = "✅ " if chosen is not None and k == step["ans"] else "❌ " if k == chosen else ""
        lines.append(f"{mark}{k + 1}.  <b>{esc(o)}</b>")
    return step["text"] + "\n\n" + "\n".join(lines)


def word_line(jp, kana, ru="", tail=""):
    """Слово: написание → чтение каной, оба жирным (по кане запоминают произношение), ниже чтение русскими
    буквами и перевод. Русские буквы: よっか «ёкка» и ようか «ёока» каной и на слух путали."""
    head = f"<b>{esc(jp)}</b>" + (f" → <b>{esc(kana)}</b>" if kana and kana != jp else "")
    rd = azbuka.ru_read(kana or jp)
    rest = " · ".join(x for x in (esc(ru), tail) if x)
    rest = ", ".join(x for x in (f"«{esc(rd)}»" if rd else "", rest) if x)
    return head + (f"\n{rest}" if rest else "")


def filled(x, gap=""):
    """Вопрос-пропуск грамматики с ответом. Для показа ответ приклеен к слову слева, как в примерах: としょかんで
    べんきょうします. Для чтения gap=" ": в пропусках частица слита со словом (トイレは（どこ）ですか), а так читается."""
    s = x["blank"].replace("（　）", gap + x["ans"] + " ")
    return re.sub(r" +([。？！、])", r"\1", s).replace("  ", " ").strip()


def jp_line(jp, ru="", src=None):
    """Фраза грамматики: каной жирным, ниже чтение русскими буквами (если собирается, src: откуда читать) и перевод."""
    rd = azbuka.ru_read(src or jp)
    return f"<b>{esc(jp)}</b>" + (f"\n«{esc(rd)}»" if rd else "") + (f"\n{esc(ru)}" if ru else "")


def alike(a, b):
    """Фраза похожа видом: обе вопросы или обе нет, длина в словах близкая. Иначе ответ выдаёт себя длиной."""
    na, nb = len(a.split()), len(b.split())
    return a.rstrip().endswith("?") == b.rstrip().endswith("?") and abs(na - nb) <= max(1, na // 4)


def similar(a, b):
    """Близкие значения («думать» и «обдумывать», «мир» и «мир; граница») не ставим рядом в варианты."""
    a, b = a.lower(), b.lower()
    return any(w[:3] in b for w in re.findall(r"[а-яё]{3,}", a)) or any(w[:3] in a for w in re.findall(r"[а-яё]{3,}", b))


class Store:
    def __init__(self, path=None):
        self.db = sqlite3.connect(path or os.environ.get("JP_DB") or f"{DATA}/bot.db")
        self.db.executescript("""
            create table if not exists users (uid integer primary key, chat integer, role text, label text,
                created text, tz integer default 3, remind text default '09:00', reminded text);
            create table if not exists kv (uid integer, key text, value text, primary key (uid, key));
            create table if not exists results (uid integer, ts text, data text);
            create table if not exists answers (uid integer, ts text, qt text, ok integer);
            create table if not exists reviews (uid integer, key text, jp text, kana text, ru text, box integer,
                due text, primary key (uid, key));
            create table if not exists activity (uid integer, day text, primary key (uid, day));
            create table if not exists studytime (uid integer, day text, sec integer, primary key (uid, day));
            create table if not exists done (uid integer, ts text, mode text, n integer, score integer, total integer);
            create table if not exists tgfiles (path text primary key, file_id text);
            create table if not exists sounds (key text primary key, texts text, voice text, cap text);
            create table if not exists feedback (id integer primary key, uid integer, ts text, kind text, text text,
                file_id text, ctx text, chat integer, mid integer);
        """)

    def q(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def run(self, sql, args=()):
        self.db.execute(sql, args)
        self.db.commit()


class Engine:
    def __init__(self, bank_path=f"{DATA}/bank.json", store=None, rnd=None):
        if not os.path.exists(bank_path):
            raise SystemExit(f"Нет файла {bank_path}: сначала python fetch_data.py, потом python build.py (см. README)")
        self.b = json.load(open(bank_path, encoding="utf-8"))
        self.st = store or Store()
        if OWNER_ID:  # явный владелец главнее записи в базе: роли подгоняем под него
            self.st.run("update users set role = case when uid=? then 'owner' else 'member' end", (OWNER_ID,))
        self.rnd = rnd or random.Random()
        self.listen_by_hid = {hid(x["jp"]): x for x in self.b["listen"]}
        self.abc_ix = {}  # ключ карточки повторения -> текст, картинка, что звучит
        for L in self.abc():
            for c in L["cards"]:
                if c["type"] == "group":
                    for x in c["items"]:
                        self.abc_ix[f"abc|{x['ch']}"] = {
                            "text": f"{x['emo']} <b>{esc(x['ch'])}</b> читается «{x['cyr']}»\n<i>{esc(x['hint'])}</i>\n"
                                    f"В хирагане: <b>{esc(x['pair'])}</b>", "img": x.get("img"), "say": [azbuka.sound(x["ch"])]}
                else:
                    key = f"abc|{c['word']['jp'] if c['type'] == 'rule' else c['ch']}"
                    self.abc_ix[key] = {"text": self.abc_card(c), "img": c.get("img"), "say": c["say"]}
            for x in L["phrases"]:
                self.abc_ix[f"phr|{x['jp']}"] = {"text": self.phrase_text(x, True), "img": None, "say": [x["jp"]]}
        self.dc = drills.course(self.b["valid"])
        for x in (x for L in self.dc for x in L["items"]):
            for p in x.get("parts", []):
                p["cyr"] = azbuka.cyr(p["kana"])
            if not x.get("cyr"):
                x["cyr"] = " · ".join(p["cyr"] for p in x["parts"]) if x.get("parts") else azbuka.cyr(x["kana"])

    # ---------- ученики и доступ
    def user(self, uid):
        r = self.st.q("select uid, chat, role, label, tz, remind, reminded from users where uid=?", (uid,))
        return dict(zip(("uid", "chat", "role", "label", "tz", "remind", "reminded"), r[0])) if r else None

    def owner(self):
        """Владелец: при заданном OWNER_ID он (как только написал боту), иначе первый, кто написал боту."""
        if OWNER_ID:
            return OWNER_ID if self.user(OWNER_ID) else None
        r = self.st.q("select uid from users where role='owner'")
        return r[0][0] if r else None

    def role_for(self, uid):
        """Роль нового ученика: владелец это OWNER_ID, а без него самый первый ученик."""
        return "owner" if (uid == OWNER_ID if OWNER_ID else self.owner() is None) else "member"

    def add_user(self, uid, chat, role, label):
        self.st.run("insert or replace into users (uid, chat, role, label, created) values (?,?,?,?,?)",
                    (uid, chat, role, label, dt.datetime.utcnow().isoformat(timespec="minutes")))

    def today(self, uid):
        u = self.user(uid)
        return (dt.datetime.utcnow() + dt.timedelta(hours=u["tz"] if u else 3)).date()

    # ---------- хранилище ученика
    def get(self, uid, key, default=None):
        r = self.st.q("select value from kv where uid=? and key=?", (uid, key))
        return json.loads(r[0][0]) if r else default

    def set(self, uid, key, value):
        self.st.run("insert into kv values(?,?,?) on conflict(uid, key) do update set value=excluded.value",
                    (uid, key, json.dumps(value, ensure_ascii=False)))

    def touch(self, uid):
        """Каждое нажатие ученика: время занятий по дням складывается из пауз между нажатиями (до IDLE)."""
        now, last = dt.datetime.utcnow().replace(microsecond=0), self.get(uid, "seen")
        self.set(uid, "seen", now.isoformat())
        gap = (now - dt.datetime.fromisoformat(last)).total_seconds() if last else 0
        if 0 < gap <= IDLE:
            self.st.run("insert into studytime values(?,?,?) on conflict(uid, day) do update set sec = sec + excluded.sec",
                        (uid, self.today(uid).isoformat(), int(gap)))

    def session(self, uid):
        return self.get(uid, "session")

    def begin(self, uid, mode, steps, **extra):
        cur = self.session(uid)
        if cur and cur["mode"] in ("test", "onkun") + LESSON_MODES and cur["i"] < len(cur["steps"]) - 1 and \
                mode != cur["mode"]:
            self.set(uid, f"paused_{cur['mode']}", cur)  # тест и урок не теряются, если отвлечься на тренажёр
        s = {"mode": mode, "steps": steps, "i": 0, "score": 0, "total": 0, "nonce": self.rnd.randint(1, 10 ** 6),
             "at": time.time(), **extra}
        self.set(uid, "session", s)
        return self.render(uid)

    def resume(self, uid, mode):
        s = self.get(uid, f"paused_{mode}")
        if not s:
            return None
        self.set(uid, f"paused_{mode}", None)
        self.set(uid, "session", s)
        return [Out("text", "Продолжаем с того места, где остановились 👌")] + self.render(uid)

    def advance(self, uid, s):
        s["i"] += 1
        s["nonce"] += 1
        s["at"] = time.time()
        if s["mode"] == "test" and s["i"] >= len(s["steps"]):
            self.test_extend(s)
        self.set(uid, "session", s)
        return self.render(uid)

    def stale(self, uid, s, toast):
        """Нажатие на прошлый шаг. Если шаг сменился давно, новое сообщение, видимо, не дошло (сбой связи
        с Телеграмом: оценка засчиталась, а следующая карточка потерялась): присылаем текущий шаг снова."""
        if not s or s["steps"][min(s["i"], len(s["steps"]) - 1)]["t"] == "end" or time.time() - s.get("at", 0) < RESEND_AFTER:
            return [Out("toast", toast=toast)]
        s["at"] = time.time()
        self.set(uid, "session", s)
        return [Out("toast", toast=toast)] + self.render(uid)

    # ---------- отрисовка
    def audio_of(self, step):
        if step.get("path"):
            return step["path"]
        if step.get("say"):
            return say_path(step["say"], step.get("voice", "f"), step.get("rate", tts.NORMAL))
        return ""

    def render(self, uid):
        s = self.session(uid)
        step = s["steps"][s["i"]]
        n = s["nonce"]
        if step["t"] == "msg":
            path = self.audio_of(step)
            slow = self.slow_btn(step) if path else None
            btn = [([slow] if slow else []) + [(step.get("btn", "Дальше ▶"), f"n:{n}")]] + ([step["alt"]] if step.get("alt") else [])
            img = [Out("photo", "", f"{IMG}/{step['img']}")] if step.get("img") else []
            return img + [Out("voice" if path else "text", step["text"], path, btn)]
        if step["t"] == "q":
            rows = [[(o, f"a:{n}:{k}")] for k, o in enumerate(step["opts"])]
            if numbered(step):  # японские варианты крупно в тексте, на кнопках только номера
                rows = [[(str(k + 1), f"a:{n}:{k}") for k in range(len(step["opts"]))]]
            elif len(step["opts"]) == 4 and max(len(o) for o in step["opts"]) <= 8:
                rows = [[(step["opts"][0], f"a:{n}:0"), (step["opts"][1], f"a:{n}:1")],
                        [(step["opts"][2], f"a:{n}:2"), (step["opts"][3], f"a:{n}:3")]]
            path = self.audio_of(step) if not step.get("after") else ""
            extra = [x for x in [self.slow_btn(step) if path and s["mode"] != "test" else None] if x]
            if step.get("idk"):
                extra.append(("🤷 Не знаю", f"a:{n}:{IDK}"))
            img = [Out("photo", "", f"{IMG}/{step['img']}")] if step.get("img") else []
            return img + [Out("voice" if path else "text", q_body(step), path, rows + ([extra] if extra else []))]
        if step["t"] == "card":
            pic = kanji_pic(step["jp"]) if not step["kana"] and step["jp"] in self.b["course"]["kanji"] else None
            img = [Out("photo", "", f"{IMG}/{pic}")] if pic else []
            return img + [Out("text", self.card_front(step), buttons=[[("👀 Показать ответ", f"r:{n}")]])]
        if step["t"] == "end":
            return self.finish(uid, s)
        raise ValueError(step["t"])

    def card_front(self, c):
        head = f"{c['head']}\n\n" if c.get("head") else ""
        if c["key"].startswith("phr|"):
            return f"{head}Как сказать по-японски:\n<b>«{esc(c['ru'])}»</b>\n\nСкажи вслух, потом открой ответ."
        if c["key"].startswith("abc|"):
            return f"{head}<b>{esc(c['jp'])}</b>\nКак это читается? Вспомни, потом открой ответ."
        return f"{head}<b>{esc(c['jp'])}</b>\nВспомни чтение и значение, потом открой ответ."

    # ---------- нажатия
    def on_next(self, uid, nonce):
        s = self.session(uid)
        if not s or s["nonce"] != nonce:
            return self.stale(uid, s, "Этот шаг уже пройден")
        return [Out("edit", target="markup", buttons=None)] + self.advance(uid, s)

    def on_answer(self, uid, nonce, k):
        s = self.session(uid)
        if not s or s["nonce"] != nonce:
            return self.stale(uid, s, "Этот вопрос уже позади")
        q = s["steps"][s["i"]]
        ok = k == q["ans"]
        s["total"] += 1
        s["score"] += ok
        self.st.run("insert into answers values(?,?,?,?)", (uid, dt.datetime.utcnow().isoformat(timespec="seconds"),
                                                            q["qt"], int(ok)))
        again = s["mode"] in LESSON_MODES and not ok and not q.get("retry")
        if again:  # ошибка вернётся в конце урока
            s["steps"].insert(len(s["steps"]) - 1, {**q, "retry": True})
        if s["mode"] == "dates" and not ok:
            s.setdefault("err", {})[q["qt"]] = s.get("err", {}).get(q["qt"], 0) + 1
        if s["mode"] == "test":
            s["res"].setdefault(q["sec"], []).append([q.get("lv", 0), int(ok)])
            if not ok and q.get("key"):
                s.setdefault("miss", []).append(q["key"])
        chosen = q["opts"][k] if k < len(q["opts"]) else ""
        tail = "\n<i>Этот вопрос вернётся в конце урока.</i>" if again else ""
        if numbered(q):  # в списке уже отмечены верный и выбранный
            text = f"{q_body(q, k)}\n\n" + (f"✅ {q['fb']}" if ok else f"Правильный ответ: {q['fb']}{tail}")
        elif ok:
            text = f"{q['text']}\n\n✅ {q['fb']}"
        else:
            text = f"{q['text']}\n\n" + (f"Твой ответ: {esc(chosen)}\n" if chosen else "") + f"Правильный ответ: {q['fb']}{tail}"
        target = "caption" if self.audio_of(q) and not q.get("after") else "text"
        slow = self.slow_btn(q) if self.audio_of(q) and s["mode"] != "test" else None
        keep = [[slow]] if slow and target == "caption" else None
        toast = self.rnd.choice(YES) if ok else "Ничего, сейчас запомним 🙂" if k == IDK else "Не совсем, смотри ответ"
        outs = [Out("edit", text, target=target, buttons=keep, toast=toast)]
        if q.get("after") and self.audio_of(q):  # голос сразу с текстом: чтение видно рядом со звуком
            outs.append(Out("voice", q["fb"], self.audio_of(q), [[slow]] if slow else None))
        return outs + self.advance(uid, s)

    def slow_btn(self, step):
        """«🐢 Медленно»: то, что звучит в шаге, синтезом на 30% медленнее. Ключ в базе: старые кнопки живут."""
        texts = step.get("slow") or step.get("say")
        if not texts or step.get("rate") == tts.SLOW:
            return None
        texts = texts if isinstance(texts, list) else [texts]
        v = step.get("voice", "f")
        key = hid(v + "|" + "|".join(texts))
        self.st.run("insert or ignore into sounds values(?,?,?,?)",
                    (key, json.dumps(texts, ensure_ascii=False), v, step.get("slow_cap") or " · ".join(texts)))
        return ("🐢 Медленно", f"sl:{key}")

    def on_slow(self, key):
        r = self.st.q("select texts, voice, cap from sounds where key=?", (key,))
        if r:
            texts, v, cap = json.loads(r[0][0]), r[0][1], r[0][2]
        elif key in self.listen_by_hid:  # кнопки «Слуха» старого формата
            x = self.listen_by_hid[key]
            texts, v, cap = [x["jp"]], "f", x.get("kana") or x["jp"]
        else:
            return [Out("toast", toast="Звук не найден")]
        paths = [say_path(t, v, tts.SLOW) for t in texts]
        if not all(os.path.exists(p) for p in paths):
            return [Out("toast", toast="Медленная запись ещё готовится, попробуй чуть позже")]
        return [Out("voice", f"🐢 <b>{esc(cap)}</b>", paths[0] if len(paths) == 1 else tts.concat(paths))]

    # ---------- тест уровня
    def start_test(self, uid):
        r = self.resume(uid, "test")
        if r:
            return r
        cur = self.session(uid)
        if cur and cur["mode"] == "test" and cur["i"] < len(cur["steps"]) - 1:
            return [Out("text", "Продолжаем тест 👌")] + self.render(uid)
        intro = {"t": "msg", "btn": "Начать ▶", "text":
                 "<b>Тест уровня</b>\n\nЭто не экзамен: по тесту я подберу уроки под тебя. 7 коротких частей по 2-3 "
                 "минуты: кана, слова, значения и чтения кандзи, даты, слух, грамматика. В словах и кандзи уровень "
                 "растёт от N5 к N3, пока отвечаешь уверенно.\n\n"
                 "Не знаешь ответ, смело жми «🤷 Не знаю»: так подбор точнее, чем наугад. "
                 "Прерваться можно в любой момент, продолжить кнопкой «📝 Тест»."}
        s_steps = [intro] + self.section_steps(0, 0)
        return self.begin(uid, "test", s_steps, sec=0, lv=0, res={}, miss=[])

    def section_steps(self, si, lv):
        key, name, adaptive, hint = SECTIONS[si]
        head = {"t": "msg", "text": f"<b>Часть {si + 1} из {len(SECTIONS)}: {name}</b>\n{hint}"}
        return [head] + self.test_block(key, lv)

    def distract(self, correct, pool, n=3):
        pool = [p for p in dict.fromkeys(pool) if p and p != correct]
        self.rnd.shuffle(pool)
        opts = [correct] + pool[:n]
        self.rnd.shuffle(opts)
        return opts, opts.index(correct)

    def test_block(self, key, lv):
        b, R, L = self.b, self.rnd, LEVELS[lv]
        qs = []
        if key == "kana":
            items = R.sample([x for x in b["kana"] if not any("ァ" <= c <= "ヶ" for c in x["jp"])], 3) + \
                R.sample([x for x in b["kana"] if any("ァ" <= c <= "ヶ" for c in x["jp"])], 3)
            for x in items:
                o, a = self.distract(x["jp"], x["wrong"])
                qs.append({"t": "q", "qt": "kana", "sec": key, "say": x["kana"], "text": "Как пишется это слово?",
                           "opts": o, "ans": a, "idk": True, "fb": f"<b>{esc(x['jp'])}</b>" +
                           (f" «{esc(azbuka.ru_read(x['kana']))}»" if azbuka.ru_read(x["kana"]) else "") + f"  {esc(x['ru'])}"})
        elif key == "vocab":
            pool = b["vocab"][L]
            for x in R.sample(pool, 4):
                o, a = self.distract(x["ru"], [y["ru"] for lvl in b["vocab"].values() for y in lvl])
                qs.append({"t": "q", "qt": "vocab", "sec": key, "lv": lv, "say": x["kana"],
                           "text": f"<b>{esc(x['kana'])}</b>\nЧто это значит?", "opts": o, "ans": a, "idk": True,
                           "fb": word_line(x["jp"], x["kana"], x["ru"]), "key": [x["jp"], x["kana"], x["ru"]]})
        elif key == "kmean":
            for x in R.sample(b["kanji_mean"][L], 4):
                o, a = self.distract(x["ru"], [y["ru"] for lvl in b["kanji_mean"].values() for y in lvl])
                qs.append({"t": "q", "qt": "kmean", "sec": key, "lv": lv, "img": kanji_pic(x["ch"]),
                           "text": f"<b>{esc(x['ch'])}</b>\nЧто значит этот кандзи?", "opts": o, "ans": a, "idk": True,
                           "fb": f"<b>{esc(x['ch'])}</b>  {esc(x['ru'])}\nон: {esc('、'.join(x['on'][:3]))} · "
                                 f"кун: {esc('、'.join(x['kun'][:3]))}",
                           "key": [x["ch"], "", x["ru"]]})
        elif key == "kread":
            for x in R.sample(b["kanji_read"][L], 4):
                o, a = self.distract(x["kana"], x["wrong"])
                qs.append({"t": "q", "qt": "kread", "sec": key, "lv": lv, "say": x["kana"], "after": True,
                           "text": f"<b>{esc(x['jp'])}</b>\nКак читается?", "opts": o, "ans": a, "idk": True,
                           "fb": word_line(x["jp"], x["kana"], x["ru"]), "key": [x["jp"], x["kana"], x["ru"]]})
        elif key == "dates":
            for kind in ["date_listen", "date_listen", "date_read", "date_read", "time_listen", "num_listen"]:
                q = drills.make(kind, R, b)
                qs.append({"t": "q", "sec": key, "idk": True, "voice": R.choice("fm"), **q})
        elif key == "listen":
            items = R.sample([x for x in b["listen"] if x["lvl"] <= 2], 6)
            for i, x in enumerate(items):
                o, a = self.distract(x["ru"], [y["ru"] for y in b["listen"] if y["lvl"] == x["lvl"]])
                mode = ["slow", "slow", "normal", "normal", "native", "native"][i]
                q = {"t": "q", "qt": f"listen_{mode}", "sec": key, "text": "Что сказали?", "opts": o, "ans": a,
                     "idk": True, "fb": self.phrase_fb(x)}
                if mode == "native" and self.native_ok(x):
                    q.update(path=self.native(x), fb=q["fb"] + f"\n<i>{self.credit(x)}</i>")
                else:
                    q.update(say=x["jp"], rate=tts.SLOW if mode == "slow" else tts.NORMAL)
                qs.append(q)
        elif key == "grammar":
            for x in b["grammar"]:
                filled = x["q"].replace("（　）", x["opts"][x["ans"]])
                o, a = self.distract(x["opts"][x["ans"]], x["opts"])
                qs.append({"t": "q", "qt": "grammar", "sec": key, "lv": 0 if x["lv"] == "N5" else 1,
                           "text": f"{esc(x['q'])}\nЧто вставить?", "opts": o, "ans": a, "idk": True,
                           "fb": f"{esc(filled)}  ({esc(x['ru'])})"})
        return qs

    def test_extend(self, s):
        """Конец блока: в адаптивной части либо уровень выше, либо следующая часть."""
        key, name, adaptive, _ = SECTIONS[s["sec"]]
        if adaptive:
            at = [ok for lv, ok in s["res"].get(key, []) if lv == s["lv"]]
            if len(at) >= 4 and sum(at) >= 3 and s["lv"] < len(LEVELS) - 1:
                s["lv"] += 1
                s["steps"] += [{"t": "msg", "text": f"Хорошо получается! Поднимаю уровень: <b>{LEVELS[s['lv']]}</b>."}] + \
                    self.test_block(key, s["lv"])
                return
        if key == "kana" and sum(ok for _, ok in s["res"].get("kana", [])) <= 2:
            s["steps"].append({"t": "msg", "btn": "Продолжить тест ▶", "alt": [("🌱 Начать с азбуки", "tr:start")], "text":
                               "Если японская азбука пока незнакома, это нормально 🙂 Можно начать с неё: 24 коротких "
                               "урока, от первых знаков до фраз «здравствуйте» и «спасибо». Или продолжить тест, он займёт "
                               "ещё минут десять."})
        s["sec"] += 1
        s["lv"] = 0
        if s["sec"] >= len(SECTIONS):
            s["steps"].append({"t": "end"})
        else:
            s["steps"] += self.section_steps(s["sec"], 0)

    def test_summary(self, res):
        lines, weak = [], []
        for key, name, adaptive, _ in SECTIONS:
            ans = res.get(key, [])
            if adaptive:
                parts, reached = [], None
                for lv, L in enumerate(LEVELS):
                    at = [ok for l, ok in ans if l == lv]
                    if at:
                        parts.append(f"{L} {sum(at)} из {len(at)}")
                        if sum(at) >= 3:
                            reached = L
                lines.append(f"{name}: {', '.join(parts)}" + (f" → уровень {reached}" if reached else " → ниже N5"))
                if not reached:
                    weak.append(name.lower())
            elif key == "listen":
                by = {}
                for q_ok, qt in zip([ok for _, ok in ans], ["медленно", "медленно", "обычно", "обычно", "носители", "носители"]):
                    by.setdefault(qt, []).append(q_ok)
                lines.append(f"{name}: " + ", ".join(f"{k} {sum(v)} из {len(v)}" for k, v in by.items()))
                if sum(by.get("носители", [])) < 2:
                    weak.append("живая речь")
            else:
                ok = sum(o for _, o in ans)
                lines.append(f"{name}: {ok} из {len(ans)}")
                if ans and ok / len(ans) < 0.67:
                    weak.append(name.lower())
        return lines, weak

    # ---------- урок «два чтения кандзи»
    def start_onkun(self, uid):
        r = self.resume(uid, "onkun")
        if r:
            return r
        ok = self.b["onkun"]
        steps = [{"t": "msg", "btn": "Начать ▶", "text":
                  "<b>Два чтения кандзи: где какое</b>\n\n"
                  "У большинства кандзи два вида чтения. Онное пришло из китайского, в словарях его пишут катаканой: 学 ガク. "
                  "Кунное японское, его пишут хираганой: 学ぶ まなぶ.\n\n"
                  "Подсказка, которая работает для большинства слов:\n"
                  "• кандзи один или с окончанием хираганой (食べる, 大きい, 山) → кунное\n"
                  "• два и больше кандзи подряд (食堂, 大学, 富士山) → онное\n\n"
                  "Исключения есть, их учат отдельно, они в конце урока. Слушай пары: один кандзи, два чтения."}]
        for p in ok["pairs"]:
            k, o = p["kun"], p["on"]
            steps.append({"t": "msg", "path": tts.concat([say_path(k["kana"]), say_path(o["kana"])]),
                          "slow": [k["kana"], o["kana"]],
                          "text": f"<b>{esc(p['ch'])}</b>\n\n{word_line(k['jp'], k['kana'], k['ru'], 'кунное чтение')}\n\n"
                                  f"{word_line(o['jp'], o['kana'], o['ru'], 'онное чтение')}"})
        for p in self.rnd.sample(ok["pairs"], 4):
            w = self.rnd.choice(["kun", "on"])
            x = p[w]
            steps.append({"t": "q", "qt": "onkun_kind", "text": f"<b>{esc(x['jp'])}</b>\nКакое здесь чтение?",
                          "opts": ["кунное", "онное"], "ans": 0 if w == "kun" else 1, "say": x["kana"], "after": True,
                          "fb": word_line(x["jp"], x["kana"], x["ru"], "кунное чтение" if w == "kun" else "онное чтение")})
        for p in ok["pairs"]:
            w = self.rnd.choice(["kun", "on"])
            x = p[w]
            o, a = self.distract(x["kana"], p[f"wrong_{w}"])
            steps.append({"t": "q", "qt": "onkun_read", "text": f"<b>{esc(x['jp'])}</b>\nКак читается?", "opts": o,
                          "ans": a, "say": x["kana"], "after": True,
                          "fb": word_line(x["jp"], x["kana"], x["ru"])})
        exc = ok["except"]
        steps.append({"t": "msg", "path": tts.concat([say_path(x["kana"]) for x in exc]), "slow": [x["kana"] for x in exc],
                      "text": "<b>Исключения</b>, их просто запоминают:\n\n" + "\n\n".join(
                          word_line(x["jp"], x["kana"], x["ru"] or "") for x in exc)})
        steps.append({"t": "end"})
        return self.begin(uid, "onkun", steps)

    # ---------- тренажёр дат и чисел
    def date_weights(self, uid):
        rows = self.st.q("select qt, ok from answers where uid=? order by ts desc limit 300", (uid,))
        w = {}
        for kind in DATE_KINDS:
            xs = [ok for qt, ok in rows if qt == kind or (kind == "weekday" and qt == "weekday_listen")][:20]
            w[kind] = 1 + 2 * (1 - (sum(xs) / len(xs) if xs else 0.5))
        return w

    def start_dates(self, uid):
        """📅 Даты: пока не пройдены уроки по шагам, следующий урок; потом тренажёр, 10 заданий вперемешку."""
        r = self.resume(uid, "dl")
        if r:
            return r
        cur = self.session(uid)
        if cur and cur["mode"] == "dl" and cur["i"] < len(cur["steps"]) - 1:
            return [Out("text", "Продолжаем урок 👌")] + self.render(uid)
        if self.get(uid, "dl_i", 0) < len(self.dc):
            return self.start_dl(uid, self.get(uid, "dl_i", 0))
        w = self.date_weights(uid)
        kinds = self.rnd.choices(DATE_KINDS, weights=[w[k] for k in DATE_KINDS], k=10)
        qs = [{"t": "q", "voice": self.rnd.choice("fm"), **drills.make(k, self.rnd, self.b)} for k in kinds]
        return self.begin(uid, "dates", qs + [{"t": "end"}])

    def start_dl(self, uid, i):
        return self.begin(uid, "dl", self.warm(uid) + self.dl_steps(i), di=i)

    def dl_skip(self, uid):
        """«Знаю, сразу тренажёр» на первом уроке дат: уроки считаются пройденными."""
        self.set(uid, "session", None)
        self.set(uid, "dl_i", len(self.dc))
        return [Out("text", "Хорошо, сразу тренажёр 👌 Шпаргалка всегда под кнопкой 📋 в конце.")] + self.start_dates(uid)

    def dl_card(self, x):
        note = f"\n<i>{esc(azbuka.annotate(x['note']))}</i>" if x["note"] else ""
        return f"<b>{esc(x['jp'])}</b> → <b>{esc(x['kana'])}</b>\n«{esc(x['cyr'])}», {esc(x['ru'])}{note}"

    def dl_steps(self, i):
        L = self.dc[i]
        steps = [{"t": "msg", "btn": "Начать ▶", "text": f"📅 <b>Даты, урок {i + 1} из {len(self.dc)}</b>\n"
                  f"<b>{esc(L['title'])}</b>\n\n{esc(azbuka.annotate(L['intro']))}",
                  "alt": [("🎯 Это я знаю, сразу тренажёр", "dl:skip")] if i == 0 else None}]
        for x in L["items"]:
            say = [p["kana"] for p in x["parts"]] if x.get("parts") else [x["kana"]]
            steps.append({"t": "msg", "text": self.dl_card(x), "path": self.join_audio(say), "slow": say,
                          "slow_cap": x["kana"]})
        steps.append({"t": "msg", "btn": "К вопросам ▶", "text": "Теперь проверим. Звук можно переслушать, а ошибка "
                      "вернётся в конце урока ещё раз."})
        return steps + self.dl_quiz(i) + [{"t": "end"}]

    def dl_quiz(self, i):
        """Каждое слово урока на слух (выбрать написание), до трёх «как читается?» и два слова прошлых уроков."""
        R = self.rnd
        words = lambda les: [p for x in les["items"] for p in (x.get("parts") or [x])]
        cur, prev = words(self.dc[i]), [p for L in self.dc[:i] for p in words(L)]
        alike = lambda x, pool: [p for p in pool if p["kind"] == x["kind"] and p["jp"] != x["jp"]]
        fb = lambda x: f"<b>{esc(x['jp'])}</b> → <b>{esc(x['kana'])}</b>\n«{esc(x['cyr'])}», {esc(x['ru'])}"

        def ear(x):
            o, a = self.pick_opts(x["jp"], [x.get("near", []), [p["jp"] for p in alike(x, cur)],
                                            [p["jp"] for p in alike(x, prev)]])
            return {"t": "q", "qt": "dl_ear", "text": "Что прозвучало?", "opts": o, "ans": a, "say": x["kana"],
                    "fb": fb(x)}

        def read(x):
            o, a = self.pick_opts(x["kana"], [x["wrong"], [p["kana"] for p in alike(x, cur)],
                                              [p["kana"] for p in alike(x, prev)]])
            return {"t": "q", "qt": "dl_read", "text": f"<b>{esc(x['jp'])}</b>\nКак читается?", "opts": o, "ans": a,
                    "say": x["kana"], "after": True, "fb": fb(x)}

        own = [x for x in self.dc[i]["items"] if not x.get("parts")]
        heard = own + R.sample([p for x in self.dc[i]["items"] for p in x.get("parts", [])],
                               min(3, sum(len(x.get("parts", [])) for x in self.dc[i]["items"])))
        reads = [x for x in own if x.get("wrong")]
        qs = [ear(x) for x in heard] + [read(x) for x in R.sample(reads, min(3, len(reads)))] + \
            [ear(x) for x in R.sample(prev, min(2, len(prev)))]
        R.shuffle(qs)
        return qs

    def cheat_steps(self):
        """Шпаргалка построчно: у строки свой голос, каждое слово отдельной медленной записью с паузой."""
        out = []
        for c in drills.cheat():
            paths = [say_path(k, "f", tts.SLOW) for _, k in c["words"]]
            text = (f"<b>{c['title']}</b>\n" if c["title"] else "") + azbuka.annotate(c["prefix"]) + \
                " · ".join(f"{a} {k}" + (f" [{azbuka.ru_read(k)}]" if azbuka.ru_read(k) else "") for a, k in c["words"]) + \
                (f"\n{azbuka.annotate(c['tail'])}" if c["tail"] else "")
            out.append({"t": "msg", "text": text,
                        "path": tts.concat(paths) if all(os.path.exists(p) for p in paths) else ""})
        return out

    def cheat(self):
        return [Out("voice" if self.audio_of(s) else "text", s["text"], self.audio_of(s)) for s in self.cheat_steps()]

    # ---------- слух: живые записи носителей
    def native(self, x):
        return f"{DATA}/audio/native/{x['audio_id']}.mp3"

    def native_ok(self, x):
        import os
        return os.path.exists(self.native(x))

    def phrase_fb(self, x):
        """Фраза носителя: как пишется, как читается (чтение из Tatoeba), перевод."""
        kana = x.get("kana") if x.get("kana") != x["jp"] else ""
        return f"<b>{esc(x['jp'])}</b>" + (f"\n<b>{esc(kana)}</b>" if kana else "") + f"\n{esc(x['ru'])}"

    def credit(self, x):
        return f"Голос: {esc(x['speaker'])}, Tatoeba, {esc(x['license'])}"

    def start_listen(self, uid):
        lvl = self.get(uid, "listen_lvl", 1)
        items = [x for x in self.b["listen"] if x["lvl"] == lvl]
        native = [x for x in items if self.native_ok(x)]
        other = [x for x in items if not self.native_ok(x)]
        self.rnd.shuffle(native)
        self.rnd.shuffle(other)
        qs = []
        for x in (native + other)[:8]:  # сначала живые голоса, не хватает записей, добираем синтезом
            same = [y["ru"] for y in items if y is not x]
            o, a = self.pick_opts(x["ru"], [[y for y in same if alike(y, x["ru"])], same])
            q = {"t": "q", "qt": "listen", "text": "Что сказали? Можно переслушать сколько угодно.", "opts": o, "ans": a,
                 "idk": True, "slow": x["jp"], "slow_cap": x.get("kana") or x["jp"], "fb": self.phrase_fb(x)}
            if self.native_ok(x):
                q.update(path=self.native(x), fb=q["fb"] + f"\n<i>{self.credit(x)}</i>")
            else:
                q.update(say=x["jp"])
            qs.append(q)
        head = {"t": "msg", "text": f"<b>На слух, уровень {lvl} из 3</b>\nЖивые голоса носителей. Не разобрать, "
                                    "жми «🐢 Медленно»: та же фраза медленно синтезом. Потом снова слушай оригинал."}
        return self.begin(uid, "listen", [head] + qs + [{"t": "end"}], lvl=lvl)

    # ---------- уроки кандзи
    def lessons(self):
        return self.b["course"]["lessons"]

    def placement(self, uid):
        """С какого урока начать: уровень, где тест показал уверенные и значения, и чтения кандзи, пропускаем.
        Отдаёт (номер урока, пройдены ли все уровни курса)."""
        ts, res = self.last_result(uid)
        start = 0
        for li, lv in enumerate(LEVELS):
            first = next((i for i, x in enumerate(self.lessons()) if x["lvl"] == lv), None)
            if first is None:
                return start, True
            start = first
            if not res or not all(sum(o for l, o in res["res"].get(sec, []) if l == li) >= 3 for sec in ("kmean", "kread")):
                return start, False
        return start, True

    def course_pos(self, uid):
        i = self.get(uid, "course_i")
        if i is None:
            i = self.placement(uid)[0]
            self.set(uid, "course_i", i)
        return i

    def kanji_card(self, ch):
        x = self.b["course"]["kanji"][ch]
        lines = [f"<b>{esc(ch)}</b>  {x['emo']}  {esc(x['ru'])}"]
        if x["hook"]:
            lines.append(f"<i>{esc(azbuka.annotate(x['hook']))}</i>")
        for e in x["ex"]:
            lines += ["", word_line(e["jp"], e["kana"], e["ru"], KIND_RU[e["kind"]] + (" чтение" if e["kind"] != "exc" else ""))]
        return "\n".join(lines)

    def card_audio(self, ch):
        """Оба слова-примера одной записью (готовит сборка). Нет озвучки, карточка уйдёт текстом."""
        paths = [p for p in (say_path(e["kana"]) for e in self.b["course"]["kanji"][ch]["ex"]) if os.path.exists(p)]
        if len(paths) < 2:
            return paths[0] if paths else ""
        try:
            return tts.concat(paths)
        except Exception:
            return paths[0]

    def word_fb(self, e):
        return word_line(e["jp"], e["kana"], e["ru"], "исключение" if e["kind"] == "exc" else "")

    def start_lesson(self, uid, idx=None):
        """📚 Урок: кандзи и грамматика по очереди. idx: повтор конкретного урока кандзи (кнопка ls:N)."""
        start = self.get(uid, "track") == "start"
        if idx is None:
            for mode in (("abc",) if start else ()) + ("lesson", "grammar"):
                r = self.resume(uid, mode)
                if r:
                    return r
            if self.need_choice(uid):
                return self.welcome(uid)
            if start and self.abc_pos(uid) < len(self.abc()):
                return self.start_abc(uid, self.abc_pos(uid))
        kind = "kanji" if idx is not None else self.next_kind(uid)
        if kind == "kanji" and not self.get(uid, "onkun_done"):
            return [Out("text", "Перед кандзи короткий вводный урок: как понять, какое у кандзи чтение. Потом уроки "
                                "кандзи и грамматики пойдут по очереди.")] + self.start_onkun(uid)
        if idx is not None:
            return self.begin(uid, "lesson", self.warm(uid) + self.lesson_steps(idx), les=idx)
        if kind == "grammar":
            return self.start_grammar(uid, self.gram_pos(uid))
        if kind == "kanji":
            i = self.course_pos(uid)
            return self.begin(uid, "lesson", self.warm(uid) + self.lesson_steps(i), les=i)
        return [Out("text", "🎉 Все уроки кандзи и грамматики пройдены! Дальше 🔁 повторение и тренажёры: так знания "
                            "не забудутся.")]

    def next_kind(self, uid):
        k = self.course_pos(uid) < len(self.lessons())
        g = self.gram_pos(uid) < len(self.gcourse())
        if k and g:
            return "grammar" if self.get(uid, "last_lesson") == "kanji" else "kanji"
        return "kanji" if k else "grammar" if g else None

    # ---------- уроки грамматики
    def gcourse(self):
        return self.b.get("gcourse", [])

    def gram_placement(self, uid):
        """Грамматика с урока 1; если в тесте верны все 4 вопроса N5 (は, を, で, ました), с урока 10."""
        ts, res = self.last_result(uid)
        n5 = [ok for lv, ok in (res or {}).get("res", {}).get("grammar", []) if lv == 0]
        return 9 if len(n5) == 4 and all(n5) and len(self.gcourse()) > 9 else 0

    def gram_pos(self, uid):
        i = self.get(uid, "gram_i")
        if i is None:
            i = self.gram_placement(uid)
            self.set(uid, "gram_i", i)
        return i

    def start_grammar(self, uid, i):
        if i >= len(self.gcourse()):
            return [Out("text", "Уроки грамматики пройдены 🎉")]
        return self.begin(uid, "grammar", self.warm(uid) + self.grammar_steps(i), gi=i)

    def grammar_steps(self, i):
        g, R = self.gcourse()[i], self.rnd
        title, rule = azbuka.annotate(g["title"] + "\n\n" + "\n".join(g["rule"])).split("\n\n", 1)  # чтение раз на урок
        steps = [{"t": "msg", "btn": "Начать ▶", "text":
                  f"📖 <b>Грамматика, урок {g['n']} из {len(self.gcourse())}</b>\n<b>{esc(title)}</b>\n\n{esc(rule)}"}]
        steps += [{"t": "msg", "say": x["jp"], "text": jp_line(x["jp"], x["ru"])} for x in g["ex"]]
        qs = []
        for x in g["q"]:
            o, a = self.distract(x["ans"], x["wrong"])
            qs.append({"t": "q", "qt": "g_fill", "text": f"<b>{esc(x['blank'])}</b>\n<i>{esc(x['ru'])}</i>\n\nЧто вставить?",
                       "opts": o, "ans": a, "say": x["full"], "after": True, "fb": jp_line(filled(x), src=filled(x, " "))})
        for x in g["tr"]:
            o, a = self.distract(x["opts"][0], x["opts"][1:])
            qs.append({"t": "q", "qt": "g_tr", "text": f"Как сказать по-японски:\n<b>{esc(x['ru'])}</b>", "opts": o,
                       "ans": a, "say": x["opts"][0], "after": True, "fb": jp_line(x["opts"][0])})
        for x in R.sample(g["ex"], min(2, len(g["ex"]))):
            o, a = self.distract(x["ru"], [y["ru"] for y in g["ex"]])
            qs.append({"t": "q", "qt": "g_listen", "text": "Что сказали?", "opts": o, "ans": a, "say": x["jp"],
                       "fb": jp_line(x["jp"], x["ru"])})
        R.shuffle(qs)
        return steps + qs + [{"t": "end"}]

    def lesson_steps(self, i):
        C, les, R = self.b["course"]["kanji"], self.lessons()[i], self.rnd
        ks = les["kanji"]
        seen = [c for x in self.lessons()[:i + 1] if x["lvl"] == les["lvl"] for c in x["kanji"]]
        words = [(ch, e) for ch in ks for e in C[ch]["ex"]]
        steps = [{"t": "msg", "btn": "Начать ▶", "text":
                  f"<b>Урок {les['n']} из {len(self.lessons())} · {les['lvl']}</b>\nКандзи: {' '.join(ks)}\n\n"
                  "Сначала познакомимся с каждым: значение, подсказка и слова, где видно, какое чтение где. "
                  "Потом вопросы. Ошибёшься, не страшно: вопрос вернётся в конце урока."}]
        steps += [{"t": "msg", "text": self.kanji_card(ch), "path": self.card_audio(ch), "slow": [e["kana"] for e in C[ch]["ex"]],
                   "img": kanji_pic(ch)} for ch in ks]
        qs, rest = [], []
        for ch in ks:
            mates = lambda xs: [C[c]["ru"] for c in xs if c != ch and not similar(C[c]["ru"], C[ch]["ru"])]
            o, a = self.pick_opts(C[ch]["ru"], [mates(ks), mates(seen)])  # сначала кандзи этого урока
            qs.append({"t": "q", "qt": "c_mean", "text": f"<b>{esc(ch)}</b>\nЧто значит?", "opts": o, "ans": a, "img": kanji_pic(ch),
                       "fb": f"<b>{esc(ch)}</b>  {esc(C[ch]['ru'])}"})
        for n, ch in enumerate(ks):
            e = C[ch]["ex"][n % len(C[ch]["ex"])]  # то кунное слово, то онное
            o, a = self.distract(e["kana"], e["wrong"])
            rest.append({"t": "q", "qt": "c_read", "text": f"<b>{esc(e['jp'])}</b>\nКак читается?", "opts": o, "ans": a,
                         "say": e["kana"], "after": True, "fb": self.word_fb(e)})
        pool = [(ch, e) for ch, e in words if e["kind"] != "exc"]
        for ch, e in R.sample(pool, min(2, len(pool))):
            why = ("Кандзи один или с окончанием хираганой: кунное." if e["kind"] == "kun" else
                   "Кандзи в составе слова: онное." if len(e["jp"]) > 1 else
                   "Кандзи один, но это слово читается по онному. Такие слова просто запоминают.")
            rest.append({"t": "q", "qt": "c_kind", "text": f"<b>{esc(e['jp'])}</b>\nКак здесь читается {esc(ch)}?",
                         "opts": ["кунное", "онное"], "ans": 0 if e["kind"] == "kun" else 1, "say": e["kana"],
                         "after": True, "fb": f"{self.word_fb(e)}\n{why}"})
        uniq = list({e["jp"]: e for _, e in words}.values())
        for e in R.sample(uniq, min(2, len(uniq))):
            o, a = self.distract(e["jp"], [x["jp"] for x in uniq])
            rest.append({"t": "q", "qt": "c_listen", "text": "Какое слово прозвучало?", "opts": o, "ans": a,
                         "say": e["kana"], "fb": self.word_fb(e)})
        ch = R.choice(ks)
        o, a = self.distract(ch, ks)
        rest.append({"t": "q", "qt": "c_rev", "text": f"Как пишется «{esc(C[ch]['ru'])}»?", "opts": o, "ans": a,
                     "fb": f"<b>{esc(ch)}</b>  {esc(C[ch]['ru'])}"})
        R.shuffle(rest)
        return steps + qs + rest + [{"t": "end"}]

    # ---------- азбука с нуля: хирагана, катакана и первые фразы
    def abc(self):
        return self.b.get("abc", {}).get("lessons", [])

    def akana(self):
        return self.b.get("abc", {}).get("kana", {})

    def abc_pos(self, uid):
        i = self.get(uid, "abc_i")
        if i is None:
            i = 0
            self.set(uid, "abc_i", 0)
        return i

    def need_choice(self, uid):
        """Новичок ещё не выбрал, с чего начать: ни выбора, ни уроков, ни ответов."""
        return self.get(uid, "track") is None and self.get(uid, "course_i") is None and \
            self.get(uid, "abc_i") is None and not self.st.q("select 1 from answers where uid=? limit 1", (uid,))

    def menu_for(self, uid):
        return MENU_START if self.get(uid, "track") == "start" else MENU

    def join_audio(self, texts):
        """Несколько записей одной: знак и слово-пример. Нет записи, карточка уйдёт текстом."""
        paths = [say_path(t) for t in texts]
        if not paths or not all(os.path.exists(p) for p in paths):
            return ""
        if len(paths) == 1:
            return paths[0]
        try:
            return tts.concat(paths)
        except Exception:
            return paths[0]

    def abc_card(self, c):
        w = c.get("word")
        word = f"\n\nСлово: <b>{esc(w['jp'])}</b> «{esc(w['cyr'])}», {esc(w['ru'])}" if w else ""
        if c["type"] == "kana":
            return f"{c['emo']} <b>{esc(c['ch'])}</b> читается «{c['cyr']}»\n<i>{esc(c['hint'])}</i>{word}"
        if c["type"] == "group":
            return f"<b>{' '.join(x['ch'] for x in c['items'])}</b>\n\n" + "\n".join(
                f"{x['emo']} <b>{esc(x['ch'])}</b> «{x['cyr']}». {esc(x['hint'])}" for x in c["items"])
        if c["type"] == "row":
            return f"{c['emo']} <b>{' '.join(c['ch'])}</b>\n«{' '.join(c['cyrs'])}»\n<i>{esc(c['hint'])}</i>{word}"
        return f"{c['emo']} <b>{esc(c['title'])}</b>\n{esc(azbuka.annotate(c['text']))}{word}"

    def phrase_text(self, x, note=False):
        tip = azbuka.annotate(x["note"][:1].upper() + x["note"][1:]) if note and x["note"] else ""
        return f"<b>{esc(x['jp'])}</b>\n«{esc(x['cyr'])}»\n{esc(x['ru'])}" + (f"\n<i>{esc(tip)}</i>" if tip else "")

    def start_abc(self, uid, i):
        if i >= len(self.abc()):
            return [Out("text", "Азбука уже пройдена 🎉")]
        return self.begin(uid, "abc", self.warm(uid) + self.abc_steps(i), ai=i)

    def abc_steps(self, i):
        L = self.abc()[i]
        icon = "💬" if L["kind"] == "p" else "🔤"
        steps = [{"t": "msg", "btn": "Начать ▶", "text": f"🌱 <b>Урок {i + 1} из {len(self.abc())}</b>\n{icon} "
                  f"<b>{esc(L['title'])}</b>\n\n{esc(L['intro'])}"}]
        if L["kind"] == "p":
            steps += [{"t": "msg", "say": x["jp"], "text": self.phrase_text(x, True)} for x in L["phrases"]]
            steps.append({"t": "msg", "btn": "К вопросам ▶", "text": "Теперь проверим. Не торопись: фразу можно "
                          "переслушать, а ошибка вернётся в конце урока ещё раз."})
            steps += self.phrase_quiz(i)
        else:
            steps += [{"t": "msg", "text": self.abc_card(c), "img": c.get("img"), "path": self.join_audio(c["say"]),
                       "slow": c["say"], "slow_cap": " · ".join(c["say"])} for c in L["cards"]]
            steps += self.kana_quiz(i)
        return steps + [{"t": "end"}]

    def pick_opts(self, correct, pools, key=lambda x: x, n=4):
        """Верный вариант и неверные по порядку пулов (сначала похожие), без повторов по key; перемешано."""
        opts, seen = [correct], {key(correct)}
        for pool in pools:
            pool = list(pool)
            self.rnd.shuffle(pool)
            for x in pool:
                if len(opts) == n:
                    break
                if key(x) not in seen:
                    seen.add(key(x))
                    opts.append(x)
        self.rnd.shuffle(opts)
        return opts, opts.index(correct)

    def kana_quiz(self, i):
        """Как читается знак, какой знак прозвучал, как читается и что значит слово; два знака прошлых уроков."""
        L, R, K = self.abc()[i], self.rnd, self.akana()
        learned = [c for c, x in K.items() if x["les"] <= i]
        new = L["kana"]
        cyr = lambda c: K[c]["cyr"]

        def read(ch):
            o, a = self.pick_opts(ch, [[c for c in K[ch]["conf"] if c in learned], new, learned], key=cyr)
            return {"t": "q", "qt": "abc_read", "text": f"<b>{esc(ch)}</b>\nКак читается этот знак?",
                    "opts": [cyr(c) for c in o], "ans": a, "say": K[ch]["say"], "after": True,
                    "fb": f"<b>{esc(ch)}</b> «{cyr(ch)}»"}

        first = [read(ch) for ch in (new if len(new) <= 10 else R.sample(new, 6))]  # каждый новый знак хоть раз
        kata = L["kind"] == "k"  # в катакане знаков вдвое больше, остальных вопросов меньше
        rest = []
        for ch in R.sample(new, min(3, len(new))):
            o, a = self.pick_opts(ch, [[c for c in K[ch]["conf"] if c in learned], new, learned], key=cyr)
            rest.append({"t": "q", "qt": "abc_ear", "text": "Какой знак прозвучал?", "opts": o, "ans": a,
                         "say": K[ch]["say"], "fb": f"<b>{esc(ch)}</b> «{cyr(ch)}»"})
        words = L["words"]
        fb = lambda w: f"<b>{esc(w['jp'])}</b> «{esc(w['cyr'])}», {esc(w['ru'])}"
        for w in R.sample(words, min(5 if not new else 2 if kata else 3, len(words))):
            o, a = self.pick_opts(w["cyr"], [[x["cyr"] for x in w["wrong"]]])
            rest.append({"t": "q", "qt": "abc_word", "text": f"<b>{esc(w['jp'])}</b>\nКак читается слово?", "opts": o,
                         "ans": a, "say": w["jp"], "after": True, "fb": fb(w)})
        if kata or not new:  # катакана и маленькие знаки: слово на слух среди похожих написаний
            for w in R.sample(words, min(2 if kata else 3, len(words))):
                o, a = self.pick_opts(w["jp"], [[x["jp"] for x in w["wrong"]]])
                rest.append({"t": "q", "qt": "abc_wear", "text": "Какое слово прозвучало?", "opts": o, "ans": a,
                             "say": w["jp"], "fb": fb(w)})
        w = R.choice([x for x in words if x["card"]] or words)
        older = [x["ru"] for y in self.abc()[:i + 1] for x in y["words"] if not similar(x["ru"], w["ru"])]
        o, a = self.pick_opts(w["ru"], [[x["ru"] for x in words if not similar(x["ru"], w["ru"])], older])
        rest.append({"t": "q", "qt": "abc_mean", "text": f"<b>{esc(w['jp'])}</b> «{esc(w['cyr'])}»\nЧто значит это слово?",
                     "opts": o, "ans": a, "say": w["jp"], "after": True, "fb": fb(w)})
        prev = [c for c in learned if K[c]["les"] < i]
        same = [c for c in prev if azbuka.is_kata(c) == azbuka.is_kata((new or ["あ"])[0])]
        rest += [read(ch) for ch in R.sample(same or prev, min(1 if kata else 2, len(same or prev)))]
        R.shuffle(rest)
        return first + rest

    def phrase_quiz(self, i):
        """Сначала узнать на слух, потом сказать самой: «как сказать…» и «что скажешь, если…»."""
        L, R = self.abc()[i], self.rnd
        older = [x for y in self.abc()[:i] for x in y["phrases"]]
        opt = lambda x: f"{x['jp']} ({x['cyr']})"
        ear, say = [], []
        for x in L["phrases"]:  # переводы фраз разные по построению (проверяет сборка)
            others = [y for y in L["phrases"] if y is not x]
            far = older
            o, a = self.pick_opts(x["ru"], [[y["ru"] for y in others], [y["ru"] for y in far]])
            ear.append({"t": "q", "qt": "phr_ear", "text": "Что сказали? Можно переслушать.", "opts": o, "ans": a,
                        "say": x["jp"], "fb": self.phrase_text(x)})
            o, a = self.pick_opts(opt(x), [[opt(y) for y in others], [opt(y) for y in far]])
            ask = f"Как сказать: <b>«{esc(x['ru'])}»</b>" + ("" if x["ru"][-1:] in "?!" else "?")
            say.append({"t": "q", "qt": "phr_say", "text": ask, "opts": o, "ans": a,
                        "say": x["jp"], "after": True, "fb": self.phrase_text(x)})
        for sit in L["sits"]:  # варианты только из этого урока: фразы прошлых уроков могут тоже подойти
            x = next(y for y in L["phrases"] if y["jp"] == sit["jp"])
            o, a = self.pick_opts(opt(x), [[opt(y) for y in L["phrases"] if y is not x]])
            ask = esc(sit["text"]) + ("" if sit["text"].endswith("?") else ". Что скажешь?")
            say.append({"t": "q", "qt": "phr_sit", "text": ask, "opts": o, "ans": a,
                        "say": x["jp"], "after": True, "fb": self.phrase_text(x)})
        R.shuffle(ear)
        R.shuffle(say)
        return ear + say

    def abc_reviews(self, i):
        """Что из урока азбуки уходит в повторение: знаки, ряды, слова правил и фразы (ключ, jp, чтение, перевод)."""
        out = []
        for c in self.abc()[i]["cards"]:
            if c["type"] == "group":
                out += [(f"abc|{x['ch']}", x["ch"], x["ch"], x["cyr"]) for x in c["items"]]
            elif c["type"] == "rule":
                out.append((f"abc|{c['word']['jp']}", c["word"]["jp"], c["word"]["jp"], c["word"]["ru"]))
            elif c["type"] == "row":
                out.append((f"abc|{c['ch']}", c["ch"], c["ch"], " ".join(c["cyrs"])))
            else:
                out.append((f"abc|{c['ch']}", c["ch"], c["ch"], c["cyr"]))
        return out + [(f"phr|{x['jp']}", x["jp"], x["cyr"], x["ru"]) for x in self.abc()[i]["phrases"]]

    def abc_table(self, uid):
        """🔤 Азбука: в азбуке с нуля таблица выученных знаков, у остальных вся таблица с чтением."""
        K, done = self.akana(), self.get(uid, "abc_i") or 0
        if self.get(uid, "track") != "start":
            done = len(self.abc())
        got = {c for c, x in K.items() if x["les"] < done}
        if not got:
            return [Out("text", "🔤 Здесь будет твоя азбука: знаки появятся после первого урока. Жми «📚 Урок» 🙂")]
        parts = []
        for name, conv in (("Хирагана", lambda r: r), ("Катакана", azbuka.kata)):
            rows = [" · ".join(f"<b>{conv(c)}</b> {K[conv(c)]['cyr']}" for c in row if conv(c) in got)
                    for row, _ in azbuka.HIRA_ROWS if name == "Хирагана" or row in azbuka.BASIC]
            rows = [r for r in rows if r]
            if rows:
                parts.append(f"<b>{name}</b>\n" + "\n".join(rows))
        basic = sum(1 for c in got if c in azbuka.BASIC or c in azbuka.kata(azbuka.BASIC))
        head = f"🔤 <b>Твоя азбука</b>: {basic} {plural(basic, 'знак', 'знака', 'знаков')} из 92 (46 хираганы и 46 катаканы)" \
            if done < len(self.abc()) \
            else "🔤 <b>Азбука</b>: хирагана и катакана"
        txt = head + "\n\n" + "\n\n".join(parts)
        if done < len(self.abc()):
            txt += f"\n\nДальше урок {done + 1}: {esc(self.abc()[done]['title'])}."
        return [Out("text", txt)]

    def start_place(self, uid, stage):
        """Уровень за пару вопросов вместо длинного теста на входе: на ступень два вопроса, «не знаю» тоже ответ."""
        R, K, qs = self.rnd, self.akana(), []
        if stage in ("h", "k"):
            base = [c for c in K if len(c) == 1 and ("\u30a0" <= c <= "\u30ff") == (stage == "k")
                    and unicodedata.normalize("NFD", c) == c and "SMALL" not in unicodedata.name(c, "")]
            cyr = lambda c: K[c]["cyr"]
            for ch in R.sample(base, 2):
                o, a = self.pick_opts(ch, [[c for c in K[ch]["conf"] if c in base], base], key=cyr)
                qs.append({"t": "q", "qt": f"place_{stage}", "text": f"<b>{esc(ch)}</b>\nКак читается этот знак?",
                           "opts": [cyr(c) for c in o], "ans": a, "say": K[ch]["say"], "after": True, "idk": True,
                           "fb": f"<b>{esc(ch)}</b> «{cyr(ch)}»"})
        else:
            others = [y["ru"] for lv in self.b["kanji_mean"].values() for y in lv]
            for x in R.sample(self.b["kanji_mean"]["N" + stage], 2):
                o, a = self.distract(x["ru"], others)
                qs.append({"t": "q", "qt": f"place_{stage}", "text": f"<b>{esc(x['ch'])}</b>\nЧто значит этот кандзи?",
                           "opts": o, "ans": a, "idk": True, "img": kanji_pic(x["ch"]),
                           "fb": f"<b>{esc(x['ch'])}</b>  {esc(x['ru'])}"})
        head = {"h": "Пара вопросов, и начнём с нужного места. Сначала хирагана. «🤷 Не знаю» тоже ответ: так точнее.",
                "k": "✅ Хирагану знаешь. Теперь катакана, тоже два знака.",
                "5": "✅ И катакану. Теперь два кандзи уровня N5.",
                "4": "✅ Кандзи N5 знакомы. Последняя пара: кандзи N4."}[stage]
        return [Out("text", head)] + self.begin(uid, "place", qs + [{"t": "end"}], stage=stage)

    def place_next(self, uid, s):
        """Обе верно: следующая ступень. Иначе ученик встаёт на эту ступень курса."""
        st, ok = s["stage"], s["total"] and s["score"] == s["total"]
        if ok and st != PLACE[-1]:
            return self.start_place(uid, PLACE[PLACE.index(st) + 1])
        return self.place(uid, "top" if ok else {"h": "abc", "k": "kata", "5": "n5", "4": "n4"}[st])

    def place(self, uid, where):
        """Итог лесенки: место в курсе и план. Пройденное раньше не теряется: из двух мест берём дальнее."""
        A, L = self.abc(), self.lessons()
        self.set(uid, "placed", where)
        self.set(uid, "menu_v", MENU_V)
        if where in ("abc", "kata"):
            if where == "kata":
                k = next(i for i, x in enumerate(A) if x["kind"] == "k")
                self.set(uid, "abc_i", max(k, self.get(uid, "abc_i") or 0))
            outs = self.choose(uid, "start")
            if where == "kata" and outs and outs[0].kind == "text" and "Продолжаем азбуку" in outs[0].text:
                outs[0].text = ("✅ Хирагану ты знаешь, начнём с катаканы: до конца азбуки 6 уроков по 5-10 минут, "
                                "потом кандзи и грамматика.\n\n" + ABC_BUTTONS + "\n\n" + self.remind_line(uid))
            return outs
        n4 = next(i for i, x in enumerate(L) if x["lvl"] == "N4")
        self.set(uid, "track", "full")
        self.set(uid, "course_i", max({"n5": 0, "n4": n4, "top": n4}[where], self.get(uid, "course_i") or 0))
        head = {"n5": "✅ Кану ты знаешь, начинаем уроки кандзи N5 и грамматики по очереди, урок минут на десять. "
                      "У каждого кандзи крупная картинка, подсказка по частям и слова с обоими чтениями. "
                      "Если окажется легко, 📝 Тест подберёт место точнее.",
                "n4": "✅ Кандзи N5 знакомы, начинаем с уровня N4. Грамматика пойдёт с основ: если она лёгкая, "
                      "📝 Тест подберёт место точнее.",
                "top": "✅ Знакомы и кандзи N4, а уроки в курсе пока до N4. Начнём с N4, а точный уровень по кандзи, "
                       "грамматике и слуху покажет 📝 Тест, минут на пятнадцать."}[where]
        return [Out("text", f"{head}\n\n{FULL_BUTTONS}\n\n{self.remind_line(uid)}", menu=True)] + self.start_lesson(uid)

    def choose(self, uid, what):
        """Выбор старта: с нуля, знаю азбуку, тест. Прогресс при любом выборе сохраняется."""
        old = self.get(uid, "course_i") is not None or self.get(uid, "gram_i") is not None
        if what == "start":
            self.set(uid, "track", "start")
            i = self.abc_pos(uid)
            if i >= len(self.abc()):
                self.set(uid, "track", "full")
                return [Out("text", "Азбука у тебя уже пройдена 🎉 Внизу все кнопки.", menu=True)]
            txt = ("🌱 Начинаем с азбуки. Урок 5-10 минут: знак с картинкой и голосом, потом пара вопросов. Через неделю, "
                   "если по уроку в день, будет 20 знаков хираганы и фразы, чтобы поздороваться, поблагодарить, "
                   "извиниться и познакомиться.\n\n" + ABC_BUTTONS + "\n\n" + self.remind_line(uid)
                   if i == 0 else f"🌱 Продолжаем азбуку с урока {i + 1}.")
            if old:
                txt += "\n\nУроки кандзи и грамматики сохранены, вернуться к ним можно через /start."
            return [Out("text", txt, menu=True)] + self.start_lesson(uid)
        if self.get(uid, "track") != "full":
            self.set(uid, "track", "full")
        if what == "test":
            return [Out("text", "Внизу появились кнопки, они пригодятся после теста.", menu=True)] + self.start_test(uid)
        return [Out("text", "Хорошо! " + FULL_BUTTONS, menu=True),
                Out("text", "С чего начнём?", buttons=[[("📚 Урок", "m:lesson"), ("📝 Тест", "m:test")]])]

    # ---------- кана на слух
    def start_kana(self, uid):
        """10 слов, 5 хираганой и 5 катаканой: звучит слово, выбрать написание среди похожих."""
        kata = [x for x in self.b["kana_ear"] if re.search(r"[ァ-ヶ]", x["kana"])]
        hira = [x for x in self.b["kana_ear"] if not re.search(r"[ァ-ヶ]", x["kana"])]
        items = self.rnd.sample(hira, 5) + self.rnd.sample(kata, 5)
        self.rnd.shuffle(items)
        qs = []
        for x in items:
            o, a = self.distract(x["kana"], x["wrong"])
            rd = azbuka.ru_read(x["kana"])
            fb = f"<b>{esc(x['kana'])}</b>" + (f" «{esc(rd)}»" if rd else "") + \
                (f"  {esc(x['jp'])}" if x["jp"] != x["kana"] else "") + f"  {esc(x['ru'])}"
            qs.append({"t": "q", "qt": "kana_ear", "say": x["kana"], "text": "Как пишется то, что прозвучало?",
                       "opts": o, "ans": a, "fb": fb})
        head = [] if self.get(uid, "kana_seen") else [{"t": "msg", "btn": "Начать ▶", "text":
                "<b>Кана на слух</b>\n" + azbuka.annotate(
                    "Звучит слово, выбери, как оно пишется. Ловушки те же, что мешают понимать даты "
                    "и живую речь: долгие гласные (おばさん и おばあさん), маленькая っ (きて и きって), звонкие (か и が), "
                    "похожие ソ и ン, シ и ツ. Переслушивать можно сколько угодно.")}]
        self.set(uid, "kana_seen", True)
        return self.begin(uid, "kana", head + qs + [{"t": "end"}])

    # ---------- повторение
    def add_review(self, uid, jp, kana, ru, key=None):
        key = key or f"{jp}|{kana}"
        tomorrow = (self.today(uid) + dt.timedelta(days=1)).isoformat()
        self.st.run("insert or ignore into reviews values (?,?,?,?,?,?,?)", (uid, key, jp, kana, ru, 1, tomorrow))

    def due(self, uid):
        return self.st.q("select key, jp, kana, ru from reviews where uid=? and due<=? order by due, box",
                         (uid, self.today(uid).isoformat()))

    def warm(self, uid):
        """Разминка перед уроком: до WARMUP карточек, которым пора. Повторение встроено в урок: отдельную кнопку
        повторения легко забыть, и карточки ждут с опозданием.
        Стоит перед вступлением урока, чтобы не отрывать правило грамматики от примеров."""
        steps = [{"t": "card", "key": k, "jp": jp, "kana": kana, "ru": ru, "rev": True}
                 for k, jp, kana, ru in self.due(uid)[:WARMUP]]
        if steps:
            n = len(steps)
            steps[0]["head"] = f"🔁 <b>Разминка</b>: {n} {plural(n, 'карточка', 'карточки', 'карточек')} из повторения, потом урок."
        return steps

    def due_note(self, uid):
        """Конец урока: сколько ждёт повторения и кнопка к нему."""
        due = len(self.due(uid))
        return (f"\nЖдут повторения: {due}, за раз до {REVIEW_LIMIT}." if due else ""), \
            ([("🔁 Повторение", "m:review")] if due else [])

    def start_review(self, uid):
        d = self.due(uid)[:REVIEW_LIMIT]
        if not d:
            nxt = self.st.q("select min(due) from reviews where uid=?", (uid,))[0][0]
            return [Out("text", f"На сегодня всё повторено 👍 Следующие карточки придут "
                                f"{dt.date.fromisoformat(nxt).strftime('%d.%m')}." if nxt else
                        "Карточек пока нет. Они появятся после первого урока: всё новое попадает сюда само.")]
        steps = [{"t": "card", "key": k, "jp": jp, "kana": kana, "ru": ru} for k, jp, kana, ru in d] + [{"t": "end"}]
        return [Out("text", f"🔁 На сегодня {len(d)} {plural(len(d), 'карточка', 'карточки', 'карточек')}, это пара минут. "
                            "Вспомни ответ, открой и отметь, как вспомнилось: трудное вернётся раньше.")] + \
            self.begin(uid, "review", steps, again=[])

    def on_reveal(self, uid, nonce):
        s = self.session(uid)
        if not s or s["nonce"] != nonce:
            return self.stale(uid, s, "Уже открыто")
        c, n = s["steps"][s["i"]], s["nonce"]
        grades = [[("Не помню", f"g:{n}:0"), ("С трудом", f"g:{n}:1"), ("Помню", f"g:{n}:2")]]
        if c["key"] in self.abc_ix:  # азбука: картинка знака, голос, чтение
            x = self.abc_ix[c["key"]]
            path = self.join_audio(x["say"])
            btn = self.slow_btn({"slow": x["say"]}) if path else None
            img = [Out("photo", "", f"{IMG}/{x['img']}")] if x.get("img") else []
            return [Out("edit", target="markup", buttons=None)] + img + \
                [Out("voice" if path else "text", x["text"], path, grades + ([[btn]] if btn else []))]
        if not c["kana"] and c["jp"] in self.b["course"]["kanji"]:
            cap, path = self.kanji_card(c["jp"]), self.card_audio(c["jp"])
            slow = [e["kana"] for e in self.b["course"]["kanji"][c["jp"]]["ex"]]
        else:
            cap, path, slow = word_line(c["jp"], c["kana"], c["ru"]), say_path(c["kana"]) if c["kana"] else "", [c["kana"]]
        if path and not os.path.exists(path):
            path = ""
        btn = self.slow_btn({"slow": slow}) if path else None
        return [Out("edit", target="markup", buttons=None),
                Out("voice" if path else "text", cap, path, grades + ([[btn]] if btn else []))]

    def on_grade(self, uid, nonce, g):
        s = self.session(uid)
        if not s or s["nonce"] != nonce:
            return self.stale(uid, s, "Уже оценено")
        c = s["steps"][s["i"]]
        box = self.st.q("select box from reviews where uid=? and key=?", (uid, c["key"]))
        box = box[0][0] if box else 0
        box = 0 if g == 0 else box if g == 1 else min(MAX_BOX, box + 1)
        self.st.run("update reviews set box=?, due=? where uid=? and key=?",
                    (box, (self.today(uid) + dt.timedelta(days=INTERVALS[box])).isoformat(), uid, c["key"]))
        if s["mode"] == "review":  # разминка в уроке результат урока не меняет
            s["total"] += 1
            s["score"] += g > 0
        again = s.setdefault("again", [])
        if g == 0 and c["key"] not in again:  # забытое вернётся в конце повторения или урока
            again.append(c["key"])
            s["steps"].insert(len(s["steps"]) - 1, {k: v for k, v in c.items() if k != "head"})
        return [Out("edit", target="markup", buttons=None,
                    toast={0: "Ничего, вернётся в конце", 1: "Повторим пораньше", 2: "Отлично! 👍"}[g])] + self.advance(uid, s)

    # ---------- концовки
    def finish(self, uid, s):
        outs = self._finish(uid, s)
        if self.get(uid, "menu_v") != MENU_V:
            self.set(uid, "menu_v", MENU_V)
            outs = [Out("text", "Внизу новая кнопка ✍️ <b>Написать</b>: вопрос, пожелание или что неудобно. "
                                "Я передам автору бота.", menu=True)] + outs
        return outs

    def next_title(self, uid):
        """Что даст кнопка 📚 Урок в следующий раз."""
        if self.get(uid, "track") == "start" and self.abc_pos(uid) < len(self.abc()):
            i = self.abc_pos(uid)
            return f"🌱 урок {i + 1}: {esc(self.abc()[i]['title'])}"
        kind = self.next_kind(uid)
        if kind == "kanji":
            les = self.lessons()[self.course_pos(uid)]
            return f"📚 урок кандзи {les['n']}: {' '.join(les['kanji'])}"
        if kind == "grammar":
            g = self.gcourse()[self.gram_pos(uid)]
            return f"📖 грамматика, урок {g['n']}: {esc(g['title'])}"
        return ""

    def praise(self, s):
        r = s["score"] / s["total"] if s["total"] else 1
        return ("Отлично! 🎉" if r >= 0.9 else "Хороший результат 👍" if r >= 0.7 else
                "Урок пройден, и это главное 💪 Трудное вернётся в повторении: так и запоминается.")

    def lesson_done(self, uid, name, s, note, again):
        nxt = self.next_title(uid)
        line, rv = self.due_note(uid)
        txt = f"✅ <b>{name} пройден</b>: верно {s['score']} из {s['total']}. {self.praise(s)}\n{note}{line}"
        txt += f"\n\nДальше: {nxt}" if nxt else "\n\nЭто был последний урок курса."
        rows = [rv + ([("📚 Следующий урок", "m:lesson")] if nxt else []), [("↩️ Этот ещё раз", again)]]
        return [Out("text", txt, buttons=[r for r in rows if r])]

    def _finish(self, uid, s):
        self.set(uid, "session", None)
        self.st.run("insert or ignore into activity values(?,?)", (uid, self.today(uid).isoformat()))
        m = s["mode"]
        self.st.run("insert into done values(?,?,?,?,?,?)", (uid, dt.datetime.utcnow().isoformat(timespec="minutes"), m,
                    {"abc": s.get("ai"), "lesson": s.get("les"), "grammar": s.get("gi"), "dl": s.get("di"), "onkun": 0}.get(m),
                    s["score"], s["total"]))
        if m == "place":
            return self.place_next(uid, s)
        if m == "test":
            lines, _ = self.test_summary(s["res"])
            self.st.run("insert into results values(?,?,?)", (uid, dt.datetime.utcnow().isoformat(timespec="minutes"),
                                                              json.dumps({"res": s["res"], "lines": lines}, ensure_ascii=False)))
            for jp, kana, ru in s.get("miss", []):
                self.add_review(uid, jp, kana, ru)
            start, top = self.placement(uid)
            if self.get(uid, "course_i") is None or self.get(uid, "course_i") < start:  # назад по курсу не двигаем
                self.set(uid, "course_i", start)
            gstart = self.gram_placement(uid)
            if self.get(uid, "gram_i") is None or self.get(uid, "gram_i") < gstart:
                self.set(uid, "gram_i", gstart)
            good, weak = self.test_digest(s["res"])
            txt = "📝 <b>Тест пройден, спасибо!</b> Вот что получилось."
            if good:
                txt += "\n\n✅ <b>Уже хорошо</b>\n" + "\n".join(f"• {x['name']}: {x['detail']}" for x in good)
            if weak:
                txt += "\n\n⚠️ <b>Над этим поработаем</b>\n" + "\n".join(f"• {x['name']}: {x['detail']} → {x['tool']}" for x in weak)
            les = self.lessons()[min(self.get(uid, "course_i"), len(self.lessons()) - 1)]
            txt += "\n\n📚 <b>План</b>\n" + ("• сначала 🌱 азбука с нуля: знаки и первые фразы\n" if self.kana_new(s["res"]) else "")
            txt += f"• кандзи с урока {les['n']} ({les['lvl']})"
            if top:
                txt += ": N5 и N4 уже уверенно, уроков N3 пока нет, поэтому N4 для закрепления"
            if self.gcourse():
                txt += f"\n• грамматика с урока {self.get(uid, 'gram_i') + 1}"
            txt += "\n• каждый день 🔁 повторение и один 📚 урок, кандзи и грамматика по очереди"
            if s.get("miss"):
                txt += f"\n\nОшибки теста ({len(s['miss'])}) уже в 🔁 повторении."
            kana = [("🌱 Азбука", "tr:start")] if self.kana_new(s["res"]) else \
                [("🔤 Кана", "m:kana")] if any(x["key"] == "kana" for x in weak) else []
            btn = [("📚 Урок", "m:lesson")] + kana + [("📅 Даты", "m:dates")]
            return [Out("text", txt, buttons=[btn])]
        if m == "onkun":
            for p in self.b["onkun"]["pairs"]:
                for w in ("kun", "on"):
                    self.add_review(uid, p[w]["jp"], p[w]["kana"], p[w]["ru"])
            self.set(uid, "onkun_done", True)
            return self.lesson_done(uid, "Вводный урок", s, "Слова урока завтра придут в 🔁 повторение.", "m:onkun")
        if m == "abc":
            i = s["ai"]
            for key, jp, kana, ru in self.abc_reviews(i):
                self.add_review(uid, jp, kana, ru, key)
            if self.get(uid, "abc_i", 0) == i:
                self.set(uid, "abc_i", i + 1)
            last = i + 1 >= len(self.abc()) and self.get(uid, "track") == "start"
            if last:  # азбука пройдена: открываем всё, первым уроком грамматика «は и です»
                self.set(uid, "track", "full")
                if self.get(uid, "last_lesson") is None:
                    self.set(uid, "last_lesson", "kanji")
            what = "Фразы" if self.abc()[i]["kind"] == "p" else "Знаки"
            outs = self.lesson_done(uid, f"Урок {i + 1}", s, f"{what} урока завтра придут в 🔁 повторение.", f"la:{i}")
            if last:
                outs.append(Out("text", "🎉 <b>Азбука пройдена!</b> Хирагана, катакана и первые фразы позади: теперь ты "
                                        "прочитаешь почти любое слово, написанное каной.\n\nВнизу теперь все кнопки:\n"
                                        "📚 <b>Урок</b>: дальше грамматика и кандзи по очереди\n📅 <b>Даты</b>, "
                                        "🎧 <b>Слух</b>, 🔤 <b>Кана</b>: тренажёры по 10 заданий\n"
                                        "📝 <b>Тест</b>: можно проверить себя", menu=True))
            return outs
        if m == "lesson":
            i = s["les"]
            les = self.lessons()[i]
            for ch in les["kanji"]:
                self.add_review(uid, ch, "", self.b["course"]["kanji"][ch]["ru"])
            if self.get(uid, "course_i", 0) == i:
                self.set(uid, "course_i", i + 1)
            self.set(uid, "last_lesson", "kanji")
            return self.lesson_done(uid, f"Урок кандзи {les['n']}", s,
                                    f"Кандзи {' '.join(les['kanji'])} завтра придут в 🔁 повторение.", f"ls:{i}")
        if m == "grammar":
            i = s["gi"]
            g = self.gcourse()[i]
            for x in g["ex"][:2]:
                self.add_review(uid, x["jp"], x["jp"], x["ru"])
            if self.get(uid, "gram_i", 0) == i:
                self.set(uid, "gram_i", i + 1)
            self.set(uid, "last_lesson", "grammar")
            return self.lesson_done(uid, f"Урок грамматики {g['n']}", s,
                                    "Два примера урока завтра придут в 🔁 повторение.", f"lg:{i}")
        if m == "dl":
            i = s["di"]
            for x in self.dc[i]["items"]:
                if not x.get("parts"):
                    self.add_review(uid, x["jp"], x["kana"], x["ru"])
            if self.get(uid, "dl_i", 0) == i:
                self.set(uid, "dl_i", i + 1)
            txt = f"✅ <b>Даты, урок {i + 1} пройден</b>: верно {s['score']} из {s['total']}. {self.praise(s)}\n"
            line, rv = self.due_note(uid)
            if i + 1 < len(self.dc):
                txt += f"Слова урока завтра придут в 🔁 повторение.{line}\n\nДальше: 📅 урок {i + 2}: " \
                       f"{esc(self.dc[i + 1]['title'])}"
                return [Out("text", txt, buttons=[rv + [("📅 Следующий урок", f"dl:{i + 1}")],
                                                  [("↩️ Этот ещё раз", f"dl:{i}")]])]
            txt += f"\n🎉 Все уроки дат пройдены! Теперь кнопка 📅 Даты даёт тренажёр: 10 заданий вперемешку.{line}"
            return [Out("text", txt, buttons=[[("📅 Тренажёр", "m:dates"), ("📋 Шпаргалка", "m:cheat")]] + ([rv] if rv else []))]
        if m == "dates":
            err = s.get("err", {})
            worst = max(err, key=err.get) if err else None
            txt = f"📅 <b>Даты</b>: верно {s['score']} из {s['total']}. {self.praise(s)}"
            if worst and err[worst] >= 2:
                txt += f"\nТруднее всего: {DATE_RU.get(worst, worst)} ({err[worst]}). Такие задания будут попадаться чаще."
            return [Out("text", txt, buttons=[[("📅 Ещё 10", "m:dates"), ("📋 Шпаргалка", "m:cheat")],
                                              [("📖 Уроки дат", "dl:0")]])]
        if m == "listen":
            lvl = s["lvl"]
            if s["total"] and s["score"] / s["total"] >= 0.85 and lvl < 3:
                self.set(uid, "listen_lvl", lvl + 1)
                tail = f"\nВ следующий раз уровень {lvl + 1}: фразы длиннее."
            elif s["total"] and s["score"] / s["total"] <= 0.4 and lvl > 1:
                self.set(uid, "listen_lvl", lvl - 1)
                tail = f"\nВ следующий раз уровень {lvl - 1}, фразы покороче. Так быстрее привыкнешь к живой речи."
            else:
                tail = ""
            return [Out("text", f"🎧 <b>Слух</b>: верно {s['score']} из {s['total']}. {self.praise(s)}{tail}",
                        buttons=[[("🎧 Ещё", "m:listen")]])]
        if m == "kana":
            return [Out("text", f"🔤 <b>Кана на слух</b>: верно {s['score']} из {s['total']}. {self.praise(s)}",
                        buttons=[[("🔤 Ещё 10", "m:kana"), ("📋 Таблица азбуки", "m:abc")]])]
        if m == "review":
            left = len(self.due(uid))
            return [Out("text", f"🔁 <b>Повторение</b>: {s['total']} {plural(s['total'], 'карточка', 'карточки', 'карточек')}, "
                                f"вспомнилось {s['score']}." + (f"\nНа сегодня осталось ещё {left}." if left else
                                                                 "\nНа сегодня всё, молодец 👏"),
                        buttons=[([("🔁 Ещё повторение", "m:review")] if left else []) + [("📚 Урок", "m:lesson")]])]
        return [Out("text", "Готово 👍")]

    # ---------- прогресс и списки
    def last_result(self, uid):
        r = self.st.q("select ts, data from results where uid=? order by ts desc limit 1", (uid,))
        return (r[0][0], json.loads(r[0][1])) if r else (None, None)

    def kana_new(self, res):
        """Кана в тесте на уровне угадывания (0-1 из 6): лучше начать с азбуки. 2 из 6 бывает и у тех, кто кану
        знает, но путает на слух долготу и っ, им азбуку с нуля не предлагаем."""
        k = (res or {}).get("kana", [])
        return bool(k) and sum(ok for _, ok in k) <= 1

    def test_digest(self, res):
        """Итог теста простыми словами: что уже хорошо, что тренировать и какой кнопкой."""
        good, weak = [], []
        for key, name, adaptive, _ in SECTIONS:
            ans = res.get(key, [])
            if not ans:
                continue
            x = {"key": key, "name": SEC_RU[key], "tool": "🌱 азбука с нуля" if key == "kana" and self.kana_new(res) else SEC_TOOL[key]}
            if adaptive:
                best = None
                for lv, L in enumerate(LEVELS):
                    at = [ok for l, ok in ans if l == lv]
                    if at and sum(at) >= 3:
                        best = (L, sum(at), len(at))
                if best:
                    good.append({**x, "detail": f"уровень {best[0]} ({best[1]} из {best[2]})", "short": f"{x['name']} {best[0]}"})
                else:
                    at = [ok for l, ok in ans if l == 0]
                    weak.append({**x, "detail": f"{sum(at)} из {len(at)}"})
                continue
            ok, n = sum(o for _, o in ans), len(ans)
            detail = f"{ok} из {n}"
            fine = ok / n >= 0.67
            if key == "listen":
                native = sum(o for _, o in ans[4:])
                detail += f", живые голоса {native} из {len(ans[4:])}"
                fine = fine and native >= 1
            (good if fine else weak).append({**x, "detail": detail, "short": x["name"]})
        return good, weak

    def pos_text(self, i, items, fmt):
        if i is None:
            return "ещё не начаты"
        return "все пройдены" if i >= len(items) else f"дальше урок {i + 1} из {len(items)}{fmt(items[i])}"

    def progress(self, uid):
        L, G = self.lessons(), self.gcourse()
        txt = "📊 <b>Прогресс</b>\n\n"
        ai = self.get(uid, "abc_i")
        if ai is not None and ai < len(self.abc()):
            K = self.akana()
            got = [c for c, x in K.items() if x["les"] < ai]
            h = sum(1 for c in got if c in azbuka.BASIC)
            k = sum(1 for c in got if c in azbuka.kata(azbuka.BASIC))
            ph = sum(len(x["phrases"]) for x in self.abc()[:ai])
            txt += (f"🌱 <b>Азбука и первые фразы</b>: пройдено уроков {ai} из {len(self.abc())}\n"
                    f"• хирагана: {h} из 46\n• катакана: " + (f"{k} из 46" if k else "впереди") + f"\n• фраз: {ph}\n")
        if self.get(uid, "track") != "start":
            txt += ("🌱 Азбука и первые фразы пройдены ✅\n\n" if ai is not None and ai >= len(self.abc()) else
                    "\n" if ai is not None else "") + "📚 <b>Уроки</b>\n"
            txt += "• кандзи: " + self.pos_text(self.get(uid, "course_i"), L, lambda x: f" ({x['lvl']})") + "\n"
            if G:
                txt += "• грамматика: " + self.pos_text(self.get(uid, "gram_i"), G, lambda x: f": {esc(x['title'])}") + "\n"
        n_rev = self.st.q("select count(*) from reviews where uid=?", (uid,))[0][0]
        txt += f"\n🔁 <b>Повторение</b>: карточек {n_rev}, на сегодня {len(self.due(uid))}\n"
        d = {qt: (n, ok) for qt, n, ok in self.st.q("select qt, count(*), sum(ok) from answers where uid=? group by qt", (uid,))}
        dn = sum(d.get(k, (0, 0))[0] for k in DATE_KINDS + ["weekday_listen"])
        dok = sum(d.get(k, (0, 0))[1] for k in DATE_KINDS + ["weekday_listen"])
        tr = []
        dl = self.get(uid, "dl_i", 0)
        if dl:
            tr.append(f"• 📅 даты по шагам: пройдено уроков {dl} из {len(self.dc)}" if dl < len(self.dc) else
                      "• 📅 уроки дат пройдены ✅")
        if dn:
            tr.append(f"• 📅 даты: {round(100 * dok / dn)}% верно из {dn}")
        if "kana_ear" in d:
            tr.append(f"• 🔤 кана: {round(100 * d['kana_ear'][1] / d['kana_ear'][0])}% верно из {d['kana_ear'][0]}")
        if "listen" in d:
            tr.append(f"• 🎧 слух: {round(100 * d['listen'][1] / d['listen'][0])}% верно из {d['listen'][0]}, "
                      f"уровень {self.get(uid, 'listen_lvl', 1)} из 3")
        if tr:
            txt += "\n🎯 <b>Тренажёры</b>\n" + "\n".join(tr) + "\n"
        days = {r[0] for r in self.st.q("select day from activity where uid=?", (uid,))}
        d0, streak = self.today(uid), 0
        if d0.isoformat() not in days:
            d0 -= dt.timedelta(days=1)
        while d0.isoformat() in days:
            streak += 1
            d0 -= dt.timedelta(days=1)
        u = self.user(uid)
        txt += f"\n🔥 Дней подряд: {streak}. " + (f"Напоминание в {u['remind']} (UTC{u['tz']:+d})." if u["remind"] != "off"
                                                   else "Напоминание выключено.")
        ts, res = self.last_result(uid)
        if res:
            good, weak = self.test_digest(res["res"])
            txt += f"\n\n📝 Тест {ts[8:10]}.{ts[5:7]}: " + ("поработать над: " + ", ".join(x["name"] for x in weak)
                                                            if weak else "всё уверенно") + "."
        elif self.get(uid, "track") != "start":
            txt += "\n\n📝 Тест ещё не пройден: кнопка 📝 Тест."
        return [Out("text", txt)]

    def unfinished(self, uid):
        """Начатые и отложенные уроки: на каком шаге ученик остановился."""
        L, G, out = self.lessons(), self.gcourse(), []
        for s in [self.session(uid)] + [self.get(uid, f"paused_{m}") for m in LESSON_MODES + ("onkun",)]:
            if s and s["mode"] in LESSON_MODES + ("onkun",) and 0 < s["i"] < len(s["steps"]) - 1:
                m = s["mode"]
                name = (f"азбука, урок {s['ai'] + 1}" if m == "abc" else f"урок кандзи {L[s['les']]['n']}" if m == "lesson"
                        else f"грамматика, урок {G[s['gi']]['n']}" if m == "grammar"
                        else f"даты, урок {s['di'] + 1}" if m == "dl" else "«Два чтения»")
                out.append(f"{name} (шаг {s['i'] + 1} из {len(s['steps']) - 1})")
        return out

    def funnel(self):
        """Новички за последние 30 дней: сколько дошло до каждого шага."""
        cutoff = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        new = [u for u, c, r in self.st.q("select uid, created, role from users") if c >= cutoff and r != "owner"]
        if not new:
            return ""
        days = lambda u: len({d for (d,) in self.st.q("select day from activity where uid=? union "
                                                      "select day from studytime where uid=? and sec > 0", (u, u))})
        les = lambda u: self.st.q(f"select 1 from done where uid=? and mode in ({','.join('?' * len(LESSON_MODES))}) "
                                  "limit 1", (u, *LESSON_MODES))
        return (f"\n🧭 Новички за 30 дней: пришли {len(new)}, выбрали старт {sum(not self.need_choice(u) for u in new)}, "
                f"прошли урок {sum(bool(les(u)) for u in new)}, занимались 2 дня и больше {sum(days(u) >= 2 for u in new)}")

    def users_report(self, names=None):
        """Итоги учеников для владельца (/users). names: {uid: имя} бот подтягивает из Телеграма в момент запроса,
        в базе имён нет. Время занятий считает touch."""
        L, G, A = self.lessons(), self.gcourse(), self.abc()
        rows, active = [], 0
        for uid, role, label, created in self.st.q("select uid, role, label, created from users"):
            t0 = self.today(uid)
            days = sorted(r[0] for r in self.st.q("select day from activity where uid=? union "
                                                  "select day from studytime where uid=? and sec > 0", (uid, uid)))
            lines = [f"👤 <b>{esc((names or {}).get(uid) or label or str(uid))}</b>" + (" (владелец)" if role == "owner" else "")]
            if days:
                n = (t0 - dt.date.fromisoformat(days[-1])).days
                active += n < 7
                last = "сегодня" if n <= 0 else "вчера" if n == 1 else \
                    f"{days[-1][8:10]}.{days[-1][5:7]}, {n} {plural(n, 'день', 'дня', 'дней')} назад"
                lines.append(f"📅 С {created[8:10]}.{created[5:7]}: дней с занятиями {len(days)}, последний раз {last}")
            else:
                lines.append(f"📅 С {created[8:10]}.{created[5:7]}, занятий пока не было")
            spent = self.st.q("select day, sec from studytime where uid=? and sec > 0", (uid,))
            total = sum(x for _, x in spent)
            week = sum(x for d, x in spent if d > (t0 - dt.timedelta(days=7)).isoformat())
            if total >= 60:
                lines.append(f"⏱ Время ≈ {dur(total)}" + (f", за 7 дней {dur(week)}" if week < total else "") +
                             (f", в среднем {dur(total / len(spent))} в день занятий" if len(spent) > 1 else ""))
            got = dict(self.st.q("select mode, count(distinct n) from done where uid=? group by mode", (uid,)))
            parts = ([f"азбука {got['abc']} из {len(A)}"] if got.get("abc") else []) + \
                (["«Два чтения»"] if got.get("onkun") else []) + ([f"кандзи {got['lesson']}"] if got.get("lesson") else []) + \
                ([f"грамматика {got['grammar']}"] if got.get("grammar") else []) + \
                ([f"даты {got['dl']} из {len(self.dc)}"] if got.get("dl") else [])
            lines.append(f"✅ Пройдено уроков: {sum(got.get(m, 0) for m in LESSON_MODES + ('onkun',))}" +
                         (f" ({', '.join(parts)})" if parts else ""))
            if self.unfinished(uid):
                lines.append("⏸ Не закончено: " + ", ".join(self.unfinished(uid)))
            ci, gi = self.get(uid, "course_i") or 0, self.get(uid, "gram_i") or 0
            if self.need_choice(uid):
                nxt = "путь ещё не выбран (азбука с нуля или уроки)"
            elif self.get(uid, "track") == "start" and self.abc_pos(uid) < len(A):
                nxt = f"азбука, урок {self.abc_pos(uid) + 1} из {len(A)}"
            else:
                nxt = (f"кандзи {ci + 1} из {len(L)} ({L[ci]['lvl']})" if ci < len(L) else "кандзи все пройдены") + \
                    ((f", грамматика {gi + 1} из {len(G)}" if gi < len(G) else ", грамматика вся пройдена") if G else "")
            lines.append("▶️ Дальше по курсу: " + nxt)
            if self.get(uid, "placed"):
                lines.append("🧭 Старт по лесенке: " + {"abc": "азбука с нуля", "kata": "с катаканы", "n5": "кандзи N5",
                                                        "n4": "кандзи N4", "top": "выше N4, начал с N4"}[self.get(uid, "placed")])
            dr = dict(self.st.q("select mode, count(*) from done where uid=? and mode in ('dates', 'listen', 'kana') "
                                "group by mode", (uid,)))
            if dr:
                lines.append("🧩 Тренажёры: " + ", ".join(f"{ru} {dr[m]} {plural(dr[m], 'раз', 'раза', 'раз')}" for m, ru in
                                                         (("dates", "даты"), ("listen", "слух"), ("kana", "кана")) if m in dr))
            n, ok = self.st.q("select count(*), coalesce(sum(ok), 0) from answers where uid=?", (uid,))[0]
            if n:
                lines.append(f"🎯 Ответов {n}, верно {round(100 * ok / n)}%")
            n_rev = self.st.q("select count(*) from reviews where uid=?", (uid,))[0][0]
            lines.append(f"🔁 Карточек {n_rev}, ждут повторения {len(self.due(uid))}" if n_rev else "🔁 Карточек пока нет")
            ts, res = self.last_result(uid)
            if res:
                good, weak = self.test_digest(res["res"])
                parts = (["хорошо: " + ", ".join(x["short"] for x in good)] if good else []) + \
                    (["поработать: " + ", ".join(x["name"] for x in weak)] if weak else [])
                lines.append(f"📝 Тест {ts[8:10]}.{ts[5:7]}: " + "; ".join(parts))
            else:
                t = next((s for s in (self.session(uid), self.get(uid, "paused_test")) if s and s["mode"] == "test"), None)
                lines.append(f"📝 Тест начат и не закончен (шаг {t['i'] + 1})" if t else "📝 Тест не пройден")
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

    # ---------- напоминание
    def reminder(self, uid):
        if self.need_choice(uid):
            return [Out("text", "Привет! 👋 Японский ждёт. Начнём с азбуки с нуля или пара вопросов покажет, с какого места?",
                        buttons=[[("🌱 С нуля", "tr:start"), ("🔤 Кое-что знаю", "tr:check")]])]
        start = self.get(uid, "track") == "start"
        ts, res = self.last_result(uid)
        due, nxt = len(self.due(uid)), self.next_title(uid)
        lines = ["Привет! 👋 На сегодня:"]
        if due:
            lines.append(f"🔁 повторение: {due} {plural(due, 'карточка', 'карточки', 'карточек')}")
        if nxt:
            lines.append(nxt)
        if not start:
            lines.append("Если будет ещё 5 минут: 📅 даты или 🔤 кана.")
        btn = ([("🔁 Повторение", "m:review")] if due else []) + ([("📚 Урок", "m:lesson")] if nxt else [])
        if not start and self.get(uid, "abc_i") is None and self.kana_new((res or {}).get("res")):
            lines.append("🌱 Если азбука пока трудная, начни с нуля: по 5-10 знаков за урок, с картинкой и голосом.")
            btn.append(("🌱 Азбука", "tr:start"))
        if len(btn) < 2 and not start:
            btn.append(("📅 Даты", "m:dates"))
        return [Out("text", "\n".join(lines), buttons=[btn])]

    def welcome(self, uid):
        """Первое сообщение и /start: с чего начать. Прогресс при любом выборе сохраняется."""
        new = self.need_choice(uid)
        head = ("Привет! 👋 Я помогу учить японский: понемногу каждый день, с голосом, картинками и подсказками."
                if new else "С возвращением! 👋 Прогресс сохранится при любом выборе.")
        btn = [[("🌱 Ничего, начнём с нуля", "tr:start")], [("🔤 Кое-что знаю", "tr:check")]]
        if not new:
            btn.append([("▶ Продолжить", "m:lesson")])
        return [Out("text", f"{head}\n\nЧто ты уже знаешь по-японски?\n\n"
                            "🌱 <b>Ничего</b>: начнём с азбуки, за урок 5-10 знаков и первые фразы.\n"
                            "🔤 <b>Кое-что знаю</b>: пара вопросов покажет, с какого места начать.\n\n"
                            f"{self.remind_line(uid)}", buttons=btn)]

    def remind_line(self, uid):
        u = self.user(uid)
        zone = "по Москве" if not u or u["tz"] == 3 else f"(UTC{u['tz']:+d})"
        return (f"Напоминание приходит каждый день в {u['remind']} {zone}. Другое время: /time 8:30, часовой пояс: "
                "/tz +3." if u and u["remind"] != "off" else "Напоминание выключено, включить: /time 09:00.")

    def fallback(self, uid, m=None):
        """Текст вместо кнопки, голосовое или картинка без ✍️: предлагаем передать автору."""
        if self.need_choice(uid):
            return self.welcome(uid)
        if not m:
            return [Out("text", "Я понимаю только кнопки внизу 🙂 Если их не видно, нажми /menu.", menu=True)]
        if m.get("group") and m["group"] == self.get(uid, "fb_group"):
            return []  # альбом: предлагаем один раз
        self.set(uid, "fb_group", m.get("group"))
        self.set(uid, "fb_draft", {**m, "ctx": self.where(uid)})
        return [Out("text", "Я понимаю только кнопки внизу 🙂 Если это вопрос или пожелание, передам автору бота. "
                            "Кнопок не видно: /menu.", buttons=[[("✍️ Передать автору", "fb:send")]])]

    # ---------- ✍️ Написать: вопросы и пожелания автору
    def where(self, uid):
        """Где был ученик, когда написал: режим, урок, шаг. Чтобы отзыв было к чему привязать."""
        s = self.session(uid)
        if not s:
            return ""
        parts = [MODE_RU.get(s["mode"], s["mode"])]
        num = {"lesson": lambda: self.lessons()[s["les"]]["n"], "grammar": lambda: self.gcourse()[s["gi"]]["n"],
               "abc": lambda: s["ai"] + 1, "dl": lambda: s["di"] + 1}.get(s["mode"])
        try:
            parts[0] += f" {num()}" if num else ""
        except (KeyError, IndexError):
            pass
        step = s["steps"][min(s["i"], len(s["steps"]) - 1)]
        if step["t"] == "end":
            parts.append("закончен")
        elif step["t"] == "card":
            parts.append(f"карточка {step['jp']} {step['kana']}".strip())
        else:
            first = html.unescape(re.sub(r"<[^>]+>", "", step.get("text") or "")).strip().split("\n")[0]
            parts.append(f"«{first[:60]}»" + (f", ответ {step['opts'][step['ans']]}" if step["t"] == "q" else ""))
        return " · ".join(parts)

    def live(self, uid):
        s = self.session(uid)
        return bool(s) and s["steps"][min(s["i"], len(s["steps"]) - 1)]["t"] != "end"

    def ask_feedback(self, uid):
        self.set(uid, "fb_wait", {"ts": dt.datetime.utcnow().isoformat(), "ctx": self.where(uid), "n": 0})
        return [Out("text", "✍️ Напиши одним сообщением вопрос, пожелание или что неудобно. Можно голосовым или "
                            "скриншотом. Я передам автору бота.", buttons=[[("Отмена", "fb:no")]])]

    def save_feedback(self, uid, m, ctx):
        """В базу текст, файл в Телеграме и где был ученик; имени нет. Out «fb» бот превращает в сообщение владельцу."""
        cur = self.st.db.execute("insert into feedback (uid, ts, kind, text, file_id, ctx, chat, mid) values (?,?,?,?,?,?,?,?)",
                                 (uid, dt.datetime.utcnow().isoformat(timespec="seconds"), m["kind"], m.get("text", ""),
                                  m.get("file_id"), ctx, m.get("chat"), m.get("mid")))
        self.st.db.commit()
        self.set(uid, "fb_wait", {"ts": dt.datetime.utcnow().isoformat(), "ctx": ctx, "n": 1})
        return Out("fb", str(cur.lastrowid))

    def fb_thanks(self, uid):
        txt = "Записал 👌" if uid == self.owner() else "Спасибо! Передал автору 🙏"
        return [Out("text", f"{txt} Можно дописать или прислать скриншот следом.",
                    buttons=[[("▶ Продолжить", "fb:back")]] if self.live(uid) else None)]

    def take_feedback(self, uid, m):
        """Сообщение после ✍️ Написать. None, если ученик ничего не собирался писать."""
        w = self.get(uid, "fb_wait")
        if not w:
            return None
        if (dt.datetime.utcnow() - dt.datetime.fromisoformat(w["ts"])).total_seconds() > (FB_MORE if w["n"] else FB_WAIT):
            self.set(uid, "fb_wait", None)
            return None
        out = self.save_feedback(uid, m, w["ctx"])
        return [out] if w["n"] else [out] + self.fb_thanks(uid)

    def on_fb(self, uid, what):
        if what == "send":
            d = self.get(uid, "fb_draft")
            if not d:
                return [Out("toast", toast="Уже передано 🙂")]
            self.set(uid, "fb_draft", None)
            return [Out("edit", target="markup", buttons=None), self.save_feedback(uid, d, d.get("ctx", ""))] + \
                self.fb_thanks(uid)
        self.set(uid, "fb_wait", None)
        self.set(uid, "fb_draft", None)  # ученик не стал передавать текст: не храним его
        if what == "back" and self.live(uid):
            return [Out("edit", target="markup", buttons=None)] + self.render(uid)
        return [Out("edit", target="markup", buttons=None), Out("toast", toast="Хорошо 👌")]

    # ---------- разбор нажатий: общий для бота и тестового прогона
    def on_callback(self, uid, data):
        kind, _, rest = data.partition(":")
        if kind != "fb" and self.get(uid, "fb_wait"):  # ученик нажал другое: ✍️ больше не ждём
            self.set(uid, "fb_wait", None)
        try:
            if kind == "fb":
                return self.on_fb(uid, rest)
            if kind == "n":
                return self.on_next(uid, int(rest))
            if kind == "a":
                nonce, k = rest.split(":")
                return self.on_answer(uid, int(nonce), int(k))
            if kind == "r":
                return self.on_reveal(uid, int(rest))
            if kind == "g":
                nonce, g = rest.split(":")
                return self.on_grade(uid, int(nonce), int(g))
            if kind == "sl":
                return self.on_slow(rest)
            if kind == "ls":
                return self.start_lesson(uid, int(rest))
            if kind == "lg":
                return self.start_grammar(uid, int(rest))
            if kind == "la":
                return self.start_abc(uid, int(rest))
            if kind == "dl":
                return self.dl_skip(uid) if rest == "skip" else self.start_dl(uid, int(rest))
            if kind == "tr" and rest == "check":
                return self.start_place(uid, PLACE[0])
            if kind == "tr" and rest in ("start", "full", "test"):
                return self.choose(uid, rest)
            if kind == "m":
                return self.on_menu(uid, {"test": "📝 Тест", "dates": "📅 Даты", "listen": "🎧 Слух", "kana": "🔤 Кана",
                                          "onkun": "🈶 Два чтения", "cheat": "cheat", "lesson": "📚 Урок",
                                          "review": "🔁 Повторение", "abc": "🔤 Азбука"}.get(rest, ""))
        except (ValueError, IndexError):
            pass
        return [Out("toast", toast="Эта кнопка уже устарела 🙂")]

    def on_menu(self, uid, text):
        if text == "cheat":
            return self.cheat()
        if text == "✍️ Написать":
            return self.ask_feedback(uid)
        actions = {"📚 Урок": self.start_lesson, "📝 Тест": self.start_test, "📅 Даты": self.start_dates,
                   "🎧 Слух": self.start_listen, "🔤 Кана": self.start_kana,
                   "📅 Даты и числа": self.start_dates, "🎧 Слушать": self.start_listen,  # кнопки старой раскладки
                   "🈶 Два чтения": self.start_onkun, "🔁 Повторение": self.start_review, "📊 Прогресс": self.progress,
                   "🔤 Азбука": self.abc_table}
        if text in actions and self.get(uid, "fb_wait"):
            self.set(uid, "fb_wait", None)
        return actions[text](uid) if text in actions else None
