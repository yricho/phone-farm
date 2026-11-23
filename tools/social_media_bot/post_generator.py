"""Offline generator for casual, everyday-life post texts (Indonesian).

The dashboard's **Generate** button fills the post text with a short status
like a real person would write about their day: what they are doing, how it
feels, sometimes a question to friends, an emoji or a hashtag. Everything is
assembled from local phrase banks - no network, no API key - and the phrases
follow the time of day and the day of the week, so a morning run does not post
about dinner.

Example::

    from post_generator import PostTextGenerator

    generator = PostTextGenerator()
    print(generator.generate())
    print(generator.generate_many(5))
"""

from __future__ import annotations

import random
import threading
from collections import deque
from datetime import datetime
from typing import Callable, Deque, Dict, List, Optional, Sequence

#: Upper bound for ``generate_many``.
MAX_BATCH = 50

#: Hour ranges (inclusive start, exclusive end) of each time-of-day bucket.
_TIME_BUCKETS = (
    ("pagi", 4, 10),
    ("siang", 10, 15),
    ("sore", 15, 18),
    ("malam", 18, 28),  # wraps past midnight (hour + 24 for 0-3)
)

_ACTIVITIES: Dict[str, Sequence[str]] = {
    "pagi": (
        "ngopi dulu sebelum mulai kerja",
        "bangun kepagian, akhirnya sempat jalan pagi keliling komplek",
        "sarapan nasi uduk di depan gang",
        "antar anak ke sekolah, jalanan lumayan lancar",
        "nyiram tanaman di teras sambil dengar burung",
        "olahraga ringan sepuluh menit, badan langsung enakan",
        "beli bubur ayam langganan, antreannya sudah panjang",
        "bikin teh hangat dan roti bakar seadanya",
        "~berangkat kerja lebih awal biar nggak kena macet",
        "jemur cucian mumpung matahari lagi bagus",
        "dengar podcast sambil siap-siap berangkat",
        "lihat langit pagi ini cerah banget",
    ),
    "siang": (
        "makan siang di warteg langganan",
        "~panas banget siang ini, es teh manis jadi penyelamat",
        "~istirahat sebentar habis meeting yang panjang",
        "pesan makanan online, nunggu abang ojolnya datang",
        "numpang ngadem di minimarket sebentar",
        "~kerjaan numpuk, tapi pelan-pelan beres juga",
        "nyobain warung bakso baru dekat kantor",
        "~ngantuk berat habis makan siang",
        "sempat tidur siang lima belas menit, lumayan seger",
        "beres-beres meja kerja yang sudah berantakan",
        "ngobrol santai sama teman kantor pas jam istirahat",
    ),
    "sore": (
        "sore-sore hujan, paling enak ditemani gorengan",
        "~pulang kerja kena macet seperti biasa",
        "jalan sore di taman dekat rumah",
        "nemenin anak main sepeda di depan rumah",
        "ngopi sore sambil lihat langit oranye",
        "mampir beli martabak buat orang rumah",
        "~akhirnya kerjaan hari ini selesai juga",
        "nyapu halaman sambil nunggu azan magrib",
        "main bola sama anak-anak komplek",
        "beli sayur di tukang sayur keliling",
        "duduk di teras, angin sorenya adem banget",
    ),
    "malam": (
        "masak mi instan pakai telur dan cabai rawit",
        "nonton film sambil rebahan",
        "~baru sampai rumah, langsung mandi air hangat",
        "~begadang beresin kerjaan yang belum selesai",
        "makan malam bareng keluarga",
        "lagi dengar lagu lama, jadi kangen masa sekolah",
        "~nyetrika baju buat besok",
        "ngobrol panjang sama teman lama lewat telepon",
        "cari angkringan buat nongkrong sebentar",
        "baca buku sebelum tidur",
        "~beres-beres dapur habis masak",
        "lihat bulan malam ini terang banget",
    ),
    "any": (
        "~bersih-bersih kamar yang sudah lama nggak dirapikan",
        "ketemu teman lama tanpa sengaja di jalan",
        "nyobain resep baru dari internet",
        "rapikan foto-foto lama di HP",
        "~servis motor yang sudah bunyi aneh dari kemarin",
        "belanja bulanan ke pasar",
        "ganti sprei dan buka jendela biar kamar segar",
        "~bantu tetangga angkat barang pindahan",
        "potong rambut, rasanya jadi lebih ringan",
        "telepon orang tua di kampung",
    ),
}

#: Activities starting with "~" are tiring; they get matching feelings/emojis.
_TIRED = "~"

_FEELINGS_COMMON = (
    "Bersyukur masih dikasih sehat.",
    "Nikmati saja prosesnya.",
    "Hidup memang begini, dijalani saja.",
)

_FEELINGS: Dict[str, Sequence[str]] = {
    "senang": _FEELINGS_COMMON
    + (
        "Alhamdulillah, hari ini terasa ringan.",
        "Hal kecil begini yang bikin bahagia.",
        "Sederhana, tapi cukup.",
        "Rasanya tenang.",
        "Nggak ada yang spesial, tapi hati senang.",
        "Lumayan buat naikin mood.",
    ),
    "lelah": _FEELINGS_COMMON
    + (
        "Capek, tapi tetap disyukuri.",
        "Pelan-pelan saja, yang penting jalan terus.",
        "Kadang memang butuh jeda sebentar.",
        "Semangat terus buat yang lagi berjuang.",
        "Sabar, sebentar lagi beres.",
        "Habis ini mau istirahat dulu.",
    ),
}

_QUESTIONS = (
    "Kalian lagi ngapain?",
    "Ada yang sama?",
    "Siapa yang juga begini?",
    "Gimana hari kalian?",
    "Kalian sendiri gimana?",
)

_REFLECTIONS = (
    "Nggak semua hari harus produktif. Istirahat juga bagian dari usaha.",
    "Bahagia itu sederhana: kopi hangat dan obrolan santai.",
    "Jangan lupa bersyukur untuk hal-hal kecil hari ini.",
    "Pelan bukan berarti berhenti.",
    "Hari yang biasa saja juga patut disyukuri.",
    "Kadang yang kita butuhkan cuma tidur cukup dan makan enak.",
    "Semua ada waktunya, nggak perlu buru-buru.",
    "Capek itu wajar, menyerah jangan.",
    "Rumah adalah tempat paling nyaman untuk pulang.",
    "Senyum dulu, urusan lain belakangan.",
)

_DAY_LINES: Dict[int, Sequence[str]] = {
    0: ("Senin lagi, semangat!", "Awal minggu, bismillah."),
    4: ("Jumat berkah.", "Akhirnya Jumat juga."),
    5: ("Akhir pekan, santai dulu.", "Sabtu yang tenang."),
    6: ("Minggu santai di rumah.", "Hari Minggu, waktunya istirahat."),
}

_EMOJIS: Dict[str, Sequence[str]] = {
    "senang": ("😊", "🙏", "✨", "😌", "☺️"),
    "lelah": ("😅", "🥱", "💪", "🙏"),
}

#: Extra emoji that only fit one time of day.
_TIME_EMOJIS: Dict[str, Sequence[str]] = {
    "pagi": ("☀️",),
    "malam": ("🌙",),
}

_HASHTAGS = (
    "#ceritahariini",
    "#dailylife",
    "#hariini",
    "#santai",
    "#bersyukur",
    "#keseharian",
)


def time_bucket(moment: datetime) -> str:
    """Return ``pagi``, ``siang``, ``sore`` or ``malam`` for *moment*."""

    hour = moment.hour if moment.hour >= 4 else moment.hour + 24
    for name, start, end in _TIME_BUCKETS:
        if start <= hour < end:
            return name
    return "malam"


class PostTextGenerator:
    """Build random everyday-life status texts.

    Args:
        rng: Optional :class:`random.Random` (tests pass a seeded one).
        clock: Returns the current local time; drives the time-of-day phrases.
        recent: How many recent texts are remembered to avoid repeats.
    """

    def __init__(
        self,
        rng: Optional[random.Random] = None,
        clock: Callable[[], datetime] = datetime.now,
        recent: int = 100,
    ) -> None:
        self._rng = rng or random.Random()
        self._clock = clock
        self._recent: Deque[str] = deque(maxlen=max(1, int(recent)))
        self._lock = threading.Lock()

    def generate(self) -> str:
        """Return one post text, avoiding recently generated ones."""

        with self._lock:
            text = self._compose()
            for _ in range(20):
                if text not in self._recent:
                    break
                text = self._compose()
            self._recent.append(text)
            return text

    def generate_many(self, count: int) -> List[str]:
        """Return *count* distinct texts (``1..MAX_BATCH``)."""

        count = max(1, min(MAX_BATCH, int(count)))
        return [self.generate() for _ in range(count)]

    # ------------------------------------------------------------------
    def _compose(self) -> str:
        rng = self._rng
        now = self._clock()
        bucket = time_bucket(now)
        activity = rng.choice(
            list(_ACTIVITIES[bucket]) * 2 + list(_ACTIVITIES["any"])
        )
        mood = "lelah" if activity.startswith(_TIRED) else "senang"
        activity = activity.lstrip(_TIRED)
        style = rng.choices(
            ("feeling", "question", "day", "reflection", "plain"),
            weights=(40, 18, 12, 15, 15),
        )[0]
        day_lines = _DAY_LINES.get(now.weekday())
        if style == "day" and not day_lines:
            style = "feeling"

        if style == "reflection":
            parts = [rng.choice(_REFLECTIONS)]
            mood = "senang"
        else:
            sentence = _sentence(activity)
            if style == "feeling":
                parts = [sentence, rng.choice(_FEELINGS[mood])]
            elif style == "question":
                parts = [sentence, rng.choice(_QUESTIONS)]
            elif style == "day":
                parts = [rng.choice(day_lines), sentence]
            else:
                parts = [sentence]

        text = " ".join(parts)
        if rng.random() < 0.6:
            emojis = list(_EMOJIS[mood]) + list(_TIME_EMOJIS.get(bucket, ()))
            text = f"{text} {rng.choice(emojis)}"
        if rng.random() < 0.2:
            tags = rng.sample(_HASHTAGS, k=rng.choice((1, 2)))
            text = f"{text}\n\n{' '.join(tags)}"
        return text


def _sentence(clause: str) -> str:
    """Capitalize the first letter and end *clause* with a full stop."""

    clause = " ".join(clause.split())
    if not clause:
        return clause
    clause = clause[0].upper() + clause[1:]
    if clause[-1] not in ".!?":
        clause += "."
    return clause


if __name__ == "__main__":  # pragma: no cover - manual preview
    generator = PostTextGenerator()
    for line in generator.generate_many(10):
        print(line)
        print("-" * 40)
