# 📸 SCADA Bot v2 — Multi Image Session OCR

Bot Telegram untuk mengambil data dari layar HMI SCADA menggunakan OCR, lalu menyimpannya langsung ke Google Sheets.

---

## 🧭 Fitur Utama

| Fitur                | Keterangan                                                               |
| -------------------- | ------------------------------------------------------------------------ |
| **Session-based**    | Perintah `/mulai` membuat session baru dengan pilihan Time               |
| **Multi-foto**       | Kirim banyak foto per session — data di-merge otomatis (tanpa overwrite) |
| **OCR**              | Gemini Vision API                                                        |
| **Google Sheets**    | Data otomatis ditulis saat `/selesai`                                    |
| **Progress Tracker** | `/status` melihat 21 parameter sudah terisi berapa                       |
| **Timeout**          | Session otomatis expired setelah 30 menit                                |
| **Multi-user**       | Banyak user bisa pakai bot bersamaan, session terpisah                   |

---

## 🚀 Perintah Bot

```
/mulai    — Buat session baru, pilih Time (1–24)
/status   — Lihat progress session
/selesai  — Simpan data ke Google Sheet
/batal    — Hapus session aktif
```

---

## 🧩 Flow Kerja

1. User ketik `/mulai` → bot tanya Time
2. User pilih angka 1–24 → session dibuat
3. User kirim foto HMI SCADA → bot proses OCR
4. Bot tampilkan progress → user kirim foto lagi atau `/status`
5. User ketik `/selesai` → bot validasi → tulis ke Google Sheet
6. Bot tampilkan ringkasan perubahan

---

## 🏗️ Arsitektur

```
Telegram User
    ↓
Bot (Telegram Bot API)
    ↓
OCR Engine (Gemini Vision / PaddleOCR)
    ↓
Google Sheets API (Service Account)
```

---

## 📋 Environment Variables

| Variable                      | Wajib | Keterangan                                |
| ----------------------------- | ----- | ----------------------------------------- |
| `TELEGRAM_BOT_TOKEN`          | ✅    | Token dari @BotFather                     |
| `OPERATOR_CHAT_ID`            | ❌    | Chat ID operator untuk notifikasi startup |
| `GEMINI_API_KEY`              | ✅    | API Key Gemini (jika pakai Gemini)        |
| `GOOGLE_SHEET_ID`             | ✅    | ID Google Sheet                           |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ✅    | JSON credentials service account Google   |

> ⚠️ Jangan commit `api-key.txt` atau `credentials.json` ke repo publik.

---

## 📦 Instalasi & Setup

### 1️⃣ Clone Repository

```bash
git clone https://github.com/<username>/scada-bot.git
cd scada-bot
```

### 2️⃣ Buat Virtual Environment

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash)
# atau
source .venv/bin/activate        # Linux/Mac
```

### 3️⃣ Install Dependencies

```bash
pip install -r requirements.txt
```

### 4️⃣ Buat File `api-key.txt`

```
Telegram Bot Token=<YOUR_BOT_TOKEN>
Chat ID operator=<YOUR_CHAT_ID>
Gemini API Key=<YOUR_GEMINI_KEY>
```

### 5️⃣ Jalankan Bot

```bash
python scada_bot_v2.py
```

---

## 🚂 Deploy ke Railway

### Langkah cepat:

1. **Push ke GitHub**

   ```bash
   git remote add origin https://github.com/<username>/scada-bot.git
   git push -u origin master
   ```

2. **Login Railway** → https://railway.app → New Project → Deploy from GitHub

3. **Environment Variables** di dashboard Railway:

   ```
   TELEGRAM_BOT_TOKEN = 8741176186:AAxxxxx
   OPERATOR_CHAT_ID = 1210087147
   GEMINI_API_KEY = AQ.Ab8xxxxxxx
   GOOGLE_SHEET_ID = 19CVGvZmEYiMQCQek1pHUKtdF9wABab7vyIjdM7dK1o4
   GOOGLE_SERVICE_ACCOUNT_JSON = { paste isi lengkap credentials.json }
   ```

4. **Settings**
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python scada_bot_v2.py`

5. **Deploy** → Tunggu hingga selesai → Cek log

---

## 🧪 Testing (Lokal)

### Unit Tests

```bash
python -m unittest discover -s tests -v
```

### Multi-user

- Buka **Telegram Desktop** + **Telegram Web** atau **HP** dengan akun berbeda
- Ketik `/mulai` → pilih Time → kirim foto
- Setiap user memiliki session terpisah

---

## 🛠️ Struktur Project

```
scada-bot/
├── .gitignore           # Ignori file sensitif
├── requirements.txt     # Dependencies
├── scada_bot_v2.py      # Kode utama
├── tests/
│   └── test_scada_bot.py
└── README.md
```

---

## ⚠️ Catatan

- **Google Sheets** yang ditulis SAMA untuk semua user (berbeda berdasarkan Time/baris)
- **Session timeout** 30 menit — otomatis dihapus
- **OCR Engine**: default `gemini-2.0-flash`, bisa diganti ke `paddle` (gratis unlimited, tapi akurasi lebih rendah)
- **Telegram Bot**: dibatasi 30 pesan per detik oleh Telegram API (tidak masalah untuk penggunaan normal)

---

## 📄 License

MIT — Silakan gunakan untuk kebutuhan internal. 🚀
