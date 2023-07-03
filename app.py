import threading
from datetime import datetime

import requests as requests
from chalice import Chalice
import openai
import os
import yt_dlp

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import Update
from telegram.ext import (CallbackContext, Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, ConversationHandler)
from pydub import AudioSegment

app = Chalice(app_name='whisper-transcriber-bot')
app.debug = True

ALLOWED_USER_IDS = [int(user_id) for user_id in os.environ["ALLOWED_USER_IDS"].split(",")]

WAITING_FOR_URL = 1
WAITING_FOR_START_TIME = 2
WAITING_FOR_END_TIME = 3
PROCESSING_AUDIO = 4

processing_done_event = threading.Event()


def parse_time(time_str):
    parts = list(map(int, time_str.split(":")))
    if len(parts) == 3:  # HH:MM:SS
        hours, minutes, seconds = parts
    elif len(parts) == 2:  # MM:SS
        hours = 0
        minutes, seconds = parts
    else:
        raise ValueError("Неверный формат времени. Должен быть 'MM:SS' или 'HH:MM:SS'.")
    return hours * 3600 + minutes * 60 + seconds


def download_and_trim_audio(video_url: str, start_time: int = None, end_time: int = None) -> tuple[str, float]:
    print(
        f"Downloading and trimming audio for url: {video_url}, start_time: {start_time}, end_time: {end_time}")  # Добавлено для отладки

    audio_file_path = 'chalicelib/data/sounds/temp.webm'
    trimmed_audio_file_path = 'chalicelib/data/sounds/trimmed_temp.webm'

    if os.path.exists(audio_file_path):
        os.remove(audio_file_path)
    if os.path.exists(trimmed_audio_file_path):
        os.remove(trimmed_audio_file_path)

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': audio_file_path
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(video_url, download=True)

    # Обрезка аудиофайла
    audio = AudioSegment.from_file(audio_file_path)
    start_time = start_time * 1000 if start_time is not None else 0
    end_time = end_time * 1000 if end_time is not None else len(audio)
    trimmed_audio = audio[start_time:end_time]
    trimmed_audio.export(trimmed_audio_file_path, format="webm")

    # Calculate duration in seconds
    duration_seconds = len(trimmed_audio) / 1000

    return trimmed_audio_file_path, duration_seconds


def transcribe(audio_file_path: str) -> str:
    openai.api_key = os.environ["OPENAI_API_KEY"]

    with open(audio_file_path, "rb") as audio_file:
        response = openai.Audio.transcribe("whisper-1", audio_file, response_format="text", language="ru")

    return response


# Обработчик команды /start
def start_handler(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_chat.id
    if user_id in ALLOWED_USER_IDS:
        context.bot.send_message(chat_id=user_id, text="Привет! Я бот для транскрибации аудиофайлов.")
    else:
        context.bot.send_message(chat_id=user_id, text="Вы не авторизованы для использования этого бота.")


# Обработчик команды /transcribe
def transcribe_handler(update: Update, context: CallbackContext) -> int:
    context.bot.send_message(chat_id=update.effective_chat.id, text="Отправь мне URL аудиофайла на YouTube.")
    return WAITING_FOR_URL


def cancel_handler(update: Update, context: CallbackContext) -> int:
    context.bot.send_message(chat_id=update.effective_chat.id, text="Не понимаю твою команду!")
    return ConversationHandler.END


def url_handler(update: Update, context: CallbackContext) -> int:
    url = update.message.text.strip()
    context.user_data['url'] = url  # сохраняем URL в пользовательских данных

    keyboard = [[InlineKeyboardButton("Пропустить", callback_data='skip')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.bot.send_message(chat_id=update.effective_chat.id,
                             text="Введите время начала в формате HH:MM:SS (MM:SS) или нажмите кнопку 'Пропустить'",
                             reply_markup=reply_markup)
    return WAITING_FOR_START_TIME


def start_time_handler(update: Update, context: CallbackContext) -> int:
    start_time = update.message.text.strip()

    context.user_data['start_time'] = start_time  # сохраняем время начала в пользовательских данных
    keyboard = [[InlineKeyboardButton("Пропустить", callback_data='skip')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.bot.send_message(chat_id=update.effective_chat.id,
                             text="Введите время конца в формате HH:MM:SS (MM:SS) или нажмите кнопку 'Пропустить'",
                             reply_markup=reply_markup)
    return WAITING_FOR_END_TIME


def skip_start_time_handler(update: Update, context: CallbackContext) -> int:
    print(f"skip_start_time_handler")  # Добавлено для отладки
    context.user_data['start_time'] = None  # устанавливаем время начала в None
    keyboard = [[InlineKeyboardButton("Пропустить", callback_data='skip')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.bot.send_message(chat_id=update.effective_chat.id,
                             text="Введите время конца в формате HH:MM:SS (MM:SS) или нажмите кнопку 'Пропустить'",
                             reply_markup=reply_markup)
    return WAITING_FOR_END_TIME


def skip_end_time_handler(update: Update, context: CallbackContext) -> int:
    print(f"skip_end_time_handler")  # Добавлено для отладки

    context.user_data['end_time'] = None  # устанавливаем время конца в None
    start_time_str = context.user_data.get('start_time', None)

    start_time = parse_time(start_time_str) if start_time_str else None
    end_time = None

    url = context.user_data['url']
    if not url:
        print("URL is missing in user data")
        return ConversationHandler.END

    processing_done_event.clear()
    threading.Thread(target=process_audio, args=(url, start_time, end_time, update, context)).start()

    return processing_audio_handler(update, context)  # переходим к обработке аудио


def log_transcribe_request(update: Update, context: CallbackContext, duration: float) -> None:
    user_id = update.effective_user.id
    time = datetime.now()
    cost = duration / 60 * 0.006
    message = f"Пользователь {user_id} начал транскрибацию в {time}. Стоимость составляет ${cost}."
    context.bot.send_message(chat_id=os.environ["SUPPORT_CHAT_ID"], text=message)


def process_audio(url, start_time, end_time, update, context):
    # Загружаем аудиофайл по URL
    try:
        audio_file, duration = download_and_trim_audio(url, start_time, end_time)
        # Транскрибируем аудиофайл
        transcript = transcribe(audio_file)
        # Отправляем транскрипцию пользователю
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=f"Ваш запрос обработан! Результат:\n\n{transcript}")
        # Отправляем статистику вызова специальному боту
        log_transcribe_request(update, context, duration)
        # Удаляем временный аудиофайл
        os.remove(audio_file)
    except Exception as e:
        print(f"Error in processing audio: {e}")
    finally:
        # Когда обработка завершена, устанавливаем событие
        processing_done_event.set()


def end_time_handler(update: Update, context: CallbackContext) -> int:
    print(f"end_time_handler")  # Добавлено для отладки

    end_time_str = update.message.text.strip()
    context.user_data['end_time'] = end_time_str  # сохраняем время конца в пользовательских данных

    start_time_str = context.user_data.get('start_time', None)
    end_time_str = context.user_data.get('end_time', None)

    start_time = parse_time(start_time_str) if start_time_str else None
    end_time = parse_time(end_time_str) if end_time_str else None

    url = context.user_data['url']
    if not url:
        print("URL is missing in user data")
        return ConversationHandler.END

    processing_done_event.clear()
    threading.Thread(target=process_audio, args=(url, start_time, end_time, update, context)).start()

    return PROCESSING_AUDIO  # переходим в состояние обработки аудио


def processing_audio_handler(update: Update, context: CallbackContext) -> int:
    # Если обработка завершена, завершаем диалог
    if processing_done_event.is_set():
        return ConversationHandler.END
    else:
        # Если обработка еще не завершена, отправляем сообщение и остаемся в том же состоянии
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text="Ваш запрос обрабатывается. Пожалуйста, подождите")
        return PROCESSING_AUDIO


bot_token = os.environ["TELEGRAM_BOT_ID"]
updater = Updater(token=bot_token, use_context=True)

conv_handler = ConversationHandler(
    entry_points=[
        CommandHandler('transcribe', transcribe_handler),
        CommandHandler('start', start_handler)

    ],
    states={
        WAITING_FOR_URL: [
            MessageHandler(Filters.text & ~Filters.command, url_handler)
        ],
        WAITING_FOR_START_TIME: [
            MessageHandler(Filters.text & ~Filters.command, start_time_handler),
            CallbackQueryHandler(skip_start_time_handler)  # Обработчик для нажатия кнопки

        ],
        WAITING_FOR_END_TIME: [
            MessageHandler(Filters.text & ~Filters.command, end_time_handler),
            CallbackQueryHandler(skip_end_time_handler)
        ],
        PROCESSING_AUDIO: [
            MessageHandler(Filters.text & ~Filters.command, processing_audio_handler),
            MessageHandler(Filters.command, processing_audio_handler),
        ]
    },
    fallbacks=[CommandHandler('cancel', cancel_handler)]
)

updater.dispatcher.add_handler(conv_handler)


@app.route('/webhook', methods=['POST'], content_types=['application/json'])
def webhook():
    updater.dispatcher.process_update(Update.de_json(app.current_request.json_body, updater.bot))
    return 'Success', 200


@app.route('/transcribe', methods=['POST'])
def transcribe_webhook():
    try:
        video_url = app.current_request.json_body['video_url']
        start_time_str = app.current_request.json_body['start_time']
        end_time_str = app.current_request.json_body['end_time']

        start_time = parse_time(start_time_str) if start_time_str else None
        end_time = parse_time(end_time_str) if end_time_str else None

        transcript, duration = download_and_trim_audio(video_url, start_time, end_time)
        transcript_text = transcribe(transcript)

        return {'transcript': transcript_text}, 200
    except Exception as e:
        return {'error': str(e)}


# webhook_url = "https://hdwyooncln2d7mxb4e324r3ju40xculq.lambda-url.ap-southeast-2.on.aws/"
# webhook_url = "https://46f7-110-139-179-36.ngrok-free.app/webhook"
@app.route('/set_webhook', methods=['POST'])
def set_webhook():
    url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
    webhook_url = app.current_request.json_body['webhook_url']
    data = {"url": webhook_url}
    response = requests.post(url, data=data)
    return response.json()
