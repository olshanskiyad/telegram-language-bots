"""Озвучка нейроголосами Microsoft через бесплатный канал читалки Edge (пакет edge-tts).

Каждая фраза синтезируется один раз и лежит файлом в кэше, поэтому бот
не зависит от канала во время уроков, а сервер не нагружается.

Канал неофициальный. Адрес, клиентский токен и заголовки живут в пакете edge-tts, а не здесь: если канал начнёт
отвечать 403 на все фразы, обновите пакет (pip install -U edge-tts). Готовый кэш озвучки при этом не пропадает.
Официальная замена с теми же голосами: Azure AI Speech (есть бесплатный уровень F0).
"""
import asyncio
import hashlib
import os
import subprocess

import edge_tts

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.environ.get("THAI_DATA") or os.path.join(HERE, "data"))  # по умолчанию ./data рядом с кодом
AUDIO_DIR = f"{DATA}/audio"
os.makedirs(AUDIO_DIR, exist_ok=True)
MALE, FEMALE = "th-TH-NiwatNeural", "th-TH-PremwadeeNeural"
NORMAL, SLOW = "+0%", "-35%"


def path_for(text, voice=MALE, rate=NORMAL):
    key = hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:16]
    return f"{AUDIO_DIR}/{key}.mp3"


async def synth(text, voice=MALE, rate=NORMAL):
    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():  # mp3 24 кГц 48 кбит/с: Телеграм принимает как голосовое
        if chunk["type"] == "audio":
            audio += chunk["data"]
    if len(audio) < 500:
        raise RuntimeError(f"пустой ответ синтеза для {text!r}")
    return bytes(audio)


async def ensure(text, voice=MALE, rate=NORMAL, retries=3):
    """Файл из кэша, при отсутствии синтезирует и сохраняет."""
    p = path_for(text, voice, rate)
    if os.path.exists(p):
        return p
    for attempt in range(retries):
        try:
            data = await synth(text, voice, rate)
            tmp = p + ".tmp"
            open(tmp, "wb").write(data)
            os.replace(tmp, p)
            return p
        except Exception:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(2 * (attempt + 1))


# ---------- разные «дикторы» и склейки для тренировки слуха на тоны
HI, LO = "1.189", "0.841"  # ±3 полутона: другая высота голоса, мелодия тона та же, форманты сохраняются


def variant_path(path, kind):
    return path[:-4] + ("_hi.mp3" if kind == "hi" else "_lo.mp3")


def make_variant(path, kind):
    out = variant_path(path, kind)
    if os.path.exists(path) and not os.path.exists(out):
        tmp = out[:-4] + ".tmp.mp3"
        subprocess.run(["nice", "-n", "19", "ffmpeg", "-loglevel", "error", "-y", "-i", path,
                        "-af", f"rubberband=pitch={HI if kind == 'hi' else LO}:formant=preserved",
                        "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "48k", tmp], check=True)
        os.replace(tmp, out)
    return out


def concat(paths):
    """Несколько записей подряд с паузой, один файл; повторный вызов берёт готовый из кэша.
    Записи нет: склеиваем что есть, а не роняем урок. Нет ни одной (озвучку делает build.py --audio):
    пустая строка, бот пришлёт текст без голоса."""
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        return ""
    out = f"{AUDIO_DIR}/cmb_{hashlib.sha1('|'.join(paths).encode()).hexdigest()[:16]}.mp3"
    if not os.path.exists(out):
        gap = f"{AUDIO_DIR}/_silence.mp3"
        if not os.path.exists(gap):  # без паузы ffmpeg молча обрывает склейку на первом слове
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                            "-t", "0.7", "-c:a", "libmp3lame", "-b:a", "48k", gap], check=True)
        seq = [x for p in paths for x in (p, gap)][:-1]
        lst, tmp = out + ".txt", out[:-4] + ".tmp.mp3"
        open(lst, "w").write("".join(f"file '{p}'\n" for p in seq))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                        "-c", "copy", tmp], check=True)
        os.replace(tmp, out)
        os.remove(lst)
    return out
