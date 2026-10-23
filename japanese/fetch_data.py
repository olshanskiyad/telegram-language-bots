"""Скачивает открытые данные для сборки японского бота в JP_DATA/build (по умолчанию ./data/build).

Что берём и под какой лицензией (полный список в NOTICE и README):
  JMdict, KANJIDIC2, KRADFILE   EDRDG, CC BY-SA 4.0 (слова, чтения, русские значения, кандзи, части кандзи)
  списки JLPT N5-N3             Jonathan Waller (CC BY), в виде CSV из двух открытых репозиториев (MIT)
  Tatoeba                       фразы, переводы, чтение каной, список записей носителей (CC BY 2.0 FR)
  шрифт Klee One                картинки азбуки и кандзи (SIL OFL 1.1)

  python3 fetch_data.py            скачать то, чего ещё нет
  python3 fetch_data.py --force    скачать всё заново

Записи самих носителей (mp3) здесь не качаются: их по одной докачивает fetch_native.py после сборки.
Скачивается около 60 МБ, после распаковки словари и предложения занимают около 350 МБ.
"""
import bz2
import gzip
import os
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.environ.get("JP_DATA") or os.path.join(HERE, "data"))
BUILD = f"{DATA}/build"
UA = "telegram-language-bots-fetch/1.0 (learning bot build)"

EDRDG = "http://ftp.edrdg.org/pub/Nihongo"  # у ftp.edrdg.org нет действующего https-сертификата
TATOEBA = "https://downloads.tatoeba.org/exports"
# Адреса с зафиксированными коммитами: списки не поменяются под ногами.
JLPT_VOCAB = "https://raw.githubusercontent.com/jamsinclair/open-anki-jlpt-decks/1ad66734417aca9dbcca6b2d5ee440cb13ab3ba0/src/n{n}.csv"
JLPT_KANJI = ("https://raw.githubusercontent.com/Bluskyo/JLPT_Vocabulary/4358f932937ad0232194a36e9f4f875094910c6b/"
              "data/kanji/parsedData/n{n}_kanji.csv")
FONT = "https://raw.githubusercontent.com/google/fonts/130fcfb0ff422fec96b335616b2ca99f91914792/ofl/kleeone/{name}"

# (файл в build/, адрес, способ: gz | bz2 | tar:<имя файла в архиве> | None как есть, начало файла для проверки)
FILES = [
    ("JMdict", f"{EDRDG}/JMdict.gz", "gz", "<?xml"),
    ("kanjidic2.xml", "https://www.edrdg.org/kanjidic/kanjidic2.xml.gz", "gz", "<?xml"),
    ("kradfile", f"{EDRDG}/kradfile.gz", "gz", None),
    *[(f"vocab_n{n}.csv", JLPT_VOCAB.format(n=n), None, "expression,reading,meaning") for n in (5, 4, 3)],
    *[(f"kanji_n{n}.csv", JLPT_KANJI.format(n=n), None, "Kanji") for n in (5, 4, 3)],
    ("jpn_sentences.tsv", f"{TATOEBA}/per_language/jpn/jpn_sentences.tsv.bz2", "bz2", None),
    ("rus_sentences.tsv", f"{TATOEBA}/per_language/rus/rus_sentences.tsv.bz2", "bz2", None),
    ("jpn-rus_links.tsv", f"{TATOEBA}/per_language/jpn/jpn-rus_links.tsv.bz2", "bz2", None),
    ("jpn_transcriptions.tsv", f"{TATOEBA}/per_language/jpn/jpn_transcriptions.tsv.bz2", "bz2", None),
    ("sentences_with_audio.csv", f"{TATOEBA}/sentences_with_audio.tar.bz2", "tar:sentences_with_audio.csv", None),
    ("fonts/KleeOne-SemiBold.ttf", FONT.format(name="KleeOne-SemiBold.ttf"), None, None),
    ("fonts/OFL.txt", FONT.format(name="OFL.txt"), None, "Copyright 2020 The Klee Project Authors"),
]


def open_url(url):
    last = None
    for attempt in range(4):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60)
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise SystemExit(f"не скачалось: {url}: {last}")


def fetch(name, url, mode, start):
    path = f"{BUILD}/{name}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open_url(url) as resp, open(tmp, "wb") as out:
        if mode == "gz":
            shutil.copyfileobj(gzip.GzipFile(fileobj=resp), out)
        elif mode == "bz2":
            shutil.copyfileobj(bz2.BZ2File(resp), out)
        elif mode and mode.startswith("tar:"):
            with tarfile.open(fileobj=resp, mode="r|bz2") as tar:  # потоком: архив с записями ~70 МБ после распаковки
                for m in tar:
                    if m.name.split("/")[-1] == mode[4:]:
                        shutil.copyfileobj(tar.extractfile(m), out)
                        break
                else:
                    raise SystemExit(f"в {url} нет файла {mode[4:]}")
        else:
            shutil.copyfileobj(resp, out)
    size = os.path.getsize(tmp)
    head = open(tmp, "rb").read(200).decode("utf-8", "replace").lstrip("﻿")
    if size < 100 or (start and not head.startswith(start)):
        os.remove(tmp)
        raise SystemExit(f"{name}: неожиданное содержимое (источник поменял формат?)")
    os.replace(tmp, path)
    return size


def main():
    force = "--force" in sys.argv
    for name, url, mode, start in FILES:
        if os.path.exists(f"{BUILD}/{name}") and not force:
            print(f"есть: {name}")
            continue
        size = fetch(name, url, mode, start)
        print(f"скачано: {name} ({size / 1e6:.1f} МБ)", flush=True)
    print(f"готово: {BUILD}\nДальше: python3 build.py (лицензии данных: NOTICE)")


if __name__ == "__main__":
    main()
