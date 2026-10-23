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
DATA = os.path.abspath(os.environ.get("JP_DATA") or os.path.join(HERE, "data"))  # по умолчанию ./data рядом с кодом
AUDIO_DIR = f"{DATA}/audio"
os.makedirs(AUDIO_DIR, exist_ok=True)
MALE, FEMALE = "ja-JP-KeitaNeural", "ja-JP-NanamiNeural"
NORMAL, SLOW, FAST = "+0%", "-30%", "+25%"


# Keita на тех же фразах говорит на ~22% быстрее Nanami (замер по датам, времени и числам: медиана 1.22):
# на слух это «очень быстро, зажевано». Его обычная и медленная скорость ниже, темп как у Nanami.
MALE_RATE = {NORMAL: "-18%", SLOW: "-40%"}


def rate_for(voice, rate):
    return MALE_RATE.get(rate, rate) if voice == MALE else rate


def path_for(text, voice=MALE, rate=NORMAL):
    rate = rate_for(voice, rate)
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
            data = await synth(text, voice, rate_for(voice, rate))
            tmp = p + ".tmp"
            open(tmp, "wb").write(data)
            os.replace(tmp, p)
            return p
        except Exception:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(2 * (attempt + 1))


# ---------- склейки


def concat(paths):
    """Несколько записей подряд с паузой, один файл; повторный вызов берёт готовый из кэша.
    Записей ещё нет (озвучку делает build.py --audio): пустая строка, бот пришлёт текст без голоса."""
    if not all(os.path.exists(p) for p in paths):
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
