"""Проверка склейки с Телеграмом без токена: подставной бот пишет вызовы, обработчики настоящие.
Код выхода не 0, если нашлась проблема. Нужны собранные данные (python3 build.py --audio) или тестовые
(python3 tests/smoke.py запускает всё на тестовых данных).

  python3 test_bot.py
Двое учеников: мужская и женская речь, у каждого свой прогресс, настройки и напоминание, сценка «Разговора».
"""
import asyncio
import datetime as dt
import os
import tempfile
from types import SimpleNamespace as NS

os.environ.setdefault("THAI_LOG", tempfile.gettempdir())
os.environ["THAI_DB"] = tempfile.mktemp(suffix=".db")  # импорт bot открывает базу: не боевую
from telegram.error import BadRequest, Forbidden  # noqa: E402

import bot as B  # noqa: E402
import engine as E  # noqa: E402

calls, sent, n = [], [], [0]


class Msg:
    def __init__(self, kind, chat_id=1, text="", markup=None):
        n[0] += 1
        self.message_id, self.chat_id, self.kind, self.text, self.markup = n[0], chat_id, kind, text or "", markup
        self.voice = NS(file_id=f"V{n[0]}") if kind == "voice" else None
        self.audio = NS(file_id=f"A{n[0]}") if kind == "audio" else None

    async def edit_reply_markup(self, markup):
        calls.append(("edit_markup", self.message_id))

    async def edit_caption(self, caption, parse_mode=None, reply_markup=None):
        calls.append(("edit_caption", caption[:40]))

    async def edit_text(self, text, parse_mode=None, reply_markup=None):
        calls.append(("edit_text", text[:40]))


class FakeBot:
    def __init__(self, forbid_voice=False, blocked=()):
        self.forbid_voice, self.blocked = forbid_voice, blocked

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        if chat_id in self.blocked:
            raise Forbidden("Forbidden: bot was blocked by the user")
        calls.append(("text", chat_id, text[:50]))
        m = Msg("text", chat_id, text, reply_markup)
        sent.append(m)
        return m

    async def send_voice(self, chat_id, voice, caption=None, parse_mode=None, reply_markup=None):
        if self.forbid_voice:
            raise BadRequest("Voice_messages_forbidden".upper())
        calls.append(("voice", chat_id, "file" if hasattr(voice, "read") else voice, (caption or "")[:30]))
        m = Msg("voice", chat_id, caption, reply_markup)
        sent.append(m)
        return m

    async def copy_message(self, chat_id, from_chat_id, message_id):
        calls.append(("copy", chat_id, from_chat_id, message_id))

    async def send_audio(self, chat_id, audio, caption=None, parse_mode=None, reply_markup=None):
        calls.append(("audio", chat_id, "file" if hasattr(audio, "read") else audio, (caption or "")[:30]))
        m = Msg("audio", chat_id, caption, reply_markup)
        sent.append(m)  # у ученика с запретом голосовых урок идёт аудио: кнопки на них
        return m


def upd(uid, text=None, data=None, msg=None):
    async def reply_text(t, **kw):
        calls.append(("reply", uid, t[:60]))

    async def answer(t=None):
        calls.append(("answer", t))
    u = NS(effective_user=NS(id=uid), effective_chat=NS(id=uid))
    u.message = NS(text=text or "", reply_text=reply_text) if text is not None else None
    u.callback_query = NS(data=data, message=msg, answer=answer) if data else None
    return u


def last_buttons(uid):
    """Кнопки последнего сообщения ученику с клавиатурой."""
    for m in reversed(sent):
        if m.chat_id == uid and m.markup is not None and hasattr(m.markup, "inline_keyboard"):
            return m, [b.callback_data for row in m.markup.inline_keyboard for b in row]
    return None, []


async def play(uid, ctx):
    """Начатый урок или сценку кнопками до конца через обработчики: дальше, ответ, «помню»."""
    for _ in range(120):
        m, bs = last_buttons(uid)
        s = B.eng.user(uid).session()
        if not s:
            break
        step = s["steps"][s["i"]]
        grades = [d for d in bs if d.startswith("g:")]
        pick = grades[-1] if grades else next((d for d in bs if d.startswith(("n:", "r:"))), None)
        if step["t"] == "q":
            pick = f"a:{s['nonce']}:{step['ans']}"
        if not pick:
            break
        await B.on_button(upd(uid, data=pick, msg=m), ctx)


async def lesson1(uid, ctx):
    """Урок 1 кнопками до конца через обработчики."""
    await B.on_text(upd(uid, text="▶ Урок"), ctx)
    await play(uid, ctx)
    return "s1_01" in B.eng.user(uid).done()


async def main():
    db = tempfile.mktemp(suffix=".db")
    B.eng = E.Engine(store=E.Store(db))
    fake = FakeBot()
    ctx = NS(bot=fake, args=[])
    problems = []
    st = B.eng.st

    # новичок: /start -> вопрос о речи; до выбора уроки не начинаются
    await B.cmd_start(upd(111, text="/start"), ctx)
    if not any("Какую речь учим" in m.text for m in sent if m.chat_id == 111):
        problems.append("новичку не задан вопрос о речи")
    await B.on_text(upd(111, text="▶ Урок"), ctx)
    if B.eng.user(111).session():
        problems.append("урок начался до выбора речи")
    m, _ = last_buttons(111)
    await B.on_button(upd(111, data="gv:m", msg=m), ctx)
    await B.on_button(upd(222, data="ls", msg=Msg("text", 222)), ctx)  # второй начал с кнопки, без /start
    m, _ = last_buttons(222)
    await B.on_button(upd(222, data="gv:f", msg=m), ctx)
    users = st.q("select uid, g, tz from users order by uid")
    if users != [(111, "m", 3), (222, "f", 3)]:
        problems.append(f"ученики записаны не так: {users}")

    # каждый проходит урок 1 в своей речи
    for uid in (111, 222):
        if not await lesson1(uid, ctx):
            problems.append(f"урок 1 не пройден у {uid}")
    caps = {uid: " ".join(m.text for m in sent if m.chat_id == uid and m.kind == "voice") for uid in (111, 222)}
    if "ครับ" not in caps[111] or "ค่ะ" in caps[111]:
        problems.append("у ученика с мужской речью не те фразы")
    if "ค่ะ" not in caps[222] or "ครับ" in caps[222]:
        problems.append("у ученицы с женской речью не те фразы")
    if len(st.q("select uid from lessons_done")) != 2 or st.q("select count(distinct uid) from reviews")[0][0] != 2:
        problems.append("прогресс не разложился по ученикам")

    # «💬 Разговор»: меню, шпаргалка, урок-сценка; старая кнопка «Я сейчас в…» ведёт туда же и чинит клавиатуру
    k0 = len(sent)
    for uid, sid in ((111, "hello"), (222, "eat")):
        await B.on_text(upd(uid, text="💬 Разговор"), ctx)
        m, bs = last_buttons(uid)
        if f"pl:{sid}" not in bs:
            problems.append(f"в меню «Разговор» нет сценки {sid}")
        await B.on_button(upd(uid, data=f"pl:{sid}", msg=m), ctx)
        m, bs = last_buttons(uid)
        await B.on_button(upd(uid, data=f"tk:{sid}", msg=m), ctx)
        await play(uid, ctx)
        if sid not in B.eng.user(uid).done():
            problems.append(f"сценка {sid} у {uid} не пройдена")
    said = " ".join(m.text for m in sent[k0:] if m.chat_id == 222)  # без озвучки фраза уходит текстом
    if "กินแล้วค่ะ" not in said or "ครับ" in said:
        problems.append("в сценке у ученицы не её речь")
    k = len(sent)
    await B.on_text(upd(111, text=E.OLD_PLACES), ctx)
    if not any(m.markup is B.MENU_KB for m in sent[k:]) or "💬 Разговор" not in str(B.MENU_KB):
        problems.append("старая кнопка мест не обновила клавиатуру")

    # 🐢 по старой кнопке и устаревшая кнопка
    th = B.eng.user(111).lessons["s1_01"]["items"][0]
    await B.on_button(upd(111, data=B.eng.user(111).slow_key(th), msg=Msg("voice", 111)), ctx)
    await B.on_button(upd(111, data="n:1", msg=Msg("text", 111)), ctx)
    cached = st.q("select count(*) from tgfiles")[0][0]

    # запрет голосовых у одной ученицы: ей аудио, остальным голосовые
    ready = next(p for p in (os.path.join(E.tts.AUDIO_DIR, f) for f in sorted(os.listdir(E.tts.AUDIO_DIR))) if p.endswith(".mp3"))
    await B.send_voice(FakeBot(forbid_voice=True), B.eng.user(222), 222, E.Out("voice", "x", ready))
    if not B.eng.user(222).get("as_audio") or B.eng.user(111).get("as_audio"):
        problems.append("переход на аудио не у того ученика")

    # /time и /tz у каждого свои
    for uid, args in ((111, ["7:30"]), (111, ["25:00"]), (222, ["off"])):
        ctx.args = args
        await B.cmd_time(upd(uid, text="/time " + args[0]), ctx)
    ctx.args = ["+7"]
    await B.cmd_tz(upd(111, text="/tz +7"), ctx)
    ctx.args = ["abc"]
    await B.cmd_tz(upd(222, text="/tz abc"), ctx)
    if st.q("select uid, tz, remind from users order by uid") != [(111, 7, "07:30"), (222, 3, "off")]:
        problems.append(f"/time или /tz: {st.q('select uid, tz, remind from users order by uid')}")

    # новичок через Телеграм: речь -> цель -> «ничего» -> план и голосовое первого урока
    k = len(sent)
    await B.cmd_start(upd(333, text="/start"), ctx)
    m, _ = last_buttons(333)
    await B.on_button(upd(333, data="gv:m", msg=m), ctx)
    m, bs = last_buttons(333)
    await B.on_button(upd(333, data="ob:goal:live", msg=m), ctx)
    m, bs2 = last_buttons(333)
    await B.on_button(upd(333, data="ob:lvl:0", msg=m), ctx)
    got = [x.kind + ":" + x.text[:20] for x in sent[k:]]
    if "ob:goal:trip" not in bs or "ob:lvl:0" not in bs2 or not any("План под жизнь" in x.text for x in sent[k:]) \
            or sent[-1].kind != "voice":
        problems.append(f"знакомство новичка: {got[-4:]}")

    # алфавит по желанию: /abc открывает урок алфавита, в основном пути его нет
    k, ctx.args = len(sent), []
    await B.cmd_simple(upd(222, text="/abc"), ctx)
    if not any("Урок алфавита 1." in m.text for m in sent[k:]):
        problems.append(f"/abc не открыл урок алфавита: {[m.text[:50] for m in sent[k:]]}")

    # /users: только владельцу (первый ученик), время занятий копится между нажатиями
    k = len(sent)
    await B.cmd_users(upd(222, text="/users"), ctx)
    if len(sent) > k:
        problems.append("/users ответила не владельцу")
    B.eng.user(111).set("seen", (B.E.dt.datetime.utcnow() - B.E.dt.timedelta(seconds=90)).isoformat(timespec="seconds"))
    await B.on_text(upd(111, text="📊 Прогресс"), ctx)
    k = len(sent)
    await B.cmd_users(upd(111, text="/users"), ctx)
    rep = "\n".join(m.text for m in sent[k:])
    if B.eng.owner() != 111 or "Ученики: 3" not in rep or "Время ≈ 2 мин" not in rep:
        problems.append(f"/users владельцу: {rep[:300]}")

    # смена речи командой: повторение переезжает на мужские фразы и обратно
    before = sorted(r[0] for r in st.q("select th from reviews where uid=222"))
    await B.cmd_simple(upd(222, text="/gender"), ctx)
    m, _ = last_buttons(222)
    await B.on_button(upd(222, data="gv:m", msg=m), ctx)
    male = [r[0] for r in st.q("select th from reviews where uid=222")]
    await B.on_button(upd(222, data="gv:f", msg=m), ctx)
    after = sorted(r[0] for r in st.q("select th from reviews where uid=222"))
    if before != after or not any("ครับ" in x for x in male):
        problems.append("смена речи потеряла повторение")

    # ✍️ Написать: сообщение ученика владельцу с местом в уроке, скриншот вдогонку копией без нового ответа,
    # текст без кнопки только по «передать», заметка владельца только в базе, старому ученику новое меню с итогом
    to_owner = lambda: [c for c in calls if c[0] in ("text", "copy") and c[1] == 111]  # noqa: E731
    fbs = lambda: st.q("select uid, text, ctx from feedback order by id")  # noqa: E731
    B.eng.user(222).set("menu_v", 1)
    await B.on_text(upd(222, text="▶ Урок"), ctx)
    k, was = len(sent), len(to_owner())
    await B.on_text(upd(222, text="✍️ Написать"), ctx)
    await B.on_text(upd(222, text="не слышу тон"), ctx)
    if not fbs() or fbs()[-1][1] != "не слышу тон" or not fbs()[-1][2].startswith("урок 2"):
        problems.append(f"✍️ не записал сообщение с местом в уроке: {fbs()}")
    new = to_owner()[was:]
    if not new or "Сообщение ученика" not in new[0][2] or not any("Передал автору" in (x.text or "") for x in sent[k:]):
        problems.append(f"✍️ не дошло владельцу: {new}")
    m, bs = last_buttons(222)
    if "fb:back" not in bs:
        problems.append("после ✍️ нет «▶ Продолжить» к уроку")
    pic = upd(222, text=None)
    pic.message = NS(photo=[NS(file_id="PH1")], caption="скрин", chat_id=222, message_id=555, media_group_id=None)
    k = len(sent)
    await B.on_media(pic, ctx)
    if ("copy", 111, 222, 555) not in calls or any("Передал" in (x.text or "") for x in sent[k:]):
        problems.append("скриншот вдогонку не ушёл копией или ответил ещё раз")
    await B.on_button(upd(222, data="fb:back", msg=m), ctx)
    await play(222, ctx)
    news = [x for x in sent if x.chat_id == 222 and "новая кнопка ✍️" in (x.text or "")]
    if len(news) != 1 or "✍️ Написать" not in [b.text for row in news[0].markup.keyboard for b in row]:
        problems.append("старому ученику не пришло новое меню с ✍️")
    n_fb = len(fbs())
    await B.on_text(upd(222, text="а где алфавит?"), ctx)
    m, bs = last_buttons(222)
    if len(fbs()) != n_fb or "fb:send" not in bs:
        problems.append("текст без ✍️ записался сам или нет «передать автору»")
    await B.on_button(upd(222, data="fb:send", msg=m), ctx)
    if fbs()[-1][1] != "а где алфавит?":
        problems.append("«передать автору» не записал текст")
    was = len(to_owner())
    await B.on_text(upd(111, text="✍️ Написать"), ctx)
    await B.on_text(upd(111, text="заметка владельца"), ctx)
    if any("Сообщение ученика" in c[2] for c in to_owner()[was:]) or fbs()[-1][:2] != (111, "заметка владельца"):
        problems.append("заметка владельца ушла ему же или не записалась")

    # напоминание: в его время один раз; закрывшему бота выключаем
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=7)))
    st.run("update users set remind=?, reminded=null where uid=111", (f"{now.hour:02d}:{now.minute:02d}",))
    k = len(sent)
    await B.remind_due(fake)
    await B.remind_due(fake)
    got = [m.chat_id for m in sent[k:]]
    if got != [111]:
        problems.append(f"напоминание ушло не так: {got}")
    st.run("update users set reminded=null where uid=111")
    await B.remind_due(FakeBot(blocked=(111,)))
    if st.q("select remind from users where uid=111")[0][0] != "off":
        problems.append("закрывшему бота напоминание не выключено")

    # явный владелец: при OWNER_ID им становится только этот id, даже если первым написал другой
    E.OWNER_ID = 777
    e2 = E.Engine(store=E.Store(tempfile.mktemp(suffix=".db")))
    e2.add_user(701, 1)
    if e2.owner() is not None or e2.st.q("select role from users where uid=701")[0][0] != "member":
        problems.append("OWNER_ID: первый написавший стал владельцем при заданном OWNER_ID")
    e2.add_user(777, 2)
    if e2.owner() != 777 or e2.st.q("select uid from users where role='owner'") != [(777,)]:
        problems.append("OWNER_ID: владельцем не стал заданный id")
    E.OWNER_ID = None
    kinds = {}
    for c in calls:
        kinds[c[0]] = kinds.get(c[0], 0) + 1
    print("вызовы:", kinds, "| file_id в кэше:", cached)
    print("ответы команд:", [c[2] for c in calls if c[0] == "reply"])
    os.remove(db)
    print("ПРОБЛЕМЫ: " + "; ".join(problems) if problems else "ок")
    return problems


if asyncio.run(main()):
    raise SystemExit(1)
