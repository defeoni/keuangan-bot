from dotenv import load_dotenv
load_dotenv()
import os, json, logging, base64
from datetime import datetime, time as dtime
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)
WIB = pytz.timezone('Asia/Jakarta')
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.0-flash')

def get_sheet():
    creds_json = base64.b64decode(os.environ.get("GOOGLE_CREDENTIALS_B64")).decode('utf-8')
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=[
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ])
    client = gspread.authorize(creds)
    sheet = client.open_by_key(os.environ.get("SHEET_ID")).sheet1
    if not sheet.row_values(1):
        sheet.append_row(["Tanggal","Jam","User ID","Jenis","Jumlah","Kategori","Keterangan"])
    return sheet

def parse_transaction(text):
    prompt = f"""Analisis teks berikut dan ekstrak informasi transaksi keuangan.
Teks: "{text}"
Balas HANYA dengan JSON tanpa markdown, contoh:
{{"jenis":"pengeluaran","jumlah":25000,"kategori":"makanan","keterangan":"beli makan siang"}}
Kategori pilihan: makanan, transportasi, belanja, hiburan, tagihan, gaji, bisnis, lainnya
Jika bukan transaksi keuangan balas: {{"error":"bukan transaksi"}}"""
    response = model.generate_content(prompt)
    clean = response.text.strip().replace('```json','').replace('```','').strip()
    return json.loads(clean)

def get_today_data(user_id):
    sheet = get_sheet()
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    records = sheet.get_all_records()
    today_rec = [r for r in records if str(r.get('Tanggal',''))==today and str(r.get('User ID',''))==user_id]
    masuk = sum(int(r['Jumlah']) for r in today_rec if r['Jenis']=='pemasukan')
    keluar = sum(int(r['Jumlah']) for r in today_rec if r['Jenis']=='pengeluaran')
    return today_rec, masuk, keluar

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Halo! Saya bot pencatat keuanganmu 💰\n\n"
        "Ketik transaksi secara bebas:\n"
        "• beli makan siang 25000\n"
        "• terima gaji 5000000\n"
        "• bayar listrik 150000\n\n"
        "Perintah:\n"
        "/saldo — ringkasan hari ini\n"
        "/rekap — detail transaksi hari ini"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try:
        data = parse_transaction(update.message.text)
        if "error" in data:
            await update.message.reply_text("Saya tidak mengenali ini sebagai transaksi 🤔\nCoba: 'beli makan 25000' atau 'terima gaji 5000000'")
            return
        sheet = get_sheet()
        now = datetime.now(WIB)
        sheet.append_row([now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), user_id,
                          data['jenis'], int(data['jumlah']), data['kategori'], data['keterangan']])
        e = "💸" if data['jenis']=='pengeluaran' else "💰"
        await update.message.reply_text(
            f"{e} Tercatat!\n"
            f"Jenis      : {data['jenis'].capitalize()}\n"
            f"Jumlah     : Rp {int(data['jumlah']):,}\n"
            f"Kategori   : {data['kategori'].capitalize()}\n"
            f"Keterangan : {data['keterangan'].capitalize()}"
        )
    except Exception as ex:
        logging.error(f"Error: {ex}")
        await update.message.reply_text("Maaf, terjadi kesalahan. Coba lagi!")

async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try:
        _, masuk, keluar = get_today_data(user_id)
        s = masuk - keluar
        await update.message.reply_text(
            f"📊 Ringkasan Hari Ini\n{'─'*26}\n"
            f"💰 Pemasukan  : Rp {masuk:,}\n"
            f"💸 Pengeluaran: Rp {keluar:,}\n{'─'*26}\n"
            f"{'✅' if s>=0 else '⚠️'} Saldo       : Rp {s:,}"
        )
    except Exception as ex:
        logging.error(f"Error saldo: {ex}")
        await update.message.reply_text("Gagal mengambil data saldo.")

async def rekap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await kirim_rekap(str(update.effective_user.id), context.bot)

async def kirim_rekap(user_id, bot):
    try:
        today = datetime.now(WIB).strftime("%Y-%m-%d")
        records, masuk, keluar = get_today_data(user_id)
        if not records:
            await bot.send_message(chat_id=user_id, text=f"🌙 Rekap {today}\n\nTidak ada transaksi hari ini.")
            return
        detail = "".join(f"  {'💸' if r['Jenis']=='pengeluaran' else '💰'} {r['Keterangan']} — Rp {int(r['Jumlah']):,}\n" for r in records)
        s = masuk - keluar
        await bot.send_message(chat_id=user_id, text=(
            f"🌙 Rekap Keuangan {today}\n{'─'*26}\n{detail}{'─'*26}\n"
            f"💰 Total Masuk  : Rp {masuk:,}\n"
            f"💸 Total Keluar : Rp {keluar:,}\n{'─'*26}\n"
            f"{'✅' if s>=0 else '⚠️'} Saldo Hari Ini: Rp {s:,}"
        ))
    except Exception as ex:
        logging.error(f"Error kirim_rekap {user_id}: {ex}")

async def jadwal_rekap_malam(context: CallbackContext):
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        user_ids = set(str(r.get('User ID','')) for r in records if r.get('User ID'))
        for uid in user_ids:
            await kirim_rekap(uid, context.bot)
    except Exception as ex:
        logging.error(f"Error jadwal: {ex}")

def main():
    app = Application.builder().token(os.environ.get("TELEGRAM_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("saldo", saldo))
    app.add_handler(CommandHandler("rekap", rekap))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # 21:00 WIB = 14:00 UTC
    app.job_queue.run_daily(jadwal_rekap_malam, time=dtime(hour=14, minute=0, tzinfo=pytz.utc))
    app.run_polling()

if __name__ == "__main__":
    main()
