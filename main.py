from dotenv import load_dotenv
load_dotenv()

import os, json, logging, base64
from datetime import datetime, time as dtime
from io import BytesIO
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext
from google import genai
import gspread
from google.oauth2.service_account import Credentials
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

logging.basicConfig(level=logging.INFO)
WIB = pytz.timezone('Asia/Jakarta')
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
MODEL = "gemini-2.5-flash-lite"

def get_spreadsheet():
    creds_json = base64.b64decode(os.environ.get("GOOGLE_CREDENTIALS_B64")).decode('utf-8')
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=[
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ])
    return gspread.authorize(creds).open_by_key(os.environ.get("SHEET_ID"))

def get_sheet():
    sp = get_spreadsheet()
    try: sheet = sp.worksheet("Transaksi")
    except: sheet = sp.add_worksheet("Transaksi", 1000, 10)
    if not sheet.row_values(1):
        sheet.append_row(["Tanggal","Jam","User ID","Jenis","Jumlah","Kategori","Keterangan"])
    return sheet

def get_budget_sheet():
    sp = get_spreadsheet()
    try: sheet = sp.worksheet("Budget")
    except: sheet = sp.add_worksheet("Budget", 100, 3)
    if not sheet.row_values(1):
        sheet.append_row(["User ID","Kategori","Jumlah"])
    return sheet

def get_hutang_sheet():
    sp = get_spreadsheet()
    try: sheet = sp.worksheet("Hutang")
    except: sheet = sp.add_worksheet("Hutang", 100, 6)
    if not sheet.row_values(1):
        sheet.append_row(["Tanggal","User ID","Nama","Jumlah","Arah","Status"])
    return sheet

def parse_transaction(text):
    prompt = f"""Analisis teks berikut dan ekstrak informasi transaksi keuangan.
Teks: "{text}"
Balas HANYA dengan JSON tanpa markdown, contoh:
{{"jenis":"pengeluaran","jumlah":25000,"kategori":"makanan","keterangan":"beli makan siang"}}
Kategori pilihan: makanan, transportasi, belanja, hiburan, tagihan, gaji, bisnis, lainnya
Jika bukan transaksi keuangan balas: {{"error":"bukan transaksi"}}"""
    response = client.models.generate_content(model=MODEL, contents=prompt)
    clean = response.text.strip().replace('```json','').replace('```','').strip()
    return json.loads(clean)

def parse_hutang(text):
    prompt = f"""Analisis teks berikut untuk mencari informasi hutang/piutang.
Teks: "{text}"
Balas HANYA dengan JSON tanpa markdown:
- Jika saya yang hutang ke orang lain: {{"arah":"saya_hutang","nama":"nama orang","jumlah":50000}}
- Jika orang lain yang hutang ke saya: {{"arah":"mereka_hutang","nama":"nama orang","jumlah":50000}}
- Jika bukan hutang/piutang: {{"error":"bukan hutang"}}
Contoh input "hutang ke budi 50000" -> {{"arah":"saya_hutang","nama":"budi","jumlah":50000}}
Contoh input "budi pinjam 50000" -> {{"arah":"mereka_hutang","nama":"budi","jumlah":50000}}"""
    response = client.models.generate_content(model=MODEL, contents=prompt)
    clean = response.text.strip().replace('```json','').replace('```','').strip()
    return json.loads(clean)

def get_today_data(user_id=None):
    sheet = get_sheet()
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    records = sheet.get_all_records()
    today_rec = [r for r in records if str(r.get('Tanggal',''))==today and
                 (user_id is None or str(r.get('User ID',''))==user_id)]
    masuk = sum(int(r['Jumlah']) for r in today_rec if r['Jenis']=='pemasukan')
    keluar = sum(int(r['Jumlah']) for r in today_rec if r['Jenis']=='pengeluaran')
    return today_rec, masuk, keluar

def get_period_data(user_id, period='bulan'):
    sheet = get_sheet()
    now = datetime.now(WIB)
    records = sheet.get_all_records()
    result = []
    for r in records:
        if str(r.get('User ID','')) != user_id: continue
        try:
            tgl = datetime.strptime(str(r['Tanggal']), "%Y-%m-%d")
            if period == 'minggu' and tgl.isocalendar()[1]==now.isocalendar()[1] and tgl.year==now.year:
                result.append(r)
            elif period == 'bulan' and tgl.month==now.month and tgl.year==now.year:
                result.append(r)
        except: pass
    masuk = sum(int(r['Jumlah']) for r in result if r['Jenis']=='pemasukan')
    keluar = sum(int(r['Jumlah']) for r in result if r['Jenis']=='pengeluaran')
    return result, masuk, keluar

def get_budget(user_id):
    records = get_budget_sheet().get_all_records()
    return {r['Kategori']: int(r['Jumlah']) for r in records if str(r.get('User ID',''))==user_id}

def check_budget_warning(user_id, kategori):
    budgets = get_budget(user_id)
    if kategori not in budgets: return None
    limit = budgets[kategori]
    records, _, _ = get_period_data(user_id, 'bulan')
    spent = sum(int(r['Jumlah']) for r in records if r['Jenis']=='pengeluaran' and r['Kategori']==kategori)
    if spent >= limit:
        return f"🔴 Budget {kategori} bulan ini HABIS!\nBudget: Rp {limit:,} | Terpakai: Rp {spent:,}"
    elif spent >= limit * 0.8:
        return f"⚠️ Budget {kategori} hampir habis!\nSisa: Rp {limit-spent:,} dari Rp {limit:,}"
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Halo! Saya bot pencatat keuanganmu 💰\n\n"
        "Ketik transaksi secara bebas:\n"
        "• beli makan siang 25000\n"
        "• terima gaji 5000000\n"
        "• hutang ke budi 50000\n\n"
        "Perintah:\n"
        "/saldo — ringkasan hari ini\n"
        "/rekap — detail hari ini\n"
        "/rekap minggu — rekap minggu ini\n"
        "/rekap bulan — rekap bulan ini\n"
        "/rekap berdua — rekap gabungan\n"
        "/hapus — hapus transaksi terakhir\n"
        "/hapus 2 — hapus transaksi ke-2\n"
        "/reset — hapus semua transaksi hari ini\n"
        "/budget — lihat anggaran bulanan\n"
        "/budget makanan 2000000 — set anggaran\n"
        "/grafik — grafik pengeluaran bulan ini\n"
        "/hutang — daftar hutang piutang\n"
        "/lunas budi — tandai hutang lunas"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.lower()
    hutang_keywords = ['hutang ke','hutang dari','pinjam ke','pinjemin','dipinjam','piutang']
    is_hutang = any(kw in text for kw in hutang_keywords)

    try:
        if is_hutang:
            data = parse_hutang(update.message.text)
            if "error" not in data:
                sheet = get_hutang_sheet()
                now = datetime.now(WIB)
                sheet.append_row([now.strftime("%Y-%m-%d"), user_id,
                                   data['nama'].capitalize(), int(data['jumlah']),
                                   data['arah'], 'aktif'])
                if data['arah'] == 'saya_hutang':
                    msg = f"📝 Hutang dicatat!\nSaya hutang ke {data['nama'].capitalize()}: Rp {int(data['jumlah']):,}"
                else:
                    msg = f"📝 Piutang dicatat!\n{data['nama'].capitalize()} hutang ke saya: Rp {int(data['jumlah']):,}"
                await update.message.reply_text(msg)
                return

        data = parse_transaction(update.message.text)
        if "error" in data:
            await update.message.reply_text("Saya tidak mengenali ini sebagai transaksi 🤔\nCoba: 'beli makan 25000'")
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
        if data['jenis'] == 'pengeluaran':
            warning = check_budget_warning(user_id, data['kategori'])
            if warning:
                await update.message.reply_text(warning)
    except Exception as ex:
        logging.error(f"Error: {ex}")
        await update.message.reply_text(f"Debug error: {str(ex)}")

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
        await update.message.reply_text(f"Debug error: {str(ex)}")

async def rekap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    args = context.args
    try:
        if args and args[0].lower() == 'berdua':
            today = datetime.now(WIB).strftime("%Y-%m-%d")
            records, masuk, keluar = get_today_data(user_id=None)
            if not records:
                await update.message.reply_text(f"👫 Rekap Berdua {today}\n\nTidak ada transaksi."); return
            detail = "".join(f"  {'💸' if r['Jenis']=='pengeluaran' else '💰'} ...{str(r['User ID'])[-4:]} {r['Keterangan']} — Rp {int(r['Jumlah']):,}\n" for r in records)
            s = masuk - keluar
            await update.message.reply_text(
                f"👫 Rekap Berdua {today}\n{'─'*26}\n{detail}{'─'*26}\n"
                f"💰 Masuk : Rp {masuk:,}\n💸 Keluar: Rp {keluar:,}\n{'─'*26}\n"
                f"{'✅' if s>=0 else '⚠️'} Saldo: Rp {s:,}"
            )
            return

        if args and args[0].lower() in ['minggu','bulan']:
            period = args[0].lower()
            records, masuk, keluar = get_period_data(user_id, period)
            label = "Minggu Ini" if period=='minggu' else "Bulan Ini"
            if not records:
                await update.message.reply_text(f"📅 Rekap {label}\n\nTidak ada transaksi."); return
            by_cat = {}
            for r in records:
                if r['Jenis']=='pengeluaran':
                    by_cat[r['Kategori']] = by_cat.get(r['Kategori'],0) + int(r['Jumlah'])
            cat_detail = "\nPer kategori:\n" + "".join(f"  • {k.capitalize()}: Rp {v:,}\n" for k,v in sorted(by_cat.items(), key=lambda x:-x[1])) if by_cat else ""
            s = masuk - keluar
            await update.message.reply_text(
                f"📅 Rekap {label}\n{'─'*26}\n"
                f"💰 Pemasukan  : Rp {masuk:,}\n"
                f"💸 Pengeluaran: Rp {keluar:,}\n"
                f"{cat_detail}\n{'─'*26}\n"
                f"{'✅' if s>=0 else '⚠️'} Saldo: Rp {s:,}"
            )
            return

        await kirim_rekap(user_id, context.bot, reply_to=update.message)
    except Exception as ex:
        await update.message.reply_text(f"Debug error: {str(ex)}")

async def hapus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        user_records = [(i+2, r) for i, r in enumerate(records) if str(r.get('User ID',''))==user_id]
        if not user_records:
            await update.message.reply_text("Tidak ada transaksi yang bisa dihapus."); return
        args = context.args
        n = int(args[0]) if args and args[0].isdigit() else 1
        if n < 1 or n > len(user_records):
            await update.message.reply_text(f"Nomor tidak valid. Kamu punya {len(user_records)} transaksi."); return
        row_idx, row_data = user_records[-n]
        sheet.delete_rows(row_idx)
        e = "💸" if row_data['Jenis']=='pengeluaran' else "💰"
        await update.message.reply_text(
            f"🗑 Dihapus!\n{e} {row_data['Keterangan'].capitalize()} — Rp {int(row_data['Jumlah']):,}\n"
            f"({row_data['Tanggal']} {row_data['Jam']})"
        )
    except Exception as ex:
        await update.message.reply_text(f"Debug error: {str(ex)}")

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try:
        args = context.args
        if not args or args[0].lower() != 'ya':
            today_rec, _, _ = get_today_data(user_id)
            count = len(today_rec)
            if count == 0:
                await update.message.reply_text("Tidak ada transaksi hari ini."); return
            await update.message.reply_text(f"⚠️ Yakin hapus {count} transaksi hari ini?\nKetik /reset ya untuk konfirmasi.")
            return
        sheet = get_sheet()
        today = datetime.now(WIB).strftime("%Y-%m-%d")
        records = sheet.get_all_records()
        rows = sorted([i+2 for i, r in enumerate(records)
                       if str(r.get('Tanggal',''))==today and str(r.get('User ID',''))==user_id], reverse=True)
        for row_idx in rows: sheet.delete_rows(row_idx)
        await update.message.reply_text(f"🗑 {len(rows)} transaksi hari ini dihapus. Saldo direset ke 0.")
    except Exception as ex:
        await update.message.reply_text(f"Debug error: {str(ex)}")

async def budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    args = context.args
    try:
        if len(args) >= 2:
            kategori = args[0].lower()
            jumlah_str = args[1].replace('.','').replace(',','')
            if not jumlah_str.isdigit():
                await update.message.reply_text("Format: /budget makanan 2000000"); return
            jumlah = int(jumlah_str)
            sheet = get_budget_sheet()
            records = sheet.get_all_records()
            found = False
            for i, r in enumerate(records):
                if str(r.get('User ID',''))==user_id and r.get('Kategori','')==kategori:
                    sheet.update_cell(i+2, 3, jumlah); found = True; break
            if not found: sheet.append_row([user_id, kategori, jumlah])
            await update.message.reply_text(f"✅ Budget {kategori.capitalize()} diset Rp {jumlah:,}/bulan")
        else:
            budgets = get_budget(user_id)
            if not budgets:
                await update.message.reply_text("Belum ada budget.\nContoh: /budget makanan 2000000"); return
            records, _, _ = get_period_data(user_id, 'bulan')
            msg = f"💼 Budget Bulan Ini\n{'─'*26}\n"
            for kat, limit in budgets.items():
                spent = sum(int(r['Jumlah']) for r in records if r['Jenis']=='pengeluaran' and r['Kategori']==kat)
                pct = min(int(spent/limit*100), 100) if limit > 0 else 0
                bar = '█'*(pct//10) + '░'*(10-pct//10)
                status = '✅' if pct < 80 else ('⚠️' if pct < 100 else '🔴')
                msg += f"{status} {kat.capitalize()}\n   [{bar}] {pct}%\n   Rp {spent:,} / Rp {limit:,}\n\n"
            await update.message.reply_text(msg)
    except Exception as ex:
        await update.message.reply_text(f"Debug error: {str(ex)}")

async def grafik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try:
        records, _, _ = get_period_data(user_id, 'bulan')
        pengeluaran = [r for r in records if r['Jenis']=='pengeluaran']
        if not pengeluaran:
            await update.message.reply_text("Belum ada pengeluaran bulan ini."); return
        by_cat = {}
        for r in pengeluaran:
            cat = r['Kategori'].capitalize()
            by_cat[cat] = by_cat.get(cat, 0) + int(r['Jumlah'])
        fig, ax = plt.subplots(figsize=(6, 5))
        colors = ['#5DCAA5','#7F77DD','#EF9F27','#D85A30','#378ADD','#D4537E','#639922','#888780']
        wedges, texts, autotexts = ax.pie(
            by_cat.values(), labels=by_cat.keys(), autopct='%1.0f%%',
            colors=colors[:len(by_cat)], startangle=90, pctdistance=0.8
        )
        for t in texts: t.set_fontsize(11)
        for t in autotexts: t.set_fontsize(9); t.set_color('white')
        now = datetime.now(WIB)
        total = sum(by_cat.values())
        ax.set_title(f"Pengeluaran {now.strftime('%B %Y')}\nTotal: Rp {total:,}", fontsize=12, pad=15)
        plt.tight_layout()
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0); plt.close()
        await update.message.reply_photo(photo=buf, caption=f"📊 Pengeluaran {now.strftime('%B %Y')}")
    except Exception as ex:
        await update.message.reply_text(f"Debug error: {str(ex)}")

async def hutang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try:
        records = get_hutang_sheet().get_all_records()
        aktif = [r for r in records if str(r.get('User ID',''))==user_id and r.get('Status','')=='aktif']
        if not aktif:
            await update.message.reply_text("Tidak ada hutang/piutang aktif.\n\nContoh catat:\nhutang ke budi 50000\nbudi pinjam 100000"); return
        saya = [r for r in aktif if r['Arah']=='saya_hutang']
        mereka = [r for r in aktif if r['Arah']=='mereka_hutang']
        msg = f"📋 Hutang Piutang Aktif\n{'─'*26}\n"
        if saya:
            msg += f"💸 Saya hutang:\n" + "".join(f"  • {r['Nama']}: Rp {int(r['Jumlah']):,}\n" for r in saya)
            msg += f"  Total: Rp {sum(int(r['Jumlah']) for r in saya):,}\n\n"
        if mereka:
            msg += f"💰 Piutang saya:\n" + "".join(f"  • {r['Nama']}: Rp {int(r['Jumlah']):,}\n" for r in mereka)
            msg += f"  Total: Rp {sum(int(r['Jumlah']) for r in mereka):,}\n\n"
        msg += "Ketik /lunas [nama] untuk tandai lunas"
        await update.message.reply_text(msg)
    except Exception as ex:
        await update.message.reply_text(f"Debug error: {str(ex)}")

async def lunas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    args = context.args
    if not args:
        await update.message.reply_text("Format: /lunas budi"); return
    nama = ' '.join(args).lower()
    try:
        sheet = get_hutang_sheet()
        records = sheet.get_all_records()
        for i, r in enumerate(records):
            if str(r.get('User ID',''))==user_id and r.get('Nama','').lower()==nama and r.get('Status','')=='aktif':
                sheet.update_cell(i+2, 6, 'lunas')
                await update.message.reply_text(f"✅ Hutang {r['Nama']} — Rp {int(r['Jumlah']):,} ditandai lunas!")
                return
        await update.message.reply_text(f"Tidak ditemukan hutang aktif atas nama '{nama}'.")
    except Exception as ex:
        await update.message.reply_text(f"Debug error: {str(ex)}")

async def kirim_rekap(user_id, bot, reply_to=None):
    try:
        today = datetime.now(WIB).strftime("%Y-%m-%d")
        records, masuk, keluar = get_today_data(user_id)
        if not records:
            msg = f"🌙 Rekap {today}\n\nTidak ada transaksi hari ini."
        else:
            detail = "".join(f"  {'💸' if r['Jenis']=='pengeluaran' else '💰'} {r['Keterangan']} — Rp {int(r['Jumlah']):,}\n" for r in records)
            s = masuk - keluar
            msg = (f"🌙 Rekap Keuangan {today}\n{'─'*26}\n{detail}{'─'*26}\n"
                   f"💰 Total Masuk  : Rp {masuk:,}\n💸 Total Keluar : Rp {keluar:,}\n{'─'*26}\n"
                   f"{'✅' if s>=0 else '⚠️'} Saldo Hari Ini: Rp {s:,}")
        if reply_to: await reply_to.reply_text(msg)
        else: await bot.send_message(chat_id=user_id, text=msg)
    except Exception as ex:
        logging.error(f"Error kirim_rekap {user_id}: {ex}")

async def jadwal_rekap_malam(context: CallbackContext):
    try:
        records = get_sheet().get_all_records()
        for uid in set(str(r.get('User ID','')) for r in records if r.get('User ID')):
            await kirim_rekap(uid, context.bot)
    except Exception as ex:
        logging.error(f"Error jadwal: {ex}")

def main():
    app = Application.builder().token(os.environ.get("TELEGRAM_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("saldo", saldo))
    app.add_handler(CommandHandler("rekap", rekap))
    app.add_handler(CommandHandler("hapus", hapus))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("budget", budget))
    app.add_handler(CommandHandler("grafik", grafik))
    app.add_handler(CommandHandler("hutang", hutang_cmd))
    app.add_handler(CommandHandler("lunas", lunas))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_daily(jadwal_rekap_malam, time=dtime(hour=14, minute=0, tzinfo=pytz.utc))
    app.run_polling()

if __name__ == "__main__":
    main()