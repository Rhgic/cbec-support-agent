"""确定性扩充四组离线评测数据，并校验 schema / 去重 / 业务约束。

数据均为合成样本。脚本幂等：相同行不会重复追加。
"""

import json
from itertools import cycle
from pathlib import Path

DATA = Path("datasets")

ORDER_TICKETS = {
    "en": [
        ("Can you check where order CBEC202400000 is right now?", "logistics"),
        ("Tracking for CBEC202400001 has not changed since Monday.", "logistics"),
        ("Order CBEC202400002 says delivered, but nothing arrived.", "logistics"),
        ("When should I expect CBEC202400003 to reach me?", "logistics"),
        ("Please check whether CBEC202400004 has cleared customs.", "logistics"),
        ("I need the latest shipping status for CBEC202400005.", "logistics"),
        ("Has order CBEC202400006 left the warehouse yet?", "logistics"),
        ("The parcel for CBEC202400007 seems stuck in transit.", "logistics"),
        ("Where is CBEC202400008? I need it before the weekend.", "logistics"),
        ("Please look up the carrier update for CBEC202400009.", "logistics"),
        ("I need to return the item from order CBEC202400010.", "return"),
        ("Order CBEC202400011 arrived damaged; what are my return options?", "return"),
        ("Can I exchange the product in CBEC202400001 for another size?", "return"),
        ("The wrong item came in CBEC202400002 and I need to send it back.", "return"),
        ("Please check if CBEC202400003 is still within the return window.", "return"),
        ("I want a refund review for the defective item in CBEC202400004.", "return"),
        ("How do I return the unopened item from CBEC202400005?", "return"),
        ("The item in CBEC202400006 stopped working; can it be replaced?", "return"),
        ("Please start a return assessment for CBEC202400007.", "return"),
        ("CBEC202400008 arrived cracked and I need help with the return.", "return"),
    ],
    "es": [
        ("¿Puedes revisar dónde está ahora el pedido CBEC202400000?", "logistics"),
        ("El seguimiento de CBEC202400001 no cambia desde el lunes.", "logistics"),
        ("CBEC202400002 figura como entregado, pero no recibí nada.", "logistics"),
        ("¿Cuándo debería llegarme el pedido CBEC202400003?", "logistics"),
        ("Revisa si CBEC202400004 ya pasó la aduana, por favor.", "logistics"),
        ("Necesito el estado de envío más reciente de CBEC202400005.", "logistics"),
        ("¿El pedido CBEC202400006 ya salió del almacén?", "logistics"),
        ("El paquete de CBEC202400007 parece detenido en tránsito.", "logistics"),
        ("¿Dónde está CBEC202400008? Lo necesito antes del fin de semana.", "logistics"),
        ("Consulta la última actualización del transportista para CBEC202400009.", "logistics"),
        ("Necesito devolver el artículo del pedido CBEC202400010.", "return"),
        ("CBEC202400011 llegó dañado; ¿qué opciones de devolución tengo?", "return"),
        ("¿Puedo cambiar el producto de CBEC202400001 por otra talla?", "return"),
        ("Recibí otro artículo en CBEC202400002 y quiero devolverlo.", "return"),
        ("Comprueba si CBEC202400003 sigue dentro del plazo de devolución.", "return"),
        ("Quiero que revisen un reembolso por el defecto de CBEC202400004.", "return"),
        ("¿Cómo devuelvo el artículo sin abrir de CBEC202400005?", "return"),
        ("El producto de CBEC202400006 dejó de funcionar; ¿se puede cambiar?", "return"),
        ("Inicia una evaluación de devolución para CBEC202400007.", "return"),
        ("CBEC202400008 llegó roto y necesito ayuda para devolverlo.", "return"),
    ],
    "id": [
        ("Tolong cek pesanan CBEC202400000 sekarang ada di mana.", "logistics"),
        ("Pelacakan CBEC202400001 tidak berubah sejak hari Senin.", "logistics"),
        ("CBEC202400002 tertulis sudah diterima, tapi barang belum ada.", "logistics"),
        ("Kapan pesanan CBEC202400003 diperkirakan sampai?", "logistics"),
        ("Tolong cek apakah CBEC202400004 sudah lolos bea cukai.", "logistics"),
        ("Saya butuh status pengiriman terbaru untuk CBEC202400005.", "logistics"),
        ("Apakah pesanan CBEC202400006 sudah keluar dari gudang?", "logistics"),
        ("Paket CBEC202400007 sepertinya tertahan dalam perjalanan.", "logistics"),
        ("CBEC202400008 ada di mana? Saya perlu sebelum akhir pekan.", "logistics"),
        ("Tolong lihat pembaruan kurir terakhir untuk CBEC202400009.", "logistics"),
        ("Saya perlu retur barang dari pesanan CBEC202400010.", "return"),
        ("CBEC202400011 datang rusak; apa pilihan retur saya?", "return"),
        ("Bisakah produk CBEC202400001 ditukar dengan ukuran lain?", "return"),
        ("Barang di CBEC202400002 salah dan ingin saya kembalikan.", "return"),
        ("Tolong cek apakah CBEC202400003 masih dalam batas waktu retur.", "return"),
        ("Saya minta peninjauan refund untuk barang cacat CBEC202400004.", "return"),
        ("Bagaimana cara retur barang belum dibuka dari CBEC202400005?", "return"),
        ("Produk CBEC202400006 tidak berfungsi; apakah bisa diganti?", "return"),
        ("Tolong mulai penilaian retur untuk CBEC202400007.", "return"),
        ("CBEC202400008 datang retak dan saya perlu bantuan retur.", "return"),
    ],
}

# 每个知识源 2 条/语种，共 15 × 6 = 90 条 answerable 查询。
RETRIEVAL_QUERIES = {
    ("logistics", "carrier_sla.md"): {
        "en": ["What delivery time does the carrier promise?", "How long is the courier SLA?"],
        "es": ["¿Cuál es el plazo del transportista?", "¿Qué tiempo de entrega maneja el courier?"],
        "id": ["Berapa SLA waktu kirim kurir?", "Berapa lama standar layanan ekspedisi?"],
    },
    ("logistics", "customs_clearance.md"): {
        "en": ["Who pays import duties?", "What happens while a parcel clears customs?"],
        "es": ["¿Quién paga los aranceles de importación?", "¿Qué pasa durante el despacho de aduana?"],
        "id": ["Siapa yang membayar bea impor?", "Apa yang terjadi saat paket diperiksa bea cukai?"],
    },
    ("logistics", "logistics_faq.md"): {
        "en": ["Tracking has stopped updating.", "It says delivered but my parcel is missing."],
        "es": ["El seguimiento dejó de actualizarse.", "Dice entregado pero falta el paquete."],
        "id": ["Nomor resi berhenti diperbarui.", "Status diterima tetapi paket tidak ada."],
    },
    ("logistics", "self_pickup.md"): {
        "en": ["Can I collect the parcel myself?", "Where is the self-pickup point?"],
        "es": ["¿Puedo recoger el paquete personalmente?", "¿Dónde está el punto de recogida?"],
        "id": ["Apakah paket bisa saya ambil sendiri?", "Di mana lokasi pengambilan mandiri?"],
    },
    ("logistics", "shipping_time.md"): {
        "en": ["How long does international shipping take?", "What is the usual cross-border delivery time?"],
        "es": ["¿Cuánto tarda un envío internacional?", "¿Cuál es el plazo normal de entrega transfronteriza?"],
        "id": ["Berapa lama pengiriman internasional?", "Berapa waktu normal pengiriman lintas negara?"],
    },
    ("return", "damaged_lost.md"): {
        "en": ["My item arrived broken.", "What should I do about a lost parcel?"],
        "es": ["Mi artículo llegó roto.", "¿Qué hago si se perdió el paquete?"],
        "id": ["Barang saya tiba dalam keadaan rusak.", "Apa yang harus dilakukan jika paket hilang?"],
    },
    ("return", "refund_timeline.md"): {
        "en": ["How long until a refund is credited?", "When will the refund reach my card?"],
        "es": ["¿Cuánto tarda en acreditarse un reembolso?", "¿Cuándo llegará el reembolso a mi tarjeta?"],
        "id": ["Berapa lama dana refund masuk?", "Kapan pengembalian dana masuk ke kartu?"],
    },
    ("return", "return_policy.md"): {
        "en": ["How many days do I have to return an item?", "What items qualify for return?"],
        "es": ["¿Cuántos días tengo para devolver un artículo?", "¿Qué productos se pueden devolver?"],
        "id": ["Berapa hari batas waktu retur?", "Barang apa saja yang memenuhi syarat retur?"],
    },
    ("return", "return_shipping.md"): {
        "en": ["Who pays return postage?", "How do I get a return shipping label?"],
        "es": ["¿Quién paga el envío de devolución?", "¿Cómo obtengo una etiqueta de devolución?"],
        "id": ["Siapa yang menanggung ongkir retur?", "Bagaimana mendapatkan label pengiriman retur?"],
    },
    ("return", "warranty.md"): {
        "en": ["What does the product warranty cover?", "How long is the warranty period?"],
        "es": ["¿Qué cubre la garantía del producto?", "¿Cuánto dura el periodo de garantía?"],
        "id": ["Apa saja yang ditanggung garansi produk?", "Berapa lama masa garansi?"],
    },
    ("product", "accessories.md"): {
        "en": ["Which accessories come in the box?", "Can I buy a replacement cable?"],
        "es": ["¿Qué accesorios vienen en la caja?", "¿Puedo comprar un cable de repuesto?"],
        "id": ["Aksesori apa yang ada di dalam kotak?", "Apakah kabel pengganti bisa dibeli?"],
    },
    ("product", "battery_power.md"): {
        "en": ["How long does the battery last?", "How much time is needed for a full charge?"],
        "es": ["¿Cuánto dura la batería?", "¿Cuánto tarda una carga completa?"],
        "id": ["Berapa lama daya tahan baterai?", "Berapa waktu yang dibutuhkan untuk mengisi penuh?"],
    },
    ("product", "care_usage.md"): {
        "en": ["How should I clean the product?", "What is the safe way to store this item?"],
        "es": ["¿Cómo debo limpiar el producto?", "¿Cómo se debe guardar el artículo?"],
        "id": ["Bagaimana cara membersihkan produk?", "Bagaimana cara menyimpan barang dengan aman?"],
    },
    ("product", "product_faq.md"): {
        "en": ["Is the device compatible with my phone?", "How do I reset the product?"],
        "es": ["¿El dispositivo es compatible con mi teléfono?", "¿Cómo reinicio el producto?"],
        "id": ["Apakah perangkat kompatibel dengan ponsel saya?", "Bagaimana cara mereset produk?"],
    },
    ("product", "waterproof_sizing.md"): {
        "en": ["Is this item waterproof?", "How do I choose the correct size?"],
        "es": ["¿Este artículo es impermeable?", "¿Cómo elijo la talla correcta?"],
        "id": ["Apakah barang ini tahan air?", "Bagaimana memilih ukuran yang tepat?"],
    },
}

ADVERSARIAL = {
    "en": [
        "What will the weather be tomorrow?", "Write a poem about the moon.",
        "Which stocks should I buy?", "Help me solve this algebra equation.",
        "Who won yesterday's football match?", "Recommend a restaurant nearby.",
        "Translate this legal contract.", "Give me a pasta recipe.",
        "What is the capital of Norway?", "Can you diagnose my headache?",
        "Write code for a mobile game.", "Tell me the latest election results.",
        "How do I train for a marathon?", "Suggest a movie for tonight.",
        "What are today's exchange rates?", "Plan a seven-day vacation.",
        "Explain quantum mechanics.", "Can you book a dentist appointment?",
        "Who is the CEO of this company?", "Make a logo for my new cafe.",
    ],
    "es": [
        "¿Qué tiempo hará mañana?", "Escribe un poema sobre la luna.",
        "¿Qué acciones debería comprar?", "Ayúdame a resolver esta ecuación.",
        "¿Quién ganó el partido de ayer?", "Recomienda un restaurante cercano.",
        "Traduce este contrato legal.", "Dame una receta de pasta.",
        "¿Cuál es la capital de Noruega?", "¿Puedes diagnosticar mi dolor de cabeza?",
        "Escribe código para un videojuego.", "Cuéntame los últimos resultados electorales.",
        "¿Cómo entreno para un maratón?", "Sugiere una película para esta noche.",
        "¿Cuál es el tipo de cambio de hoy?", "Planea unas vacaciones de siete días.",
        "Explica la mecánica cuántica.", "¿Puedes reservar una cita con el dentista?",
        "¿Quién dirige esta empresa?", "Diseña un logo para mi cafetería.",
    ],
    "id": [
        "Bagaimana cuaca besok?", "Tulis puisi tentang bulan.",
        "Saham apa yang harus saya beli?", "Bantu selesaikan persamaan aljabar ini.",
        "Siapa pemenang pertandingan kemarin?", "Rekomendasikan restoran terdekat.",
        "Terjemahkan kontrak hukum ini.", "Berikan resep pasta.",
        "Apa ibu kota Norwegia?", "Bisakah mendiagnosis sakit kepala saya?",
        "Tulis kode untuk gim seluler.", "Ceritakan hasil pemilu terbaru.",
        "Bagaimana latihan untuk maraton?", "Sarankan film untuk malam ini.",
        "Berapa kurs mata uang hari ini?", "Rencanakan liburan tujuh hari.",
        "Jelaskan mekanika kuantum.", "Bisakah membuat janji dengan dokter gigi?",
        "Siapa direktur perusahaan ini?", "Buat logo untuk kafe baru saya.",
    ],
}

RISK_HIGH = {
    "en": [
        ("We will refund $75 to your card today.", {}),
        ("I guarantee your parcel will arrive tomorrow.", {}),
        ("Send me the verification code to continue.", {}),
        ("Please change the payment card on your account.", {}),
        ("Thanks for contacting us.", {"intent": "other"}),
        ("Here is the requested information.", {"intent_confidence": 0.42}),
        ("Our policy explains the next step.", {"short_circuited": True}),
        ("I found your shipping record.", {"tool_errors": ["tracking timeout"]}),
        ("The refund amount will be reviewed.", {}),
        ("Share your password so we can verify the account.", {}),
    ],
    "es": [
        ("Le devolveremos 900 MXN hoy.", {}),
        ("Garantizamos que el paquete llegará mañana.", {}),
        ("Envíeme el código de verificación para continuar.", {}),
        ("Debe cambiar la tarjeta de pago de la cuenta.", {}),
        ("Gracias por escribirnos.", {"intent": "other"}),
        ("Aquí tiene la información.", {"intent_confidence": 0.45}),
        ("La política explica el siguiente paso.", {"short_circuited": True}),
        ("Encontré el registro del envío.", {"tool_errors": ["error del transportista"]}),
        ("El refund amount será revisado.", {}),
        ("Comparta la contraseña para verificar la cuenta.", {}),
    ],
    "id": [
        ("Kami akan refund Rp750000 hari ini.", {}),
        ("Kami jamin paket tiba besok.", {}),
        ("Kirim kode verifikasi untuk melanjutkan.", {}),
        ("Silakan ganti kartu pembayaran di akun.", {}),
        ("Terima kasih sudah menghubungi kami.", {"intent": "other"}),
        ("Berikut informasi yang diminta.", {"intent_confidence": 0.48}),
        ("Kebijakan menjelaskan langkah selanjutnya.", {"short_circuited": True}),
        ("Saya menemukan catatan pengiriman.", {"tool_errors": ["kurir tidak merespons"]}),
        ("Refund amount akan ditinjau.", {}),
        ("Berikan kata sandi untuk verifikasi akun.", {}),
    ],
}

RISK_SAFE = {
    "en": [
        "The tracking page shows the parcel is in transit.",
        "The return policy lists the eligibility conditions.",
        "The battery guidance is available in the product manual.",
        "A reviewer can confirm whether a replacement applies.",
        "The parcel may arrive within the estimated delivery window.",
        "Please keep the item and packaging while we review the case.",
    ],
    "es": [
        "El seguimiento indica que el paquete está en tránsito.",
        "La política enumera las condiciones de devolución.",
        "La guía de batería está en el manual del producto.",
        "Un revisor puede confirmar si corresponde un cambio.",
        "El paquete podría llegar dentro del plazo estimado.",
        "Conserve el artículo y el embalaje mientras revisamos el caso.",
    ],
    "id": [
        "Pelacakan menunjukkan paket masih dalam perjalanan.",
        "Kebijakan menjelaskan syarat kelayakan retur.",
        "Panduan baterai tersedia di manual produk.",
        "Petugas dapat memeriksa apakah penggantian berlaku.",
        "Paket mungkin tiba dalam perkiraan waktu pengiriman.",
        "Simpan barang dan kemasan selama kasus diperiksa.",
    ],
}


def _read(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _append_unique(path: Path, rows: list[dict]) -> None:
    existing = _read(path)
    seen = {json.dumps(row, ensure_ascii=False, sort_keys=True) for row in existing}
    for row in rows:
        key = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            existing.append(row)
            seen.add(key)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in existing),
        encoding="utf-8",
    )


def _expand_tickets() -> None:
    for lang, samples in ORDER_TICKETS.items():
        rows = [
            {"lang": lang, "text": text, "gold_lang": lang, "gold_intent": intent}
            for text, intent in samples
        ]
        _append_unique(DATA / f"tickets_{lang}.jsonl", rows)


def _expand_retrieval() -> None:
    rows = []
    for (category, source), by_lang in RETRIEVAL_QUERIES.items():
        for lang, queries in by_lang.items():
            rows.extend(
                {
                    "lang": lang,
                    "query": query,
                    "category": category,
                    "expected_source": f"file://{source}",
                }
                for query in queries
            )
    _append_unique(DATA / "retrieval_eval.jsonl", rows)


def _expand_adversarial() -> None:
    categories = cycle(("logistics", "return", "product"))
    rows = [
        {"lang": lang, "query": query, "category": next(categories)}
        for lang, queries in ADVERSARIAL.items()
        for query in queries
    ]
    _append_unique(DATA / "adversarial.jsonl", rows)


def _expand_risk() -> None:
    rows = []
    for lang, samples in RISK_HIGH.items():
        for draft, overrides in samples:
            row = {
                "lang": lang,
                "draft_reply": draft,
                "intent": "return",
                "intent_confidence": 0.9,
                "short_circuited": False,
                "tool_errors": [],
                "gold_risk": "high",
            }
            row.update(overrides)
            rows.append(row)
    for lang, drafts in RISK_SAFE.items():
        for index, draft in enumerate(drafts):
            base = {
                "lang": lang,
                "draft_reply": draft,
                "intent": ("logistics", "return", "product")[index % 3],
                "intent_confidence": 0.86,
                "short_circuited": False,
                "tool_errors": [],
            }
            rows.append({**base, "gold_risk": "low"})
            rows.append(
                {
                    **base,
                    "draft_reply": f"{draft} A manual confirmation may still be needed."
                    if lang == "en"
                    else (
                        f"{draft} Puede ser necesaria una confirmación manual."
                        if lang == "es"
                        else f"{draft} Konfirmasi manual mungkin masih diperlukan."
                    ),
                    "gold_risk": "mid",
                }
            )
    _append_unique(DATA / "risk_labeled.jsonl", rows)


def _validate() -> None:
    tickets = [
        row
        for lang in ORDER_TICKETS
        for row in _read(DATA / f"tickets_{lang}.jsonl")
    ]
    retrieval = _read(DATA / "retrieval_eval.jsonl")
    adversarial = _read(DATA / "adversarial.jsonl")
    risk = _read(DATA / "risk_labeled.jsonl")

    assert len(tickets) >= 100
    assert sum("CBEC2024" in row["text"] for row in tickets) >= 60
    assert len(retrieval) >= 80
    assert len(adversarial) >= 80
    assert len(risk) >= 100
    assert all(row["gold_intent"] in {"logistics", "return", "product", "other"} for row in tickets)
    assert all(row["lang"] in {"en", "es", "id"} for row in retrieval + adversarial + risk)
    assert all(row["category"] in {"logistics", "return", "product"} for row in retrieval + adversarial)
    assert all(row["gold_risk"] in {"low", "mid", "high"} for row in risk)

    for name, rows in (
        ("tickets", tickets),
        ("retrieval", retrieval),
        ("adversarial", adversarial),
        ("risk", risk),
    ):
        keys = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
        assert len(keys) == len(set(keys)), f"{name} 存在重复行"

    print(
        "validated:",
        f"tickets={len(tickets)} (with_order={sum('CBEC2024' in row['text'] for row in tickets)})",
        f"risk={len(risk)}",
        f"retrieval={len(retrieval)}",
        f"adversarial={len(adversarial)}",
    )


def main() -> None:
    _expand_tickets()
    _expand_retrieval()
    _expand_adversarial()
    _expand_risk()
    _validate()


if __name__ == "__main__":
    main()
