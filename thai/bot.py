"""Телеграм-обёртка над engine: ученики по id Телеграма, меню, отрисовка, утреннее напоминание.

Настройки берутся из переменных окружения (см. .env.example): токен THAI_BOT_TOKEN, владелец OWNER_ID, папки THAI_DATA и THAI_LOG.
Вход открытый: кто написал боту, тот ученик, прогресс хранится по его id. Первым делом новичок
выбирает речь (ครับ или ค่ะ): от неё зависят фразы и голос уроков. Владельцу доступна /users с итогами
учеников: это OWNER_ID, а если он не задан, то первый, кто написал боту.
✍️ Написать: сообщение ученика уходит в таблицу feedback и копией владельцу; имя в шапке из Телеграма
в момент отправки, в базе его нет.
"""
import asyncio
import datetime as dt
import logging
import logging.handlers
import os
import re

from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import engine as E

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.abspath(os.environ.get("THAI_LOG") or os.path.join(HERE, "logs"))  # по умолчанию ./logs рядом с кодом
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.handlers.RotatingFileHandler(f"{LOG_DIR}/bot.log", maxBytes=1_000_000, backupCount=3)])
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("thai-bot")

eng = E.Engine()
MENU_KB = ReplyKeyboardMarkup(E.MENU, resize_keyboard=True, is_persistent=True)
REMIND_WINDOW_H = 4  # если бот лежал всё окно после времени напоминания, в этот день не шлём


def kb(buttons):
    if not buttons:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in buttons])


async def learner(update, context):
    """Движок ученика. Новичка регистрирует; пока речь не выбрана, задаёт этот вопрос и возвращает None."""
    uid, chat = update.effective_user.id, update.effective_chat.id
    e = eng.user(uid)
    u = e.profile()
    if u is None:
        e = eng.add_user(uid, chat)
        log.info("новый ученик: %s", uid)
    elif u["chat"] != chat:
        eng.st.run("update users set chat=? where uid=?", (chat, uid))
    if not e.profile()["g"]:
        await render(context.bot, e, chat, e.ask_gender(first=True))
        return None
    return e


async def send_voice(bot, e, chat_id, o):
    """Голосовое из кэша file_id или файлом; если ученик запретил голосовые, шлём ему аудио."""
    if not os.path.exists(o.path):
        log.warning("нет файла озвучки %s", o.path)
        if o.text:
            await bot.send_message(chat_id, o.text, parse_mode=ParseMode.HTML, reply_markup=kb(o.buttons))
        return
    as_audio = e.get("as_audio", False)
    key = o.path + ("|a" if as_audio else "|v")
    row = eng.st.q("select file_id from tgfiles where path=?", (key,))
    args = dict(caption=o.text or None, parse_mode=ParseMode.HTML, reply_markup=kb(o.buttons))
    try:
        with open(o.path, "rb") as f:
            media = row[0][0] if row else f
            m = await (bot.send_audio(chat_id, media, **args) if as_audio else bot.send_voice(chat_id, media, **args))
    except BadRequest as err:
        if "VOICE_MESSAGES_FORBIDDEN" in str(err) and not as_audio:
            log.info("ученик %s запретил голосовые, перехожу на аудио", e.uid)
            e.set("as_audio", True)
            return await send_voice(bot, e, chat_id, o)
        raise
    fid = (m.audio or m.voice).file_id if (m.audio or m.voice) else None
    if fid and not row:
        eng.st.run("insert or replace into tgfiles values(?,?)", (key, fid))


def msg_of(m):
    """Сообщение ученика для ✍️: вид, текст или подпись, файл в Телеграме, откуда скопировать владельцу."""
    photo = getattr(m, "photo", None)
    media = [("voice", getattr(m, "voice", None)), ("photo", photo[-1] if photo else None),
             ("video", getattr(m, "video", None)), ("video_note", getattr(m, "video_note", None)),
             ("audio", getattr(m, "audio", None)), ("document", getattr(m, "document", None))]
    kind, f = next(((k, x) for k, x in media if x), ("text", None))
    return {"kind": kind, "text": getattr(m, "text", None) or getattr(m, "caption", None) or "",
            "file_id": f.file_id if f else None, "chat": getattr(m, "chat_id", None),
            "mid": getattr(m, "message_id", None), "group": getattr(m, "media_group_id", None)}


async def notify_owner(bot, fid):
    """Сообщение ученика владельцу: кто, где был, текст; голосовое и картинку копией. Свои заметки владельца только в базе."""
    uid, kind, text, ctx, chat, mid = eng.st.q("select uid, kind, text, ctx, chat, mid from feedback where id=?", (fid,))[0]
    own = eng.owner()
    if not own or uid == own:
        return
    try:
        c = await bot.get_chat(chat or uid)
        name = " ".join(x for x in (c.first_name, c.last_name) if x)
        name += f" @{c.username}" if getattr(c, "username", None) else ""
    except Exception:
        name = ""
    head = f"✍️ <b>Сообщение ученика</b> {E.esc(name or str(uid))}" + (f"\n{E.esc(ctx)}" if ctx else "")
    to = eng.user(own).profile()["chat"]
    try:
        await bot.send_message(to, head + (f"\n\n{E.esc(text[:3500])}" if kind == "text" else ""), parse_mode=ParseMode.HTML)
        if kind != "text" and chat and mid:
            await bot.copy_message(to, chat, mid)
    except Exception:
        log.warning("сообщение %s владельцу не ушло", fid, exc_info=True)
    log.info("сообщение ученика %s: %s", uid, kind)


async def render(bot, e, chat_id, outs, source=None):
    for o in outs or []:
        if o.kind == "fb":
            await notify_owner(bot, int(o.text))
        elif o.kind == "text":
            await bot.send_message(chat_id, o.text, parse_mode=ParseMode.HTML,
                                   reply_markup=MENU_KB if o.menu else kb(o.buttons))
        elif o.kind == "voice":
            await send_voice(bot, e, chat_id, o)
        elif o.kind == "edit" and source is not None:
            try:
                if o.target == "markup":
                    await source.edit_reply_markup(kb(o.buttons))
                elif o.target == "caption":
                    await source.edit_caption(o.text, parse_mode=ParseMode.HTML, reply_markup=kb(o.buttons))
                else:
                    await source.edit_text(o.text, parse_mode=ParseMode.HTML, reply_markup=kb(o.buttons))
            except BadRequest as err:
                if "not modified" not in str(err):
                    log.warning("правка сообщения: %s", err)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    e = await learner(update, context)
    if e:
        await render(context.bot, e, update.effective_chat.id, e.welcome())


async def cmd_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    e = await learner(update, context)
    if not e:
        return
    arg = (context.args or [""])[0]
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", arg)
    if arg == "off":
        eng.st.run("update users set remind='off' where uid=?", (e.uid,))
        txt = "Напоминание выключено. Включить: /time 09:00"
    elif m and int(m[1]) < 24 and int(m[2]) < 60:
        eng.st.run("update users set remind=? where uid=?", (f"{int(m[1]):02d}:{m[2]}", e.uid))
        txt = f"Буду писать в {int(m[1]):02d}:{m[2]} {e.zone()}. Часовой пояс: /tz"
    else:
        txt = f"Сейчас: {e.profile()['remind']} {e.zone()}. Пример: /time 8:30 или /time off"
    await update.message.reply_text(txt)


async def cmd_tz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    e = await learner(update, context)
    if not e:
        return
    m = re.fullmatch(r"([+-]?\d{1,2})", (context.args or [""])[0])
    if m and -12 <= int(m[1]) <= 14:
        eng.st.run("update users set tz=? where uid=?", (int(m[1]), e.uid))
        txt = f"Теперь время {e.zone()}. Время напоминания: /time"
    else:
        txt = f"Сейчас время {e.zone()}. Таиланд: /tz +7, Москва: /tz +3"
    await update.message.reply_text(txt)


async def cmd_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    e = await learner(update, context)
    if not e:
        return
    cmd = update.message.text.split()[0].lstrip("/").split("@")[0]
    if cmd == "lessons":
        outs = e.lesson_list()
    elif cmd == "go":
        arg = (context.args or [""])[0]
        outs = e.go(int(arg)) if arg.isdigit() else [E.Out("text", "Номер урока: /go 3")]
    elif cmd == "abc":
        arg = (context.args or [""])[0]
        outs = e.alphabet(int(arg) if arg.isdigit() else None)
    elif cmd == "gender":
        outs = e.ask_gender()
    elif cmd == "menu":
        outs = [E.Out("text", "Меню внизу.", menu=True)]
    elif cmd == "feedback":
        outs = e.ask_feedback()
    else:
        outs = [E.Out("text", "Кнопки меню внизу. /lessons список уроков, /go N открыть урок, /abc алфавит по желанию, "
                              "/time время напоминания, /tz часовой пояс, /gender мужская или женская речь, "
                              "/feedback вопрос или пожелание автору.", menu=True)]
    await render(context.bot, e, update.effective_chat.id, outs)


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика учеников только владельцу, у остальных команда молчит. Имена из Телеграма, в базе их нет."""
    if eng.owner() != update.effective_user.id:
        return
    names = {}
    for uid, chat in eng.st.q("select uid, chat from users"):
        try:
            c = await context.bot.get_chat(chat)
            names[uid] = " ".join(x for x in (c.first_name, c.last_name) if x)
        except Exception:
            pass
    await render(context.bot, eng.user(update.effective_user.id), update.effective_chat.id, eng.users_report(names))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    e = await learner(update, context)
    if not e:
        return
    e.touch()
    outs = e.on_menu(update.message.text)
    if outs is None:
        outs = e.take_feedback(msg_of(update.message))
    if outs is None:
        outs = e.fallback(msg_of(update.message))
    await render(context.bot, e, update.effective_chat.id, outs)


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Голосовое, картинка, видео или файл: после ✍️ это сообщение автору, иначе предлагаем передать."""
    e = await learner(update, context)
    if not e:
        return
    outs = e.take_feedback(msg_of(update.message))
    if outs is None:
        outs = e.fallback(msg_of(update.message))
    await render(context.bot, e, update.effective_chat.id, outs)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    msg = q.message
    e = eng.user(update.effective_user.id)
    u = e.profile()
    if u is None or (not u["g"] and not q.data.startswith("gv:")):
        await q.answer()
        await learner(update, context)
        return
    e.touch()
    outs = e.on_callback(q.data, bool(msg and (msg.voice or msg.audio)))
    try:  # сбой связи на подтверждении нажатия не должен терять следующую карточку
        await q.answer(next((o.toast for o in outs if o.toast), None))
    except TelegramError as err:
        log.warning("нажатие не подтверждено: %s", err)
    await render(context.bot, e, msg.chat_id, [o for o in outs if o.kind != "toast"], source=msg)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("ошибка обработки: %s", context.error, exc_info=context.error)


async def remind_due(bot):
    """Один проход: каждому ученику в его часовом поясе, раз в день, в окне после его времени."""
    for uid, chat, tz, rt, reminded in eng.st.q("select uid, chat, tz, remind, reminded from users where g is not null"):
        if rt == "off" or not chat:
            continue
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=tz)))
        h, m = map(int, rt.split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if start <= now < start + dt.timedelta(hours=REMIND_WINDOW_H) and reminded != now.date().isoformat():
            eng.st.run("update users set reminded=? where uid=?", (now.date().isoformat(), uid))
            e = eng.user(uid)
            try:
                await render(bot, e, chat, e.reminder(now.hour))
                log.info("напоминание отправлено: %s", uid)
            except Forbidden:
                eng.st.run("update users set remind='off' where uid=?", (uid,))
                log.info("ученик %s закрыл бота, напоминания ему выключены", uid)


async def reminder_loop(app: Application):
    while True:
        await asyncio.sleep(30)
        try:
            await remind_due(app.bot)
        except Exception:
            log.exception("напоминание")


NAME = os.environ.get("BOT_NAME", "").strip()  # необязательно: отображаемое имя бота; пусто = не трогать имя из BotFather


async def post_init(app: Application):
    app.bot_data["reminder"] = asyncio.get_running_loop().create_task(reminder_loop(app))
    try:
        cmds = [BotCommand("lessons", "список уроков"), BotCommand("go", "открыть урок по номеру"),
                BotCommand("abc", "алфавит, по желанию"),
                BotCommand("time", "время утреннего урока"), BotCommand("tz", "часовой пояс"),
                BotCommand("gender", "мужская или женская речь"), BotCommand("menu", "показать меню"),
                BotCommand("feedback", "вопрос или пожелание автору")]
        await app.bot.set_my_commands(cmds)
        if eng.owner():  # /users видна в меню команд только владельцу, у остальных её нет и она молчит
            await app.bot.set_my_commands(cmds + [BotCommand("users", "статистика учеников")],
                                          scope=BotCommandScopeChat(eng.user(eng.owner()).profile()["chat"]))
        await app.bot.set_my_description("Тайский с нуля для поездок и жизни в Таиланде: звуки и тоны, алфавит, полезные "
                                         "фразы и разговорные сценки. У каждой фразы голос, транскрипция с тонами и "
                                         "перевод, всё повторяется по интервалам. Есть тренажёр тонов. Сначала выбираешь "
                                         "мужскую или женскую речь. Нажми «Старт» 🙂")
        await app.bot.set_my_short_description("Тайский с нуля: тоны, алфавит, фразы и сценки для разговора. Голос и "
                                               "транскрипция у каждой фразы")
        if NAME and (await app.bot.get_my_name()).name != NAME:  # имя видно в поиске Телеграма; у метода жёсткий лимит, зря не зовём
            await app.bot.set_my_name(NAME)
    except Exception:
        log.warning("не удалось задать команды бота", exc_info=True)
    miss = {g: sum(1 for it in v["items"].values()
                   if not os.path.exists(E.tts.path_for(it["say"], E.tts.MALE if it["voice"] == "m" else E.tts.FEMALE)))
            for g, v in eng.variants.items()}
    n = eng.st.q("select count(*) from users")[0][0]
    log.info("старт: @%s, уроков %s, учеников %s, без озвучки %s", app.bot.username, len(eng.order), n, miss)


async def post_stop(app: Application):
    """Закрываем фоновую задачу напоминаний, иначе asyncio пишет в лог ошибку при каждой остановке."""
    task = app.bot_data.get("reminder")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def main():
    if not os.environ.get("THAI_BOT_TOKEN"):
        raise SystemExit("Не задан THAI_BOT_TOKEN: токен бота от @BotFather (см. .env.example)")
    app = (Application.builder().token(os.environ["THAI_BOT_TOKEN"]).post_init(post_init).post_stop(post_stop)
           .connect_timeout(10).read_timeout(20).write_timeout(30).build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("time", cmd_time))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("tz", cmd_tz))
    app.add_handler(CommandHandler(["lessons", "go", "abc", "gender", "menu", "help", "feedback"], cmd_simple))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.PHOTO | filters.VIDEO | filters.VIDEO_NOTE | filters.AUDIO |
                                   filters.Document.ALL, on_media))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)


if __name__ == "__main__":
    main()
