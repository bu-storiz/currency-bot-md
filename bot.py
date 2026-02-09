import logging
import sqlite3
import datetime
import requests
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from functools import lru_cache
import time

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = '8548804129:AAE1gkpLVCsFaMvmgckpYqrse7fwtXc4dR8'  # ← ТВОЙ ТОКЕН

AVAILABLE_CURRENCIES = {
    'EUR': 'EUR 🇪🇺',
    'USD': 'USD 🇺🇸',
    'RON': 'RON 🇷🇴',
    'UAH': 'UAH 🇺🇦',
    'RUB': 'RUB 🇷🇺'
}

COLORS = ['🟢', '🟡', '🟠', '🔴', '⚪️', '⚪️']

conn = sqlite3.connect('user_settings.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        language TEXT DEFAULT 'RU',
        base_currency TEXT DEFAULT 'MDL',
        compare_currencies TEXT
    )
''')
conn.commit()

TEXTS = {
    'RU': {
        'welcome': "Привет! Выберите язык:",
        'menu': 'Меню:',
        'choose_base': "Выберите базовую валюту:",
        'choose_compares': "Выберите ровно 3 валюты для сравнения:",
        'reset': 'Настройки сброшены.',
        'reset_button': 'Сброс',
        'get_rates': 'Курс Национального Банка',
        'no_settings': 'Настройте валюты для сравнения.',
        'rates_error': 'Не удалось получить курсы.',
        'base_set': 'Базовая: **{curr}**',
        'compares_set': 'Сравниваем: **{val}**',
        'confirm': 'Подтвердить',
        'selected': 'Выбрано: {count}/3',
        'rates_title': "Курсы относительно **{base}** (MDL):",
        'bank_rates': 'Курсы по банкам',
        'choose_currency_for_banks': 'Выберите валюту для курсов по банкам (к MDL):',
        'bank_list': 'Курсы {curr} по банкам (лучшая покупка сверху):\n\n',
        'bank_error': 'Курсы по банкам для {curr} недоступны сейчас.',
        'back_to_menu': '← Меню',
        'data_unavailable': 'Данные недоступны',
        'average_fallback': 'Средний по банкам (curs.md)',
        'buy_label': 'Покупка',
        'sell_label': 'Продажа'
    },
    'EN': {
        'welcome': "Hello! Choose language:",
        'menu': 'Menu:',
        'choose_base': "Choose base currency:",
        'choose_compares': "Choose exactly 3 currencies to compare:",
        'reset': 'Settings reset.',
        'reset_button': 'Reset',
        'get_rates': "National Bank exchange rate",
        'no_settings': 'Set up comparison currencies.',
        'rates_error': 'Failed to fetch rates.',
        'base_set': 'Base: **{curr}**',
        'compares_set': 'Comparing: **{val}**',
        'confirm': 'Confirm',
        'selected': 'Selected: {count}/3',
        'rates_title': "Rates relative to **{base}** (MDL):",
        'bank_rates': 'Bank rates',
        'choose_currency_for_banks': 'Choose currency for bank rates (to MDL):',
        'bank_list': 'Rates for {curr} by banks (best buy first):\n\n',
        'bank_error': 'Bank rates for {curr} unavailable now.',
        'back_to_menu': '← Menu',
        'data_unavailable': 'Data unavailable',
        'average_fallback': 'Average by banks (curs.md)',
        'buy_label': 'Buy',
        'sell_label': 'Sell'
    },
    'RO': {
        'welcome': "Bună! Alegeți limba:",
        'menu': 'Meniu:',
        'choose_base': "Alegeți valuta de bază:",
        'choose_compares': "Alegeți exact 3 valute pentru comparație:",
        'reset': 'Setări resetate.',
        'reset_button': 'Resetare',
        'get_rates': "Cursul Băncii Naționale",
        'no_settings': 'Configurați valutele de comparație.',
        'rates_error': 'Nu s-au putut obține cursurile.',
        'base_set': 'Bază: **{curr}**',
        'compares_set': 'Comparăm: **{val}**',
        'confirm': 'Confirmă',
        'selected': 'Selectat: {count}/3',
        'rates_title': "Cursuri relative la **{base}** (MDL):",
        'bank_rates': 'Cursuri bancare',
        'choose_currency_for_banks': 'Alegeți valuta pentru cursuri bancare (la MDL):',
        'bank_list': 'Cursuri pentru {curr} după bănci (cel mai bun buy sus):\n\n',
        'bank_error': 'Cursuri bancare pentru {curr} indisponibile acum.',
        'back_to_menu': '← Meniu',
        'data_unavailable': 'Date indisponibile',
        'average_fallback': 'Medie după bănci (curs.md)',
        'buy_label': 'Cumpărare',
        'sell_label': 'Vânzare'
    }
}

def get_lang(uid):
    cursor.execute('SELECT language FROM users WHERE user_id=?', (uid,))
    row = cursor.fetchone()
    return row[0] if row else 'RU'

def get_bnm_rates():
    today = datetime.date.today().strftime('%d.%m.%Y')
    url = f'https://www.bnm.md/en/official_exchange_rates?get_xml=1&date={today}'
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.content)
        rates = {'MDL': 1.0}
        for valute in root.findall('Valute'):
            code = valute.find('CharCode').text
            value = float(valute.find('Value').text.replace(',', '.'))
            nominal = int(valute.find('Nominal').text or 1)
            rates[code] = value / nominal
        return rates
    except Exception as e:
        logger.error(f"BNM error: {e}")
        return None

def get_cursmd_rates():
    url = 'https://www.curs.md/en'
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.content, 'lxml')
        rates = {'MDL': 1.0}
        table = soup.find('table', class_='currency-table')
        if table:
            for row in table.find_all('tr')[1:]:
                cells = row.find_all('td')
                if len(cells) >= 3:
                    code = cells[0].text.strip().upper()
                    try:
                        buy = float(cells[1].text.strip().replace(',', '.'))
                        sell = float(cells[2].text.strip().replace(',', '.'))
                        rates[code] = (buy + sell) / 2
                    except:
                        pass
        return rates
    except Exception as e:
        logger.error(f"curs.md error: {e}")
        return None

def get_combined_rates():
    bnm = get_bnm_rates()
    curs = get_cursmd_rates()
    combined = {'MDL': 1.0}
    codes = set(bnm or {}) | set(curs or {})
    for code in codes:
        vals = []
        if bnm and code in bnm:
            vals.append(bnm[code])
        if curs and code in curs:
            vals.append(curs[code])
        if vals:
            combined[code] = sum(vals) / len(vals)
    return combined

def get_rates_text(base, compares, rates, lang):
    if not rates or base not in rates:
        return TEXTS[lang]['rates_error']
    base_rate = rates[base]
    lines = [TEXTS[lang]['rates_title'].format(base=base)]
    for c in compares.split(','):
        c = c.strip()
        if c in rates:
            rate = rates[c] / base_rate
            lines.append(f"{c}: **{rate}** {base}")  # без :.4f — точные значения
    return "\n".join(lines)

@lru_cache(maxsize=1)
def get_bank_rates_cached(_):
    rates = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get('https://valutar.md/ro', headers=headers, timeout=12)
        if r.status_code == 200:
            soup = BeautifulSoup(r.content, 'lxml')
            table = soup.find('table')
            if table:
                for row in table.find_all('tr')[1:]:
                    cells = row.find_all('td')
                    if len(cells) >= 5:
                        bank = cells[0].text.strip()
                        for idx, code in enumerate(['USD', 'EUR', 'RON', 'UAH', 'RUB'], start=1):
                            buy_idx = idx * 2 - 1
                            sell_idx = idx * 2
                            if buy_idx < len(cells) and sell_idx < len(cells):
                                buy_str = cells[buy_idx].text.strip().replace(',', '.')
                                sell_str = cells[sell_idx].text.strip().replace(',', '.')
                                try:
                                    buy = float(buy_str) if buy_str else None
                                    sell = float(sell_str) if sell_str else None
                                    if buy or sell:
                                        if code not in rates:
                                            rates[code] = []
                                        rates[code].append((bank, buy, sell))
                                except:
                                    pass
            if rates:
                logger.info(f"valutar.md спарсен для {len(rates)} валют")
                return rates
    except Exception as e:
        logger.error(f"valutar.md error: {e}")

    # Fallback — средние с curs.md
    curs = get_cursmd_rates()
    if curs:
        for code in AVAILABLE_CURRENCIES:
            if code in curs:
                avg = curs[code]
                rates[code] = [(TEXTS['RU']['average_fallback'], None, avg)]
    return rates

def get_bank_rates(curr):
    data = get_bank_rates_cached(time.time() // 900)
    if not data.get(curr):
        return []
    # Убираем Национальный банк и Rata medie
    filtered = [b for b in data[curr] if 'Națională' not in b[0] and 'Rata medie' not in b[0]]
    # Сортировка по лучшей покупке (самый высокий Buy сверху)
    filtered.sort(key=lambda x: x[1] if x[1] is not None else -float('inf'), reverse=True)
    return filtered

def get_bank_rates_text(curr, banks, lang):
    if not banks:
        return TEXTS[lang]['bank_error'].format(curr=curr)
    lines = [TEXTS[lang]['bank_list'].format(curr=curr)]
    for i, (bank, buy, sell) in enumerate(banks):
        color = COLORS[i] if i < len(COLORS) else '⚪️'
        buy_str = f"{buy}" if buy is not None else TEXTS[lang]['data_unavailable']
        sell_str = f"{sell}" if sell is not None else TEXTS[lang]['data_unavailable']
        lines.append(f"{color} {i+1}. {bank}: {TEXTS[lang]['buy_label']} {buy_str} | {TEXTS[lang]['sell_label']} {sell_str}")
    return "\n".join(lines)

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    uid = update.effective_user.id
    lang = get_lang(uid)
    keyboard = [
        [InlineKeyboardButton(TEXTS[lang]['choose_base'], callback_data='choose_base')],
        [InlineKeyboardButton(TEXTS[lang]['choose_compares'], callback_data='choose_compares')],
        [InlineKeyboardButton(TEXTS[lang]['bank_rates'], callback_data='bank_rates')],
        [InlineKeyboardButton(TEXTS[lang]['get_rates'], callback_data='get_rates')],
        [InlineKeyboardButton(TEXTS[lang]['reset_button'], callback_data='reset')]  # внизу
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = TEXTS[lang]['menu']

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (uid,))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO users (user_id) VALUES (?)', (uid,))
        conn.commit()

    keyboard = [
        [InlineKeyboardButton("Русский", callback_data='lang_RU'),
         InlineKeyboardButton("English", callback_data='lang_EN'),
         InlineKeyboardButton("Română", callback_data='lang_RO')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(TEXTS['RU']['welcome'], reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id
    lang = get_lang(uid)

    if data.startswith('lang_'):
        new_lang = data.split('_')[1]
        cursor.execute('UPDATE users SET language=? WHERE user_id=?', (new_lang, uid))
        conn.commit()
        context.user_data['selected_compares'] = []
        await show_compares_keyboard(query, context, new_lang)
        return

    if data == 'choose_base':
        keyboard = []
        row = []
        for code, disp in AVAILABLE_CURRENCIES.items():
            row.append(InlineKeyboardButton(disp, callback_data=f'set_base_{code}'))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton(TEXTS[lang]['back_to_menu'], callback_data='back_to_menu')])
        await query.edit_message_text(TEXTS[lang]['choose_base'], reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith('set_base_'):
        curr = data.split('_')[2]
        cursor.execute('UPDATE users SET base_currency=? WHERE user_id=?', (curr, uid))
        conn.commit()
        await query.edit_message_text(TEXTS[lang]['base_set'].format(curr=AVAILABLE_CURRENCIES[curr]), parse_mode='Markdown')
        await show_menu(update, context, edit=False)
        return

    if data == 'choose_compares':
        context.user_data['selected_compares'] = []
        await show_compares_keyboard(query, context, lang)
        return

    if data.startswith('toggle_compare_'):
        curr = data.split('_')[2]
        selected = context.user_data.get('selected_compares', [])
        if curr in selected:
            selected.remove(curr)
        elif len(selected) < 3:
            selected.append(curr)
        context.user_data['selected_compares'] = selected
        await show_compares_keyboard(query, context, lang)
        return

    if data == 'confirm_compares':
        selected = context.user_data.get('selected_compares', [])
        if len(selected) == 3:
            val = ','.join(selected)
            cursor.execute('UPDATE users SET compare_currencies=? WHERE user_id=?', (val, uid))
            conn.commit()
            await query.edit_message_text(TEXTS[lang]['compares_set'].format(val=val), parse_mode='Markdown')
            cursor.execute('SELECT base_currency FROM users WHERE user_id=?', (uid,))
            row = cursor.fetchone()
            base = row[0] if row and row[0] else 'MDL'
            rates = get_combined_rates()
            if rates:
                msg = get_rates_text(base, val, rates, lang)
                await query.message.reply_text(msg, parse_mode='Markdown')
            context.user_data.pop('selected_compares', None)
            await show_menu(update, context, edit=False)
        else:
            await query.answer(TEXTS[lang]['selected'].format(count=len(selected)), show_alert=True)
        return

    if data == 'bank_rates':
        keyboard = []
        row = []
        for code, disp in AVAILABLE_CURRENCIES.items():
            row.append(InlineKeyboardButton(disp, callback_data=f'bank_curr_{code}'))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton(TEXTS[lang]['back_to_menu'], callback_data='back_to_menu')])
        await query.edit_message_text(TEXTS[lang]['choose_currency_for_banks'], reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith('bank_curr_'):
        curr = data.split('_')[2]
        banks = get_bank_rates(curr)
        if banks:
            msg = get_bank_rates_text(curr, banks, lang)
            await query.edit_message_text(msg, parse_mode='Markdown')
        else:
            await query.edit_message_text(TEXTS[lang]['bank_error'].format(curr=curr))
        await show_menu(update, context, edit=False)
        return

    if data == 'reset':
        cursor.execute(
            'UPDATE users SET base_currency="MDL", compare_currencies=NULL WHERE user_id=?',
            (uid,)
        )
        conn.commit()
        await query.edit_message_text(TEXTS[lang]['reset'])
        await show_menu(update, context, edit=False)
        return

    if data == 'get_rates':
        cursor.execute('SELECT base_currency, compare_currencies FROM users WHERE user_id=?', (uid,))
        row = cursor.fetchone()
        if row and row[0] and row[1]:
            rates = get_combined_rates()
            if rates:
                msg = get_rates_text(row[0], row[1], rates, lang)
                await query.edit_message_text(msg, parse_mode='Markdown')
            else:
                await query.edit_message_text(TEXTS[lang]['rates_error'])
        else:
            await query.edit_message_text(TEXTS[lang]['no_settings'])
        await show_menu(update, context, edit=False)
        return

    if data == 'back_to_menu':
        await show_menu(update, context, edit=True)
        return

async def show_compares_keyboard(query, context, lang):
    selected = context.user_data.get('selected_compares', [])
    keyboard = []
    row = []
    for code, disp in AVAILABLE_CURRENCIES.items():
        btn_text = f"{disp} ✅" if code in selected else disp
        row.append(InlineKeyboardButton(btn_text, callback_data=f'toggle_compare_{code}'))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton(TEXTS[lang]['confirm'], callback_data='confirm_compares')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = TEXTS[lang]['choose_compares'] + f"\n{TEXTS[lang]['selected'].format(count=len(selected))}"
    await query.edit_message_text(text, reply_markup=reply_markup)

async def send_daily(context: ContextTypes.DEFAULT_TYPE):
    cursor.execute('SELECT user_id, base_currency, compare_currencies, language FROM users WHERE compare_currencies IS NOT NULL')
    for row in cursor.fetchall():
        uid, base, comp, lang = row
        if base and comp:
            rates = get_combined_rates()
            if rates:
                text = get_rates_text(base, comp, rates, lang)
                try:
                    await context.bot.send_message(chat_id=uid, text=text, parse_mode='Markdown')
                except Exception as e:
                    logger.error(f"Ошибка отправки {uid}: {e}")

def schedule_notifications(app):
    scheduler = app.job_queue.scheduler
    for job in scheduler.get_jobs():
        job.remove()
    trigger = CronTrigger(hour=9, minute=5, timezone='Europe/Chisinau')
    scheduler.add_job(send_daily, trigger, args=(app,), name="daily_notify_all")

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button_handler))

    schedule_notifications(app)

    print("Бот запущен. Уведомления ежедневно в 09:05 по Кишинёву")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':

    main()
