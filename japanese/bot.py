"""Телеграм-обёртка над engine: ученики по id Телеграма, меню, отрисовка, напоминания по часовому поясу ученика.

Настройки берутся из переменных окружения (см. .env.example): токен JP_BOT_TOKEN, владелец OWNER_ID, папки JP_DATA и JP_LOG.
Любой, кто написал боту, становится учеником, прогресс хранится по id Телеграма. Владельцу доступна /users с итогами
учеников: это OWNER_ID, а если он не задан, то первый, кто написал боту. Имён в базе нет, для /users бот берёт их
из Телеграма в момент запроса.
Кнопки внизу у каждого свои: пока идёт азбука с нуля, их пять (engine.menu_for).
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
LOG_DIR = os.path.abspath(os.environ.get("JP_LOG") or os.path.join(HERE, "logs"))  # по умолчанию ./logs рядом с кодом
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.handlers.RotatingFileHandler(f"{LOG_DIR}/bot.log", maxBytes=1_000_000, backupCount=3)])
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("japanese-bot")

eng = E.Engine()
REMIND_WINDOW_H = 4


def menu_kb(uid):
    return ReplyKeyboardMarkup(eng.menu_for(uid), resize_keyboard=True, is_persistent=True)


def kb(buttons):
    if not buttons:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in buttons])


async def newcomer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Первое обращение: заводим ученика по id и показываем приветствие. True, если человек новый."""
    uid, chat = update.effective_user.id, update.effective_chat.id
    if eng.user(uid):
        return False
    eng.add_user(uid, chat, eng.role_for(uid), "")
    eng.st.run("update users set reminded=? where uid=?", (eng.today(uid).isoformat(), uid))  # в первый день не напоминаем
    log.info("новый ученик: %s", uid)
    eng.set(uid, "menu_v", E.MENU_V)
    await render(context.bot, uid, chat, eng.welcome(uid))
    return True


async def send_voice(bot, uid, chat_id, o):
    if not o.path or not os.path.exists(o.path):
        if o.path:
            log.warning("нет файла озвучки %s", o.path)
        if o.text:
            await bot.send_message(chat_id, o.text, parse_mode=ParseMode.HTML, reply_markup=kb(o.buttons))
        return
    as_audio = eng.get(uid, "as_audio", False)
    key = o.path + ("|a" if as_audio else "|v")
    row = eng.st.q("select file_id from tgfiles where path=?", (key,))
    args = dict(caption=o.text or None, parse_mode=ParseMode.HTML, reply_markup=kb(o.buttons))
    try:
        with open(o.path, "rb") as f:
            media = row[0][0] if row else f
            m = await (bot.send_audio(chat_id, media, **args) if as_audio else bot.send_voice(chat_id, media, **args))
    except BadRequest as e:
        if "VOICE_MESSAGES_FORBIDDEN" in str(e) and not as_audio:
            log.info("у %s голосовые запрещены, перехожу на аудио", uid)
            eng.set(uid, "as_audio", True)
            return await send_voice(bot, uid, chat_id, o)
        raise
    media = m.audio or m.voice
    if media and not row:
        eng.st.run("insert or replace into tgfiles values(?,?)", (key, media.file_id))


async def send_photo(bot, chat_id, o):
    """Картинка знака азбуки; file_id в кэше, чтобы не грузить файл заново."""
    if not os.path.exists(o.path):
        log.warning("нет картинки %s", o.path)
        return
    key = o.path + "|p"
    row = eng.st.q("select file_id from tgfiles where path=?", (key,))
    with open(o.path, "rb") as f:
        m = await bot.send_photo(chat_id, row[0][0] if row else f, caption=o.text or None, parse_mode=ParseMode.HTML)
    if m.photo and not row:
        eng.st.run("insert or replace into tgfiles values(?,?)", (key, m.photo[-1].file_id))


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
    to = eng.user(own)["chat"]
    try:
        await bot.send_message(to, head + (f"\n\n{E.esc(text[:3500])}" if kind == "text" else ""), parse_mode=ParseMode.HTML)
        if kind != "text" and chat and mid:
            await bot.copy_message(to, chat, mid)
    except Exception:
        log.warning("сообщение %s владельцу не ушло", fid, exc_info=True)
    log.info("сообщение ученика %s: %s", uid, kind)


async def render(bot, uid, chat_id, outs, source=None):
    for o in outs or []:
        if o.kind == "fb":
            await notify_owner(bot, int(o.text))
        elif o.kind == "text":
            await bot.send_message(chat_id, o.text, parse_mode=ParseMode.HTML,
                                   reply_markup=menu_kb(uid) if o.menu else kb(o.buttons))
        elif o.kind == "voice":
            await send_voice(bot, uid, chat_id, o)
        elif o.kind == "photo":
            await send_photo(bot, chat_id, o)
        elif o.kind == "edit" and source is not None:
            try:
                if o.target == "markup":
                    await source.edit_reply_markup(kb(o.buttons))
                elif o.target == "caption":
                    await source.edit_caption(o.text, parse_mode=ParseMode.HTML, reply_markup=kb(o.buttons))
                else:
                    await source.edit_text(o.text, parse_mode=ParseMode.HTML, reply_markup=kb(o.buttons))
            except BadRequest as e:
                if "not modified" not in str(e):
                    log.warning("правка сообщения: %s", e)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await newcomer(update, context):
        return
    uid, chat = update.effective_user.id, update.effective_chat.id
    eng.st.run("update users set chat=? where uid=?", (chat, uid))
    eng.set(uid, "menu_v", E.MENU_V)
    await render(context.bot, uid, chat, eng.welcome(uid))


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if eng.owner() != update.effective_user.id:
        return
    names = {}
    for uid, chat in eng.st.q("select uid, chat from users"):
        try:
            c = await context.bot.get_chat(chat)
            names[uid] = " ".join(x for x in (c.first_name, c.last_name) if x)
        except Exception:
            pass
    await render(context.bot, update.effective_user.id, update.effective_chat.id, eng.users_report(names))


async def cmd_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await newcomer(update, context):
        return
    uid, arg = update.effective_user.id, (context.args or [""])[0]
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", arg)
    if arg == "off":
        eng.st.run("update users set remind='off' where uid=?", (uid,))
        txt = "Напоминание выключено. Включить: /time 09:00"
    elif m and int(m[1]) < 24 and int(m[2]) < 60:
        eng.st.run("update users set remind=? where uid=?", (f"{int(m[1]):02d}:{m[2]}", uid))
        txt = f"Буду писать в {int(m[1]):02d}:{m[2]} по твоему времени (UTC{eng.user(uid)['tz']:+d}). Пояс: /tz"
    else:
        u = eng.user(uid)
        txt = f"Сейчас: {u['remind']}, пояс UTC{u['tz']:+d}. Пример: /time 8:30 или /time off"
    await update.message.reply_text(txt)


async def cmd_tz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await newcomer(update, context):
        return
    uid, arg = update.effective_user.id, (context.args or [""])[0]
    m = re.fullmatch(r"([+-]?\d{1,2})", arg)
    if m and -12 <= int(m[1]) <= 14:
        eng.st.run("update users set tz=? where uid=?", (int(m[1]), uid))
        txt = f"Часовой пояс: UTC{int(m[1]):+d}. Сейчас у тебя {(dt.datetime.utcnow() + dt.timedelta(hours=int(m[1]))):%H:%M}."
    else:
        txt = f"Сейчас пояс UTC{eng.user(uid)['tz']:+d}. Москва: /tz +3, Новосибирск: /tz +7, Япония: /tz +9"
    await update.message.reply_text(txt)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await newcomer(update, context):
        return
    uid = update.effective_user.id
    eng.set(uid, "menu_v", E.MENU_V)
    outs = eng.welcome(uid) if eng.need_choice(uid) else \
        [E.Out("text", "Кнопки внизу 👇 Время напоминания: /time 09:00, часовой пояс: /tz +3.", menu=True)]
    await render(context.bot, uid, update.effective_chat.id, outs)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await newcomer(update, context):
        return
    uid = update.effective_user.id
    eng.touch(uid)
    outs = eng.on_menu(uid, update.message.text)
    if outs is None:
        outs = eng.take_feedback(uid, msg_of(update.message))
    if outs is None:
        outs = eng.fallback(uid, msg_of(update.message))
    await render(context.bot, uid, update.effective_chat.id, outs)


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Голосовое, картинка, видео или файл: после ✍️ это сообщение автору, иначе предлагаем передать."""
    if await newcomer(update, context):
        return
    uid = update.effective_user.id
    outs = eng.take_feedback(uid, msg_of(update.message))
    if outs is None:
        outs = eng.fallback(uid, msg_of(update.message))
    await render(context.bot, uid, update.effective_chat.id, outs)


async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await newcomer(update, context):
        return
    await render(context.bot, update.effective_user.id, update.effective_chat.id, eng.ask_feedback(update.effective_user.id))


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if eng.user(update.effective_user.id) is None:
        await q.answer()
        await newcomer(update, context)
        return
    uid, msg = update.effective_user.id, q.message
    eng.touch(uid)
    outs = eng.on_callback(uid, q.data)
    try:  # сбой связи на подтверждении нажатия не должен терять следующую карточку
        await q.answer(next((o.toast for o in outs if o.toast), None))
    except TelegramError as err:
        log.warning("нажатие не подтверждено: %s", err)
    await render(context.bot, uid, msg.chat_id, [o for o in outs if o.kind != "toast"], source=msg)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("ошибка обработки: %s", context.error, exc_info=context.error)


async def reminder_loop(app: Application):
    while True:
        await asyncio.sleep(30)
        try:
            for uid, chat, tz, rt, reminded in eng.st.q("select uid, chat, tz, remind, reminded from users"):
                if rt == "off" or not chat:
                    continue
                now = dt.datetime.utcnow() + dt.timedelta(hours=tz)
                h, m = map(int, rt.split(":"))
                start = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if start <= now < start + dt.timedelta(hours=REMIND_WINDOW_H) and reminded != now.date().isoformat():
                    eng.st.run("update users set reminded=? where uid=?", (now.date().isoformat(), uid))
                    try:
                        await render(app.bot, uid, chat, eng.reminder(uid))
                        log.info("напоминание %s", uid)
                    except Forbidden:
                        log.info("%s заблокировал бота, напоминания выключаю", uid)
                        eng.st.run("update users set remind='off' where uid=?", (uid,))
        except Exception:
            log.exception("напоминания")


NAME = os.environ.get("BOT_NAME", "").strip()  # необязательно: отображаемое имя бота; пусто = не трогать имя из BotFather


async def post_init(app: Application):
    me = await app.bot.get_me()
    app.bot_data["username"] = me.username
    app.bot_data["reminder"] = asyncio.get_running_loop().create_task(reminder_loop(app))
    try:
        cmds = [BotCommand("menu", "показать меню"), BotCommand("time", "время напоминания"), BotCommand("tz", "часовой пояс"),
                BotCommand("feedback", "вопрос или пожелание автору")]
        await app.bot.set_my_commands(cmds)
        if eng.owner():  # /users видна в меню команд только владельцу, у остальных её нет и она молчит
            await app.bot.set_my_commands(cmds + [BotCommand("users", "статистика учеников")],
                                          scope=BotCommandScopeChat(eng.user(eng.owner())["chat"]))
        await app.bot.set_my_description("Японский с нуля и дальше: азбука по 5-10 знаков за урок, первые фразы, "
                                         "даты и числа по шагам, грамматика и кандзи до N4. У каждого знака картинка "
                                         "и голос, всё повторяется по интервалам. Уроки по 5-10 минут. Нажми «Старт» 🙂")
        await app.bot.set_my_short_description("Японский с нуля: азбука, первые фразы, даты и числа, грамматика, кандзи, слух")
        if NAME and (await app.bot.get_my_name()).name != NAME:  # имя видно в поиске Телеграма; у метода жёсткий лимит, зря не зовём
            await app.bot.set_my_name(NAME)
    except Exception:
        log.warning("не удалось задать команды бота", exc_info=True)
    log.info("старт: @%s, учеников %s", me.username, eng.st.q("select count(*) from users")[0][0])


async def post_stop(app: Application):
    task = app.bot_data.get("reminder")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def main():
    if not os.environ.get("JP_BOT_TOKEN"):
        raise SystemExit("Не задан JP_BOT_TOKEN: токен бота от @BotFather (см. .env.example)")
    app = (Application.builder().token(os.environ["JP_BOT_TOKEN"]).post_init(post_init).post_stop(post_stop)
           .connect_timeout(10).read_timeout(20).write_timeout(30).build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("time", cmd_time))
    app.add_handler(CommandHandler("tz", cmd_tz))
    app.add_handler(CommandHandler(["menu", "help"], cmd_menu))
    app.add_handler(CommandHandler("feedback", cmd_feedback))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.PHOTO | filters.VIDEO | filters.VIDEO_NOTE | filters.AUDIO |
                                   filters.Document.ALL, on_media))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)


if __name__ == "__main__":
    main()
