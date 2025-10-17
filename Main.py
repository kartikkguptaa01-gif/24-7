import asyncio
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
import aiohttp
import re
import os

# ---------------- Config ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "7510786889:AAHVZ1O6RHqNQaXPVO7OWTC8F9rqTh3aunE")
OWNER_ID = int(os.getenv("OWNER_ID", "5390129810"))
OSIENT_API_URL = os.getenv("OSIENT_API_URL", "https://osient.vercel.app/v1/mobile")
FANTOM_API_BASE = os.getenv("FANTOM_API_BASE", "https://fantomdeluxe.vercel.app/api")
FANTOM_API_KEY = os.getenv("FANTOM_API_KEY", "ALILUBABA")  # place your key here or in env

# ---------------- State ----------------
users = {}

# ---------------- Logging ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ---------------- Utility ----------------
def get_user(user_id, username=None):
    """Get or create user data"""
    if user_id not in users:
        users[user_id] = {
            'balance': 0 if user_id != OWNER_ID else 999999,  # Owner unlimited
            'banned': False,
            'searches': 0,
            'username': username or f"User_{user_id}"
        }
    else:
        # update username if provided and changed
        if username:
            users[user_id]['username'] = username
    return users[user_id]

def is_owner(user_id):
    return user_id == OWNER_ID

def clean_number(text):
    """Return only digits. Keep leading zeros if present."""
    if not text:
        return ""
    number = re.sub(r'[^\d]', '', text)
    # remove any leading country codes like +91 or 91 for mobile searches
    # but keep if it's an Aadhaar length (12) or mobile (10)
    return number

def is_mobile(number):
    return len(number) == 10 and number.isdigit()

def is_aadhaar(number):
    return len(number) == 12 and number.isdigit()

# ---------------- API Calls ----------------
async def fetch_osient(phone):
    """Call the osient mobile API (returns parsed JSON or None)"""
    try:
        url = f"{OSIENT_API_URL}?num={phone}"
        logger.info(f"OSIENT -> {url}")
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                text = await resp.text()
                logger.info(f"OSIENT status={resp.status} len={len(text)}")
                if resp.status == 200:
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        logger.error("OSIENT: JSON decode error")
                        return None
                else:
                    logger.error(f"OSIENT error {resp.status}: {text[:200]}")
                    return None
    except Exception as e:
        logger.exception(f"OSIENT fetch failed: {e}")
        return None

async def fetch_fantom_id(term, key=FANTOM_API_KEY, id_type="id_number"):
    """Call the FantomDeluxe id lookup endpoint. Returns parsed JSON or None."""
    try:
        params = {
            "key": key,
            "type": id_type,
            "term": term
        }
        # Build URL (simple)
        url = f"{FANTOM_API_BASE}?key={params['key']}&type={params['type']}&term={params['term']}"
        logger.info(f"FANTOM -> {url}")
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                text = await resp.text()
                logger.info(f"FANTOM status={resp.status} len={len(text)}")
                if resp.status == 200:
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        logger.error("FANTOM: JSON decode error")
                        return None
                else:
                    logger.error(f"FANTOM error {resp.status}: {text[:200]}")
                    return None
    except Exception as e:
        logger.exception(f"FANTOM fetch failed: {e}")
        return None

# ---------------- Parsing helpers ----------------
def extract_osint_fields(data):
    """
    Normalize and extract several common fields from the osient response.
    Returns a list of dict records (could be empty).
    """
    results = []
    if not data:
        return results

    # If wrapper
    if isinstance(data, dict) and 'data' in data:
        payload = data['data']
    else:
        payload = data

    # If a single dict record -> wrap into list for uniform handling
    if isinstance(payload, dict):
        payload = [payload]

    if isinstance(payload, list):
        for record in payload:
            if not isinstance(record, dict):
                continue
            osint_record = {
                'Name': record.get('Name') or record.get('name') or "",
                'Father Name': record.get('Father Name') or record.get('father_name') or "",
                'Address': record.get('Address') or record.get('address') or "",
                'Circle': record.get('Circle') or record.get('circle') or "",
                'Aadhar Number': record.get('Aadhar Number') or record.get('aadhar_number') or record.get('adhar') or record.get('aadhaar') or "",
                'Email': record.get('Email') or record.get('email') or "",
                'Alternate Mobile': record.get('Alternate Mobile') or record.get('alternate_mobile') or record.get('alt_mobile') or ""
            }
            # Add only if any non-empty meaningful field
            if any(str(v).strip() and str(v).lower() not in ['n/a', 'null'] for v in osint_record.values()):
                results.append(osint_record)
    return results

def extract_fantom_aadhaar(data):
    """
    Extract Aadhaar-like info from FantomDeluxe response (structure unknown).
    We'll try common keys and fallback to raw.
    """
    if not data:
        return None

    # If the response contains a field called "result" or "data" or "records" use it
    candidate = None
    if isinstance(data, dict):
        for key in ("result", "data", "records", "response"):
            if key in data:
                candidate = data[key]
                break
        if candidate is None:
            candidate = data  # use whole object

    # Try to find id-like fields
    aadha = {}
    if isinstance(candidate, dict):
        aadha['Aadhaar'] = candidate.get('aadhaar') or candidate.get('Aadhar') or candidate.get('id') or candidate.get('id_number') or ""
        aadha['Name'] = candidate.get('name') or candidate.get('fullname') or ""
        aadha['Address'] = candidate.get('address') or ""
        return aadha if any(aadha.values()) else None
    # if list, try first element
    if isinstance(candidate, list) and candidate:
        first = candidate[0]
        if isinstance(first, dict):
            return {
                'Aadhaar': first.get('aadhaar') or first.get('id_number') or "",
                'Name': first.get('name') or "",
                'Address': first.get('address') or ""
            }
    # fallback: return None
    return None

def build_result_message(records, aadhaar_info, queried_number, user_data, username):
    """Create a nice markdown message summarizing results + metadata"""
    time_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    header = "🌐 *Mobile / ID Lookup Result*\n"
    header += f"🕗 _{time_now}_\n\n"
    if not records and not aadhaar_info:
        body = "❌ *No information found.*\nTry another number or contact admin.\n\n"
    else:
        body = ""
        if records:
            for i, rec in enumerate(records, 1):
                body += f"🔸 *Record {i}*\n"
                body += f"• *Name:* `{rec.get('Name') or '—'}`\n"
                body += f"• *Father:* `{rec.get('Father Name') or '—'}`\n"
                body += f"• *Address:* `{rec.get('Address') or '—'}`\n"
                body += f"• *Circle:* `{rec.get('Circle') or '—'}`\n"
                body += f"• *Aadhar:* `{rec.get('Aadhar Number') or '—'}`\n"
                body += f"• *Email:* `{rec.get('Email') or '—'}`\n"
                body += f"• *Alt Mobile:* `{rec.get('Alternate Mobile') or '—'}`\n"
                body += "────────────────────────\n"
        if aadhaar_info:
            body += "🆔 *Aadhaar / ID Lookup*\n"
            body += f"• *Aadhaar:* `{aadhaar_info.get('Aadhaar') or '—'}`\n"
            body += f"• *Name:* `{aadhaar_info.get('Name') or '—'}`\n"
            body += f"• *Address:* `{aadhaar_info.get('Address') or '—'}`\n"
            body += "────────────────────────\n"

    footer = f"🔎 *Queried:* `{queried_number}`\n"
    footer += f"👤 *Requested by:* @{username}\n"
    footer += f"💳 *Credits left:* {'Unlimited' if is_owner(user_data['username']) else user_data['balance']}\n"
    footer += f"📊 *Total searches by user:* {user_data['searches']}\n"

    return header + body + "\n" + footer

# ---------------- Bot Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = get_user(user.id, user.username or user.first_name)

    if user_data['banned']:
        await update.message.reply_text("🚫 *You are banned.* Contact admin.", parse_mode='Markdown')
        return

    balance = "Unlimited" if is_owner(user.id) else f"{user_data['balance']}"

    msg = (
        f"🌟 *Mobile & ID OSINT Bot* 🌟\n\n"
        f"👤 *User:* @{user.username or user.first_name}\n"
        f"🆔 *ID:* `{user.id}`\n"
        f"💰 *Balance:* `{balance}` credits\n\n"
        f"📱 *Search examples:*\n"
        f"• `/search 9876543210` — mobile lookup\n"
        f"• `/search 123412341234` — 12-digit Aadhaar lookup\n\n"
        f"⚠️ *Cost:* 1 credit per search (owner free)\n"
    )

    keyboard = [
        [InlineKeyboardButton("🔍 New Search", callback_data="search")],
        [InlineKeyboardButton("📞 Contact Admin", callback_data="contact"),
         InlineKeyboardButton("📈 My Stats", callback_data="stats")]
    ]
    if is_owner(user.id):
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin")])

    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Alias to handle /search"""
    # reuse the same logic as direct message handler
    await handle_search_request(update, context)

async def handle_search_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main search flow (used by /search and direct message)"""
    # Determine who invoked
    user = update.effective_user
    from_text = None
    if context.args:
        from_text = " ".join(context.args)
    elif update.message and update.message.text:
        # for /search 987... or direct number
        from_text = update.message.text.replace('/search', '').strip()

    if not from_text:
        await update.message.reply_text("📌 Send a 10-digit mobile number or 12-digit Aadhaar number.\nEx: `9876543210`", parse_mode='Markdown')
        return

    number = clean_number(from_text)
    if not (is_mobile(number) or is_aadhaar(number)):
        await update.message.reply_text("❌ Send a valid *10-digit mobile* or *12-digit Aadhaar* number.", parse_mode='Markdown')
        return

    user_data = get_user(user.id, user.username or user.first_name)
    if user_data['banned']:
        await update.message.reply_text("🚫 You are banned.", parse_mode='Markdown')
        return

    # Check balance (owner bypass)
    if not is_owner(user.id) and user_data['balance'] <= 0:
        await update.message.reply_text("💳 You have no credits left. Ask admin to `/add YOUR_ID 10`", parse_mode='Markdown')
        return

    # Deduct
    if not is_owner(user.id):
        user_data['balance'] -= 1
    user_data['searches'] += 1

    loading_msg = await update.message.reply_text("🔎 Searching... please wait", parse_mode='Markdown')

    # Call APIs concurrently where appropriate
    osient_data = None
    fantom_data = None
    try:
        tasks = []
        # If mobile -> call osient by phone, also try fantom with term=phone
        if is_mobile(number):
            tasks.append(fetch_osient(number))
            tasks.append(fetch_fantom_id(number))
        else:
            # Aadhaar -> call fantom primarily, and optionally osient (rare)
            tasks.append(fetch_fantom_id(number))
            tasks.append(fetch_osient(number))  # won't hurt; many APIs will return nothing

        res = await asyncio.gather(*tasks, return_exceptions=True)
        # Map results by type
        # If mobile: res[0]=osient, res[1]=fantom
        if is_mobile(number):
            osient_data = res[0] if not isinstance(res[0], Exception) else None
            fantom_data = res[1] if not isinstance(res[1], Exception) else None
        else:
            fantom_data = res[0] if not isinstance(res[0], Exception) else None
            osient_data = res[1] if len(res) > 1 and not isinstance(res[1], Exception) else None

    except Exception as e:
        logger.exception(f"Error during API calls: {e}")

    records = extract_osint_fields(osient_data)
    aadhaar_info = extract_fantom_aadhaar(fantom_data)

    # If osient returned an 'Aadhar Number' but fantom didn't, create aadhaar_info
    if not aadhaar_info:
        for rec in records:
            a = rec.get('Aadhar Number') or rec.get('aadhar') or rec.get('Aadhar')
            if a and len(str(a)) >= 8:
                aadhaar_info = {'Aadhaar': str(a), 'Name': rec.get('Name') or "", 'Address': rec.get('Address') or ""}
                break

    # Build message
    msg_text = build_result_message(records, aadhaar_info, number, user_data, user.username or user.first_name)

    # Keyboard
    kb = [
        [InlineKeyboardButton("🔍 New Search", callback_data="search"), InlineKeyboardButton("📊 My Stats", callback_data="stats")],
        [InlineKeyboardButton("📞 Contact Admin", callback_data="contact")]
    ]
    if is_owner(user.id):
        kb[0].append(InlineKeyboardButton("👑 Admin Panel", callback_data="admin"))

    # Edit the loading message with results
    try:
        await loading_msg.edit_text(msg_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        # Fallback: send new message
        await update.message.reply_text(msg_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

# ---------------- Admin / Utility Commands ----------------
async def add_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/add USER_ID CREDITS`\nEx: `/add 123456 50`", parse_mode='Markdown')
        return
    try:
        uid, amount = int(context.args[0]), int(context.args[1])
        # ensure user exists
        get_user(uid)
        users[uid]['balance'] += amount
        await update.message.reply_text(f"✅ Added {amount} credits to `{uid}`", parse_mode='Markdown')
    except Exception as e:
        logger.exception(e)
        await update.message.reply_text("❌ Error. Usage: `/add USER_ID CREDITS`", parse_mode='Markdown')

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/ban USER_ID`", parse_mode='Markdown')
        return
    try:
        uid = int(context.args[0])
        get_user(uid)  # ensure exists
        users[uid]['banned'] = True
        await update.message.reply_text(f"🚫 Banned `{uid}`", parse_mode='Markdown')
    except Exception as e:
        logger.exception(e)
        await update.message.reply_text("❌ Error. Usage: `/ban USER_ID`", parse_mode='Markdown')

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/unban USER_ID`", parse_mode='Markdown')
        return
    try:
        uid = int(context.args[0])
        get_user(uid)
        users[uid]['banned'] = False
        await update.message.reply_text(f"✅ Unbanned `{uid}`", parse_mode='Markdown')
    except Exception as e:
        logger.exception(e)
        await update.message.reply_text("❌ Error. Usage: `/unban USER_ID`", parse_mode='Markdown')

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = get_user(user.id, user.username or user.first_name)
    bal = "Unlimited" if is_owner(user.id) else user_data['balance']
    await update.message.reply_text(f"💳 *Balance:* `{bal}`", parse_mode='Markdown')

# ---------------- Callback Query Handler ----------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data

    if data == "search":
        await query.edit_message_text("📱 Send `/search 9876543210` or just `9876543210`", parse_mode='Markdown')
    elif data == "contact":
        await query.edit_message_text("📞 *Contact Admin*\nAsk owner to add credits: `/add YOUR_ID 10`", parse_mode='Markdown')
    elif data == "stats":
        ud = get_user(user.id, user.username or user.first_name)
        text = f"📊 *Your Stats*\n• Searches: `{ud['searches']}`\n• Balance: `{'Unlimited' if is_owner(user.id) else ud['balance']}`"
        await query.edit_message_text(text, parse_mode='Markdown')
    elif data == "admin" and is_owner(user.id):
        text = (
            "👑 *Admin Panel*\n\n"
            "`/add USER_ID AMOUNT` — add credits\n"
            "`/ban USER_ID` — ban user\n"
            "`/unban USER_ID` — unban user\n"
            "`/balance` — check your balance\n"
        )
        await query.edit_message_text(text, parse_mode='Markdown')

# ---------------- Message Handler (direct numbers) ----------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    number = clean_number(text)
    # Accept 10-digit mobile or 12-digit Aadhaar
    if is_mobile(number) or is_aadhaar(number):
        # Use context.args so search flow can pick it up
        context.args = [number]
        await handle_search_request(update, context)
    else:
        await update.message.reply_text("❓ Send a *10-digit mobile* or *12-digit Aadhaar* number.\nEx: `9876543210`", parse_mode='Markdown')

# ---------------- Run ----------------
def main():
    print("🚀 Starting Enhanced OSINT Mobile Bot...")
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("add", add_credits))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))

    # Callbacks & messages
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot Running (polling)...")
    app.run_polling()

if __name__ == "__main__":
    main() 
