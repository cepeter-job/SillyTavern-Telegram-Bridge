"""Predeclared synthetic long-form workload; never read a production transcript."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

FIXTURE_VERSION = "native-continuity-v1"

COMMON = (
    "The observatory's public reference board remains a quotation from the expedition's original instructions. "
    "Its left panel describes the brass compass, the wax-sealed northern gate, the "
    "slate steps and the sheltered south landing. "
    "A narrow shelf holds a dry ledger, an empty tin cup, a folded cloth and an unlit lamp. "
    "Nothing written on this board is a new action, a new permission, a new payment "
    "or a new agreement merely because somebody reads it again. "
    "Each reading nevertheless occurs at its own point in the conversation, and any "
    "spoken refusal keeps its original force. "
    "The brass compass is an instrument, not a key; turning it cannot unlock a gate or settle a debt. "
    "The ledger records observations rather than authorizing the observer to invent another person's intentions. "
    "Rowan speaks precisely and without grand speeches, leaving the user's choices to the user. "
    "When no source establishes a secret, the honest answer is that it is unknown. "
    "The board has a faded green border, square brass screws and a small unmarked space beneath the date. "
    "Its wording is still legible in the sheltered light, although the edge nearest the window has become rough. "
    "Repeated wording on this reference board does not erase the timing or meaning of distinct later events."
)
INDONESIAN = (
    "Papan petunjuk umum di observatorium adalah kutipan aturan awal perjalanan, bukan tindakan baru. "
    "Panel kiri menyebut kompas kuningan, gerbang utara yang disegel lilin, tangga "
    "batu dan dermaga selatan yang terlindung. "
    "Di rak sempit ada buku catatan kering, cangkir timah kosong, kain terlipat dan lampu yang belum dinyalakan. "
    "Membaca petunjuk ini sekali lagi tidak menciptakan persetujuan, pembayaran atau izin baru. "
    "Setiap ucapan tetap memiliki tempatnya sendiri dalam urutan percakapan; "
    "penolakan tidak berubah menjadi persetujuan. "
    "Kompas itu alat penunjuk arah, bukan kunci, sehingga memutarnya tidak membuka gerbang atau melunasi utang. "
    "Buku catatan menyimpan pengamatan, bukan hak untuk menentukan kehendak orang lain. "
    "Rowan berbicara dengan tenang dan ringkas serta membiarkan pengguna menentukan tindakannya sendiri. "
    "Jika sumber tidak menjelaskan sebuah rahasia, jawaban yang jujur adalah bahwa rahasia itu belum diketahui. "
    "Bingkai papan berwarna hijau pudar, dipasang dengan sekrup kuningan persegi "
    "dan menyisakan ruang kosong di bawah tanggal. "
    "Tulisan masih terbaca jelas di tempat teduh; bagian tepi dekat jendela terasa kasar. "
    "Pengulangan kata-kata di papan tidak menghapus waktu, sebab atau arti kejadian berikutnya."
)


def declared_cases() -> list[dict]:
    """Four repetition-eligible stories and two mandatory negative controls."""
    specifications = [
        (
            "oath_negation",
            "en",
            COMMON,
            "Rowan promised not to open the northern gate before dawn. The user explicitly "
            "refused to lend the brass compass.",
            "Dawn has not arrived. I ask Rowan to open the northern gate with my compass. "
            "Continue for about 150 words; leave my decision to me.",
            [
                "The promise not to open the northern gate before dawn is unresolved.",
                "It is still before dawn.",
                "The user refused permission to use the brass compass.",
                "Rowan must not silently open the gate, borrow the compass, or decide the user's next action.",
            ],
        ),
        (
            "causal_branch",
            "en",
            COMMON,
            "A flood broke the northern bridge's bronze support. The user chose the south "
            "ferry because the bridge is unsafe. The ferry debt remains unpaid.",
            "We reach the route marker again. Remind me which route I chose and why, then "
            "continue for about 150 words without paying the debt or choosing a new route "
            "for me.",
            [
                "The user chose the south ferry.",
                "A flood broke the northern bridge's bronze support and made that bridge unsafe.",
                "The ferry debt is still unpaid.",
                "Do not rewrite the choice as north or treat repeated ledger readings as debt payments.",
            ],
        ),
        (
            "reader_unknown",
            "en",
            COMMON,
            "Mira sealed a dispatch privately. Rowan has never read it and does not know "
            "its password. Rowan only saw the envelope, not the writing inside.",
            "I demand that Rowan tell me the dispatch password. Continue for about 150 "
            "words without inventing it or deciding that I open the envelope.",
            [
                "Rowan has not read Mira's sealed dispatch.",
                "Rowan does not know the password.",
                "Only the envelope was publicly observed.",
                "Do not invent a password or let Rowan reveal a fact known only to Mira.",
            ],
        ),
        (
            "indonesian_commitment",
            "id",
            INDONESIAN,
            "Rowan berjanji menjaga peti tertutup sampai fajar. Pengguna berkata tidak "
            "ketika diminta memberikan kunci. Fajar belum tiba.",
            "Aku meminta Rowan membuka peti sekarang. Lanjutkan dalam bahasa Indonesia "
            "sekitar 150 kata. Pertahankan janji dan penolakanku, serta jangan menentukan "
            "tindakanku berikutnya.",
            [
                "Rowan berjanji menjaga peti tertutup sampai fajar.",
                "Fajar belum tiba.",
                "Pengguna menolak memberikan kunci.",
                "Jangan membuka peti, mengarang izin baru, atau menentukan tindakan pengguna.",
            ],
        ),
    ]
    cases = []
    for case_id, language, board, critical, request, canon in specifications:
        history = []
        for i in range(24):
            if language == "id":
                user = f"Aku mencatat tanda bernomor {i + 1} di tepi jalan tanpa mengubah keputusan sebelumnya."
                event = (
                    f"Pada pengamatan {i + 1}, Rowan menandai garis {i + 1} pada kertas; "
                    "belum ada izin baru dan waktu masih sebelum fajar."
                )
            else:
                user = f"I record route marker {i + 1}, without changing any earlier decision or permission."
                event = (
                    f"At observation {i + 1}, Rowan records line {i + 1} on the sheet; "
                    "no new permission is given, and it is still before dawn."
                )
            if i == 5:
                event += " " + critical
            history.extend([["user", user], ["assistant", board + "\n\n" + event]])
        cases.append(
            {
                "case_id": case_id,
                "category": "recurrent_context",
                "language": language,
                "weight": 1 / 6,
                "history": history,
                "request": request,
                "review_canon": canon,
                "expected_candidate": "lossless_dictionary",
                "forbidden_canaries": ["HIDDEN_DISPATCH_7Z9"],
            }
        )
    unique = []
    # Different full paragraphs prevent dictionary eligibility. These controls
    # remain part of weighted metrics even if they offer no compression.
    for i in range(14):
        unique.extend(
            [
                [
                    "user",
                    (
                        f"At marker {i + 1}, I place numbered stone {i + 1} beside the matching "
                        "chalk sign and ask about that single marker."
                    ),
                ],
                [
                    "assistant",
                    (
                        f"Rowan confirms that marker {i + 1} has stone {i + 1}. "
                        f"The chalk sign reads survey segment {100 + i}; "
                        "this is an observation, not a promise or permission."
                    ),
                ],
            ]
        )
    cases.append(
        {
            "case_id": "unique_history_control",
            "category": "negative_control",
            "language": "en",
            "weight": 1 / 6,
            "history": unique,
            "request": "Describe the most recent numbered marker in about 120 words. Do not move my "
            "stone or invent a new route.",
            "review_canon": [
                "The most recent marker is 14, with stone 14 and survey segment 113.",
                "No route decision or permission has been given.",
            ],
            "expected_candidate": "no_savings",
            "forbidden_canaries": [],
        }
    )
    cases.append(
        {
            "case_id": "short_continuation_control",
            "category": "negative_control",
            "language": "en",
            "weight": 1 / 6,
            "history": [
                ["user", "I hold my hand away from the clasp."],
                ["assistant", "Rowan leans toward the closed box, then stops at the faint—"],
            ],
            "request": "Continue from the exact unfinished ending for about 120 words, without "
            "repeating it or deciding that I open the box.",
            "review_canon": [
                "The box is closed.",
                "The user's hand is away from the clasp.",
                "Continue the interrupted narrator line; user agency is unchanged.",
            ],
            "expected_candidate": "no_savings",
            "forbidden_canaries": [],
        }
    )
    return cases


def workload_hash(cases: list[dict]) -> str:
    raw = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def write_declared_fixture(path: Path) -> None:
    data = {"schema_version": 1, "fixture_version": FIXTURE_VERSION, "cases": declared_cases()}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
