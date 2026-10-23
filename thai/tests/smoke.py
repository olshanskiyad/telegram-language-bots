"""Прогон simulate.py и test_bot.py на курсе, собранном с фиктивным словарём: Викисловарь и озвучка не нужны.

  python3 tests/smoke.py          данные во временной папке, затем оба прогона
  python3 tests/smoke.py DIR      то же, но данные остаются в DIR (для ручных прогонов с THAI_DATA=DIR)

Курс настоящий: content/*.yaml лежит в репозитории, build.py собирает его как обычно. Фиктивен только словарь
транскрипций: там, где в тексте курса рядом со словом стоит его транскрипция («ไม่ (mâi)»), берётся она, для остальных
слов выдумывается, чтобы сборка прошла без выгрузки Викисловаря. Поэтому транскрипции слов без подсказки в таких
данных неверные: годится для проверки движка и бота, не для учёбы. На каждый путь озвучки, который запросит бот,
кладётся запись с тишиной (нужен ffmpeg: бот склеивает записи им же).

Полный прогон на настоящих данных: python3 fetch_data.py && python3 build.py --audio, потом
python3 simulate.py && python3 test_bot.py (см. README).
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.dirname(HERE)

PAIR = re.compile(r"([฀-๿]+)\s*\(([^)]+)\)")  # слово и его транскрипция в скобках, как в build.check_text
THAI_WORD = re.compile(r"[฀-๿]+")
# опорные слова тонов (engine.ANCHORS): здесь транскрипция настоящая, по ним строятся вопросы о тонах
ANCHORS = {"มา": "maa", "ใหม่": "mài", "ไม่": "mâi", "ม้า": "máa", "หมา": "mǎa"}
BASE = ["ka", "kha", "ngaa", "cha", "sa", "da", "ta", "na", "pa", "ma", "ya", "ra", "la", "wa", "ha", "dii", "kaa", "taa"]
TONE_MARKS = ["", "̀", "̂", "́", "̌"]  # средний, низкий, нисходящий, высокий, восходящий


def to_paiboon(tr):
    """Обратное к thaidict.convert: наш вид транскрипции -> вид Викисловаря, чтобы convert вернул то же самое."""
    def syl(s):
        for ours, theirs in (("ph", "p"), ("th", "t"), ("kh", "k")):
            if s.startswith(ours):
                return theirs + s[len(ours):]
        for ours, theirs in (("p", "bp"), ("t", "dt"), ("k", "g")):
            if s.startswith(ours):
                return theirs + s[len(ours):]
        return s
    return " ".join("-".join(syl(x) for x in w.split("-")) for w in tr.split(" "))


def fake(word, variant):
    """Выдуманная транскрипция: слоги по хэшу слова, тон по хэшу и номеру варианта (разные варианты разных тонов)."""
    h = zlib.crc32(word.encode()) + variant * 7
    n = max(1, min(3, len(word) // 3))
    syls = [BASE[(h >> (4 * i)) % len(BASE)] for i in range(n)]
    first = syls[0]
    i = next(k for k, c in enumerate(first) if c in "aiu")
    syls[0] = first[:i + 1] + TONE_MARKS[(h >> 12) % 5] + first[i + 1:]
    return "-".join(syls)


class FakeIndex(dict):
    """Словарь, в котором есть любое тайское слово: build.py спрашивает только .get() и «слово in словарь»."""
    def __init__(self, known):
        super().__init__()
        self.known = known

    def get(self, word, default=None):
        if not word or not THAI_WORD.fullmatch(word):
            return default
        p = [to_paiboon(tr) for tr in self.known.get(word, [])] + [fake(word, k) for k in range(3)]
        if word in ANCHORS:
            p = [ANCHORS[word]] + p
        return {"p": p, "g": ["(слово тестового словаря)"]}

    def __contains__(self, word):
        return self.get(word) is not None


def known_transcriptions():
    """Все пары «слово (транскрипция)» из текстов курса."""
    known = {}
    for path in glob.glob(f"{BOT}/content/*.yaml"):
        for th, tr in PAIR.findall(open(path, encoding="utf-8").read()):
            if len(th) > 1 and " " not in tr and tr not in known.setdefault(th, []):
                known[th].append(tr)
    return known


def silent_mp3(path):
    """Секунда тишины в mp3: настоящий файл, чтобы ffmpeg мог склеивать записи."""
    if not shutil.which("ffmpeg"):
        raise SystemExit("Нужен ffmpeg (бот склеивает записи им же): sudo apt-get install ffmpeg")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "1",
                    "-c:a", "libmp3lame", "-b:a", "48k", path], check=True)


def link(src, dst):
    """Жёсткая ссылка на тишину (места почти не занимает); если файловая система не умеет, копия."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def make(data):
    """Собирает курс с фиктивным словарём в папку data (THAI_DATA) и раскладывает озвучку-тишину."""
    os.environ["THAI_DATA"] = data
    sys.path.insert(0, BOT)
    import thaidict
    thaidict.load_index = lambda: FakeIndex(known_transcriptions())  # build.py берёт словарь при импорте
    import build
    import tts
    os.makedirs(f"{data}/build", exist_ok=True)
    build.main()  # course.json и course_f.json; при ошибке сборки печатает её и выходит с кодом 1
    silent = f"{data}/audio/_silent.mp3"
    silent_mp3(silent)

    def put(path):
        if not os.path.exists(path):
            link(silent, path)
        return path

    courses = [json.load(open(f"{data}/{name}", encoding="utf-8")) for name in ("course.json", "course_f.json")]
    voice = lambda v: tts.MALE if v == "m" else tts.FEMALE  # noqa: E731
    tonal = sorted({it["say"] for c in courses for it in c["items"].values() if it["tone"]})
    for t, v in sorted(build.audio_jobs):  # то же, что build.make_audio озвучивает по-настоящему
        for r in (tts.NORMAL, tts.SLOW):
            put(tts.path_for(t, voice(v), r))
    for t in tonal:
        for v in ("m", "f"):
            put(tts.path_for(t, voice(v), tts.NORMAL))
        for v, kind in (("m", "hi"), ("f", "lo")):
            put(tts.variant_path(tts.path_for(t, voice(v), tts.NORMAL), kind))
    for c in courses:
        for P in c["scenes"]:
            build.place_track(c, P)
    return f"{data}/course.json"


def main():
    own = len(sys.argv) < 2
    data = tempfile.mkdtemp(prefix="thai-fixture-") if own else os.path.abspath(sys.argv[1])
    os.makedirs(data, exist_ok=True)
    try:
        print("данные:", make(data), flush=True)
        env = {**os.environ, "THAI_DATA": data, "THAI_LOG": f"{data}/logs"}
        code = 0
        for script in ("simulate.py", "test_bot.py"):
            print(f"\n=== {script}", flush=True)
            r = subprocess.run([sys.executable, script], cwd=BOT, env=env, capture_output=True, text=True)
            print("\n".join(r.stdout.strip().splitlines()[-6:]) + (("\n" + r.stderr[-1500:]) if r.returncode else ""))
            code = code or r.returncode
        print("\nитог:", "ошибка" if code else "ок")
        return code
    finally:
        if own:
            shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
