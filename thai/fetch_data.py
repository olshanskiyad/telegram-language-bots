"""Скачивает тайский словарь для сборки курса: выгрузку Викисловаря с kaikki.org в THAI_DATA/build (по умолчанию ./data/build).

Из словаря build.py берёт транскрипцию Paiboon каждого тайского слова и значения слов для отчёта.
Данные: английский Викисловарь, разобранный проектом Wiktextract (kaikki.org); лицензия CC BY-SA 4.0 и GFDL,
подробнее в NOTICE и README. Файл около 50 МБ.

  python3 fetch_data.py            скачать, если файла ещё нет
  python3 fetch_data.py --force    скачать заново (Викисловарь правят, выгрузка kaikki.org обновляется каждые пару недель)
"""
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.environ.get("THAI_DATA") or os.path.join(HERE, "data"))
BUILD = f"{DATA}/build"
URL = "https://kaikki.org/dictionary/Thai/kaikki.org-dictionary-Thai.jsonl"
PATH = f"{BUILD}/kaikki-thai.jsonl"
UA = "telegram-language-bots-fetch/1.0 (learning bot build)"


def open_url(url):
    last = None
    for attempt in range(4):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60)
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise SystemExit(f"не скачалось: {url}: {last}")


def main():
    if os.path.exists(PATH) and "--force" not in sys.argv:
        print(f"есть: {PATH}")
        return
    os.makedirs(BUILD, exist_ok=True)
    tmp = PATH + ".part"
    with open_url(URL) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out)
    if os.path.getsize(tmp) < 1_000_000 or not open(tmp, encoding="utf-8").readline().startswith("{"):
        os.remove(tmp)
        raise SystemExit("в файле не JSON-строки (источник поменял формат?)")
    os.replace(tmp, PATH)
    index = f"{BUILD}/dict_index.json"
    if os.path.exists(index):  # индекс строится из выгрузки: после новой выгрузки пересобираем при следующей сборке курса
        os.remove(index)
    print(f"скачано: {PATH} ({os.path.getsize(PATH) / 1e6:.1f} МБ)\nДальше: python3 build.py (лицензия данных: NOTICE)")


if __name__ == "__main__":
    main()
