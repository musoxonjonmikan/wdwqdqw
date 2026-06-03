import os
import logging
import asyncio
from typing import Optional
from pymongo import MongoClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ContextTypes,
)

# ─── Config ───────────────────────────────────────────────────────────────────

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "8883352839:AAGMdKlOhpgZgfjo6jdsNOJ2maMD9i_I-Nw")
DATABASE_URL  = os.environ.get("DATABASE_URL", "mongodb+srv://musoxonshovkatov_db_user:2010@cluster.ivoyjac.mongodb.net/?appName=Cluster")
WEBHOOK_URL   = os.environ.get("WEBHOOK_URL", "https://your-render-app.onrender.com")
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

# ─── MongoDB connection ───────────────────────────────────────────────────────

client = MongoClient(DATABASE_URL)
db = client["bot_db"]

users_col = db["bot_users"]
join_col = db["join_requests"]


# ─── DB setup ─────────────────────────────────────────────────────────────────

def init_db():
    logger.info("MongoDB connected successfully")


# ─── DB helpers ───────────────────────────────────────────────────────────────

def upsert_user(telegram_id: int, username, first_name, invited_by=None):
    return users_col.find_one_and_update(
        {"telegram_id": telegram_id},
        {
            "$set": {
                "username": username,
                "first_name": first_name,
            },
            "$setOnInsert": {
                "telegram_id": telegram_id,
                "is_verified": False,
                "referral_count": 0,
                "join_link_sent": False,
                "invited_by": invited_by,
            }
        },
        upsert=True
    )

def get_user(telegram_id: int):
    return users_col.find_one({"telegram_id": telegram_id})


def set_verified(telegram_id: int):
    users_col.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"is_verified": True}}
    )


def set_join_link_sent(telegram_id: int):
    users_col.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"join_link_sent": True}}
    )


def increment_referral_count(telegram_id: int) -> int:
    res = users_col.find_one_and_update(
        {"telegram_id": telegram_id},
        {"$inc": {"referral_count": 1}},
        return_document=True
    )
    return res.get("referral_count", 0)


def record_join_request(user_id: int, chat_id: int):
    join_col.update_one(
        {"user_id": user_id, "chat_id": chat_id},
        {"$setOnInsert": {"user_id": user_id, "chat_id": chat_id}},
        upsert=True
    )


def has_join_request(user_id: int, chat_id: int) -> bool:
    return join_col.find_one({"user_id": user_id, "chat_id": chat_id}) is not None

def get_total_user_count() -> int:
    return users_col.count_documents({})


def get_all_user_ids() -> list:
    return [u["telegram_id"] for u in users_col.find({}, {"telegram_id": 1})]


def clear_all_referrals() -> int:
    join_col.delete_many({})
    users_col.update_many(
        {},
        {"$set": {
            "is_verified": False,
            "referral_count": 0,
            "join_link_sent": False,
            "invited_by": None
        }}
    )
    return users_col.count_documents({})

# ─── Bot helpers ──────────────────────────────────────────────────────────────

def ref_link(user_id: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 1-Kanal | " + CHANNEL_1_USERNAME, url="https://t.me/" + CHANNEL_1_USERNAME.replace("@", ""))],
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
        "✅ 1-Kanalga obuna bo'ling\n"
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

    if db_user.get("is_verified", False):
        await query.message.reply_html(
            f"✅ <b>Siz allaqachon ro'yxatdan o'tgansiz!</b>\n\n"
            f"🔗 Sizning shaxsiy havolangiz:\n"
            f"{ref_link(user.id)}\n\n"
            f"👥 Taklif qilganlar: <b>{db_user.get('referral_count', 0)}</b> / {REQUIRED_INVITES}"
        )
        return

    ch1ok = await is_channel1_member(context.bot, user.id)
    ch2ok = has_join_request(user.id, CHANNEL_2_ID)
    ch3ok = has_join_request(user.id, CHANNEL_3_ID)

    if not (ch1ok and ch2ok and ch3ok):
        lines = ["❌ <b>Barcha shartlar bajarilmagan!</b>\n"]
        lines.append(
            ("✅" if ch1ok else "❌") + f" 1-Kanal ({CHANNEL_1_USERNAME}) — " +
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

    if db_user.get("invited_by"):
        inviter = get_user(db_user["invited_by"])
        if inviter and not inviter.get("join_link_sent", False):
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

# ─── Entry point (Custom Async Event Loop Mode) ───────────────────────────────

async def run_bot():
    init_db()

    # Build python-telegram-bot application
    app = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_subs, pattern="^check_subs$"))
    app.add_handler(ChatJoinRequestHandler(handle_join_request))
    app.add_handler(CommandHandler("odam", odam_command))
    app.add_handler(CommandHandler("xabar", xabar_command))
    app.add_handler(CommandHandler("clear", clear_command))

    logger.info("Initializing bot with custom async runner...")
    
    # When running manually inside an async event loop in python-telegram-bot v20+
    # we initialize, start, and turn on the polling process asynchronously.
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        
        logger.info("Bot is active and polling. Idling now...")
        
        # ⚠️ CRITICAL FIX FOR YOUR ERROR:
        # 'Updater' object has no attribute 'idle' in python-telegram-bot v20+
        # 'idle' is now an asynchronous coroutine method on the Application itself!
        # DO NOT call: await app.updater.idle()
        # DO call:     await app.idle()
        await app.idle()
        
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    try:
        # Standard Python entry to run our async main runner
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")


if __name__ == "__main__":
    main()
