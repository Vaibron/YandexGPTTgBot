import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import random
import os
from dotenv import load_dotenv
from datetime import datetime
import json

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Загружаем переменные из .env файла
load_dotenv()

# Данные из переменного окружения
OAUTH_TOKEN = os.getenv('OAUTH_TOKEN')
FOLDER_ID = os.getenv('FOLDER_ID')
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
WEATHER_API_KEY = os.getenv('WEATHER_API_KEY')


# Загрузка настроек из файла settings.json
def load_settings():
    with open("settings.json", "r", encoding="utf-8") as file:
        return json.load(file)


settings = load_settings()

# Список тем из настроек
TOPICS = settings.get("topics_list")


def get_iam_token():
    response = requests.post(
        'https://iam.api.cloud.yandex.net/iam/v1/tokens',
        json={'yandexPassportOauthToken': OAUTH_TOKEN}
    )
    response.raise_for_status()
    return response.json()['iamToken']


# Функция для получения погоды
def get_weather():
    cities = settings.get("cities_for_weather")
    weather_data = {}
    for city in cities:
        response = requests.get(
            f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru")
        if response.status_code == 200:
            data = response.json()
            weather_data[city] = {
                "temp": data["main"]["temp"],
                "description": data["weather"][0]["description"],
                "humidity": data["main"]["humidity"],
                "wind": data["wind"]["speed"]
            }
        else:
            weather_data[city] = "Ошибка при получении данных"
    return weather_data


# Функция для отправки прогноза погоды
async def send_weather_update(context: CallbackContext):
    if settings.get("send_weather") == "Нет":
        return  # Если отправка погоды выключена, выходим из функции

    weather_data = get_weather()

    # Формируем текущее время для отображения в сообщении
    current_date = datetime.now().strftime("%d-%m-%Y %H:%M")

    message = f"🌤️ <b>Прогноз погоды на {current_date}:</b>\n\n"

    for city, data in weather_data.items():
        if isinstance(data, dict):
            message += f"🌍 <b>{city.capitalize()}:</b>\n"
            message += f"🌡️ Температура: {data['temp']}°C\n"
            message += f"☁️ Погода: {data['description']}\n"
            message += f"💧 Влажность: {data['humidity']}%\n"
            message += f"🌬️ Ветер: {data['wind']} м/с\n\n"
        else:
            message += f"<b>{city.capitalize()}:</b> {data}\n\n"

    # Добавляем хештеги
    message += "#Погода #Прогноз #Weather #Forecast"

    # Отправка сообщения
    await context.bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML")


async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        f'Привет! Я {settings.get("bot_name")}')


async def process_message(update: Update, context: CallbackContext) -> None:
    user_text = update.message.text
    forwarded_text = ""

    # Проверяем, есть ли пересланное сообщение с текстом
    if update.message.reply_to_message:
        if update.message.reply_to_message.text:
            forwarded_text = update.message.reply_to_message.text
        elif update.message.reply_to_message.caption:
            forwarded_text = update.message.reply_to_message.caption

    # Объединяем текст из текущего сообщения и пересланного сообщения
    combined_text = f"{user_text}\n\nПересланное сообщение: {forwarded_text}" if forwarded_text else user_text

    logger.info(f'Получено сообщение от пользователя: {combined_text}')

    # Проверяем, начинается ли сообщение с "Имя бота"
    name = settings.get("bot_name")
    if str(name).lower() not in user_text.lower():  # Проверка на наличие имени бота
        logger.info(f'Сообщение не содержит "{name}". Ответ не отправлен.')
        return

    # Получаем IAM токен
    try:
        iam_token = get_iam_token()
        logger.info(f'Получен IAM-токен: {iam_token}')
    except requests.RequestException as e:
        logger.error(f'Ошибка при получении IAM-токена: {e}')
        await update.message.reply_text('Произошла ошибка при получении токена.')
        return

    # Отправляем запрос к Yandex GPT
    data = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt",
        "completionOptions": {"temperature": 0.5, "maxTokens": 1000},
        "messages": [
            {"role": "system",
             "text": f"Ты {settings.get('bot_name')}. {settings.get('bot_behavior')}"},
            {"role": "user", "text": combined_text}
        ]
    }

    try:
        response = requests.post(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            headers={"Accept": "application/json", "Authorization": f"Bearer {iam_token}"},
            json=data
        )
        response.raise_for_status()
        result = response.json()
        logger.info(f'Получен ответ от Yandex GPT: {result}')
        answer = result.get('result', {}).get('alternatives', [{}])[0].get('message', {}).get('text',
                                                                                              'Ошибка получения ответа.')
        answer = answer.replace('«', '').replace('»', '')  # Убираем кавычки-елочки
    except requests.RequestException as e:
        logger.error(f'Ошибка при запросе к Yandex GPT: {e}')
        answer = 'Произошла ошибка при запросе к Yandex GPT.'

    await update.message.reply_text(answer, quote=False)


async def post_to_channel(context: CallbackContext) -> None:
    if settings.get("send_posts") == "Нет":
        return  # Если отправка постов выключена, выходим из функции

    topic = random.choice(TOPICS)  # Случайная тема из списка
    logger.info(f'Выбрана тема для поста: {topic}')

    # Получаем IAM токен
    try:
        iam_token = get_iam_token()
    except requests.RequestException as e:
        logger.error(f'Ошибка при получении IAM-токена: {e}')
        return

    # Запрос к Yandex GPT для генерации поста с конкретным фокусом на одну тему
    data = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt",
        "completionOptions": {"temperature": 0.5, "maxTokens": 500},
        "messages": [
            {"role": "system",
             "text": "Ты генерируешь посты для Telegram. Для каждой темы ты создаешь пост, который фокусируется на одной конкретной части этой темы. Пост должен быть красиво оформлен с использованием эмодзи, заголовков, выделений и других визуальных элементов для улучшения восприятия."},
            {"role": "user",
             "text": f"Тема: {topic}. Расскажи мне о конкретной части этой темы."}
        ]
    }

    try:
        response = requests.post(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            headers={"Accept": "application/json", "Authorization": f"Bearer {iam_token}"},
            json=data
        )
        response.raise_for_status()
        result = response.json()
        post_text = result.get('result', {}).get('alternatives', [{}])[0].get('message', {}).get('text',
                                                                                                 'Ошибка генерации поста.')
    except requests.RequestException as e:
        logger.error(f'Ошибка при запросе к Yandex GPT: {e}')
        return

    # Формирование сообщения с кодом и эмодзи
    post_message = f"*Тема:* {topic}\n\n{post_text}"

    # Отправка с parse_mode="Markdown" для правильного форматирования
    try:
        await context.bot.send_message(chat_id=CHAT_ID, text=post_message, parse_mode="Markdown")
        logger.info('Пост успешно опубликован в группе.')
    except Exception as e:
        logger.error(f'Ошибка при отправке поста в Telegram: {e}')


def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    scheduler = AsyncIOScheduler()

    # Запланированное задание на отправку погоды, если включено
    if settings.get("send_weather") == "Да":
        weather_time = settings.get("weather_time", "07:30").split(":")
        scheduler.add_job(send_weather_update, "cron", hour=int(weather_time[0]), minute=int(weather_time[1]),
                          timezone="Europe/Moscow", args=[application])

    # Запланированное задание на отправку постов, если включено
    if settings.get("send_posts") == "Да":
        posts_interval = int(settings.get("posts_interval"))  # Читаем интервал в минутах из настроек
        scheduler.add_job(post_to_channel, "interval", minutes=posts_interval, timezone="Europe/Moscow",
                          args=[application])

    scheduler.start()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))

    application.run_polling()


if __name__ == '__main__':
    main()
