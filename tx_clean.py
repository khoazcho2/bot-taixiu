#!/usr/bin/env python3
"""
Bot Tài Xỉu + Bầu Cua Telegram
Cài: pip install python-telegram-bot
"""

import random
import asyncio
import logging
import time
import json
import os
from collections import deque
from datetime import date
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
DATA_FILE = "lich_su.json"
lich_su = {}  # cid -> deque(10) of KQ_TAI/KQ_XIU
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN  = "8412067172:AAEjPZJepvJS9AKbTkaBkpb3Xnxw-Adn7xk"
ADMIN_IDS  = [ 8337495954 ]
THUONG_DD  = 50_000
TIEN_BD    = 50_000
THOI_GIAN  = 30        # giây đặt cược
XOA_SAU    = 10        # giây sau khi gửi kết quả thì tự xóa (0 = không xóa)

KQ_TAI, KQ_XIU = "tai", "xiu"

BC_BIEU = {"bau": "Bầu", "cua": "Cua", "ca": "Cá", "ga": "Gà", "tom": "Tôm", "nai": "Nai"}
BC_KEYS  = list(BC_BIEU.keys())

users   = {}   # uid  → {ten, tien, diem_danh}
tx_game = {}   # cid  → {dang_chay, cuoc:{uid→{so,cu}}, msg_ids:[]}
bc_game = {}   # cid  → {dang_chay, cuoc:{uid→{so,con}}, msg_ids:[]}
lich_su = {}   # cid  → deque(10) of KQ_TAI/KQ_XIU

def get_user(uid, ten="Khách"):
    if uid not in users:
        users[uid] = {"ten": ten, "tien": TIEN_BD, "diem_danh": None}
    return users[uid]

def fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")

def is_admin(uid): return uid in ADMIN_IDS

def parse_tai_xiu(s):
    s = s.lower().strip()
    if s in ("tai", "tài", "t"): return KQ_TAI
    if s in ("xiu", "xỉu", "x"): return KQ_XIU
    return None

def render_ls(cid):
    ls = lich_su.get(cid)
    if not ls: return "(Chưa có kết quả nào)"
    row = " ".join("Đ" if k == KQ_TAI else "T" for k in ls)
    return row + "\nĐ=Tài  T=Xỉu"

async def send_and_track(ctx, cid, text, game_dict, parse_mode=None):
    """Gửi tin nhắn và lưu message_id để xóa sau."""
    try:
        kw = {"parse_mode": parse_mode} if parse_mode else {}
        m = await ctx.bot.send_message(cid, text, **kw)
        if cid in game_dict:
            game_dict[cid].setdefault("msg_ids", []).append(m.message_id)
        return m
    except Exception as e:
        logger.error(e)
async def xoa_tin_nhan(ctx, cid, msg_ids: list, delay=0):
    """Xóa danh sách tin nhắn sau `delay` giây."""

    if not msg_ids or delay <= 0:
        return

    await asyncio.sleep(delay)

    for mid in msg_ids:
        try:
            await ctx.bot.delete_message(chat_id=cid, message_id=mid)
        except Exception as e:
            logger.error(f"Lỗi xoá tin {mid}: {e}")
async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t0  = time.monotonic()
    msg = await update.message.reply_text("Đang đo ping...")
    ms  = int((time.monotonic() - t0) * 1000)
    await msg.edit_text(f"Pong! {ms}ms")

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id, update.effective_user.first_name)
    u["ten"] = update.effective_user.first_name
    await update.message.reply_text(
        f"Chào {u['ten']}! Bot Tài Xỉu & Bầu Cua\n"
        f"Số dư: {fmt(u['tien'])} xu\n\n"
        "Lệnh:\n"
        "  /lac            - Bắt đầu Tài Xỉu\n"
        "  /cuoc tien tai/xiu\n"
        "  /cuoc all tai/xiu\n"
        "  /baucua         - Bắt đầu Bầu Cua\n"
        "  /bc tien ten_con  (bau/cua/ca/ga/tom/nai)\n"
        "  /bc all ten_con\n"
        "  /sodu           - Số dư\n"
        "  /lichsu         - 10 kết quả Tài Xỉu gần nhất\n"
        "  /top            - Bảng xếp hạng\n"
        f"  /diemdanh       - Nhận {fmt(THUONG_DD)} xu/ngày\n"
        "  /chuyentien [id] [tiền] - Chuyển tiền cho member\n"
        "  /ping           - Ping bot"
    )

async def cmd_diemdanh(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id, update.effective_user.first_name)
    if u["diem_danh"] == date.today():
        await update.message.reply_text("Bạn đã điểm danh hôm nay rồi! Quay lại ngày mai.")
        return
    u["diem_danh"] = date.today()
    u["tien"] += THUONG_DD
    await update.message.reply_text(
        f"Điểm danh thành công!\n"
        f"Nhận: +{fmt(THUONG_DD)} xu\n"
        f"Số dư: {fmt(u['tien'])} xu"
    )

async def cmd_sodu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id, update.effective_user.first_name)
    await update.message.reply_text(f"Số dư của {u['ten']}: {fmt(u['tien'])} xu")

async def cmd_lichsu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"10 kết quả Tài Xỉu gần nhất:\n{render_ls(update.effective_chat.id)}"
    )

async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not users:
        await update.message.reply_text("Chưa có ai chơi!")
        return
    top10 = sorted(users.values(), key=lambda x: x["tien"], reverse=True)[:10]
    lines = ["BẢNG XẾP HẠNG TOP 10", "-" * 22]
    for i, u in enumerate(top10, 1):
        lines.append(f"{i}. {u['ten']}: {fmt(u['tien'])} xu")
    await update.message.reply_text("\n".join(lines))

async def _tx_ket_thuc(ctx, cid):
    """Xử lý kết quả Tài Xỉu và tự xóa tin nhắn."""
    if cid not in tx_game or not tx_game[cid]["dang_chay"]:
        return

    state = tx_game.pop(cid)
    state["dang_chay"] = False

    dice    = [random.randint(1, 6) for _ in range(3)]
    tong    = sum(dice)
    ket_qua = KQ_TAI if tong >= 11 else KQ_XIU

    lich_su.setdefault(cid, deque(maxlen=10)).append(ket_qua)
    save_history()
    ten_kq = "TÀI" if ket_qua == KQ_TAI else "XỈU"
    lines  = [
        f"[{dice[0]}] [{dice[1]}] [{dice[2]}]",
        f"Tổng: {tong} ({dice[0]}+{dice[1]}+{dice[2]})",
        f"Kết quả: {ten_kq}",
    ]

    cuoc = state.get("cuoc", {})
    if not cuoc:
        lines.append("Không có ai đặt cược ván này.")
    else:
        lines.append("Kết quả cược:")
        for uid, info in cuoc.items():
            u  = users.get(uid)
            if not u: continue
            so, cu = info["so"], info["cu"]
            if cu == ket_qua:
                u["tien"] += so * 2   # hoàn vốn + lãi (tiền đã bị trừ khi đặt)
                lines.append(f"  + {u['ten']}: +{fmt(so)} xu (số dư: {fmt(u['tien'])} xu)")
            else:
                lines.append(f"  - {u['ten']}: -{fmt(so)} xu (số dư: {fmt(u['tien'])} xu)")


    all_ids = list(state.get("msg_ids", []))
    try:
        m = await ctx.bot.send_message(cid, "\n".join(lines))
        all_ids.append(m.message_id)
    except Exception as e:
        logger.error(e)

    asyncio.create_task(xoa_tin_nhan(ctx, cid, all_ids, delay=XOA_SAU))
async def _tx_countdown(ctx, cid):
    if cid not in tx_game or not tx_game[cid]["dang_chay"]:
        return

    header = (
        f"BẮT ĐẦU TÀI XỈU!\n"
        f"Đặt cược trong {THOI_GIAN} giây\n\n"
        f"/cuoc 1000 tài  |  /cuoc all xỉu\n\n"
    )

    msg = await ctx.bot.send_message(cid, header + f"⏳ {THOI_GIAN}...")
    tx_game[cid]["msg_ids"].append(msg.message_id)

    for i in range(THOI_GIAN - 1, 0, -1):

        if cid not in tx_game or not tx_game[cid]["dang_chay"]:
            return

        await asyncio.sleep(1)

        try:
            await msg.edit_text(header + f"⏳ {i}...")
        except:
            pass

    await _tx_ket_thuc(ctx, cid)

async def cmd_lac(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id

    if cid in tx_game and tx_game[cid]["dang_chay"]:
        await update.message.reply_text("Đang có ván Tài Xỉu chạy rồi!")
        return

    tx_game[cid] = {"dang_chay": True, "cuoc": {}, "msg_ids": []}

    asyncio.create_task(_tx_countdown(ctx, cid))
async def cmd_cuoc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cid = update.effective_chat.id
    u   = get_user(uid, update.effective_user.first_name)
    u["ten"] = update.effective_user.first_name

    if cid not in tx_game or not tx_game[cid]["dang_chay"]:
        await update.message.reply_text("Chưa có ván Tài Xỉu! Dùng /lac để bắt đầu.")
        return

    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Cú pháp: /cuoc [tiền/all] [tài/xỉu]")
        return

    so_arg = args[0].lower().strip()
    is_all = so_arg == "all"
    if is_all:
        if u["tien"] <= 0:
            await update.message.reply_text("Bạn không có xu nào để cược!")
            return
        so_tien = u["tien"]
    else:
        try:
            so_tien = int(so_arg)
        except ValueError:
            await update.message.reply_text("Số tiền không hợp lệ!")
            return

    chon = parse_tai_xiu(args[1])
    if chon is None:
        await update.message.reply_text("Phải chọn tài hoặc xỉu!")
        return
    if so_tien <= 0:
        await update.message.reply_text("Số tiền phải lớn hơn 0!")
        return
    if so_tien > u["tien"]:
        await update.message.reply_text(f"Không đủ tiền! Số dư: {fmt(u['tien'])} xu")
        return
    if uid in tx_game[cid]["cuoc"]:
        await update.message.reply_text("Bạn đã đặt cược ván này rồi!")
        return

    u["tien"] -= so_tien
    tx_game[cid]["cuoc"][uid] = {"so": so_tien, "cu": chon}

    tag   = " (ALL IN)" if is_all else ""
    ten_c = "TÀI" if chon == KQ_TAI else "XỈU"
    m = await update.message.reply_text(
        f"{u['ten']} đặt cược{tag}\n"
        f"{fmt(so_tien)} xu → {ten_c}\n"
        f"Số dư còn: {fmt(u['tien'])} xu"
    )
    tx_game[cid]["msg_ids"].append(m.message_id)

async def _bc_ket_thuc(ctx, cid):
    if cid not in bc_game or not bc_game[cid]["dang_chay"]:
        return

    state = bc_game.pop(cid)
    state["dang_chay"] = False

    dice = [random.choice(BC_KEYS) for _ in range(3)]
    count = {k: dice.count(k) for k in BC_KEYS}

    lines = [
        "  ".join(BC_BIEU[d] for d in dice),
    ]

    cuoc = state.get("cuoc", {})
    if not cuoc:
        lines.append("Không có ai đặt cược ván này.")
    else:
        lines.append("Kết quả cược:")
        for uid, info in cuoc.items():
            u   = users.get(uid)
            if not u: continue
            so, con = info["so"], info["con"]
            so_con  = count.get(con, 0)
            if so_con > 0:
                thang = so * so_con   # thắng x1, x2, x3 tùy số mặt trùng
                u["tien"] += so + thang
                lines.append(f"  + {u['ten']} ({BC_BIEU[con]}x{so_con}): +{fmt(thang)} xu")
            else:
                lines.append(f"  - {u['ten']} ({BC_BIEU[con]}): -{fmt(so)} xu")

    all_ids = list(state.get("msg_ids", []))
    try:
        m = await ctx.bot.send_message(cid, "\n".join(lines))
        all_ids.append(m.message_id)
    except Exception as e:
        logger.error(e)

    asyncio.create_task(xoa_tin_nhan(ctx, cid, all_ids, delay=XOA_SAU))

async def _bc_countdown(ctx, cid):
    if cid not in bc_game or not bc_game[cid]["dang_chay"]:
        return
    try:
        header = (
            f"BẮT ĐẦU BẦU CUA!\n"
            f"Đặt cược trong {THOI_GIAN} giây\n\n"
            f"Các con: Bầu / Cua / Cá / Gà / Tôm / Nai\n"
            f"/bc 1000 bau  |  /bc all tom\n\n"
        )
        msg = await ctx.bot.send_message(cid, header + f"⏳ {THOI_GIAN}...")
        bc_game[cid]["msg_ids"].append(msg.message_id)
        for i in range(THOI_GIAN - 1, 0, -1):
            await asyncio.sleep(1)
            if cid not in bc_game or not bc_game[cid]["dang_chay"]:
                return
            await msg.edit_text(header + f"⏳ {i}...")
        await asyncio.sleep(1)
    except Exception as e:
        logger.error(e)
    await _bc_ket_thuc(ctx, cid)

async def cmd_baucua(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if cid in bc_game and bc_game[cid]["dang_chay"]:
        await update.message.reply_text("Đang có ván Bầu Cua chạy rồi!\n/bc [tiền/all] [bau/cua/ca/ga/tom/nai]")
        return

    bc_game[cid] = {"dang_chay": True, "cuoc": {}, "msg_ids": []}
    task = asyncio.create_task(_bc_countdown(ctx, cid))
    bc_game[cid]["task"] = task

async def cmd_bc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cid = update.effective_chat.id
    u   = get_user(uid, update.effective_user.first_name)
    u["ten"] = update.effective_user.first_name

    if cid not in bc_game or not bc_game[cid]["dang_chay"]:
        await update.message.reply_text("Chưa có ván Bầu Cua! Dùng /baucua để bắt đầu.")
        return

    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Cú pháp: /bc [tiền/all] [bau/cua/ca/ga/tom/nai]")
        return

    so_arg = args[0].lower().strip()
    is_all = so_arg == "all"
    if is_all:
        if u["tien"] <= 0:
            await update.message.reply_text("Bạn không có xu nào để cược!")
            return
        so_tien = u["tien"]
    else:
        try:
            so_tien = int(so_arg)
        except ValueError:
            await update.message.reply_text("Số tiền không hợp lệ!")
            return

    con = args[1].lower().strip()
    if con not in BC_KEYS:
        await update.message.reply_text(f"Con không hợp lệ!\nChọn: {', '.join(BC_KEYS)}")
        return
    if so_tien <= 0:
        await update.message.reply_text("Số tiền phải lớn hơn 0!")
        return
    if so_tien > u["tien"]:
        await update.message.reply_text(f"Không đủ tiền! Số dư: {fmt(u['tien'])} xu")
        return
    if uid in bc_game[cid]["cuoc"]:
        await update.message.reply_text("Bạn đã đặt cược ván này rồi!")
        return

    u["tien"] -= so_tien
    bc_game[cid]["cuoc"][uid] = {"so": so_tien, "con": con}

    tag = " (ALL IN)" if is_all else ""
    m = await update.message.reply_text(
        f"{u['ten']} đặt cược{tag}\n"
        f"{fmt(so_tien)} xu → {BC_BIEU[con]}\n"
        f"Số dư còn: {fmt(u['tien'])} xu"
    )
    bc_game[cid]["msg_ids"].append(m.message_id)

async def cmd_chuyentien(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    u    = get_user(uid, update.effective_user.first_name)
    u["ten"] = update.effective_user.first_name

    reply_msg = update.message.reply_to_message
    args      = ctx.args

    if reply_msg:
        if len(args) < 1:
            await update.message.reply_text("Reply tin nhắn rồi gõ: /chuyentien [số tiền]")
            return
        try:
            so  = int(args[0])
            tid = reply_msg.from_user.id
            get_user(tid, reply_msg.from_user.first_name)  # đảm bảo user tồn tại
        except (ValueError, AttributeError):
            await update.message.reply_text("Số tiền không hợp lệ!")
            return
    else:
        if len(args) < 2:
            await update.message.reply_text(
                "Cú pháp:\n"
                "  Reply tin nhắn + /chuyentien [số tiền]\n"
                "  Hoặc: /chuyentien [user_id] [số tiền]"
            )
            return
        try:
            tid = int(args[0])
            so  = int(args[1])
        except ValueError:
            await update.message.reply_text("user_id và số tiền phải là số nguyên!")
            return
        if tid not in users:
            await update.message.reply_text(f"Không tìm thấy user ID {tid}!\nHọ phải đã từng nhắn tin với bot.")
            return

    if tid == uid:
        await update.message.reply_text("Không thể chuyển tiền cho chính mình!")
        return
    if so <= 0:
        await update.message.reply_text("Số tiền phải lớn hơn 0!")
        return
    if so > u["tien"]:
        await update.message.reply_text(f"Không đủ tiền!\nSố dư của bạn: {fmt(u['tien'])} xu")
        return

    u["tien"]          -= so
    users[tid]["tien"] += so
    ten_nhan = users[tid]["ten"]

    await update.message.reply_text(
        f"Chuyển tiền thành công!\n"
        f"Người nhận: {ten_nhan} (ID: {tid})\n"
        f"Số tiền: {fmt(so)} xu\n"
        f"Số dư còn lại: {fmt(u['tien'])} xu"
    )
    try:
        await ctx.bot.send_message(
            tid,
            f"{u['ten']} vừa chuyển cho bạn {fmt(so)} xu!\n"
            f"Số dư hiện tại: {fmt(users[tid]['tien'])} xu"
        )
    except:
        pass

async def cmd_addtien(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Bạn không có quyền!")
        return

    reply_msg = update.message.reply_to_message
    args      = ctx.args

    if not reply_msg:
        await update.message.reply_text("Hãy reply vào tin nhắn của user rồi gõ:\n/addtien [số tiền]")
        return
    if len(args) < 1:
        await update.message.reply_text("Cú pháp: reply tin nhắn user + /addtien [số tiền]")
        return

    try:
        so  = int(args[0])
        tid = reply_msg.from_user.id
        get_user(tid, reply_msg.from_user.first_name)
    except (ValueError, AttributeError):
        await update.message.reply_text("Số tiền không hợp lệ!")
        return

    if so <= 0:
        await update.message.reply_text("Số tiền phải > 0!")
        return

    ten = users[tid]["ten"]
    msg = await update.message.reply_text(f"Nạp {fmt(so)} xu cho {ten}...\n5")
    for i in range(4, 0, -1):
        await asyncio.sleep(1)
        try:
            await msg.edit_text(f"Nạp {fmt(so)} xu cho {ten}...\n{i}")
        except:
            pass
    await asyncio.sleep(1)

    users[tid]["tien"] += so
    await msg.edit_text(
        f"Đã nạp {fmt(so)} xu cho {ten}\n"
        f"Số dư mới: {fmt(users[tid]['tien'])} xu"
    )
    try:
        await ctx.bot.send_message(tid, f"Admin đã nạp {fmt(so)} xu!\nSố dư: {fmt(users[tid]['tien'])} xu")
    except:
        pass

async def cmd_trutien(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Bạn không có quyền!")
        return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Cú pháp: /trutien [user_id] [số tiền]")
        return
    try:
        tid, so = int(args[0]), int(args[1])
    except ValueError:
        await update.message.reply_text("Sai định dạng!")
        return
    if tid not in users:
        await update.message.reply_text(f"Không tìm thấy user {tid}!")
        return
    users[tid]["tien"] = max(0, users[tid]["tien"] - so)
    await update.message.reply_text(f"Đã trừ {fmt(so)} xu của {users[tid]['ten']}\nSố dư mới: {fmt(users[tid]['tien'])} xu")

async def cmd_danhsach(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Bạn không có quyền!")
        return
    if not users:
        await update.message.reply_text("Chưa có user nào.")
        return
    rows  = sorted(users.items(), key=lambda x: x[1]["tien"], reverse=True)
    lines = ["DANH SÁCH USER:", "-" * 26]
    for uid, u in rows:
        lines.append(f"ID: {uid} | {u['ten']} | {fmt(u['tien'])} xu")
    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:4000] + "\n..."
    await update.message.reply_text(msg)
def save_history():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {str(k): list(v) for k, v in lich_su.items()},
            f,
            ensure_ascii=False,
            indent=4
        )

def load_history():
    global lich_su
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            lich_su = {
                int(k): deque(v, maxlen=20)
                for k, v in data.items()
            }
def main():
    load_history() 
    
    app = Application.builder().token(BOT_TOKEN).build()

    for cmd, fn in [
        ("start",     cmd_start),
        ("ping",      cmd_ping),
        ("lac",       cmd_lac),
        ("cuoc",      cmd_cuoc),
        ("baucua",    cmd_baucua),
        ("bc",        cmd_bc),
        ("diemdanh",  cmd_diemdanh),
        ("sodu",      cmd_sodu),
        ("lichsu",    cmd_lichsu),
        ("top",       cmd_top),
        ("chuyentien",cmd_chuyentien),
        ("addtien",   cmd_addtien),
        ("trutien",   cmd_trutien),
        ("danhsach",  cmd_danhsach),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    print("Bot Tài Xỉu & Bầu Cua đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
