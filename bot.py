import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonWebApp,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from dotenv import load_dotenv

load_dotenv()

# ----------------------------------------------------------------- sozlamalar
BOT_TOKEN = "8917355987:AAEM0fBNmCpfDzQ_4Z0HJtmllHR6AH5AVXo"
ADMIN_IDS = {1830462480}
SUPPORT_USERNAME = "Uzbetttttt"   #@ Uzbetttttt
PROMO_CODE = "SKAROL"  # sizning promokodingiz (Mini App da ko'rsatiladi)

DB_PATH = os.getenv("DB_PATH", "cashboost.db")
CURRENCY = os.getenv("CURRENCY", "сум")
MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", "10000"))
REF_BONUS = int(os.getenv("REF_BONUS", "1000"))
TZ = timezone(timedelta(hours=5))   #Toshkent

# Mini App manzili. Telegram faqat HTTPS qabul qiladi (README dagi "tunnel" bo'limiga qarang).
 #.env ga WEBAPP_URL=https://t.me/hisobot_1c_test_bot
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://cashboost-3x.vercel.app").strip().rstrip("/")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080"))
WEBAPP_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp.html")

# =============================================================================
#  ILOVALAR RO'YXATI (Mini App → "Ilovalar" bo'limi va "Получить кэшбек" tugmasi)
#
#  Yangi ilova qo'shish: shu ro'yxatga bitta {...} blok qo'shing, botni qayta ishga tushiring.
#  O'chirish: blokni o'chiring. Tartib shu ro'yxatdagi tartibda bo'ladi.
#
#    name     - ilova nomi
#    percent = shu ilovadagi kэshbek foizi (HOZIRGILARI NA'MUNA, o'zingiznikiga almashtiring)
#    desc     - qisqa tavsif (Mini App da ko'rinadi)
#    url      - ilovaga havola (bo'sh bo'lsa, "Ilovani ochish" tugmasi chiqmaydi)
#    logo     - logotip rasmi havolasi (https://...). Bo'sh bo'lsa, rangli harf belgisi chiqadi
#    color    - harf belgisi rangi (logo bo'lmaganda)
#    promo    - (ixtiyoriy) shu ilova uchun alohida promokod, bo'lmasa PROMO_CODE olinadi
# =============================================================================
APPS = [
    {"name": "Uzum Market", "percent": 5, "desc": "Onlayn do'kon", "url": "", "logo": "", "color": "#7C3AED"},
    {"name": "Yandex Eda", "percent": 4, "desc": "Taom yetkazib berish", "url": "", "logo": "", "color": "#D97706"},
    {"name": "Express24", "percent": 4, "desc": "Mahsulot yetkazib berish", "url": "", "logo": "", "color": "#DC2626"},
    {"name": "Yandex Go", "percent": 3, "desc": "Taksi", "url": "", "logo": "", "color": "#CA8A04"},
    {"name": "Olcha", "percent": 4, "desc": "Elektronika va maishiy texnika", "url": "", "logo": "", "color": "#2563EB"},
]

# Darajalar: (nomi, shu darajaga kerakli jami xaridlar summasi, qo'shimcha kэshbek foizi)
TIERS = [
    ("Bronze", 0, 0),
    ("Silver", 3_000_000, 1),
    ("Gold", 10_000_000, 2),
    ("Platinum", 30_000_000, 3),
]

# ------------------------------------------------------------------- tugmalar
BTN_HOME = "🏠 Главная"
BTN_CASHBACK = "💰 Получить кэшбек"
BTN_WITHDRAW = "💸 Вывод средств"
BTN_HISTORY = "📋 История"
BTN_NEWS = "📰 Новости"
BTN_PROFILE = "👤 Профиль"
BTN_SUPPORT = "🆘 Поддержка"


def main_kb() -> ReplyKeyboardMarkup:
    rows = [
        [BTN_HOME, BTN_CASHBACK],
        [BTN_WITHDRAW, BTN_HISTORY],
        [BTN_NEWS, BTN_PROFILE],
        [BTN_SUPPORT],
    ]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in r] for r in rows],
        resize_keyboard=True,
        input_field_placeholder="Нажмите одну из кнопок...",
    )


# ----------------------------------------------------------------------- baza
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row


def init_db() -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance INTEGER NOT NULL DEFAULT 0,
            total_earned INTEGER NOT NULL DEFAULT 0,
            referrer_id INTEGER,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS claims(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            shop TEXT,
            amount INTEGER NOT NULL,
            proof_file_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            cashback INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS withdrawals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            requisites TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            amount INTEGER NOT NULL,
            note TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS news(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        """
    )
    # Eski bazalar uchun yangi ustunlar (bor bo'lsa, o'tkazib yuboriladi)
    for ddl in (
        "ALTER TABLE claims ADD COLUMN player_id TEXT",
        "ALTER TABLE users ADD COLUMN turnover INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.commit()


def now() -> int:
    return int(time.time())


def fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ") + f" {CURRENCY}"


def fdate(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y %H:%M")


def esc(s) -> str:
    return html.escape(str(s or ""))


def parse_amount(text: str | None) -> int | None:
    """'250 000' -> 250000. Noto'g'ri bo'lsa None."""
    raw = (text or "").replace(" ", "")
    if re.fullmatch(r"[0-9]{1,13}", raw) and int(raw) > 0:
        return int(raw)
    return None


def register(tg_user, referrer_id: int | None = None) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM users WHERE id=?", (tg_user.id,)).fetchone()
    if row is None:
        if referrer_id == tg_user.id or not conn.execute(
            "SELECT 1 FROM users WHERE id=?", (referrer_id,)
        ).fetchone():
            referrer_id = None
        conn.execute(
            "INSERT INTO users(id, username, full_name, referrer_id, created_at) VALUES(?,?,?,?,?)",
            (tg_user.id, tg_user.username, tg_user.full_name, referrer_id, now()),
        )
    else:
        conn.execute(
            "UPDATE users SET username=?, full_name=? WHERE id=?",
            (tg_user.username, tg_user.full_name, tg_user.id),
        )
    conn.commit()
    return conn.execute("SELECT * FROM users WHERE id=?", (tg_user.id,)).fetchone()


EARN_KINDS = {"cashback", "referral"}
KIND_LABELS = {
    "cashback": "Кэшбек",
    "referral": "Бонус за друга",
    "withdraw_hold": "Вывод (в обработке)",
    "withdraw_refund": "Возврат по выводу",
}


def add_tx(uid: int, amount: int, kind: str, note: str = "") -> None:
    earned = amount if kind in EARN_KINDS else 0
    conn.execute(
        "UPDATE users SET balance=balance+?, total_earned=total_earned+? WHERE id=?",
        (amount, earned, uid),
    )
    conn.execute(
        "INSERT INTO transactions(user_id, kind, amount, note, created_at) VALUES(?,?,?,?,?)",
        (uid, kind, amount, note, now()),
    )
    conn.commit()


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def notify_admins(bot: Bot, text: str, kb: InlineKeyboardMarkup, photo: str | None = None):
    for admin_id in ADMIN_IDS:
        try:
            if photo:
                await bot.send_photo(admin_id, photo, caption=text, reply_markup=kb)
            else:
                await bot.send_message(admin_id, text, reply_markup=kb)
        except Exception as e:  # admin botni hali ishga tushirmagan bo'lishi mumkin
            logging.warning("Admin %s ga yuborib bo'lmadi: %s", admin_id, e)


# ------------------------------------------------- darajalar, ilovalar, kэшbek
def find_app(name: str | None) -> dict | None:
    return next((a for a in APPS if a["name"] == name), None)


def max_percent() -> float:
    return max((float(a["percent"]) for a in APPS), default=0.0)


def tier_info(turnover: int) -> dict:
    """Jami xaridlar summasiga qarab daraja, keyingi darajagacha progress (0..100)."""
    idx = 0
    for i, (_, need, _) in enumerate(TIERS):
        if turnover >= need:
            idx = i
    name, need, bonus = TIERS[idx]
    nxt = TIERS[idx + 1] if idx + 1 < len(TIERS) else None
    if nxt:
        span = nxt[1] - need
        progress = min(100, int((turnover - need) * 100 / span)) if span > 0 else 100
    else:
        progress = 100
    return {
        "name": name,
        "bonus": bonus,
        "next_name": nxt[0] if nxt else None,
        "next_need": nxt[1] if nxt else None,
        "progress": progress,
    }


def credit_claim(claim_id: int, purchase: int) -> dict | None:
    """Admin xarid summasini kiritgach: kэshbekni hisoblaydi, aylanma va darajani yangilaydi.
    Kэshbek foizi = ilova foizi + joriy daraja bonusi."""
    c = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    if not c or c["status"] != "pending":
        return None
    u = conn.execute("SELECT turnover, referrer_id FROM users WHERE id=?", (c["user_id"],)).fetchone()
    old_tier = tier_info(u["turnover"])
    app = find_app(c["shop"])
    percent = (float(app["percent"]) if app else 0.0) + old_tier["bonus"]
    cashback = round(purchase * percent / 100)
    conn.execute(
        "UPDATE claims SET status='approved', amount=?, cashback=? WHERE id=?",
        (purchase, cashback, claim_id),
    )
    conn.execute("UPDATE users SET turnover=turnover+? WHERE id=?", (purchase, c["user_id"]))
    conn.commit()
    if cashback > 0:
        add_tx(c["user_id"], cashback, "cashback", f"{c['shop']} · заявка №{claim_id}")
    new_tier = tier_info(u["turnover"] + purchase)

    bonus_to = None
    approved = conn.execute(
        "SELECT COUNT(*) FROM claims WHERE user_id=? AND status='approved'", (c["user_id"],)
    ).fetchone()[0]
    if approved == 1 and u["referrer_id"] and REF_BONUS > 0:
        add_tx(u["referrer_id"], REF_BONUS, "referral", f"Друг {c['user_id']}")
        bonus_to = u["referrer_id"]
    return {
        "claim": c,
        "cashback": cashback,
        "percent": percent,
        "old_tier": old_tier["name"],
        "new_tier": new_tier["name"],
        "bonus_to": bonus_to,
    }


# ------------------------------------------------------------------ holatlar
class Claim(StatesGroup):
    player_id = State()


class AdminCredit(StatesGroup):
    amount = State()


class Withdraw(StatesGroup):
    amount = State()
    requisites = State()


router = Router()

# ------------------------------------------------------------ asosiy tugmalar


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    arg = (command.args or "").strip()
    ref = int(arg) if arg.isascii() and arg.isdigit() else None
    register(message.from_user, ref)
    # Mini App dagi tugmalar botga shu havolalar orqali o'tadi
    if arg == "withdraw":
        return await withdraw_start(message, state)
    if arg == "cashback":
        return await cashback_start(message, state)
    await message.answer(
        f"Привет, {esc(message.from_user.first_name)}! 👋\n\n"
        "<b>Cash Boost 💰</b> — получайте кэшбек за покупки в приложениях-партнёрах "
        f"(до <b>{max_percent():g}%</b>) и выводите его.\n\n"
        "Откройте приложение кнопкой <b>CashBoost</b> слева от поля ввода или выберите действие в меню ниже.",
        reply_markup=main_kb(),
    )


@router.message(Command("app"))
async def open_app(message: Message):
    if not WEBAPP_URL.startswith("https://"):
        return await message.answer("Приложение пока не настроено.")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть CashBoost", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )
    await message.answer("Откройте CashBoost:", reply_markup=kb)


@router.message(F.text == BTN_HOME)
async def home(message: Message, state: FSMContext):
    await state.clear()
    u = register(message.from_user)
    tier = tier_info(u["turnover"])
    await message.answer(
        "🏠 <b>Главная</b>\n\n"
        f"💳 Ваш баланс: <b>{fmt(u['balance'])}</b>\n"
        f"🏅 Уровень: <b>{tier['name']}</b>\n"
        f"📈 Кэшбек: до <b>{max_percent():g}%</b> с покупок в приложениях-партнёрах\n"
        f"💸 Минимальный вывод: <b>{fmt(MIN_WITHDRAW)}</b>\n\n"
        "Нажмите «💰 Получить кэшбек», чтобы отправить заявку на проверку.",
        reply_markup=main_kb(),
    )


@router.message(F.text == BTN_PROFILE)
async def profile(message: Message, state: FSMContext):
    await state.clear()
    u = register(message.from_user)
    refs = conn.execute("SELECT COUNT(*) c FROM users WHERE referrer_id=?", (u["id"],)).fetchone()["c"]
    me = await message.bot.me()
    link = f"https://t.me/{me.username}?start={u['id']}"
    tier = tier_info(u["turnover"])
    bonus_line = f"\n🎁 За каждого друга с первым кэшбеком: <b>{fmt(REF_BONUS)}</b>" if REF_BONUS > 0 else ""
    await message.answer(
        "👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{u['id']}</code>\n"
        f"🏅 Уровень: <b>{tier['name']}</b>\n"
        f"💳 Баланс: <b>{fmt(u['balance'])}</b>\n"
        f"📈 Всего заработано: <b>{fmt(u['total_earned'])}</b>\n"
        f"🛒 Сумма покупок: <b>{fmt(u['turnover'])}</b>\n"
        f"👥 Приглашено друзей: <b>{refs}</b>\n"
        f"📅 Регистрация: {fdate(u['created_at'])}\n\n"
        f"🔗 Ваша ссылка:\n<code>{link}</code>{bonus_line}"
    )


@router.message(F.text == BTN_HISTORY)
async def history(message: Message, state: FSMContext):
    await state.clear()
    register(message.from_user)
    rows = conn.execute(
        "SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 15",
        (message.from_user.id,),
    ).fetchall()
    if not rows:
        return await message.answer("📋 История пока пуста.")
    lines = []
    for r in rows:
        sign = "+" if r["amount"] > 0 else "−"
        lines.append(
            f"{fdate(r['created_at'])} · {KIND_LABELS.get(r['kind'], r['kind'])}: "
            f"<b>{sign}{fmt(abs(r['amount']))}</b>"
        )
    await message.answer("📋 <b>Последние операции</b>\n\n" + "\n".join(lines))


@router.message(F.text == BTN_NEWS)
async def news(message: Message, state: FSMContext):
    await state.clear()
    register(message.from_user)
    rows = conn.execute("SELECT * FROM news ORDER BY id DESC LIMIT 5").fetchall()
    if not rows:
        return await message.answer("📰 Новостей пока нет.")
    await message.answer(
        "📰 <b>Новости</b>\n\n" + "\n\n".join(f"<i>{fdate(r['created_at'])}</i>\n{r['text']}" for r in rows)
    )


@router.message(F.text == BTN_SUPPORT)
async def support(message: Message, state: FSMContext):
    await state.clear()
    register(message.from_user)
    if not SUPPORT_USERNAME:
        return await message.answer("🆘 Поддержка пока не настроена.")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✉️ Написать в поддержку", url=f"https://t.me/{SUPPORT_USERNAME}")]]
    )
    await message.answer("🆘 <b>Поддержка</b>\n\nЕсть вопрос или проблема? Напишите нам:", reply_markup=kb)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_kb())


# --------------------------------------------------------------------- кэшбек

ID_RE = re.compile(r"^[A-Za-z0-9_+#.\-]{3,32}$")


def platforms_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=a["name"], callback_data=f"plat:{i}")] for i, a in enumerate(APPS)
        ]
    )


@router.message(F.text == BTN_CASHBACK)
async def cashback_start(message: Message, state: FSMContext):
    register(message.from_user)
    await state.clear()
    if not APPS:
        return await message.answer("Приложения пока не настроены.")
    await message.answer(
        "💰 <b>Получить кэшбек</b>\n\n"
        f"Кэшбек начисляется за покупки после регистрации по промокоду <code>{esc(PROMO_CODE)}</code>.\n\n"
        "Выберите приложение, в котором вы сделали покупку:",
        reply_markup=platforms_kb(),
    )


@router.callback_query(F.data.startswith("plat:"))
async def platform_chosen(cb: CallbackQuery, state: FSMContext):
    idx = cb.data.split(":")[1]
    if not idx.isascii() or not idx.isdigit() or int(idx) >= len(APPS):
        return await cb.answer("Приложение не найдено", show_alert=True)
    app_name = APPS[int(idx)]["name"]
    register(cb.from_user)
    await state.clear()
    await state.update_data(platform=app_name)
    await state.set_state(Claim.player_id)
    await cb.message.answer(
        "1/3 — Введите ваш ID на платформе\n\n"
        f"🛒 Приложение: <b>{esc(app_name)}</b>\n"
        "Укажите номер заказа или ваш ID в приложении, без пробелов.\n"
        "Отмена: /cancel"
    )
    await cb.answer()


@router.message(Claim.player_id, F.text)
async def claim_player_id(message: Message, state: FSMContext):
    player_id = message.text.strip()
    if player_id.startswith("/"):
        return await message.answer("Введите ID текстом или нажмите /cancel для отмены.")
    if not ID_RE.match(player_id):
        return await message.answer(
            "ID должен состоять из 3–32 символов: цифры, латинские буквы или знаки + - _ # . "
            "без пробелов. Попробуйте ещё раз:"
        )
    platform = (await state.get_data()).get("platform")
    if not platform:
        await state.clear()
        return await message.answer(
            "Приложение не выбрано. Нажмите «💰 Получить кэшбек» и выберите его заново.",
            reply_markup=main_kb(),
        )
    duplicate = conn.execute(
        "SELECT 1 FROM claims WHERE shop=? AND player_id=? COLLATE NOCASE AND status IN ('pending','approved')",
        (platform, player_id),
    ).fetchone()
    if duplicate:
        return await message.answer(
            "Этот номер уже отправлен на проверку или подтверждён. "
            "Проверьте номер заказа или обратитесь в поддержку."
        )

    await state.clear()
    cur = conn.execute(
        "INSERT INTO claims(user_id, shop, amount, player_id, created_at) VALUES(?,?,?,?,?)",
        (message.from_user.id, platform, 0, player_id, now()),
    )
    conn.commit()
    cid = cur.lastrowid
    await message.answer(
        f"✅ Заявка <b>№{cid}</b> отправлена на проверку.\n"
        f"🛒 Приложение: <b>{esc(platform)}</b>\n"
        f"🆔 ID: <code>{esc(player_id)}</code>\n\n"
        "Мы сообщим вам о решении.",
        reply_markup=main_kb(),
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"claim:ok:{cid}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"claim:no:{cid}"),
            ]
        ]
    )
    uname = f"@{message.from_user.username}" if message.from_user.username else "—"
    await notify_admins(
        message.bot,
        f"🧾 <b>Новая заявка №{cid}</b>\n\n"
        f"🛒 Приложение: <b>{esc(platform)}</b>\n"
        f"🆔 ID / номер заказа: <code>{esc(player_id)}</code>\n\n"
        f"👤 Пользователь: {esc(message.from_user.full_name)} ({esc(uname)})\n"
        f"Telegram ID: <code>{message.from_user.id}</code>",
        kb,
    )


@router.message(Claim.player_id)
async def claim_player_id_wrong(message: Message):
    await message.answer("Отправьте ID текстом. Отмена: /cancel")


@router.callback_query(F.data.startswith("claim:"))
async def claim_decision(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    _, action, cid = cb.data.split(":")
    c = conn.execute("SELECT * FROM claims WHERE id=?", (int(cid),)).fetchone()
    if not c or c["status"] != "pending":
        return await cb.answer("Уже обработано", show_alert=True)

    if action == "ok":
        # Kэshbek summasi xarid summasiga bog'liq, shuning uchun avval adminning o'zidan summani so'raymiz
        await state.clear()
        await state.set_state(AdminCredit.amount)
        await state.update_data(
            claim_id=c["id"],
            chat_id=cb.message.chat.id,
            message_id=cb.message.message_id,
            base_text=cb.message.html_text,
        )
        await cb.message.answer(
            f"💵 Введите сумму покупки для заявки №{c['id']} в {CURRENCY} (только число).\n"
            "Кэшбек начислится автоматически.\nОтмена: /cancel"
        )
        return await cb.answer()

    conn.execute("UPDATE claims SET status='rejected' WHERE id=?", (c["id"],))
    conn.commit()
    try:
        await cb.message.edit_text(f"{cb.message.html_text}\n\n<b>❌ Отклонено</b>", reply_markup=None)
    except Exception as e:
        logging.warning("Xabarni yangilab bo'lmadi: %s", e)
    try:
        await cb.bot.send_message(
            c["user_id"], f"❌ Заявка №{c['id']} отклонена. Если это ошибка, напишите в поддержку."
        )
    except Exception:
        pass
    await cb.answer()


@router.message(AdminCredit.amount, F.text)
async def admin_credit_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    text = message.text.strip()
    if text.startswith("/"):
        return await message.answer("Введите сумму числом или нажмите /cancel.")
    amount = parse_amount(text)
    if not amount:
        return await message.answer("Введите сумму числом, например: 250000")
    data = await state.get_data()
    await state.clear()
    res = credit_claim(data["claim_id"], amount)
    if res is None:
        return await message.answer("Эта заявка уже обработана.")
    c = res["claim"]

    try:
        await message.bot.edit_message_text(
            f"{data['base_text']}\n\n<b>✅ Подтверждено: покупка {fmt(amount)}, кэшбек {fmt(res['cashback'])}</b>",
            chat_id=data["chat_id"],
            message_id=data["message_id"],
            reply_markup=None,
        )
    except Exception as e:
        logging.warning("Xabarni yangilab bo'lmadi: %s", e)
    await message.answer(
        f"Готово. Заявка №{c['id']}: кэшбек <b>{fmt(res['cashback'])}</b> ({res['percent']:g}%) начислен."
    )

    user_text = (
        f"✅ Заявка №{c['id']} одобрена!\n"
        f"🛒 Приложение: <b>{esc(c['shop'])}</b>\n"
        f"💵 Покупка: {fmt(amount)}\n"
        f"💰 Кэшбек: <b>{fmt(res['cashback'])}</b> ({res['percent']:g}%)"
    )
    if res["new_tier"] != res["old_tier"]:
        user_text += f"\n\n🏅 Новый уровень: <b>{res['new_tier']}</b>!"
    try:
        await message.bot.send_message(c["user_id"], user_text)
    except Exception:
        pass
    if res["bonus_to"]:
        try:
            await message.bot.send_message(
                res["bonus_to"], f"🎁 Ваш друг получил первый кэшбек. Вам начислено <b>{fmt(REF_BONUS)}</b>!"
            )
        except Exception:
            pass


# ---------------------------------------------------------------------- вывод


@router.message(F.text == BTN_WITHDRAW)
async def withdraw_start(message: Message, state: FSMContext):
    u = register(message.from_user)
    await state.clear()
    if u["balance"] < MIN_WITHDRAW:
        return await message.answer(
            "💸 <b>Вывод средств</b>\n\n"
            f"Ваш баланс: <b>{fmt(u['balance'])}</b>\n"
            f"Минимальная сумма вывода: <b>{fmt(MIN_WITHDRAW)}</b>\n\n"
            "Накопите нужную сумму, и вывод станет доступен."
        )
    await state.set_state(Withdraw.amount)
    await message.answer(
        "💸 <b>Вывод средств</b>\n\n"
        f"Доступно: <b>{fmt(u['balance'])}</b>\n"
        f"Введите сумму вывода (от {fmt(MIN_WITHDRAW)}):\n\nОтмена: /cancel"
    )


@router.message(Withdraw.amount, F.text)
async def withdraw_amount(message: Message, state: FSMContext):
    u = register(message.from_user)
    amount = parse_amount(message.text)
    if not amount:
        return await message.answer("Введите сумму числом.")
    if amount < MIN_WITHDRAW:
        return await message.answer(f"Минимальная сумма: {fmt(MIN_WITHDRAW)}")
    if amount > u["balance"]:
        return await message.answer(f"Недостаточно средств. Доступно: {fmt(u['balance'])}")
    await state.update_data(amount=amount)
    await state.set_state(Withdraw.requisites)
    await message.answer("Введите реквизиты для выплаты (номер карты или кошелька):")


@router.message(Withdraw.requisites, F.text)
async def withdraw_requisites(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    u = register(message.from_user)
    amount = data["amount"]
    if amount > u["balance"]:
        return await message.answer("Недостаточно средств.", reply_markup=main_kb())
    req = message.text.strip()[:200]
    cur = conn.execute(
        "INSERT INTO withdrawals(user_id, amount, requisites, created_at) VALUES(?,?,?,?)",
        (u["id"], amount, req, now()),
    )
    conn.commit()
    wid = cur.lastrowid
    add_tx(u["id"], -amount, "withdraw_hold", f"Вывод №{wid}")
    await message.answer(
        f"✅ Заявка на вывод <b>№{wid}</b> на сумму <b>{fmt(amount)}</b> создана.\n"
        "Сумма зарезервирована, выплата обычно занимает некоторое время.",
        reply_markup=main_kb(),
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Выплачено", callback_data=f"wd:ok:{wid}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"wd:no:{wid}"),
            ]
        ]
    )
    uname = f"@{message.from_user.username}" if message.from_user.username else "—"
    await notify_admins(
        message.bot,
        f"💸 <b>Заявка на вывод №{wid}</b>\n"
        f"Пользователь: {esc(message.from_user.full_name)} ({esc(uname)}), ID <code>{u['id']}</code>\n"
        f"Сумма: <b>{fmt(amount)}</b>\nРеквизиты: <code>{esc(req)}</code>",
        kb,
    )


@router.callback_query(F.data.startswith("wd:"))
async def withdraw_decision(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    _, action, wid = cb.data.split(":")
    w = conn.execute("SELECT * FROM withdrawals WHERE id=?", (int(wid),)).fetchone()
    if not w or w["status"] != "pending":
        return await cb.answer("Уже обработано", show_alert=True)

    if action == "ok":
        conn.execute("UPDATE withdrawals SET status='paid' WHERE id=?", (w["id"],))
        conn.commit()
        verdict = "✅ Выплачено"
        user_text = f"✅ Вывод №{w['id']} на сумму <b>{fmt(w['amount'])}</b> выплачен."
    else:
        conn.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (w["id"],))
        conn.commit()
        add_tx(w["user_id"], w["amount"], "withdraw_refund", f"Вывод №{w['id']} отклонён")
        verdict = "❌ Отклонено, средства возвращены на баланс"
        user_text = f"❌ Вывод №{w['id']} отклонён. Сумма <b>{fmt(w['amount'])}</b> возвращена на баланс."

    await cb.message.edit_text(f"{cb.message.html_text}\n\n<b>{verdict}</b>", reply_markup=None)
    try:
        await cb.bot.send_message(w["user_id"], user_text)
    except Exception:
        pass
    await cb.answer()


# ---------------------------------------------------------------------- admin


@router.message(Command("stats"))
async def stats(message: Message):
    if not is_admin(message.from_user.id):
        return

    def one(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    users = one("SELECT COUNT(*) FROM users")
    pending_claims = one("SELECT COUNT(*) FROM claims WHERE status='pending'")
    pending_wd = one("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")
    balances = one("SELECT COALESCE(SUM(balance),0) FROM users")
    turnover = one("SELECT COALESCE(SUM(turnover),0) FROM users")
    paid = one("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='paid'")
    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"Пользователей: {users}\n"
        f"Заявок на кэшбек в ожидании: {pending_claims}\n"
        f"Выводов в ожидании: {pending_wd}\n"
        f"Сумма покупок: {fmt(turnover)}\n"
        f"Сумма на балансах: {fmt(balances)}\n"
        f"Выплачено всего: {fmt(paid)}"
    )


@router.message(Command("news"))
async def publish_news(message: Message, command: CommandObject):
    """/news <текст> — сохранить новость и разослать всем пользователям."""
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        return await message.answer("Использование: /news текст новости")
    text = command.args
    conn.execute("INSERT INTO news(text, created_at) VALUES(?,?)", (text, now()))
    conn.commit()
    ids = [r["id"] for r in conn.execute("SELECT id FROM users").fetchall()]
    await message.answer(f"Рассылка запущена: {len(ids)} получателей…")
    sent = 0
    for uid in ids:
        try:
            await message.bot.send_message(uid, f"📰 <b>Новость</b>\n\n{text}")
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await message.answer(f"Готово. Доставлено: {sent} из {len(ids)}.")


@router.message(Command("addbalance"))
async def add_balance(message: Message, command: CommandObject):
    """/addbalance <user_id> <сумма> — ручное начисление (можно отрицательное)."""
    if not is_admin(message.from_user.id):
        return
    try:
        uid, amount = (int(x) for x in (command.args or "").split())
    except ValueError:
        return await message.answer("Использование: /addbalance user_id сумма")
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
        return await message.answer("Пользователь не найден.")
    add_tx(uid, amount, "cashback" if amount > 0 else "withdraw_hold", "Ручная корректировка")
    await message.answer("Готово.")


# ---------------------------------------------------------------- Mini App (API)


def check_init_data(init_data: str, token: str, max_age: int = 86400) -> dict | None:
    """Telegram Mini App initData imzosini tekshiradi. To'g'ri bo'lsa foydalanuvchi dict, aks holda None.
    Shu orqali boshqa birovning ma'lumotini so'rab bo'lmaydi."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except ValueError:
        return None
    received = pairs.pop("hash", None)
    if not received:
        return None
    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        if now() - int(pairs.get("auth_date", "0")) > max_age:
            return None
        user = json.loads(pairs.get("user", ""))
    except ValueError:
        return None
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        return None
    return user


def mask_name(name: str | None) -> str:
    name = (name or "").strip()
    return (name[0].upper() if name else "?") + "***"


def ago_uz(ts: int) -> str:
    d = max(0, now() - ts)
    if d < 60:
        return "hozirgina"
    if d < 3600:
        return f"{d // 60} daqiqa oldin"
    if d < 86400:
        return f"{d // 3600} soat oldin"
    return f"{d // 86400} kun oldin"


def plain_text(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or ""))


def build_payload(tg_user: dict, bot_username: str) -> dict:
    name = " ".join(x for x in (tg_user.get("first_name"), tg_user.get("last_name")) if x).strip()
    u = register(SimpleNamespace(id=tg_user["id"], username=tg_user.get("username"), full_name=name or "Foydalanuvchi"))
    uid = u["id"]
    tier = tier_info(u["turnover"])

    refs = conn.execute("SELECT COUNT(*) FROM users WHERE referrer_id=?", (uid,)).fetchone()[0]
    rank = None
    if u["turnover"] > 0:
        rank = 1 + conn.execute(
            "SELECT COUNT(*) FROM users WHERE turnover>? OR (turnover=? AND total_earned>?) "
            "OR (turnover=? AND total_earned=? AND id<?)",
            (u["turnover"], u["turnover"], u["total_earned"], u["turnover"], u["total_earned"], uid),
        ).fetchone()[0]

    history = [
        {"kind": r["kind"], "amount": r["amount"], "date": fdate(r["created_at"])}
        for r in conn.execute(
            "SELECT kind, amount, created_at FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 12", (uid,)
        )
    ]
    rating = [
        {
            "rank": i,
            "name": mask_name(r["full_name"]),
            "turnover": r["turnover"],
            "tier": tier_info(r["turnover"])["name"],
            "me": r["id"] == uid,
        }
        for i, r in enumerate(
            conn.execute(
                "SELECT id, full_name, turnover FROM users WHERE turnover>0 "
                "ORDER BY turnover DESC, total_earned DESC, id ASC LIMIT 20"
            ),
            start=1,
        )
    ]
    last = conn.execute(
        "SELECT t.amount, t.created_at, u.full_name FROM transactions t JOIN users u ON u.id=t.user_id "
        "WHERE t.kind='cashback' AND t.amount>0 ORDER BY t.id DESC LIMIT 1"
    ).fetchone()
    ticker = (
        {"name": mask_name(last["full_name"]), "amount": last["amount"], "ago": ago_uz(last["created_at"])}
        if last
        else None
    )
    news_items = [
        {"text": plain_text(r["text"]), "date": fdate(r["created_at"])}
        for r in conn.execute("SELECT text, created_at FROM news ORDER BY id DESC LIMIT 10")
    ]
    return {
        "bot": bot_username,
        "currency": CURRENCY,
        "promo": PROMO_CODE,
        "min_withdraw": MIN_WITHDRAW,
        "ref_bonus": REF_BONUS,
        "me": {
            "id": uid,
            "first_name": tg_user.get("first_name") or "do'st",
            "name": name or "Foydalanuvchi",
            "balance": u["balance"],
            "earned": u["total_earned"],
            "turnover": u["turnover"],
            "refs": refs,
            "rank": rank,
            "tier": tier,
            "history": history,
        },
        "tiers": [{"name": n, "need": need, "bonus": b} for n, need, b in TIERS],
        "apps": [
            {
                "name": a["name"],
                "percent": float(a["percent"]),
                "desc": a.get("desc", ""),
                "url": a.get("url", ""),
                "logo": a.get("logo", ""),
                "color": a.get("color", "#0FA84A"),
                "promo": a.get("promo") or PROMO_CODE,
            }
            for a in APPS
        ],
        "rating": rating,
        "ticker": ticker,
        "news": news_items,
    }


async def start_web_server(bot: Bot) -> None:
    from aiohttp import web  # aiogram bilan birga o'rnatiladi

    me = await bot.me()

    async def index(_request):
        return web.FileResponse(
            WEBAPP_HTML, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        )

    async def api_data(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad_request"}, status=400)
        user = check_init_data(str(body.get("initData", "")), BOT_TOKEN) if isinstance(body, dict) else None
        if not user:
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(build_payload(user, me.username), headers={"Cache-Control": "no-store"})

    app = web.Application(client_max_size=64 * 1024)
    app.router.add_get("/", index)
    app.router.add_post("/api/data", api_data)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", WEBAPP_PORT).start()
    logging.info("Mini App serveri ishga tushdi: http://127.0.0.1:%s", WEBAPP_PORT)


# ------------------------------------------------------------------- ishga tushirish


async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi. bot.py ning yuqorisidagi BOT_TOKEN qatoriga tokenni yozing.")
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await start_web_server(bot)
    except Exception:
        logging.exception("Mini App serverini ishga tushirib bo'lmadi (bot baribir ishlaydi)")
    if WEBAPP_URL.startswith("https://"):
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="CashBoost 💰", web_app=WebAppInfo(url=WEBAPP_URL))
            )
            logging.info("«CashBoost» menyu tugmasi o'rnatildi: %s", WEBAPP_URL)
        except Exception:
            logging.exception("Menyu tugmasini o'rnatib bo'lmadi")
    else:
        logging.warning("WEBAPP_URL (https://...) berilmagan: «CashBoost» tugmasi chiqmaydi. README ga qarang.")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
