"""Докачка записей носителей с Tatoeba для фраз из bank.json, без загрузки словарей (мало памяти).

Tatoeba режет частые запросы (429), поэтому по одной записи с паузой; уже скачанные пропускаются.
Нужен bank.json (python3 build.py). Записи у каждого диктора со своей лицензией (CC BY или CC BY-NC), бот
подписывает диктора и лицензию под каждой записью.
  python3 fetch_native.py
"""
import json
import os
import time
import urllib.error
import urllib.request

DATA = os.path.abspath(os.environ.get("JP_DATA") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
DIR = f"{DATA}/audio/native"
PAUSE = 10

if not os.path.exists(f"{DATA}/bank.json"):
    raise SystemExit(f"Нет {DATA}/bank.json: сначала python3 fetch_data.py, потом python3 build.py")
os.makedirs(DIR, exist_ok=True)
bank = json.load(open(f"{DATA}/bank.json", encoding="utf-8"))
todo = [x for x in bank["listen"] if not os.path.exists(f"{DIR}/{x['audio_id']}.mp3")]
print(f"записей нужно: {len(todo)}", flush=True)
got = failed = limited = 0
for x in todo:
    for attempt in range(4):
        try:
            req = urllib.request.Request(f"https://tatoeba.org/en/audio/download/{x['audio_id']}",
                                         headers={"User-Agent": "jp-bot-build/1.0 (learning bot build)"})
            data = urllib.request.urlopen(req, timeout=30).read()
            if len(data) > 1000:
                tmp = f"{DIR}/{x['audio_id']}.tmp"
                open(tmp, "wb").write(data)
                os.replace(tmp, f"{DIR}/{x['audio_id']}.mp3")
                got += 1
                break
        except urllib.error.HTTPError as e:
            limited += e.code == 429
            time.sleep(30 * (attempt + 1))
        except Exception:
            time.sleep(15)
    else:
        failed += 1
    if (got + failed) % 20 == 0:
        print(f"  скачано {got}, сбоев {failed}, отказов 429: {limited}", flush=True)
    time.sleep(PAUSE)
print(f"записи носителей: скачано {got}, сбоев {failed}, отказов 429: {limited}", flush=True)
