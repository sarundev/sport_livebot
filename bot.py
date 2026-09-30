import os
import re
import html
import logging
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

load_dotenv()

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# League code on ESPN: eng.1 = Premier League, esp.1 = La Liga, uefa.champions = Champions League
LEAGUE = os.environ.get("LEAGUE", "eng.1")
ESPN = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{LEAGUE}"
CAMBODIA_TZ = timezone(timedelta(hours=7))
KHMER_DIGITS = str.maketrans("0123456789", "០១២៣៤៥៦៧៨៩")

# Telegram Premium animated icons (custom emoji). Shown only if the bot owner has Telegram Premium;
# otherwise the bot falls back to the plain emoji. Set PREMIUM_ICONS=0 in .env to turn them off.
PREMIUM_ICONS = os.environ.get("PREMIUM_ICONS", "1") == "1"
ICONS = {
    "trophy": ("🏆", "5312315739842026755"),
    "live": ("🔥", "5312241539987020022"),
    "results": ("⚽️", "5375159220280762629"),
    "schedule": ("📆", "5433614043006903194"),
    "news": ("📰", "5434144690511290129"),
    "refresh": ("⚡️", "5312016608254762256"),
}


def icon(key: str) -> str:
    emoji, emoji_id = ICONS[key]
    return f'<tg-emoji emoji-id="{emoji_id}">{emoji}</tg-emoji>'


def plain(text: str) -> str:
    return re.sub(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', r"\1", text)


WELCOME = (
    f"{icon('trophy')} <b>Sport Bot – ព័ត៌មានបាល់ទាត់</b>\n\n"
    "លទ្ធផល ពិន្ទុផ្ទាល់ និងកាលវិភាគការប្រកួតបាល់ទាត់ ដោយផ្ទាល់នៅក្នុងតេឡេក្រាម។\n\n"
    f"{icon('live')} <b>ឡាយផ្ទាល់ព័ត៌មានកីឡា</b> — ពិន្ទុផ្ទាល់ និងព័ត៌មានថ្មីៗ។\n"
    f"{icon('results')} <b>មើលលទ្ធផល</b> — លទ្ធផលការប្រកួតចុងក្រោយ។\n"
    f"{icon('schedule')} <b>កាលវិភាគការប្រកួត</b> — ការប្រកួតដែលនឹងមកដល់។\n\n"
    "👇 សូមជ្រើសរើសជម្រើសខាងក្រោម។"
)


def button(text: str, data: str, style: str = None, icon_key: str = None, premium: bool = True) -> InlineKeyboardButton:
    # style: "success" (green), "danger" (red), "primary" (blue); the library has no argument for it yet
    extra = {"style": style} if style else {}
    if icon_key:
        emoji, emoji_id = ICONS[icon_key]
        if premium:
            extra["icon_custom_emoji_id"] = emoji_id
        else:
            text = f"{emoji} {text}"
    return InlineKeyboardButton(text, callback_data=data, api_kwargs=extra or None)


def main_menu(premium: bool = True) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [button("ឡាយផ្ទាល់ព័ត៌មានកីឡា", "live", "danger", "live", premium)],
        [
            button("មើលលទ្ធផល", "results", "success", "results", premium),
            button("កាលវិភាគការប្រកួត", "schedule", "primary", "schedule", premium),
        ],
    ])


def page_menu(page: str):
    def build(premium: bool = True) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [button("ធ្វើបច្ចុប្បន្នភាព", page, "primary", "refresh", premium), button("⬅️ ត្រឡប់ក្រោយ", "back")],
        ])
    return build


def khmer(text: str) -> str:
    return text.translate(KHMER_DIGITS)


def local_time(iso: str) -> str:
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(CAMBODIA_TZ)
    return khmer(dt.strftime("%d/%m %H:%M"))


def month_code(months_from_now: int) -> str:
    now = datetime.now(CAMBODIA_TZ)
    total = now.year * 12 + now.month - 1 + months_from_now
    return f"{total // 12}{total % 12 + 1:02d}"


async def get_json(url: str, params: dict = None) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def get_matches(months: list) -> list:
    matches = []
    for m in months:
        data = await get_json(f"{ESPN}/scoreboard", {"dates": month_code(m)})
        matches.extend(data.get("events", []))
    return matches


def teams(event: dict) -> tuple:
    comp = event["competitions"][0]["competitors"]
    home = next(c for c in comp if c["homeAway"] == "home")
    away = next(c for c in comp if c["homeAway"] == "away")
    return home, away


def state(event: dict) -> str:
    return event["status"]["type"]["state"]  # pre, in, post


def score_line(event: dict) -> str:
    home, away = teams(event)
    return (
        f"{html.escape(home['team']['shortDisplayName'])} "
        f"<b>{khmer(home.get('score', '0'))} - {khmer(away.get('score', '0'))}</b> "
        f"{html.escape(away['team']['shortDisplayName'])}"
    )


async def results_text() -> str:
    finished = [e for e in await get_matches([-1, 0]) if state(e) == "post"]
    finished.sort(key=lambda e: e["date"], reverse=True)
    if not finished:
        return f"{icon('results')} <b>លទ្ធផលការប្រកួត</b>\n\nមិនទាន់មានលទ្ធផលនៅឡើយទេ។"
    lines = [f"🗓 {local_time(e['date'])}\n{score_line(e)}" for e in finished[:10]]
    return f"{icon('results')} <b>លទ្ធផលចុងក្រោយ</b>\n\n" + "\n\n".join(lines)


async def live_text() -> str:
    parts = [f"{icon('live')} <b>ការប្រកួតកំពុងលេងផ្ទាល់</b>\n"]
    data = await get_json(f"{ESPN}/scoreboard")
    live = [e for e in data.get("events", []) if state(e) == "in"]
    if live:
        for e in live:
            minute = html.escape(e["status"]["type"].get("shortDetail", ""))
            parts.append(f"⏱ {khmer(minute)}  {score_line(e)}")
    else:
        parts.append("ពេលនេះមិនមានការប្រកួតផ្ទាល់ទេ។")

    parts.append(f"\n{icon('news')} <b>ព័ត៌មានកីឡាថ្មីៗ</b>\n")
    news = await get_json(f"{ESPN}/news", {"limit": 6})
    for a in news.get("articles", [])[:6]:
        title = html.escape(a.get("headline", ""))
        link = a.get("links", {}).get("web", {}).get("href")
        parts.append(f"• <a href=\"{html.escape(link)}\">{title}</a>" if link else f"• {title}")
    return "\n".join(parts)


async def schedule_text() -> str:
    upcoming = [e for e in await get_matches([0, 1]) if state(e) == "pre"]
    upcoming.sort(key=lambda e: e["date"])
    if not upcoming:
        return f"{icon('schedule')} <b>កាលវិភាគការប្រកួត</b>\n\nមិនទាន់មានកាលវិភាគនៅឡើយទេ។"
    lines = []
    for e in upcoming[:10]:
        home, away = teams(e)
        lines.append(
            f"🕒 {local_time(e['date'])}\n"
            f"{html.escape(home['team']['shortDisplayName'])} 🆚 {html.escape(away['team']['shortDisplayName'])}"
        )
    return f"{icon('schedule')} <b>កាលវិភាគការប្រកួត</b> (ម៉ោងកម្ពុជា)\n\n" + "\n\n".join(lines)


PAGES = {"results": results_text, "live": live_text, "schedule": schedule_text}


async def show(send, text: str, build_markup) -> None:
    """Send with Premium icons; if Telegram refuses them, resend with plain emoji."""
    try:
        if PREMIUM_ICONS:
            try:
                await send(text, build_markup(True))
                return
            except BadRequest as e:
                if "not modified" in str(e):
                    raise
                logging.warning("Premium icons refused (%s), using plain emoji", e)
        await send(plain(text), build_markup(False))
    except BadRequest as e:
        if "not modified" not in str(e):  # pressing refresh with no new data
            raise


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def send(text, markup):
        await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")

    await show(send, WELCOME, main_menu)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "back":
        text, build_markup = WELCOME, main_menu
    elif query.data in PAGES:
        try:
            text = await PAGES[query.data]()
        except Exception:
            logging.exception("Failed to load %s", query.data)
            text = "⚠️ មិនអាចទាញយកទិន្នន័យបានទេ។ សូមព្យាយាមម្តងទៀត។"
        build_markup = page_menu(query.data)
    else:
        return

    async def send(text, markup):
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)

    await show(send, text, build_markup)


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token or token == "paste-your-token-here":
        raise SystemExit("សូមដាក់ BOT_TOKEN ក្នុងឯកសារ .env (យក token ពី @BotFather)។")

    app = (
        Application.builder()
        .token(token)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))

    print("Bot កំពុងដំណើរការ... ចុច Ctrl+C ដើម្បីបញ្ឈប់។")
    app.run_polling()


if __name__ == "__main__":
    main()
