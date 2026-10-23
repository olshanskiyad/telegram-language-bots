"""Прогон simulate.py и test_bot.py на маленьких тестовых данных, составленных вручную: словари, Tatoeba и озвучка не нужны.

  python3 tests/smoke.py          данные во временной папке, затем оба прогона
  python3 tests/smoke.py DIR      то же, но данные остаются в DIR (для ручных прогонов с JP_DATA=DIR)

Тестовые данные: bank.json той же формы, что у build.py, но из десятка кандзи и нескольких десятков слов,
которые написаны здесь вручную (без JMdict, KANJIDIC2, JLPT и Tatoeba). Азбука (content/abc.txt) и грамматика
(content/grammar.txt) настоящие: они лежат в репозитории и словарей не требуют. На каждый путь озвучки и картинки,
который запросит бот, кладётся пустая запись (нужен ffmpeg: бот склеивает записи им же).

Полный прогон на настоящих данных: python3 fetch_data.py && python3 build.py --audio, потом
python3 simulate.py && python3 test_bot.py (см. README).
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.dirname(HERE)

# слово, чтение, перевод
VOCAB = {
    "N5": [("水", "みず", "вода"), ("本", "ほん", "книга"), ("犬", "いぬ", "собака"), ("駅", "えき", "станция"),
           ("友達", "ともだち", "друг")],
    "N4": [("勉強", "べんきょう", "учёба"), ("病院", "びょういん", "больница"), ("約束", "やくそく", "обещание"),
           ("準備", "じゅんび", "подготовка"), ("親切", "しんせつ", "добрый, любезный")],
    "N3": [("経験", "けいけん", "опыт"), ("結果", "けっか", "результат"), ("習慣", "しゅうかん", "привычка"),
           ("予定", "よてい", "план"), ("関係", "かんけい", "отношение")],
}
# слова только каной, для «как пишется» и «кана на слух»
KANA_WORDS = [("さかな", "рыба"), ("くるま", "машина"), ("ともだち", "друг"), ("たまご", "яйцо"), ("おかね", "деньги"),
              ("ゆうびん", "почта"), ("コーヒー", "кофе"), ("テレビ", "телевизор"), ("ホテル", "отель"),
              ("パソコン", "компьютер"), ("カメラ", "фотоаппарат"), ("タクシー", "такси"), ("ビール", "пиво")]
# кандзи: (кандзи, значение, онные, кунные)
KANJI_MEAN = {
    "N5": [("日", "день; солнце", ["ニチ", "ジツ"], ["ひ", "か"]), ("山", "гора", ["サン"], ["やま"]),
           ("水", "вода", ["スイ"], ["みず"]), ("人", "человек", ["ジン", "ニン"], ["ひと"])],
    "N4": [("会", "встречаться", ["カイ", "エ"], ["あ.う"]), ("新", "новый", ["シン"], ["あたら.しい"]),
           ("駅", "станция", ["エキ"], []), ("魚", "рыба", ["ギョ"], ["さかな", "うお"])],
    "N3": [("頭", "голова", ["トウ", "ズ"], ["あたま"]), ("光", "свет", ["コウ"], ["ひかり"]),
           ("港", "порт", ["コウ"], ["みなと"]), ("石", "камень", ["セキ"], ["いし"])],
}
# слово, чтение, перевод, вид чтения, три неверных чтения
KANJI_READ = {
    "N5": [("山", "やま", "гора", "kun", ["さん", "やみ", "ざん"]), ("水", "みず", "вода", "kun", ["すい", "みす", "むず"]),
           ("学校", "がっこう", "школа", "on", ["がくこう", "がっごう", "がくごう"]),
           ("先生", "せんせい", "учитель", "on", ["せんしょう", "さきうまれ", "せんせ"])],
    "N4": [("新聞", "しんぶん", "газета", "on", ["しんもん", "しんぷん", "あたらぶん"]),
           ("会社", "かいしゃ", "компания", "on", ["かいじゃ", "えしゃ", "あいしゃ"]),
           ("魚", "さかな", "рыба", "kun", ["ぎょ", "うおな", "さけな"]),
           ("新しい", "あたらしい", "новый", "kun", ["しんしい", "あらたしい", "あたらいし"])],
    "N3": [("経験", "けいけん", "опыт", "on", ["きょうけん", "けいげん", "へけん"]),
           ("港", "みなと", "порт", "kun", ["こう", "みなど", "みなつ"]),
           ("光", "ひかり", "свет", "kun", ["こう", "ひがり", "ひかい"]),
           ("結果", "けっか", "результат", "on", ["けつか", "けっが", "むすびか"])],
}
# урок «два чтения»: кандзи, кунное слово, чтение, перевод, онное слово, чтение, перевод, неверные кунные, неверные онные
ONKUN = [
    ("山", "山", "やま", "гора", "富士山", "ふじさん", "гора Фудзи", ["さん", "やむ", "せん"], ["ふじやま", "ふしさん", "ふじざん"]),
    ("水", "水", "みず", "вода", "水曜日", "すいようび", "среда", ["すい", "みす", "むず"], ["みずようび", "すいようひ", "すいようにち"]),
    ("人", "人", "ひと", "человек", "日本人", "にほんじん", "японец", ["じん", "ひど", "ふと"], ["にほんひと", "にほんにん", "ひほんじん"]),
    ("書", "書く", "かく", "писать", "辞書", "じしょ", "словарь", ["しょく", "かこく", "かい"], ["じがく", "じしょう", "ちしょ"]),
    ("食", "食べる", "たべる", "есть", "食堂", "しょくどう", "столовая", ["しょく", "たべ", "たびる"], ["たべどう", "しょくどお", "じきどう"]),
    ("大", "大きい", "おおきい", "большой", "大学", "だいがく", "университет", ["だい", "おきい", "おうきい"], ["おおがく", "だいがっ", "たいがく"]),
]
ONKUN_EXCEPT = [("今日", "きょう", "сегодня"), ("大人", "おとな", "взрослый")]
# уроки кандзи: по пять кандзи; слова-примеры: (слово, чтение, перевод, вид, три неверных чтения)
LESSONS = [("N5", "日山水人本"), ("N4", "会新駅魚家")]
EXAMPLES = {
    "日": [("日", "ひ", "солнце, день", "kun", ["にち", "か", "じつ"]), ("毎日", "まいにち", "каждый день", "on", ["まいひ", "めいにち", "まいじつ"])],
    "山": [("山", "やま", "гора", "kun", ["さん", "やみ", "ざん"]), ("火山", "かざん", "вулкан", "on", ["ひやま", "かさん", "かやま"])],
    "水": [("水", "みず", "вода", "kun", ["すい", "みづ", "みす"]), ("水曜日", "すいようび", "среда", "on", ["みずようび", "すいようひ", "すいようにち"])],
    "人": [("人", "ひと", "человек", "kun", ["じん", "にん", "ひど"]), ("日本人", "にほんじん", "японец", "on", ["にほんひと", "にほんにん", "ひほんじん"])],
    "本": [("本", "ほん", "книга", "on", ["もと", "ぽん", "ぼん"]), ("日本", "にほん", "Япония", "on", ["にぽん", "ひほん", "にぼん"])],
    "会": [("会う", "あう", "встречаться", "kun", ["かう", "えう", "あわ"]), ("会社", "かいしゃ", "компания", "on", ["かいじゃ", "えしゃ", "あいしゃ"])],
    "新": [("新しい", "あたらしい", "новый", "kun", ["しんしい", "あらたしい", "あたらいし"]), ("新聞", "しんぶん", "газета", "on", ["しんぷん", "しんもん", "あらぶん"])],
    "駅": [("駅", "えき", "станция", "on", ["いき", "えぎ", "やく"]), ("駅員", "えきいん", "работник станции", "on", ["えきいいん", "えきじん", "えきえん"])],
    "魚": [("魚", "さかな", "рыба", "kun", ["ぎょ", "さかの", "さけな"]), ("金魚", "きんぎょ", "золотая рыбка", "on", ["きんうお", "かねぎょ", "きんさかな"])],
    "家": [("家", "いえ", "дом", "kun", ["いけ", "いお", "いや"]), ("家族", "かぞく", "семья", "on", ["いえぞく", "やぞく", "かそく"])],
}
KANJI_ON_KUN = {"日": (["ニチ", "ジツ"], ["ひ", "か"]), "山": (["サン"], ["やま"]), "水": (["スイ"], ["みず"]),
                "人": (["ジン", "ニン"], ["ひと"]), "本": (["ホン"], ["もと"]), "会": (["カイ", "エ"], ["あ.う"]),
                "新": (["シン"], ["あたら.しい"]), "駅": (["エキ"], []), "魚": (["ギョ"], ["さかな", "うお"]),
                "家": (["カ", "ケ"], ["いえ", "や", "うち"])}
# фразы на слух: (уровень, фраза, чтение каной, перевод)
LISTEN = [
    (1, "おはよう", "", "Доброе утро"), (1, "ありがとう", "", "Спасибо"), (1, "さようなら", "", "До свидания"),
    (1, "いただきます", "", "Приятного аппетита"), (1, "ただいま", "", "Я дома"),
    (2, "これは本です", "これはほんです", "Это книга"), (2, "水をください", "みずをください", "Дайте воды, пожалуйста"),
    (2, "駅はどこですか", "えきはどこですか", "Где станция?"), (2, "私は学生です", "わたしはがくせいです", "Я студент"),
    (2, "お名前は何ですか", "おなまえはなんですか", "Как вас зовут?"),
    (3, "今日はいい天気ですね", "きょうはいいてんきですね", "Сегодня хорошая погода, правда?"),
    (3, "明日は友達と会います", "あしたはともだちとあいます", "Завтра я встречусь с другом"),
    (3, "日本語を勉強しています", "にほんごをべんきょうしています", "Я учу японский язык"),
    (3, "もう少しゆっくり話してください", "もうすこしゆっくりはなしてください", "Говорите, пожалуйста, чуть медленнее"),
    (3, "毎朝コーヒーを飲みます", "まいあさコーヒーをのみます", "Каждое утро я пью кофе"),
]


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
    """Складывает тестовые данные в папку data (JP_DATA); возвращает путь к bank.json."""
    os.environ["JP_DATA"] = data
    sys.path.insert(0, BOT)
    import azbuka
    import build
    import jpnum as N
    import tts
    from PIL import Image

    rnd = random.Random(7)
    img = f"{data}/img"
    os.makedirs(f"{data}/audio/native", exist_ok=True)

    def placeholder(path, *_):  # картинка-заглушка вместо рисования шрифтом Klee One
        Image.new("RGB", (8, 8)).save(path)

    azbuka.draw = placeholder
    content = build.kanji_content()  # значения, подсказки и эмодзи кандзи: настоящие, из content/kanji.txt
    abc = azbuka.build(os.path.join(BOT, "content/abc.txt"), build.check, build.errors, build.report)
    wrong = lambda w: build.kana_distractors(w, rnd)  # noqa: E731
    kana = [{"jp": w, "kana": w, "ru": ru, "wrong": wrong(w)} for w, ru in KANA_WORDS]
    kana = [x for x in kana if len(x["wrong"]) == 3]
    course_kanji, lessons = {}, []
    for n, (lv, chars) in enumerate(LESSONS, 1):
        lessons.append({"n": n, "lvl": lv, "kanji": list(chars)})
        for ch in chars:
            c = content[ch]
            on, kun = KANJI_ON_KUN[ch]
            course_kanji[ch] = {"ru": c["ru"], "emo": c["emo"], "hook": c["hook"], "lvl": lv, "lesson": n,
                                "ex": [{"jp": j, "kana": k, "ru": r, "kind": kind, "wrong": w} for j, k, r, kind, w in EXAMPLES[ch]],
                                "on": on[:2], "kun": [x.replace("-", "") for x in kun][:2]}
    course = {"lessons": lessons, "kanji": course_kanji}
    onkun_pairs = [{"ch": ch, "kun": {"jp": kj, "kana": kr, "ru": kru}, "on": {"jp": oj, "kana": orr, "ru": oru},
                    "wrong_kun": wk, "wrong_on": wo} for ch, kj, kr, kru, oj, orr, oru, wk, wo in ONKUN]
    ear = [{"kana": e["kana"], "jp": e["jp"], "ru": e["ru"], "wrong": wrong(e["kana"])} for x in course_kanji.values() for e in x["ex"]]
    ear += [{"kana": w, "jp": w, "ru": ru, "wrong": wrong(w)} for w, ru in KANA_WORDS]
    ear = [x for x in ear if len(x["wrong"]) == 3]
    listen = [{"sid": str(100 + i), "jp": jp, "kana": kn or jp, "ru": ru, "audio_id": str(9000 + i), "speaker": f"speaker{i % 3 + 1}",
               "license": "CC BY 4.0", "lvl": lvl} for i, (lvl, jp, kn, ru) in enumerate(LISTEN)]
    bank = {"vocab": {lv: [{"jp": j, "kana": k, "ru": r} for j, k, r in xs] for lv, xs in VOCAB.items()},
            "kana": kana,
            "kanji_mean": {lv: [{"ch": ch, "ru": ru, "on": on, "kun": kun} for ch, ru, on, kun in xs] for lv, xs in KANJI_MEAN.items()},
            "kanji_read": {lv: [{"jp": j, "kana": k, "ru": r, "kind": kind, "wrong": w} for j, k, r, kind, w in xs] for lv, xs in KANJI_READ.items()},
            "onkun": {"pairs": onkun_pairs, "except": [{"jp": j, "kana": k, "ru": r} for j, k, r in ONKUN_EXCEPT]},
            "listen": listen,
            "grammar": [{"lv": lv, "q": q, "opts": o, "ans": a, "ru": ru} for lv, q, o, a, ru in build.GRAMMAR],
            # допустимые чтения дат: только правила jpnum (у настоящей сборки ещё и JMdict)
            "valid": {"m": {str(m): [N.month(m)] for m in range(1, 13)}, "d": {str(d): [N.day(d)] for d in range(1, 32)},
                      "h": {str(h): [N.HOUR[h]] for h in range(1, 13)}},
            "numbers": build.NUMBERS, "course": course, "kana_ear": ear, "gcourse": build.build_gcourse(), "abc": abc}
    if build.errors:
        raise SystemExit("Тестовые данные не собрались:\n  " + "\n  ".join(build.errors))
    azbuka.images(abc, img)
    for ch in list(course_kanji) + [x[0] for xs in KANJI_MEAN.values() for x in xs]:  # крупные кандзи: заглушки
        placeholder(f"{img}/{azbuka.kanji_name(ch)}")
    # озвучка: настоящая тишина на каждый путь, который попросит бот (hardlink, места почти не занимает)
    silent = f"{data}/audio/_silent.mp3"
    silent_mp3(silent)
    for job in build.audio_jobs(bank):
        path = tts.path_for(*job)
        if not os.path.exists(path):
            link(silent, path)
    for x in listen:
        link(silent, f"{data}/audio/native/{x['audio_id']}.mp3")
    out = f"{data}/bank.json"
    json.dump(bank, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    return out


def main():
    own = len(sys.argv) < 2
    data = tempfile.mkdtemp(prefix="jp-fixture-") if own else os.path.abspath(sys.argv[1])
    os.makedirs(data, exist_ok=True)
    try:
        print("данные:", make(data), flush=True)
        env = {**os.environ, "JP_DATA": data, "JP_LOG": f"{data}/logs"}
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
