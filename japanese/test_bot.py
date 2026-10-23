"""Проверка склейки с Телеграмом без токена: подставной бот пишет вызовы, обработчики настоящие.
Код выхода не 0, если нашлась проблема. Нужны собранные данные (python3 build.py --audio) или тестовые
(python3 tests/smoke.py запускает всё на тестовых данных).

  python3 test_bot.py
"""
import asyncio
import os
import tempfile
from types import SimpleNamespace as NS

os.environ.setdefault("JP_LOG", tempfile.gettempdir())
os.environ["JP_DB"] = tempfile.mktemp(suffix=".db")  # импорт bot открывает базу: не боевую
from telegram.error import BadRequest  # noqa: E402

import bot as B  # noqa: E402
import engine as E  # noqa: E402

calls, sent, n = [], [], [0]


class Msg:
    def __init__(self, kind, text="", markup=None):
        n[0] += 1
        self.message_id, self.chat_id, self.kind, self.text, self.markup = n[0], 1, kind, text, markup
        self.voice = NS(file_id=f"V{n[0]}") if kind == "voice" else None
        self.audio = NS(file_id=f"A{n[0]}") if kind == "audio" else None

    async def edit_reply_markup(self, markup):
        calls.append(("edit_markup",))

    async def edit_caption(self, caption, parse_mode=None, reply_markup=None):
        calls.append(("edit_caption", caption[:30]))

    async def edit_text(self, text, parse_mode=None, reply_markup=None):
        calls.append(("edit_text", text[:30]))


class FakeBot:
    def __init__(self, forbid_voice=False):
        self.forbid_voice = forbid_voice

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kw):
        calls.append(("text", chat_id, text[:60]))
        m = Msg("text", text, reply_markup)
        sent.append(m)
        return m

    async def send_voice(self, chat_id, voice, caption=None, parse_mode=None, reply_markup=None):
        if self.forbid_voice:
            raise BadRequest("VOICE_MESSAGES_FORBIDDEN")
        calls.append(("voice", chat_id, "file" if hasattr(voice, "read") else voice))
        m = Msg("voice", caption, reply_markup)
        sent.append(m)
        return m

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, reply_markup=None):
        calls.append(("photo", chat_id, "file" if hasattr(photo, "read") else photo))
        m = Msg("photo", caption, reply_markup)
        m.photo = [NS(file_id=f"P{n[0]}")]
        sent.append(m)
        return m

    async def get_chat(self, chat_id):
        return NS(first_name=f"Имя{chat_id}", last_name=None)

    async def copy_message(self, chat_id, from_chat_id, message_id):
        calls.append(("copy", chat_id, from_chat_id, message_id))

    async def send_audio(self, chat_id, audio, caption=None, parse_mode=None, reply_markup=None):
        calls.append(("audio", chat_id))
        m = Msg("audio", caption, reply_markup)
        sent.append(m)
        return m


def upd(uid, text=None, data=None, msg=None, name=None):
    async def reply_text(t, **kw):
        calls.append(("reply", uid, t[:120]))

    async def answer(t=None):
        calls.append(("answer", t))
    u = NS(effective_user=NS(id=uid, first_name=name), effective_chat=NS(id=uid))
    u.message = NS(text=text or "", reply_text=reply_text) if text is not None else None
    u.callback_query = NS(data=data, message=msg, answer=answer) if data else None
    return u


def last_markup():
    for m in reversed(sent):
        if m.markup is not None and hasattr(m.markup, "inline_keyboard"):
            return m, [b.callback_data for row in m.markup.inline_keyboard for b in row]
    return None, []


async def main():
    B.eng = E.Engine(store=E.Store(tempfile.mktemp(suffix=".db")))
    bot = FakeBot()
    ctx = NS(bot=bot, args=[], bot_data={"username": "jp_test_bot"})
    problems = []
    await B.cmd_start(upd(100, text="/start"), ctx)
    if B.eng.owner() != 100:
        problems.append("владелец не закрепился")
    await B.cmd_start(upd(200, text="/start"), ctx)
    await B.on_text(upd(300, text="привет"), ctx)  # без /start тоже заводится
    for who in (200, 300):
        u = B.eng.user(who)
        if not u or u["role"] != "member":
            problems.append(f"{who} не стал учеником")
        if sum(1 for c in calls if c[0] == "text" and c[1] == who and "Привет" in c[2]) != 1:
            problems.append(f"{who}: приветствие не один раз")
    k = len(sent)
    await B.cmd_users(upd(200, text="/users"), ctx)
    if len(sent) > k:
        problems.append("ученику открылся /users")
    # ученик 200 проходит начало теста кнопками
    await B.on_text(upd(200, text="📝 Тест"), ctx)
    for _ in range(12):
        m, bs = last_markup()
        s = B.eng.session(200)
        st = s["steps"][s["i"]]
        pick = f"a:{s['nonce']}:{st['ans']}" if st["t"] == "q" else next((d for d in bs if d.startswith("n:")), None)
        if not pick:
            break
        await B.on_button(upd(200, data=pick, msg=m), ctx)
    if not B.eng.st.q("select count(*) from answers where uid=200")[0][0]:
        problems.append("ответы ученика 200 не записались")
    # время занятий: пауза между нажатиями до 5 минут считается, дольше нет
    spent = lambda: B.eng.st.q("select coalesce(sum(sec), 0) from studytime where uid=200")[0][0]  # noqa: E731
    was, now = spent(), E.dt.datetime.utcnow().replace(microsecond=0)
    B.eng.set(200, "seen", (now - E.dt.timedelta(seconds=90)).isoformat())
    await B.on_text(upd(200, text="📊 Прогресс"), ctx)
    B.eng.set(200, "seen", (now - E.dt.timedelta(seconds=E.IDLE + 60)).isoformat())
    await B.on_text(upd(200, text="📊 Прогресс"), ctx)
    if not 90 <= spent() - was <= 91:
        problems.append(f"время занятий: {spent() - was} с вместо 90")
    # новичок: до выбора старта урок не начинается; «с нуля» даёт четыре кнопки и урок азбуки,
    # у карточки знака картинка и голос с 🐢, по ней то же медленно (или «ещё готовится», пока идёт озвучка)
    await B.on_text(upd(300, text="📚 Урок"), ctx)
    m, bs = last_markup()
    if B.eng.session(300) or "tr:start" not in bs:
        problems.append("урок начался до выбора старта")
    await B.on_button(upd(300, data="tr:start", msg=m), ctx)
    keys = next((x.markup for x in reversed(sent) if hasattr(x.markup, "keyboard")), None)
    if not keys or "🔤 Азбука" not in [b.text for row in keys.keyboard for b in row]:
        problems.append("у новичка не кнопки азбуки")
    m, bs = last_markup()
    await B.on_button(upd(300, data=next(d for d in bs if d.startswith("n:")), msg=m), ctx)
    if not any(c[0] == "photo" for c in calls):  # после нажатия кнопки подставной чат всегда 1
        problems.append("у карточки азбуки нет картинки")
    m, bs = last_markup()
    slow = next((d for d in bs if d.startswith("sl:")), None)
    if not slow or m.kind != "voice":
        problems.append("у карточки урока нет 🐢")
    else:
        k = len(sent)
        await B.on_button(upd(300, data=slow, msg=m), ctx)
        new = [x for x in sent[k:] if x.kind == "voice" and (x.text or "").startswith("🐢")]
        if not new and not any(c[0] == "answer" and "готовится" in (c[1] or "") for c in calls):
            problems.append("🐢 не сработал")
    # лесенка уровня через Телеграм: «кое-что знаю», два «не знаю» по хирагане -> азбука с нуля и план
    await B.on_text(upd(500, text="📚 Урок"), ctx)
    m, bs = last_markup()
    if "tr:check" not in bs or "tr:test" in bs:
        problems.append("на входе нет лесенки уровня или остался длинный тест")
    await B.on_button(upd(500, data="tr:check", msg=m), ctx)
    for _ in range(2):
        m, bs = last_markup()
        idk = next((d for d in bs if d.startswith("a:") and d.endswith(f":{E.IDK}")), None)
        if not idk:
            problems.append(f"в вопросе лесенки нет «не знаю»: {bs}")
            break
        await B.on_button(upd(500, data=idk, msg=m), ctx)
    if B.eng.get(500, "placed") != "abc" or not any("Начинаем с азбуки" in (x.text or "") for x in sent[-8:]):
        problems.append(f"лесенка в Телеграме: место {B.eng.get(500, 'placed')}")
    await B.on_button(upd(400, data="tr:full", msg=Msg("text")), ctx)  # новичок сразу с кнопки выбора
    await B.on_button(upd(400, data="tr:full", msg=Msg("text")), ctx)
    keys = next((x.markup for x in reversed(sent) if hasattr(x.markup, "keyboard")), None)
    if not keys or "📝 Тест" not in [b.text for row in keys.keyboard for b in row]:
        problems.append("«знаю азбуку» не дал всё меню")
    await B.on_button(upd(301, data="n:1", msg=Msg("text")), ctx)  # кнопка от нового человека
    if not B.eng.user(301):
        problems.append("нажатие кнопки новым человеком его не завело")
    await B.on_text(upd(300, text="📊 Прогресс"), ctx)
    if B.eng.st.q("select count(*) from answers where uid=300")[0][0]:
        problems.append("прогресс перемешался между учениками")
    ctx.args = ["+7"]
    await B.cmd_tz(upd(200, text="/tz +7"), ctx)
    ctx.args = ["8:15"]
    await B.cmd_time(upd(200, text="/time 8:15"), ctx)
    u = B.eng.user(200)
    if (u["tz"], u["remind"]) != (7, "08:15"):
        problems.append(f"время и пояс не сохранились: {u}")
    k = len(sent)
    await B.cmd_users(upd(100, text="/users"), ctx)
    rep = "\n".join(m.text for m in sent[k:])
    for want in ("Имя100", "Имя200", "⏱ Время ≈ 2 мин", "Пройдено уроков", "Дальше по курсу", "Тест начат"):
        if want not in rep:
            problems.append(f"в /users нет «{want}»")
    # ✍️ Написать: текст уходит владельцу с местом в уроке, скриншот вдогонку копией без нового ответа,
    # текст без кнопки только по «передать», заметка самого владельца только в базе
    to_owner = lambda: [c for c in calls if c[0] in ("text", "copy") and c[1] == 100]  # noqa: E731
    await B.on_text(upd(300, text="📚 Урок"), ctx)
    k, was = len(sent), len(to_owner())
    await B.on_text(upd(300, text="✍️ Написать"), ctx)
    await B.on_text(upd(300, text="нет русской транскрипции"), ctx)
    row = B.eng.st.q("select kind, text, ctx from feedback where uid=300")
    if not row or row[-1][1] != "нет русской транскрипции" or not row[-1][2].startswith("азбука 1"):
        problems.append(f"✍️ не записал сообщение с местом в уроке: {row}")
    new = to_owner()[was:]
    if not new or "Сообщение ученика" not in new[0][2] or not any("Передал автору" in (x.text or "") for x in sent[k:]):
        problems.append(f"✍️ не дошло владельцу: {new}")
    m, bs = last_markup()
    if "fb:back" not in bs:
        problems.append("после ✍️ нет «▶ Продолжить» к уроку")
    pic = upd(300, text=None)
    pic.message = NS(photo=[NS(file_id="PH1")], caption="вот скрин", chat_id=300, message_id=777, media_group_id=None)
    k = len(sent)
    await B.on_media(pic, ctx)
    if ("copy", 100, 300, 777) not in calls or any("Передал" in (x.text or "") for x in sent[k:]):
        problems.append("скриншот вдогонку не ушёл копией или ответил ещё раз")
    await B.on_button(upd(300, data="fb:back", msg=m), ctx)
    await B.on_text(upd(300, text="📊 Прогресс"), ctx)  # кнопка меню закрывает ожидание
    n_fb = B.eng.st.q("select count(*) from feedback")[0][0]
    await B.on_text(upd(300, text="а где кана?"), ctx)
    m, bs = last_markup()
    if B.eng.st.q("select count(*) from feedback")[0][0] != n_fb or "fb:send" not in bs:
        problems.append("текст без ✍️ записался сам или нет «передать автору»")
    await B.on_button(upd(300, data="fb:send", msg=m), ctx)
    if B.eng.st.q("select text from feedback order by id desc limit 1")[0][0] != "а где кана?":
        problems.append("«передать автору» не записал текст")
    was = len(to_owner())
    await B.on_text(upd(100, text="✍️ Написать"), ctx)
    await B.on_text(upd(100, text="заметка владельца"), ctx)
    if len([c for c in to_owner()[was:] if "Сообщение ученика" in c[2]]) or \
            not B.eng.st.q("select 1 from feedback where uid=100 and text='заметка владельца'"):
        problems.append("заметка владельца ушла ему же или не записалась")
    ready = next(os.path.join(E.tts.AUDIO_DIR, f) for f in sorted(os.listdir(E.tts.AUDIO_DIR)) if f.endswith(".mp3"))
    await B.send_voice(FakeBot(forbid_voice=True), 200, 200, E.Out("voice", "x", ready))
    if not B.eng.get(200, "as_audio"):
        problems.append("не перешёл на аудио при запрете голосовых")
    # явный владелец: при OWNER_ID им становится только этот id, даже если первым написал другой
    E.OWNER_ID = 777
    e2 = E.Engine(store=E.Store(tempfile.mktemp(suffix=".db")))
    e2.add_user(701, 1, e2.role_for(701), "")
    if e2.owner() is not None or e2.user(701)["role"] != "member":
        problems.append("OWNER_ID: первый написавший стал владельцем при заданном OWNER_ID")
    e2.add_user(777, 2, e2.role_for(777), "")
    if e2.owner() != 777 or e2.user(777)["role"] != "owner" or e2.user(701)["role"] != "member":
        problems.append("OWNER_ID: владельцем не стал заданный id")
    E.OWNER_ID = None
    kinds = {}
    for c in calls:
        kinds[c[0]] = kinds.get(c[0], 0) + 1
    print("вызовы:", kinds)
    print("ПРОБЛЕМЫ: " + "; ".join(problems) if problems else "ок")
    return problems


if asyncio.run(main()):
    raise SystemExit(1)
