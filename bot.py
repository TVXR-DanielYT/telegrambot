import os
import json
import asyncio
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from telegram.error import TelegramError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "8837982379:AAFBC0JGDF7m5ayXI8ijbi_rZgOL2vKql7U")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "5391207860").split(",")]
SUPPORT_USER = "@jeapppp"

# Credit pricing (credits → price description)
CREDIT_PACKAGES = {
    "starter":  {"credits": 100,  "price": "2€",  "label": "🟢 Starter – 100 Credits for 2€"},
    "basic":    {"credits": 300,  "price": "5€",  "label": "🔵 Basic – 300 Credits for 5€"},
    "pro":      {"credits": 1000, "price": "15€", "label": "🟣 Pro – 1,000 Credits for 15€"},
    "ultra":    {"credits": 5000, "price": "60€", "label": "🔴 Ultra – 5,000 Credits for 60€"},
}

# Cost per ad post (per group)
CREDITS_PER_GROUP = 10

# ─── REFERRAL CONFIG ───────────────────────────────────────────────────────────
REFERRAL_BONUS_CREDITS = 5

# ─── REQUIRED CHANNELS (forced join) ───────────────────────────────────────────
REQUIRED_CHANNELS = [
    {"username": "@jpxqstock", "url": "https://t.me/jpxqstock", "name": "Jpxq Stock"},
    {"username": "@jpxqbotsupport", "url": "https://t.me/jpxqbotsupport", "name": "Jpxq Bot Support"},
]

# DB file (simple JSON – use PostgreSQL for production)
DB_FILE = "database.json"

# ─── DATABASE ──────────────────────────────────────────────────────────────────
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {"users": {}, "groups": {}, "ads": [], "pending_payments": {}}

def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2, default=str)

def get_user(db, user_id):
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {
            "credits": 0,
            "ads_sent": 0,
            "joined": str(datetime.now()),
            "referred_by": None,
            "referrals": [],
            "verified": False,
        }
    # backfill fields for users created before referral system existed
    user = db["users"][uid]
    user.setdefault("referred_by", None)
    user.setdefault("referrals", [])
    user.setdefault("verified", False)
    return user

# ─── CONVERSATION STATES ───────────────────────────────────────────────────────
WAITING_AD_TEXT, WAITING_AD_CONFIRM, WAITING_PAYMENT_PROOF = range(3)
ADMIN_WAITING_GROUP_ID, ADMIN_WAITING_CREDITS_USER, ADMIN_WAITING_CREDITS_AMOUNT = range(3, 6)

# ─── HELPERS ───────────────────────────────────────────────────────────────────
def is_admin(user_id):
    return user_id in ADMIN_IDS

def credits_keyboard():
    buttons = []
    for key, pkg in CREDIT_PACKAGES.items():
        buttons.append([InlineKeyboardButton(pkg["label"], callback_data=f"buy_{key}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Post an Ad", callback_data="post_ad"),
         InlineKeyboardButton("💳 Buy Credits", callback_data="buy_credits")],
        [InlineKeyboardButton("📊 My Account", callback_data="my_account"),
         InlineKeyboardButton("ℹ️ How it works", callback_data="how_it_works")],
        [InlineKeyboardButton("🎁 Referral Program", callback_data="referral")],
    ])

def join_keyboard():
    buttons = [[InlineKeyboardButton(f"📢 Join {ch['name']}", url=ch["url"])] for ch in REQUIRED_CHANNELS]
    buttons.append([InlineKeyboardButton("✅ I joined – Check", callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)

async def is_member_of_all(ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    for ch in REQUIRED_CHANNELS:
        try:
            member = await ctx.bot.get_chat_member(ch["username"], user_id)
            if member.status in ("left", "kicked"):
                return False
        except TelegramError as e:
            logger.error(f"Could not check membership for {ch['username']}: {e}")
            # If the bot can't check (e.g. not admin in channel), fail closed
            return False
    return True

async def send_join_prompt(send_func):
    await send_func(
        "👋 *Welcome!*\n\n"
        "Before you can use this bot, please join our channels:\n\n"
        + "\n".join([f"• {ch['name']}" for ch in REQUIRED_CHANNELS]) +
        "\n\nAfter joining both, press *I joined – Check* below 👇",
        parse_mode="Markdown",
        reply_markup=join_keyboard()
    )

async def grant_referral_bonus(ctx: ContextTypes.DEFAULT_TYPE, db, new_user_id: int):
    """Credit the referrer once the new user is verified (joined channels)."""
    new_user = get_user(db, new_user_id)
    referrer_id = new_user.get("referred_by")
    if not referrer_id:
        return
    referrer = get_user(db, referrer_id)
    if str(new_user_id) in referrer.get("referrals", []):
        return  # already credited
    referrer["credits"] += REFERRAL_BONUS_CREDITS
    referrer["referrals"].append(str(new_user_id))
    save_db(db)
    try:
        await ctx.bot.send_message(
            int(referrer_id),
            f"🎉 *Referral Bonus!*\n\n"
            f"Someone joined using your referral link!\n"
            f"✅ +{REFERRAL_BONUS_CREDITS} Credits added.\n"
            f"💰 New balance: *{referrer['credits']} Credits*",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to notify referrer {referrer_id}: {e}")

# ─── /START ────────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    user_id = update.effective_user.id
    user = get_user(db, user_id)

    # Capture referral code on first ever /start (deep link: /start ref_123456)
    if user["referred_by"] is None and ctx.args:
        arg = ctx.args[0]
        if arg.startswith("ref_"):
            ref_id = arg.replace("ref_", "")
            if ref_id.isdigit() and int(ref_id) != user_id and str(user_id) not in db["users"].get(ref_id, {}).get("referrals", []):
                user["referred_by"] = ref_id
    save_db(db)

    # Forced channel join check
    if not user["verified"]:
        if await is_member_of_all(ctx, user_id):
            user["verified"] = True
            save_db(db)
            await grant_referral_bonus(ctx, db, user_id)
        else:
            await send_join_prompt(update.message.reply_text)
            return

    name = update.effective_user.first_name
    credits = user["credits"]

    await update.message.reply_text(
        f"👋 Hey {name}!\n\n"
        f"🤖 *JpxqAdvertise* – Your Telegram Advertising Network\n\n"
        f"💰 Your Credits: *{credits}*\n\n"
        f"📢 Advertise across our group network!\n"
        f"• Each group costs *{CREDITS_PER_GROUP} Credits*\n"
        f"• Your ad reaches real Telegram users\n"
        f"• Fast, simple & affordable!\n\n"
        f"What would you like to do?",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

# ─── CHECK JOIN (button) ───────────────────────────────────────────────────────
async def check_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    db = load_db()
    user_id = query.from_user.id
    user = get_user(db, user_id)

    if await is_member_of_all(ctx, user_id):
        if not user["verified"]:
            user["verified"] = True
            save_db(db)
            await grant_referral_bonus(ctx, db, user_id)
        else:
            save_db(db)

        await query.edit_message_text(
            f"✅ *Verified!* Welcome aboard.\n\n"
            f"💰 Your Credits: *{user['credits']}*\n\n"
            f"What would you like to do?",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
    else:
        await query.answer("❌ You haven't joined both channels yet!", show_alert=True)

# ─── HOW IT WORKS ──────────────────────────────────────────────────────────────
async def how_it_works(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    db = load_db()
    group_count = len(db["groups"])

    await query.edit_message_text(
        "ℹ️ *How does JpxqAdvertise work?*\n\n"
        "1️⃣ *Buy Credits* – Choose a package and complete the payment\n"
        "2️⃣ *Send proof* – Send us a screenshot of your payment\n"
        "3️⃣ *Credits unlocked* – Our admin confirms and adds your credits\n"
        "4️⃣ *Create your ad* – Write your advertising message\n"
        "5️⃣ *Choose groups* – Select how many groups to target\n"
        "6️⃣ *Ad is sent* – Instantly delivered to all chosen groups!\n\n"
        f"📊 *Current Network:* {group_count} groups\n"
        f"💸 *Cost:* {CREDITS_PER_GROUP} Credits per group\n\n"
        f"📩 Support: {SUPPORT_USER}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="back_main")
        ]])
    )

# ─── MY ACCOUNT ────────────────────────────────────────────────────────────────
async def my_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    db = load_db()
    user = get_user(db, query.from_user.id)

    await query.edit_message_text(
        f"📊 *My Account*\n\n"
        f"👤 Name: {query.from_user.first_name}\n"
        f"🆔 ID: `{query.from_user.id}`\n"
        f"💰 Credits: *{user['credits']}*\n"
        f"📢 Ads sent: *{user['ads_sent']}*\n"
        f"👥 Referrals: *{len(user['referrals'])}*\n"
        f"📅 Member since: {user['joined'][:10]}\n\n"
        f"📈 With your credits you can target *{user['credits'] // CREDITS_PER_GROUP}* more groups!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Buy Credits", callback_data="buy_credits")],
            [InlineKeyboardButton("🎁 Referral Program", callback_data="referral")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
        ])
    )

# ─── REFERRAL PROGRAM ──────────────────────────────────────────────────────────
async def referral_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    db = load_db()
    user = get_user(db, query.from_user.id)
    bot_username = (await ctx.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{query.from_user.id}"

    await query.edit_message_text(
        f"🎁 *Referral Program*\n\n"
        f"Invite friends and earn *{REFERRAL_BONUS_CREDITS} Credits* for every person "
        f"who joins using your link!\n\n"
        f"🔗 *Your referral link:*\n`{ref_link}`\n\n"
        f"👥 Total referrals: *{len(user['referrals'])}*\n"
        f"💰 Earned: *{len(user['referrals']) * REFERRAL_BONUS_CREDITS} Credits*\n\n"
        f"Just share your link – credits are added automatically once your friend "
        f"joins the required channels!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={ref_link}&text=Join%20and%20earn%20credits!")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
        ])
    )

# ─── BUY CREDITS ───────────────────────────────────────────────────────────────
async def buy_credits(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "💳 *Buy Credits*\n\n"
        "Choose your package:\n\n"
        "💳 *Payment methods:*\n"
        "• PayPal\n"
        "• Crypto (LTC, BTC, ETH)\n\n"
        "After payment, send us your proof of payment and your credits will be unlocked!",
        parse_mode="Markdown",
        reply_markup=credits_keyboard()
    )

async def select_package(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    pkg_key = query.data.replace("buy_", "")
    pkg = CREDIT_PACKAGES.get(pkg_key)
    if not pkg:
        return

    ctx.user_data["pending_package"] = pkg_key

    await query.edit_message_text(
        f"✅ *Package selected: {pkg['label']}*\n\n"
        f"💰 Price: *{pkg['price']}*\n"
        f"🎁 Credits: *{pkg['credits']}*\n\n"
        f"📤 *Payment details:*\n"
        f"• PayPal: Message `@jeapppp`\n"
        f"• LTC: `LQLgC9FiL22wEV5AJbfHYKVhFdQGLV6yMa`\n"
        f"• BTC: `bc1qqygz4wls7rdy3fq3lnd6ewgx3c2nwun7d0s0ws`\n"
        f"• ETH: `0x942a69f83C38652C09E6E062003FA50E126C53DD`\n\n"
        f"Reference: `AD-{query.from_user.id}`\n\n"
        f"After payment → press *Send Payment Proof*!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📸 Send Payment Proof", callback_data="send_proof")],
            [InlineKeyboardButton("🔙 Back", callback_data="buy_credits")]
        ])
    )

async def send_proof_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if "pending_package" not in ctx.user_data:
        await query.edit_message_text("❌ Please select a package first!")
        return ConversationHandler.END

    await query.edit_message_text(
        "📸 *Send Payment Proof*\n\n"
        "Please send a *screenshot* or *photo* of your payment confirmation as your next message.\n\n"
        "⚠️ Make sure the following is visible:\n"
        "• Amount\n"
        "• Date\n"
        "• Your User ID as the reference",
        parse_mode="Markdown"
    )
    return WAITING_PAYMENT_PROOF

async def receive_payment_proof(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    pkg_key = ctx.user_data.get("pending_package", "basic")
    pkg = CREDIT_PACKAGES.get(pkg_key, CREDIT_PACKAGES["basic"])
    user_id = update.effective_user.id
    username = update.effective_user.username or "no username"

    # Save pending payment
    db["pending_payments"][str(user_id)] = {
        "package": pkg_key,
        "credits": pkg["credits"],
        "price": pkg["price"],
        "time": str(datetime.now()),
        "username": username
    }
    save_db(db)

    # Notify admins
    for admin_id in ADMIN_IDS:
        try:
            await ctx.bot.send_message(
                admin_id,
                f"💳 *New Payment Request!*\n\n"
                f"👤 User: @{username} (ID: `{user_id}`)\n"
                f"📦 Package: {pkg['label']}\n"
                f"💰 Amount: {pkg['price']}\n"
                f"🎁 Credits: {pkg['credits']}\n\n"
                f"To approve: /give_credits {user_id} {pkg['credits']}",
                parse_mode="Markdown"
            )
            # Proof content is NOT forwarded to admins for privacy
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

    await update.message.reply_text(
        "✅ *Payment proof received!*\n\n"
        "Our team is reviewing your payment and will unlock your credits shortly.\n"
        "⏱ Wait time: *5–30 minutes*\n\n"
        "You'll get a message as soon as everything is ready! 🎉\n"
        "If no one replies with in 30+ min message @jeapppp",
        parse_mode="Markdown"
    )
    ctx.user_data.pop("pending_package", None)
    return ConversationHandler.END

# ─── POST AD ───────────────────────────────────────────────────────────────────
async def post_ad_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    db = load_db()
    user = get_user(db, query.from_user.id)
    group_count = len(db["groups"])

    if user["credits"] < CREDITS_PER_GROUP:
        await query.edit_message_text(
            f"❌ *Not enough credits!*\n\n"
            f"You have *{user['credits']} Credits* but need at least *{CREDITS_PER_GROUP}* (for 1 group).\n\n"
            f"💳 Buy credits now!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Buy Credits", callback_data="buy_credits")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
            ])
        )
        return ConversationHandler.END

    if group_count == 0:
        await query.edit_message_text(
            f"❌ No groups in the network yet. Please contact support: {SUPPORT_USER}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]])
        )
        return ConversationHandler.END

    max_groups = min(group_count, user["credits"] // CREDITS_PER_GROUP)
    ctx.user_data["max_groups"] = max_groups
    ctx.user_data["available_groups"] = group_count

    await query.edit_message_text(
        f"📢 *Create Your Ad*\n\n"
        f"💰 Your Credits: *{user['credits']}*\n"
        f"📊 Available Groups: *{group_count}*\n"
        f"🎯 You can target up to *{max_groups} groups*\n\n"
        f"✏️ *Send your ad message as the next message!*\n\n"
        f"💡 Tips:\n"
        f"• Max. 4096 characters\n"
        f"• Emojis allowed ✅\n"
        f"• Links allowed ✅\n"
        f"• Images: Send photo with caption",
        parse_mode="Markdown"
    )
    return WAITING_AD_TEXT

async def receive_ad_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    user = get_user(db, update.effective_user.id)
    max_groups = ctx.user_data.get("max_groups", 1)

    ctx.user_data["ad_text"] = update.message.text or update.message.caption or ""
    ctx.user_data["ad_photo"] = update.message.photo[-1].file_id if update.message.photo else None

    options = []
    for i in [1, 3, 5, 10, 25, 50]:
        if i <= max_groups:
            cost = i * CREDITS_PER_GROUP
            options.append([InlineKeyboardButton(
                f"📢 {i} group{'s' if i > 1 else ''} – {cost} Credits",
                callback_data=f"ad_groups_{i}"
            )])
    if max_groups > 0:
        cost = max_groups * CREDITS_PER_GROUP
        options.append([InlineKeyboardButton(
            f"🔥 ALL {max_groups} groups – {cost} Credits",
            callback_data=f"ad_groups_{max_groups}"
        )])
    options.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    preview = ctx.user_data["ad_text"][:200] + ("..." if len(ctx.user_data["ad_text"]) > 200 else "")

    await update.message.reply_text(
        f"👀 *Ad Preview:*\n\n{preview}\n\n"
        f"📊 How many groups do you want to target?\n"
        f"💰 Cost: {CREDITS_PER_GROUP} Credits per group",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(options)
    )
    return WAITING_AD_CONFIRM

async def confirm_ad(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    num_groups = int(query.data.replace("ad_groups_", ""))
    total_cost = num_groups * CREDITS_PER_GROUP

    db = load_db()
    user = get_user(db, query.from_user.id)

    if user["credits"] < total_cost:
        await query.edit_message_text("❌ Not enough credits! Please buy more.")
        return ConversationHandler.END

    user["credits"] -= total_cost
    user["ads_sent"] += 1
    save_db(db)

    groups = list(db["groups"].keys())[:num_groups]
    ad_text = ctx.user_data.get("ad_text", "")
    ad_photo = ctx.user_data.get("ad_photo")

    sent = 0
    failed = 0
    for i, group_id in enumerate(groups):
        try:
            if ad_photo:
                await ctx.bot.send_photo(int(group_id), ad_photo, caption=ad_text)
            else:
                await ctx.bot.send_message(int(group_id), ad_text)
            sent += 1
        except Exception as e:
            logger.error(f"Failed to send to group {group_id}: {e}")
            failed += 1
        # Cooldown to avoid Telegram rate limits
        if i < len(groups) - 1:
            await asyncio.sleep(3.5)

    db["ads"].append({
        "user_id": query.from_user.id,
        "groups_targeted": num_groups,
        "groups_sent": sent,
        "credits_used": total_cost,
        "time": str(datetime.now()),
        "text_preview": ad_text[:100]
    })
    save_db(db)

    await query.edit_message_text(
        f"✅ *Ad sent!*\n\n"
        f"📢 Delivered: *{sent}/{num_groups} groups*\n"
        f"💰 Credits used: *{total_cost}*\n"
        f"💳 Credits remaining: *{user['credits']}*\n\n"
        f"{'⚠️ ' + str(failed) + ' groups failed.' if failed else ''}\n"
        f"Thanks for advertising with us! 🎉",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Post Another Ad", callback_data="post_ad")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="back_main")]
        ])
    )
    return ConversationHandler.END

# ─── ADMIN COMMANDS ────────────────────────────────────────────────────────────
async def admin_give_credits(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        target_id = int(ctx.args[0])
        amount = int(ctx.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /give_credits <user_id> <amount>")
        return

    db = load_db()
    user = get_user(db, target_id)
    user["credits"] += amount
    db["pending_payments"].pop(str(target_id), None)
    save_db(db)

    await update.message.reply_text(f"✅ Added {amount} credits to User {target_id}. New balance: {user['credits']}")

    try:
        await ctx.bot.send_message(
            target_id,
            f"🎉 *Credits received!*\n\n"
            f"✅ *+{amount} Credits* have been added to your account!\n"
            f"💰 New balance: *{user['credits']} Credits*\n\n"
            f"You can now post ads! /start",
            parse_mode="Markdown"
        )
    except:
        pass

async def admin_add_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        group_id = ctx.args[0]
        group_name = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else f"Group {group_id}"
    except IndexError:
        await update.message.reply_text(
            "Usage: /add_group <group_id> <name>\n\n"
            "Tip: Add the bot to the group, make it admin, then use /getid in the group to get the ID."
        )
        return

    db = load_db()
    db["groups"][group_id] = {"name": group_name, "added": str(datetime.now())}
    save_db(db)

    await update.message.reply_text(f"✅ Group '{group_name}' ({group_id}) added!\nTotal: {len(db['groups'])} groups")

async def admin_list_groups(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    db = load_db()
    if not db["groups"]:
        await update.message.reply_text("No groups in the network.")
        return

    text = "📊 *Groups in Network:*\n\n"
    for gid, info in db["groups"].items():
        text += f"• {info['name']} (`{gid}`)\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_remove_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        group_id = ctx.args[0]
    except IndexError:
        await update.message.reply_text("Usage: /remove_group <group_id>")
        return

    db = load_db()
    if group_id in db["groups"]:
        name = db["groups"][group_id]["name"]
        del db["groups"][group_id]
        save_db(db)
        await update.message.reply_text(f"✅ Group '{name}' removed. {len(db['groups'])} groups remaining.")
    else:
        await update.message.reply_text("❌ Group not found.")

async def admin_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    db = load_db()
    total_users = len(db["users"])
    total_groups = len(db["groups"])
    total_ads = len(db["ads"])
    total_credits_sold = sum(ad.get("credits_used", 0) for ad in db["ads"])
    pending = len(db["pending_payments"])

    await update.message.reply_text(
        f"📊 *Bot Statistics*\n\n"
        f"👥 Users: *{total_users}*\n"
        f"📢 Groups: *{total_groups}*\n"
        f"🎯 Ads sent: *{total_ads}*\n"
        f"💰 Credits used: *{total_credits_sold}*\n"
        f"⏳ Pending payments: *{pending}*\n\n"
        f"Admin commands:\n"
        f"/give_credits <id> <amount>\n"
        f"/add_group <id> <name>\n"
        f"/remove_group <id>\n"
        f"/list_groups\n"
        f"/stats",
        parse_mode="Markdown"
    )

async def get_group_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(
        f"🆔 Chat ID: `{chat.id}`\nName: {chat.title or chat.first_name}",
        parse_mode="Markdown"
    )

# ─── NAVIGATION ────────────────────────────────────────────────────────────────
async def back_main(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    db = load_db()
    user = get_user(db, query.from_user.id)
    await query.edit_message_text(
        f"🏠 *Main Menu*\n\n💰 Credits: *{user['credits']}*",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ Cancelled.")
    return ConversationHandler.END

# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    buy_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(send_proof_prompt, pattern="^send_proof$")],
        states={
            WAITING_PAYMENT_PROOF: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_payment_proof)]
        },
        fallbacks=[CallbackQueryHandler(cancel, pattern="^cancel$")],
        per_message=False,
        per_chat=True,
    )

    ad_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(post_ad_start, pattern="^post_ad$")],
        states={
            WAITING_AD_TEXT: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_ad_text)],
            WAITING_AD_CONFIRM: [CallbackQueryHandler(confirm_ad, pattern="^ad_groups_")]
        },
        fallbacks=[CallbackQueryHandler(cancel, pattern="^cancel$")],
        per_message=False,
        per_chat=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("getid", get_group_id))
    app.add_handler(CommandHandler("give_credits", admin_give_credits))
    app.add_handler(CommandHandler("add_group", admin_add_group))
    app.add_handler(CommandHandler("remove_group", admin_remove_group))
    app.add_handler(CommandHandler("list_groups", admin_list_groups))
    app.add_handler(CommandHandler("stats", admin_stats))

    app.add_handler(buy_conv)
    app.add_handler(ad_conv)

    app.add_handler(CallbackQueryHandler(buy_credits, pattern="^buy_credits$"))
    app.add_handler(CallbackQueryHandler(select_package, pattern="^buy_(starter|basic|pro|ultra)$"))
    app.add_handler(CallbackQueryHandler(my_account, pattern="^my_account$"))
    app.add_handler(CallbackQueryHandler(how_it_works, pattern="^how_it_works$"))
    app.add_handler(CallbackQueryHandler(referral_menu, pattern="^referral$"))
    app.add_handler(CallbackQueryHandler(check_join, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(back_main, pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(cancel, pattern="^cancel$"))

    logger.info("🤖 JpxqAdvertise started!")
    app.run_polling()

if __name__ == "__main__":
    main()
