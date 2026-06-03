import os
import logging
import asyncio
import ssl
from typing import Optional
from urllib.parse import urlparse

import pg8000.dbapi as pg

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ContextTypes,
)

# ─── Config ───────────────────────────────────────────────────────────────────

BOT_TOKEN     = os.environ["BOT_TOKEN"]
DATABASE_URL  = os.environ["DATABASE_URL"]
WEBHOOK_URL   = os.environ.get("WEBHOOK_URL", "")
PORT          = int(os.environ.get("PORT", 8080))

CHANNEL_1_USERNAME = "@ucplanet"
CHANNEL_2_ID       = -1003934812939
CHANNEL_2_LINK     = "https://t.me/+FZ4aRhgmrvQ1ZmI6"
CHANNEL_3_ID       = -1003999645745
CHANNEL_3_LINK     = "https://t.me/+e6xEfcq-pkk0NWVi"
PRIZE_CHANNEL_ID   = -1003822385223
BOT_USERNAME       = "ucfoydabot"
ADMIN_ID           = 5523761749
REQUIRED_INVITES   = 2

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Database connection ───────────────────────────────────────────────────────

def _parse_db_url(url: str) -> dict:
    r = urlparse(url)
    params = dict(
        host=r.hostname,
        port=r.port or 5432,
        database=r.path.lstrip("/"),
        user=r.username,
        password=r.password,
    )
    if "sslmode=disable" not in url:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        params["ssl_context"] = ctx
    return params


_DB_PARAMS = _parse_db_url(DATABASE_URL)


def _connect():
    return pg.connect(**_DB_PARAMS)


def _row_to_dict(cursor, row) -> Optional[dict]:
    if row is None:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


# ─── DB setup ─────────────────────────────────────────────────────────────────

def init_db():
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_users (
            telegram_id    BIGINT PRIMARY KEY,
            username       TEXT,
            first_name     TEXT NOT NULL,
            started_at     TIMESTAMP DEFAULT NOW(),
            is_verified    BOOLEAN DEFAULT FALSE,
            invited_by     BIGINT,
            referral_count INTEGER DEFAULT 0,
            join_link_sent BOOLEAN DEFAULT FALSE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS join_requests (
            id           SERIAL PRIMARY KEY,
            user_id      BIGINT NOT NULL,
            chat_id      BIGINT NOT NULL,
            requested_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, chat_id)
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("Database initialised")


# ─── DB helpers ───────────────────────────────────────────────────────────────

def upsert_user(telegram_id: int, username: Optional[str], first_name: str,
                invited_by: Optional[int] = None) -> Optional[dict]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bot_users (telegram_id, username, first_name, invited_by)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (telegram_id) DO UPDATE SET
            username   = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            invited_by = CASE
                WHEN bot_users.invited_by IS NULL THEN EXCLUDED.invited_by
                ELSE bot_users.invited_by
            END
        RETURNING *
    """, (telegram_id, username, first_name, invited_by))
    row = _row_to_dict(cur, cur.fetchone())
    conn.commit()
    cur.close()
    conn.close()
    return row


def get_user(telegram_id: int) -> Optional[dict]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bot_users WHERE telegram_id = %s", (telegram_id,))
    row = _row_to_dict(cur, cur.fetchone())
    cur.close()
    conn.close()
    return row


def set_verified(telegram_id: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("UPDATE bot_users SET is_verified = TRUE WHERE telegram_id = %s", (telegram_id,))
    conn.commit()
    cur.close()
    conn.close()


def set_join_link_sent(telegram_id: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("UPDATE bot_users SET join_link_sent = TRUE WHERE telegram_id = %s", (telegram_id,))
    conn.commit()
    cur.close()
    conn.close()


def increment_referral_count(telegram_id: int) -> int:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE bot_users SET referral_count = referral_count + 1 "
        "WHERE telegram_id = %s RETURNING referral_count",
        (telegram_id,),
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return row[0] if row else 0


def record_join_request(user_id: int, chat_id: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO join_requests (user_id, chat_id) VALUES (%s, %s) "
        "ON CONFLICT (user_id, chat_id) DO NOTHING",
        (user_id, chat_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def has_join_request(user_id: int, chat_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM join_requests WHERE user_id = %s AND chat_id = %s",
        (user_id, chat_id),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row is not None


def get_total_user_count() -> int:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM bot_users")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else 0


def get_all_user_ids() -> list:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM bot_users")
    rows = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def clear_all_referrals() -> int:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM join_requests")
    cur.execute("""
        UPDATE bot_users SET
            is_verified    = FALSE,
            referral_count = 0,
            join_link_sent = FALSE,
            invited_by     = NULL
    """)
    cur.execute("SELECT COUNT(*) FROM bot_users")
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return row[0] if row else 0


# ─── Bot helpers ──────────────────────────────────────────────────────────────

def ref_link(user_id: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 1-Kanal | @ucplanet", url="https://t.me/ucplanet")],
        [InlineKeyboardButton("🔐 2-Kanal | Qo'shilish so'rovi yuboring", url=CHANNEL_2_LINK)],
        [InlineKeyboardButton("🔐 3-Kanal | Qo'shilish so'rovi yuboring", url=CHANNEL_3_LINK)],
        [InlineKeyboardButton("✅ Tekshirish", callback_data="check_subs")],
    ])


async def is_channel1_member(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_1_USERNAME, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


async def send_join_link(bot, user_id: int):
    try:
        link = await bot.create_chat_invite_link(
            PRIZE_CHANNEL_ID,
            member_limit=1,
            name=f"ref_{user_id}",
        )
        set_join_link_sent(user_id)
        await bot.send_message(
            user_id,
            f"🏆 <b>TABRIKLAYMIZ!</b> Siz {REQUIRED_INVITES} ta do'stingizni taklif qildingiz!\n\n"
            f"🎁 <b>Maxsus kanalga kirish uchun sizning 1 martalik havolangiz:</b>\n\n"
            f"🔗 {link.invite_link}\n\n"
            f"⚠️ <i>Bu havola faqat 1 marta ishlaydi — hech kim bilan ulashmang!</i>\n"
            f"🎮 <b>Yaxshi o'yin!</b> 💎",
            parse_mode="HTML",
        )
        logger.info(f"Join link sent to user {user_id}")
    except Exception as e:
        logger.error(f"Failed to send join link to {user_id}: {e}")


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    invited_by = None

    if context.args and context.args[0].startswith("ref_"):
        try:
            ref_id = int(context.args[0].replace("ref_", ""))
            if ref_id != user.id:
                invited_by = ref_id
        except ValueError:
            pass

    upsert_user(user.id, user.username, user.first_name, invited_by)

    await update.message.reply_html(
        "🎮 <b>PUBG UC Konkursiga xush kelibsiz!</b> 🏆\n\n"
        "💎 <b>100 UC</b> yutib olish imkoniyatini qo'ldan boy bermang!\n\n"
        "📋 <b>Ishtirok etish uchun:</b>\n"
        "✅ 1-Kanalga obuna bo'ing\n"
        "✅ 2 va 3-kanalga qo'shilish so'rovi yuboring\n\n"
        "⬇️ <i>Quyidagi kanallarga o'ting va so'ng \"Tekshirish\" tugmasini bosing:</i>",
        reply_markup=subscription_keyboard(),
    )


async def check_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔍 Tekshirilmoqda...")
    user = update.effective_user

    db_user = get_user(user.id)
    if not db_user:
        await query.message.reply_text("❗ Iltimos, /start buyrug'ini yuboring.")
        return

    if db_user["is_verified"]:
        await query.message.reply_html(
            f"✅ <b>Siz allaqachon ro'yxatdan o'tgansiz!</b>\n\n"
            f"🔗 Sizning shaxsiy havolangiz:\n"
            f"{ref_link(user.id)}\n\n"
            f"👥 Taklif qilganlar: <b>{db_user['referral_count']}</b> / {REQUIRED_INVITES}"
        )
        return

    ch1ok = await is_channel1_member(context.bot, user.id)
    ch2ok = has_join_request(user.id, CHANNEL_2_ID)
    ch3ok = has_join_request(user.id, CHANNEL_3_ID)

    if not (ch1ok and ch2ok and ch3ok):
        lines = ["❌ <b>Barcha shartlar bajarilmagan!</b>\n"]
        lines.append(
            ("✅" if ch1ok else "❌") + " 1-Kanal (@ucplanet) — " +
            ("Obuna bo'lgansiz" if ch1ok else "Obuna bo'lmadingiz")
        )
        lines.append(
            ("✅" if ch2ok else "❌") + " 2-Kanal — " +
            ("So'rov yuborgansiz" if ch2ok else "So'rov yubormagansiz")
        )
        lines.append(
            ("✅" if ch3ok else "❌") + " 3-Kanal — " +
            ("So'rov yuborgansiz" if ch3ok else "So'rov yubormagansiz")
        )
        lines.append("\n📌 <i>Barcha amallarni bajaring va qayta tekshiring.</i>")
        await query.message.reply_html("\n".join(lines), reply_markup=subscription_keyboard())
        return

    set_verified(user.id)

    if db_user["invited_by"]:
        inviter = get_user(db_user["invited_by"])
        if inviter and not inviter["join_link_sent"]:
            new_count = increment_referral_count(db_user["invited_by"])
            try:
                await context.bot.send_message(
                    db_user["invited_by"],
                    f"👥 Do'stingiz tasdiqdan o'tdi! Taklif: <b>{new_count}</b> / {REQUIRED_INVITES}",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            if new_count >= REQUIRED_INVITES:
                await send_join_link(context.bot, db_user["invited_by"])

    await query.message.reply_html(
        f"🎉 <b>BARAKALLA! Barcha shartlarni bajardingiz!</b>\n\n"
        f"🤝 Konkursda <b>g'olib</b> bo'lish uchun <b>{REQUIRED_INVITES} ta do'stingizni</b> taklif qiling!\n\n"
        f"🔗 <b>Sizning shaxsiy havolangiz:</b>\n"
        f"{ref_link(user.id)}\n\n"
        f"💡 <i>Bu havolani do'stlaringizga yuboring. Ular botni ishga tushirib, "
        f"kanallarni tasdiqlashlari bilanoq siz mukofot olasiz!</i>"
    )


async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    user_id = req.from_user.id
    chat_id = req.chat.id
    if chat_id in (CHANNEL_2_ID, CHANNEL_3_ID):
        record_join_request(user_id, chat_id)
        logger.info(f"Join request recorded: user={user_id} chat={chat_id}")


async def odam_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    count = get_total_user_count()
    await update.message.reply_html(
        f"👥 <b>Bot foydalanuvchilari statistikasi</b>\n\n"
        f"📊 Jami botni boshlagan: <b>{count}</b> ta foydalanuvchi"
    )


async def xabar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("❗ Xabar matni kiriting: /xabar <matn>")
        return

    user_ids = get_all_user_ids()
    await update.message.reply_text(
        f"📤 Xabar yuborilmoqda... {len(user_ids)} ta foydalanuvchiga"
    )

    sent = failed = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(
                uid, f"📣 <b>E'lon</b>\n\n{text}", parse_mode="HTML"
            )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.035)

    await update.message.reply_html(
        f"✅ <b>Xabar yuborildi!</b>\n\n"
        f"📨 Muvaffaqiyatli: <b>{sent}</b>\n"
        f"❌ Yuborilmadi: <b>{failed}</b>"
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("⏳ Barcha ma'lumotlar tozalanmoqda...")
    total = clear_all_referrals()
    await update.message.reply_html(
        f"✅ <b>Tozalash tugadi!</b>\n\n"
        f"🗑 Barcha referrallar, tasdiqlashlar va havola holatlari nolga qaytarildi.\n"
        f"👤 Ta'sirlangan foydalanuvchilar: <b>{total}</b>"
    )


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_subs, pattern="^check_subs$"))
    app.add_handler(ChatJoinRequestHandler(handle_join_request))
    app.add_handler(CommandHandler("odam", odam_command))
    app.add_handler(CommandHandler("xabar", xabar_command))
    app.add_handler(CommandHandler("clear", clear_command))

    if WEBHOOK_URL:
        webhook_path = f"/webhook/{BOT_TOKEN}"
        full_url = f"{WEBHOOK_URL}{webhook_path}"
        logger.info(f"Webhook mode: {full_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=full_url,
            url_path=webhook_path,
            drop_pending_updates=True,
        )
    else:
        logger.info("Polling mode")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
