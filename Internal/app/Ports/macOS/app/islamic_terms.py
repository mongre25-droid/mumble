#!/usr/bin/env python3
"""Islamic terminology dictionary for Mumble — ~250 common Islamic words and names
plus the spellings the speech recognizer tends to produce for each.

USED BY FOREIGN MODE. The local engine does NOT try to *detect* Islamic context or
*correct* terms on its own (it's poor at both). Instead, when the user selects Foreign
from the Island/Deck, `annotate_foreign()` marks any token that phonetically
resembles a known term with slash-separated CANDIDATES in probability order
(e.g. "just" -> "just//Juz"). The cloud AI then reads the `//` notation and picks the
right word from surrounding context — disambiguation is the one thing it's good at.

`correct_islamic_terms()` (the old gated auto-corrector) is retained for reference and
the offline fallback, but the live Foreign path uses `annotate_foreign()`."""

import re

# Each entry: (correct_word, [common_mis_transcriptions])
# The mis-transcriptions are what faster-whisper often produces instead.
ISLAMIC_TERMS = [
    # ---- Core concepts ----
    # "allow"/"a law" removed: both are edit-distance 2 from "allah" (len>4 →
    # guard threshold 2), so correct_islamic_terms auto-corrected the common
    # English word "allow" to "Allah" whenever any Islamic gateway word was
    # present ("Allah will allow it" -> "Allah will Allah it"). The real
    # mishearings below still cover genuine cases.
    ("Allah", ["allah", "ala", "alla", "allar", "aller"]),
    ("Quran", ["qur'an", "koran", "quran", "coran", "cure on", "core on"]),
    ("Islam", ["islam", "islam", "izlam", "his lamb", "is lamb"]),
    ("Muslim", ["muslim", "muslim", "mozlem", "muslin", "muss lim"]),
    ("Iman", ["iman", "e man", "eamon", "i man", "amen"]),
    ("Ihsan", ["ihsan", "e sun", "eason", "i sun", "i san"]),
    ("Tawheed", ["tawheed", "tawhid", "tauheed", "towed", "to heed", "tawhid"]),
    ("Shirk", ["shirk", "shark", "shirk", "sherk", "chirk"]),
    ("Kufr", ["kufr", "kufar", "coofer", "cooper", "kuffar"]),
    ("Bid'ah", ["bida", "bidah", "beater", "bidder", "beda", "bid ah"]),
    ("Sunnah", ["sunnah", "suna", "sooner", "sun nah", "sun ah"]),
    ("Hadith", ["hadith", "hadeeth", "hadif", "had eat", "hadees"]),
    ("Fiqh", ["fiqh", "fiq", "fick", "fikh", "feek", "fig", "fix", "fish"]),
    ("Shariah", ["shariah", "sharia", "shari'a", "sharia", "charia", "sharee ah"]),
    ("Fatwa", ["fatwa", "fatwah", "fat war", "fatwaa"]),
    ("Ijtihad", ["ijtihad", "ijtehad", "ijtihad", "each the had"]),
    ("Qiyas", ["qiyas", "kiyas", "key us", "key ass", "kias"]),
    ("Ijma", ["ijma", "ijmaa", "each ma", "ij ma"]),
    ("Ummah", ["ummah", "uma", "oomah", "um ah", "umma"]),
    ("Dawah", ["dawah", "dawa", "da'wah", "the one", "dower", "dawa"]),
    ("Jihad", ["jihad", "jihad", "ji had", "gee had"]),
    ("Hijrah", ["hijrah", "hijra", "hijra", "hitch raw", "hidger"]),
    # ---- Five Pillars ----
    ("Shahada", ["shahada", "shahadah", "shahada", "shehada", "shah adder"]),
    ("Salah", ["salah", "salaah", "salat", "salaat", "seller", "salad", "sala"]),
    ("Zakat", ["zakat", "zakaat", "zakah", "zucker", "the cat", "zak at"]),
    ("Sawm", ["sawm", "saum", "sawn", "some", "sum", "swam", "swarm"]),
    ("Hajj", ["hajj", "haj", "hadge", "hudge", "hajji", "hutch", "haj"]),
    # ---- Prayer terms ----
    ("Sujud", ["sujud", "sujood", "sood", "sujood", "so judge", "sue jude"]),
    ("Ruku", ["ruku", "rukoo", "rooku", "rukoo", "rookoo", "wruku"]),
    ("Rakah", ["rakah", "rakat", "raka", "wreck ah", "racker", "rock ah"]),
    ("Wudu", ["wudu", "wudhu", "wudoo", "woodoo", "would do", "who do"]),
    ("Ghusl", ["ghusl", "ghusul", "goosal", "guzzle", "ghostle", "rustle"]),
    ("Tayammum", ["tayammum", "tayammum", "tie a mum", "tay um um"]),
    ("Qibla", ["qibla", "qiblah", "kibla", "kibler", "quibla", "quibble ah"]),
    ("Adhan", ["adhan", "azan", "athan", "a than", "as an", "add han"]),
    ("Iqamah", ["iqamah", "iqama", "ikamah", "each ama", "i come ah"]),
    ("Imam", ["imam", "imam", "i mam", "e mom", "imam"]),
    ("Khutbah", ["khutbah", "khutba", "kutbah", "cut bah", "hut bah"]),
    ("Jumuah", ["jumu'ah", "jummah", "juma", "jumaat", "jew ma", "juma ah"]),
    ("Masjid", ["masjid", "masjid", "mosque", "mass jid", "musk did"]),
    ("Mihrab", ["mihrab", "mihrab", "me rob", "me rub", "mih rab"]),
    ("Minbar", ["minbar", "minbar", "min bar", "mean bar"]),
    # ---- Fasting & Ramadan ----
    ("Ramadan", ["ramadan", "ramzan", "ramadhan", "ram a don", "ram a dawn"]),
    ("Suhur", ["suhur", "suhoor", "sehri", "sue her", "soo her", "sewer"]),
    ("Iftar", ["iftar", "iftari", "if tar", "iftar", "eve tar", "lift tar"]),
    ("Tarawih", ["tarawih", "taraweeh", "tarawi", "terra we", "tara we"]),
    ("Tahajjud", ["tahajjud", "tahajud", "ta ha jud", "taha jud"]),
    ("Qiyam", ["qiyam", "kiyam", "key am", "qi yam", "key yam"]),
    ("I'tikaf", ["itikaf", "etikaaf", "itikaf", "e tea cough", "atikaf"]),
    (
        "Laylatul Qadr",
        [
            "laylatul qadr",
            "laylat al qadr",
            "late tool cudder",
            "late little coder",
            "laila till cudder",
        ],
    ),
    ("Eid", ["eid", "id", "eed", "eve", "e'd", "e d"]),
    ("Eid al-Fitr", ["eid al fitr", "eid ul fitr", "eed al fitter", "id al fitter"]),
    ("Eid al-Adha", ["eid al adha", "eid ul adha", "eed al other", "id al adha"]),
    # ---- Quranic terms ----
    ("Surah", ["surah", "sura", "sura", "sure ah", "sir ah"]),
    ("Ayah", ["ayah", "ayat", "ayah", "eye ah", "i ah"]),
    ("Juz", ["juz", "juzz", "just", "juice", "jews", "joos", "juss"]),
    ("Tafsir", ["tafsir", "tafseer", "tafsir", "tap see", "tafseer"]),
    ("Tajweed", ["tajweed", "tajwid", "tajweed", "taj weed", "tag weed"]),
    ("Hifz", ["hifz", "hifz", "hifts", "hifts", "hiffs", "hips"]),
    ("Basmala", ["basmala", "basmallah", "bismillah", "biss miller"]),
    ("Bismillah", ["bismillah", "bismillah", "biss miller", "bis mill ah"]),
    (
        "Alhamdulillah",
        [
            "alhamdulillah",
            "alhamdulillah",
            "al hum do lillah",
            "al ham do lila",
            "all hum do lil la",
        ],
    ),
    (
        "Subhanallah",
        ["subhanallah", "subhan allah", "sub han allah", "so pan allah", "subhanala"],
    ),
    (
        "Allahu Akbar",
        [
            "allahu akbar",
            "allahu akbar",
            "allah who akbar",
            "ala who akbar",
            "allah akbar",
        ],
    ),
    (
        "Astaghfirullah",
        [
            "astaghfirullah",
            "astaghfirullah",
            "a star fear ullah",
            "ustagh firullah",
            "astag fir allah",
        ],
    ),
    (
        "Inshallah",
        ["inshallah", "insha allah", "in shall ah", "inshallah", "in sha allah"],
    ),
    (
        "Mashallah",
        ["mashallah", "masha allah", "masha allah", "mash allah", "ma sha allah"],
    ),
    ("Jazakallah", ["jazakallah", "jazak allah", "jazz callah", "jazz ak allah"]),
    # ---- Islamic figures ----
    ("Muhammad", ["muhammad", "mohammed", "mohamed", "muhammed", "mohammad"]),
    ("Rasul", ["rasul", "rasool", "rasul", "russell", "russ sol"]),
    ("Nabi", ["nabi", "nabi", "navi", "na be", "navy"]),
    ("Messenger", ["messenger", "messenger"]),
    ("Sahabah", ["sahabah", "sahaba", "sahaba", "sa haba", "sah haba"]),
    ("Abu Bakr", ["abu bakr", "abu bakr", "abou bakr", "abbou backer"]),
    ("Umar", ["umar", "omar", "umar", "oomer", "oo mar"]),
    ("Uthman", ["uthman", "usman", "othman", "oothman", "use man"]),
    ("Ali", ["ali", "ali", "ally", "ollie", "ah lee"]),
    ("Aisha", ["aisha", "aisha", "ayesha", "eye sha", "a isha"]),
    ("Fatima", ["fatima", "fatimah", "fatema", "fatty ma", "fati ma"]),
    ("Khadija", ["khadija", "khadeeja", "khadeeja", "khadijah", "ka deeja"]),
    ("Hasan", ["hasan", "hassan", "hassan", "hasan", "hass in"]),
    ("Husayn", ["husayn", "hussain", "hussein", "who sane", "hussain"]),
    ("Ibrahim", ["ibrahim", "ibrahim", "ibraheem", "ibra him", "e bra him"]),
    ("Musa", ["musa", "moosa", "musa", "moo sa", "moosa"]),
    ("Isa", ["isa", "isa", "eesa", "e sa", "eesa"]),
    ("Maryam", ["maryam", "maryam", "mariam", "mary am", "marry um"]),
    ("Yusuf", ["yusuf", "yusuf", "yousuf", "you suf", "you stuff"]),
    ("Yunus", ["yunus", "yunus", "younus", "you nus", "yunus"]),
    ("Sulayman", ["sulayman", "sulaiman", "suleiman", "sulay man", "sully man"]),
    ("Dawud", ["dawud", "dawood", "dawud", "da wood", "david"]),
    ("Jibril", ["jibril", "jibreel", "gabriel", "jib reel", "jib rail"]),
    ("Mikail", ["mikail", "mikaeel", "michael", "mick ale", "mee kale"]),
    ("Israfil", ["israfil", "israfeel", "israfil", "is raffle", "isra feel"]),
    # ---- Islamic law / ethics ----
    ("Halal", ["halal", "halal", "hallal", "ha lal", "hull al"]),
    ("Haram", ["haram", "haram", "haram", "ha ram", "har um"]),
    ("Makruh", ["makruh", "makrooh", "makruh", "mack roo", "mac rue"]),
    ("Mustahabb", ["mustahabb", "mustahab", "must hab", "musta hab", "must have"]),
    ("Mubah", ["mubah", "mubah", "muba", "moo bah", "moo bar"]),
    ("Fard", ["fard", "fard", "fardh", "farred", "fared", "fard"]),
    ("Wajib", ["wajib", "wajib", "wageeb", "wa jib", "wajib"]),
    ("Nafl", ["nafl", "nafl", "nafil", "naffle", "naffal", "naffle"]),
    ("Niyyah", ["niyyah", "niyyah", "niya", "knee yah", "knee ah", "niya"]),
    ("Taqwa", ["taqwa", "taqwa", "tuckwa", "tack wa", "tuck wa"]),
    ("Sabr", ["sabr", "sabr", "sabur", "sabre", "sober", "sa ber"]),
    ("Shukr", ["shukr", "shukr", "shukur", "shooker", "sugar", "shook ur"]),
    ("Tawakkul", ["tawakkul", "tawakkul", "tawakul", "ta wackle", "tawakkul"]),
    ("Tawbah", ["tawbah", "tawba", "tauba", "taubah", "tall bar", "tau bah"]),
    ("Dua", ["dua", "dua", "duaa", "do a", "do ah", "duer", "du ah"]),
    ("Dhikr", ["dhikr", "dhikr", "zikr", "thicker", "zicker", "dicker"]),
    ("Tasbih", ["tasbih", "tasbih", "tasbeeh", "taz bee", "tass bee"]),
    (
        "Istighfar",
        [
            "istighfar",
            "istighfar",
            "istigfar",
            "a stiff far",
            "is tick far",
            "a stick far",
        ],
    ),
    ("Barakah", ["barakah", "baraka", "baraka", "barrack ah", "bear a car"]),
    ("Rizq", ["rizq", "rizq", "rizk", "risk", "riz", "risked"]),
    ("Akhirah", ["akhirah", "akhira", "akira", "a key rah", "achira"]),
    ("Jannah", ["jannah", "jannah", "janna", "jenna", "john ah", "jan ah"]),
    ("Jahannam", ["jahannam", "jahannam", "jahanam", "ja han num", "juhannam"]),
    ("Barzakh", ["barzakh", "barzakh", "barzak", "barsack", "bar sack"]),
    ("Qadar", ["qadar", "qadr", "kadar", "cutter", "cudder", "khadar"]),
    ("Mahdi", ["mahdi", "mahdi", "mahdi", "ma dee", "mardi", "mah dee"]),
    ("Dajjal", ["dajjal", "dajjal", "dajal", "dodge all", "dadge all"]),
    # ---- Places ----
    ("Makkah", ["makkah", "makkah", "mecca", "mecca", "muck ah", "macker"]),
    ("Madinah", ["madinah", "madinah", "medina", "ma deena", "mad in ah"]),
    ("Kaaba", ["kaaba", "ka'ba", "kaaba", "cobra", "car bar", "ka ba"]),
    ("Arafat", ["arafat", "arafat", "arafah", "are a fat", "air a fat"]),
    ("Mina", ["mina", "mina", "meena", "me nah", "min ah"]),
    ("Muzdalifah", ["muzdalifah", "muzdalifah", "muzdalifa", "muzz dal if ah"]),
    ("Zamzam", ["zamzam", "zam zam", "zam zam", "zam zam"]),
    ("Safa", ["safa", "safa", "safa", "safer", "suffer", "saffa"]),
    ("Marwah", ["marwah", "marwah", "marwa", "mar war", "mar wa"]),
    ("Masjid al-Haram", ["masjid al haram", "masjid ul haram", "mosque al haram"]),
    ("Masjid an-Nabawi", ["masjid an nabawi", "masjid nabvi", "mosque an nabawi"]),
    # ---- Umrah / Hajj ----
    ("Umrah", ["umrah", "umra", "umrah", "oomra", "um rah", "um ra"]),
    ("Ihram", ["ihram", "ihram", "ihram", "e ram", "e rum", "i ram"]),
    ("Tawaf", ["tawaf", "tawaf", "tawaaf", "ta waff", "two off", "tall off"]),
    ("Sa'i", ["sai", "saee", "sa'i", "sigh", "sigh ee", "sah ee"]),
    ("Talbiyah", ["talbiyah", "talbiya", "talbia", "tall be ya", "tal bee ah"]),
    # ---- Calendar ----
    ("Muharram", ["muharram", "muharram", "moharram", "mu harram", "moo haram"]),
    ("Ashura", ["ashura", "ashura", "ashoora", "a shura", "ash oora"]),
    ("Rabi al-Awwal", ["rabi al awwal", "rabi ul awwal", "rabbi al awwal"]),
    ("Rajab", ["rajab", "rajab", "rajab", "ra job", "radge ab"]),
    ("Shaban", ["shaban", "shaban", "shaban", "sha ban", "shab an"]),
    ("Shawwal", ["shawwal", "shawwal", "shawal", "shall wall", "shaw wal"]),
    ("Dhul Hijjah", ["dhul hijjah", "dhul hijja", "zul hijja", "dull hijja"]),
    # ---- Sects / schools ----
    ("Hanafi", ["hanafi", "hanafi", "hanafi", "hanna fee", "han afi"]),
    ("Maliki", ["maliki", "maliki", "maliki", "ma leeky", "ma lee key"]),
    ("Shafi'i", ["shafii", "shafii", "shafi", "shar fee", "shah fee"]),
    ("Hanbali", ["hanbali", "hanbali", "hanbali", "hun bali", "han bali"]),
    ("Sunni", ["sunni", "sunni", "sunny", "sunni", "soon knee"]),
    ("Shia", ["shia", "shia", "she a", "she ah", "shiah"]),
    ("Sufi", ["sufi", "sufi", "soofi", "sue fee", "soo fee"]),
    # ---- Additional common terms ----
    ("Amanah", ["amanah", "amana", "amana", "a mana", "ummanah"]),
    ("Adab", ["adab", "adab", "a dab", "addab", "a daab"]),
    ("Akhlaq", ["akhlaq", "akhlaq", "akhlaak", "uck lack", "ack lack"]),
    ("Aman", ["aman", "aman", "a man", "amen", "umman"]),
    ("Awrah", ["awrah", "awra", "aura", "aw rah", "orrah"]),
    ("Bay'ah", ["bayah", "baya", "bay ah", "buy ah", "biya"]),
    ("Dunya", ["dunya", "dunya", "duniya", "dun ya", "dune yah"]),
    ("Fajr", ["fajr", "fajr", "fajer", "fudger", "fudge er", "farjer"]),
    ("Dhuhr", ["dhuhr", "zuhr", "dhuhr", "zohar", "thuhr", "do her", "zur"]),
    ("Asr", ["asr", "asr", "aser", "asser", "ass er", "usser"]),
    ("Maghrib", ["maghrib", "maghrib", "magrib", "mug rib", "muck rib", "ma grebe"]),
    ("Isha", ["isha", "isha", "eesha", "e sha", "i sha", "ee sha"]),
    (
        "Witr",
        [
            "witr",
            "witr",
            "witter",
            "witter",
            "victor",
            "witter",
            "with her",
            "withdraw",
            "whitter",
            "witcher",
            "wit her",
        ],
    ),
    ("Qunut", ["qunut", "qunoot", "kunut", "coonut", "coonoot"]),
    ("Khushu", ["khushu", "khushoo", "khushu", "cushoo", "kooshoo"]),
    ("Fidyah", ["fidyah", "fidya", "fidia", "fid ya", "fit ya"]),
    ("Kaffarah", ["kaffarah", "kaffara", "kaffarah", "cuff are ah", "kaffa rah"]),
    ("Hijab", ["hijab", "hijab", "hijab", "hidge ab", "he job", "hi jab"]),
    ("Niqab", ["niqab", "niqab", "nikab", "knee cab", "neek ab"]),
    ("Jilbab", ["jilbab", "jilbab", "jilbab", "jill bab", "jill bob"]),
    ("Khimar", ["khimar", "khimar", "khimar", "key mar", "key more", "ke mar"]),
    ("Mahram", ["mahram", "mahram", "mahram", "ma ram", "ma rum", "mar ham"]),
    ("Nikah", ["nikah", "nikah", "nika", "knee car", "knee kah", "neeka"]),
    ("Talaq", ["talaq", "talaq", "talaak", "tall luck", "tall lock", "tallak"]),
    ("Mahr", ["mahr", "mahr", "maher", "mar", "maar", "myrrh"]),
    ("Wali", ["wali", "wali", "wally", "wolly", "wall ee", "walee"]),
    ("Gharar", ["gharar", "gharar", "garrar", "garaar", "ga rar"]),
    ("Riba", ["riba", "riba", "reeba", "rebar", "reeba", "ri ba"]),
    ("Mudarabah", ["mudarabah", "mudaraba", "mudarabah", "moo dara ba"]),
    ("Murabaha", ["murabaha", "murabaha", "murabaha", "moor a baha"]),
    ("Waqf", ["waqf", "waqf", "wakf", "wuck f", "walk if"]),
    ("Sadaqah", ["sadaqah", "sadaqa", "sadaqah", "sadaka", "sa duck ah"]),
    ("Zakat al-Fitr", ["zakat al fitr", "zakat ul fitr", "zucker al fitter"]),
    ("Khums", ["khums", "khums", "khums", "cooms", "coombs"]),
    ("Taharah", ["taharah", "tahara", "tahara", "ta ha rah", "tah har ah"]),
    ("Najasah", ["najasah", "najasa", "najasa", "na jasa", "naj as ah"]),
    ("Salafi", ["salafi", "salafi", "salafi", "sa laffy", "sal laffy"]),
    ("Madhhab", ["madhhab", "madhhab", "madhab", "mad hub", "math hab"]),
    ("Alim", ["alim", "alim", "alim", "a lim", "alleem", "aleem"]),
    ("Ulama", ["ulama", "ulama", "ulema", "oo lama", "oolama"]),
    ("Mufti", ["mufti", "mufti", "mufti", "muffy", "mooftee"]),
    ("Qadi", ["qadi", "qadi", "qazi", "kazi", "caddy", "cah dee"]),
    ("Ghaib", ["ghaib", "ghaib", "gaib", "guy b", "gibe"]),
    ("Shafa'ah", ["shafaah", "shafa", "shafaa", "sha far", "sha fah"]),
    ("Sirat", ["sirat", "sirat", "sirat", "seerat", "sigh rat", "see rut"]),
    ("Mizan", ["mizan", "mizan", "mizan", "me zon", "mee zan"]),
    ("Kawthar", ["kawthar", "kawthar", "kausar", "cow sir", "cow thar"]),
    ("Asma ul-Husna", ["asma ul husna", "asma al husna", "asmah ul husna"]),
    ("Ruh", ["ruh", "ruh", "rooh", "roo", "rue", "rooh"]),
    ("Nafs", ["nafs", "nafs", "nafs", "nuffs", "naffs", "naps"]),
    ("Qalb", ["qalb", "qalb", "kalb", "kalb", "calb", "kalp", "kulp"]),
    ("Aql", ["aql", "aql", "akl", "ackle", "akel", "a cool"]),
    ("Fitrah", ["fitrah", "fitrah", "fitra", "fit rah", "fit run"]),
    ("Hikmah", ["hikmah", "hikma", "hikmah", "hick mah", "hik ma"]),
    ("Rahmah", ["rahmah", "rahma", "rahmah", "ra ma", "rah mah"]),
    ("Sakinah", ["sakinah", "sakina", "sakeena", "sa keena", "suck eena"]),
    ("Mawlid", ["mawlid", "mawlid", "maulid", "mollid", "maw lid", "mould id"]),
    ("Isra", ["isra", "isra", "isra", "is raw", "his ra"]),
    ("Miraj", ["miraj", "miraj", "mirage", "mee raj", "me radge"]),
    ("Khilafah", ["khilafah", "khilafa", "khilafa", "key lafa", "kill afa"]),
    ("Bayt al-Mal", ["bayt al mal", "bait ul mal", "bait al maal"]),
    ("Hisbah", ["hisbah", "hisba", "hisba", "his bah", "his bar"]),
]

# Build lookup: mis-transcription → correct term
_CORRECTIONS = {}
for _correct, _mis in ISLAMIC_TERMS:
    _CORRECTIONS[_correct.lower()] = _correct  # exact match maps to itself
    for _m in _mis:
        _CORRECTIONS[_m.lower()] = _correct

# Islamic gateway words — these are UNMISTAKABLY Islamic and act as a context
# "bubble". Corrections only activate when ≥1 gateway word is present in the
# text. This prevents common English words (like "just") from being wrongly
# replaced with Islamic terms ("Juz") in everyday sentences.
#
# Gateway words are correct terms that have NO plausible English homophone
# and NO plausible non-Islamic usage. They are the "high-confidence" keys.
_ISLAMIC_GATEWAYS = {
    "allah",
    "quran",
    "qur'an",
    "islam",
    "muslim",
    "tawheed",
    "shirk",
    "kufr",
    "bid'ah",
    "sunnah",
    "hadith",
    "fiqh",
    "shariah",
    "fatwa",
    "ijtihad",
    "qiyas",
    "ijma",
    "ummah",
    "dawah",
    "jihad",
    "hijrah",
    "shahada",
    "salah",
    "zakat",
    "sawm",
    "hajj",
    "sujud",
    "ruku",
    "rakah",
    "wudu",
    "ghusl",
    "tayammum",
    "qibla",
    "adhan",
    "iqamah",
    "imam",
    "khutbah",
    "jumuah",
    "masjid",
    "mihrab",
    "minbar",
    "ramadan",
    "suhur",
    "iftar",
    "tarawih",
    "tahajjud",
    "qiyam",
    "i'tikaf",
    "laylatul qadr",
    "eid",
    "eid al-fitr",
    "eid al-adha",
    "surah",
    "ayah",
    "tafsir",
    "tajweed",
    "hifz",
    "basmala",
    "bismillah",
    "alhamdulillah",
    "subhanallah",
    "allahu akbar",
    "astaghfirullah",
    "inshallah",
    "mashallah",
    "jazakallah",
    "muhammad",
    "rasul",
    "nabi",
}

# Unambiguous MIS-HEARINGS of gateway terms. The gateway set above only holds the
# correctly-spelled words — but in practice the recognizer often mangles the very
# word that should unlock corrections (e.g. the whole sentence is "koran / seller /
# fick" with no clean Islamic word anywhere). These spellings have no plausible
# everyday-English meaning, so seeing one is still high-confidence Islamic context.
# Ambiguous homophones ("seller", "fick", "some", "the cat") are deliberately
# excluded — they only get corrected once a gateway has already opened the bubble.
_GATEWAY_MISHEARINGS = {
    "koran",
    "coran",
    "qur'an",
    "quraan",
    "ramzan",
    "ramadhan",
    "ramadaan",
    "izlam",
    "mozlem",
    "tawhid",
    "tauheed",
    "shariah",
    "sharia",
    "shari'a",
    "hadeeth",
    "hadees",
    "salaah",
    "salaat",
    "zakaat",
    "zakah",
    "wudhu",
    "wudoo",
    "ghusl",
    "adhan",
    "azan",
    "athan",
    "taraweeh",
    "tahajud",
    "suhoor",
    "iftari",
    "masjid",
    "khutba",
    "jummah",
    "jumuah",
    "subhanallah",
    "alhamdulillah",
    "astaghfirullah",
    "jazakallah",
    "bismillah",
    "basmallah",
    "tajwid",
    "tajweed",
    "tafseer",
}

_ALL_GATEWAYS = _ISLAMIC_GATEWAYS | _GATEWAY_MISHEARINGS


def _has_islamic_context(text_lower: str) -> bool:
    """True if the text contains at least one Islamic gateway word (correct OR an
    unambiguous mis-hearing of one), indicating an Islamic context where
    corrections are appropriate."""
    import re as _re

    words = set(_re.findall(r"[a-z']+", text_lower))
    if words & _ALL_GATEWAYS:
        return True
    # Also catch multi-word gateways / substrings (e.g. "allahu akbar").
    return any(gw in text_lower for gw in _ALL_GATEWAYS if " " in gw)


# Everyday English words that ALSO appear as mis-hearing keys (e.g. "shark"→Shirk,
# "salad"→Salah, "sunny"→Sunni, "aura"→Awrah). Inside an Islamic-context sentence the
# edit-distance guard alone passes them (the mis-hearing IS the English word, distance
# 1), so they were force-replaced — corrupting ordinary sentences. NEVER auto-correct a
# token that is itself a common English word; the AI Foreign-annotation path can still
# surface the Islamic term as a candidate to pick by context. Membership is computed
# against the live correction table, so it also covers any future English-word key.
_COMMON_ENGLISH = frozenset("""
a about above after again against all am an and any are arm arms around as ate aura away
back bad bag ball bank bar base be bean bear beat bed been before being below best better
between big bird bit black blood blue board boat body book born both box boy bread break
bring brother build but buy by call came can car card care case cat catch chair charge
cheap check child city class clean clear close cloud coat code cold come cook cool corn
cost could country course cousin cover cow cross cup cut dad dance dark date david day dead
deal dear deep desk did die dinner do dog door down draw dream dress drink drive drop dry
duck dust each ear early earth east easy eat egg eight end enough even ever every eye face
fact fall family far farm fast fat father fear feel feet fell few field fight fill find fine
fire first fish five floor flower fly food foot for found four free fresh friend from front
fruit full fun game garden gas gate gave get girl give glass go gold good got grass great
green ground group grow had hair half hall hand happy hard has hat hate have he head hear
heart heat heavy held hello help hen her here high hill him his hit hold home hope horse hot
house how huge ice idea if ill in into iron is it job join joy jump just keep key kid kill
kind king kiss knee knew know lady lake land large last late laugh lay lead learn leave left
leg less let letter life light like line lion lip list little live long look lord lose lot
loud love low lunch mad made mail main make man many map mark marks may me meal mean meat
meet men mid milk mind mine minute miss money month moon more morning most mother mountain
mouth move much music must my name near neck need net never new news next nice night no nor
north nose not note now ocean of off office often oil old on once one only open or orange
other our out over own page pain paint pair paper park part party pass past pay people pet
pick picture piece pink place plan plant play please point poor power press pull push put
queen quick quiet quite race radio rain ran reach read ready real red rest rice rich ride
right ring rise risk river road rock room rope rose round rule run sad safe said salad same
sand sat save saw say sea seat second see seed seem seen self sell send sense set seven
shark she sheep ship shoe shop short should show sick side sight sign silver since sing
sister sit six skin sky sleep slow small smell smile smoke snow so soft some son song soon
sound soup south space speak spend spoon sport spring stand star start stay step stone stop
store story street strong study sugar summer sun sunny sure table tail take talk tall tea
teach team tear tell ten test than that the their them then there these they thick thin
thing think third this those though three through throw time tiny to today toe together told
tomato too took tooth top touch town toy train tree trip true try turn two under until up
upon us use very voice wait walk wall want war warm was wash watch water way we wear week
well went were west wet what wheel when where which while white who whole why wide wife wild
will win wind window wine wing winter wise wish with woman wood word work world would write
wrong yard year yellow yes yet you young your
""".split())


def correct_islamic_terms(text: str) -> str:
    """Replace mis-transcribed Islamic words with their correct forms.
    Only activates when the text contains at least one Islamic gateway word
    (e.g. 'Allah', 'Quran', 'Salah') — this prevents common English words like
    'just' from being wrongly replaced with 'Juz' in non-Islamic sentences."""
    if not text or not text.strip():
        return text

    # Context gate: only activate Islamic corrections when the text
    # contains at least one unmistakably Islamic gateway word.
    if not _has_islamic_context(text.lower()):
        return text

    def _replacer(m):
        word = m.group(0)
        low = word.lower()
        # Never rewrite a token that is itself an everyday English word — even
        # inside an Islamic-context sentence. "the imam saw a shark" must NOT
        # become "...a Shirk"; "Muslim child ate a salad" must NOT become
        # "...ate a Salah". (The AI Foreign annotator can still offer the Islamic
        # term as a context-judged candidate.)
        if low in _COMMON_ENGLISH:
            return word
        corrected = _CORRECTIONS.get(low)
        if corrected is None:
            return word
        # Guard against over-correction: only replace when the spoken word
        # is a close phonetic match to the correct term (edit distance ≤ 1 for
        # short words, ≤ 2 for longer ones). Prevents "just"→"Juz", "some"→"Sawm".
        if _lev(low, corrected.lower()) > (2 if len(corrected) > 4 else 1):
            return word
        # Preserve original case pattern
        if word.isupper():
            return corrected.upper()
        if word[0].isupper():
            return corrected[0].upper() + corrected[1:]
        return corrected

    # Match whole words (letters and apostrophes only)
    return re.sub(r"[A-Za-z']+", _replacer, text)


# ---- Foreign mode: slash-candidate annotation --------------------------------
# Flat list of (heard_spelling, correct_term) pairs, built from every term and all
# of its known mis-hearings. Used to score how close a heard token is to a term.
_MISHEAR_PAIRS = []
for _correct, _mis in ISLAMIC_TERMS:
    for _m in [_correct.lower()] + [x.lower() for x in _mis]:
        _MISHEAR_PAIRS.append((_m.replace(" ", ""), _correct))


def _lev(a: str, b: str) -> int:
    """Levenshtein distance (small strings; cheap)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(
                min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (0 if ca == cb else 1))
            )
        prev = cur
    return prev[-1]


def candidates(token: str, k: int = 2):
    """Return up to `k` Islamic terms a heard token might be, closest first.
    Empty when nothing plausible is near — so ordinary English words are left alone.

    Thresholds scale with length: 3-letter tokens must match a KNOWN mishearing exactly
    (fuzzing them flags everything — 'sat', 'mat'); longer tokens allow 1-2 edits. The
    common-English homophones the user actually means (just->Juz, some->Sawm, seller->
    Salah) are listed as explicit mishearings, so they match exactly at any length."""
    low = re.sub(r"[^a-z']", "", (token or "").lower())
    if len(low) < 3:
        return []
    thr = 0 if len(low) == 3 else (1 if len(low) == 4 else 2)
    best = {}
    for mis, correct in _MISHEAR_PAIRS:
        d = _lev(low, mis)
        if d <= thr and (correct not in best or d < best[correct]):
            best[correct] = d
    ranked = sorted(best.items(), key=lambda x: (x[1], len(x[0])))
    return [c for c, _ in ranked[:k]]


# Pure grammatical glue with ZERO Islamic reading — never annotate these, so the slash
# notation stays meaningful. Content words the user might actually mean as a term
# (e.g. "just" -> Juz, "some" -> Sawm, "after" -> Asr) are deliberately NOT here:
# Foreign mode is explicit, so we offer the candidate and let the AI choose from context.
_FOREIGN_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "is",
    "it",
    "as",
    "at",
    "be",
    "by",
    "on",
    "so",
    "we",
    "he",
    "i",
    "you",
    "for",
    "are",
    "was",
    "with",
    "from",
    "this",
    "that",
    "they",
    "them",
    "have",
    "will",
    "your",
    "but",
    "not",
    "if",
}


def annotate_foreign(text: str) -> str:
    """Foreign mode: mark tokens that may be Islamic/Arabic terms with slash
    candidates in probability order — the ORIGINAL heard word first (most likely as
    spoken), then the closest Islamic term(s). The AI disambiguates from context.
    Example: 'I read just amma yesterday' -> 'I read just//Juz amma//Amma yesterday'.
    Non-matching words are returned untouched.

    Single-word only: each token is scored independently. (A former multi-word
    sliding-bigram pass was DEAD code — _MISHEAR_PAIRS strips spaces, so its
    `" " in orig` key test never fired. It is removed rather than revived
    because the table lists common English bigrams as mishearings — "the cat"
    -> Zakat, "a law" -> Allah, "his lamb" -> Islam — so a live multi-word pass
    would annotate ordinary Foreign-mode text. Reinstating it needs a curated
    multi-word table first.)
    Already-annotated text is returned unchanged (re-annotation guard): the
    old per-token '//' check could never fire — the token regex [A-Za-z']+
    can't capture a slash — so re-feeding annotated text double-annotated
    ('just//Juz//Juz'). Guard on the whole text instead."""
    if not text or not text.strip():
        return text
    # --- Single-word pass ---
    def repl(m):
        tok = m.group(0)
        # Guard each annotated token rather than the entire utterance.  The old
        # global check treated ordinary URLs (``https://...``) as annotations
        # and disabled correction for all later words.
        if text[m.end():m.end() + 2] == "//" or \
                text[max(0, m.start() - 2):m.start()] == "//":
            return tok
        if tok.lower() in _FOREIGN_STOP:
            return tok
        raw = candidates(tok)
        # If the token is ALREADY a correct term (its closest candidate matches it
        # exactly), it's spelled right — don't strip its own form and offer the
        # next-closest WRONG alternative ("the imam led salah" must not become
        # "the imam//Iman led salah//Ayah").
        if raw and raw[0].lower() == tok.lower():
            return tok
        cands = [c for c in raw if c.lower() != tok.lower()]
        if not cands:
            return tok
        return "//".join([tok] + cands)

    return re.sub(r"[A-Za-z']+", repl, text)
