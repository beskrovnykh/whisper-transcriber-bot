import threading
from datetime import datetime

import requests as requests
from chalice import Chalice
import openai
import os
import yt_dlp
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import Update
from telegram.ext import (CallbackContext, Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters,
                          ConversationHandler)
from pydub import AudioSegment
from typing import Tuple

app = Chalice(app_name='whisper-transcriber-bot')
app.debug = True
app.log.setLevel(logging.INFO)

WAITING_FOR_URL = 1
WAITING_FOR_START_TIME = 2
WAITING_FOR_END_TIME = 3
PROCESSING_AUDIO = 4

processing_done_event = threading.Event()
processing_done_event.set()


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


def download_and_trim_audio(video_url: str, start_time: int = None, end_time: int = None) -> Tuple[str, float]:
    app.log.info(
        f"Downloading and trimming audio for url: {video_url}, start_time: {start_time}, end_time: {end_time}")  # Добавлено для отладки

    try:
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

    except Exception as e:
        app.log.error(f"Error in download_and_trim_audio: {e}")

    app.log.info("End of download_and_trim_audio")


def transcribe(audio_file_path: str) -> str:
    openai.api_key = os.environ["OPENAI_API_KEY"]

    with open(audio_file_path, "rb") as audio_file:
        response = openai.Audio.transcribe("whisper-1", audio_file, response_format="text", language="ru")

    return response


# Обработчик команды /start
def start_handler(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_chat.id
    context.bot.send_message(chat_id=user_id, text="Привет! Я бот для транскрибации аудиофайлов!")


# Обработчик команды /transcribe
def transcribe_handler(update: Update, context: CallbackContext) -> int:
    # Проверяем, выполняется ли в данный момент другая операция
    if not processing_done_event.is_set():
        return processing_audio_handler(update, context)
    else:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Отправь мне URL аудиофайла на YouTube")
        return WAITING_FOR_URL


def cancel_handler(update: Update, context: CallbackContext) -> int:
    context.bot.send_message(chat_id=update.effective_chat.id, text="Не понимаю такую команду :(")
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
    context.user_data['start_time'] = None
    keyboard = [[InlineKeyboardButton("Пропустить", callback_data='skip')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.bot.send_message(chat_id=update.effective_chat.id,
                             text="Введите время конца в формате HH:MM:SS (MM:SS) или нажмите кнопку 'Пропустить'",
                             reply_markup=reply_markup)
    return WAITING_FOR_END_TIME


def skip_end_time_handler(update: Update, context: CallbackContext) -> int:
    app.log.info("skip_end_time_handler")
    context.user_data['end_time'] = None
    start_time_str = context.user_data.get('start_time', None)

    start_time = parse_time(start_time_str) if start_time_str else None
    end_time = None

    url = context.user_data['url']
    if not url:
        app.log.error("URL is missing in user data")
        return ConversationHandler.END

    processing_done_event.clear()
    threading.Thread(target=process_audio, args=(url, start_time, end_time, update, context)).start()

    return processing_audio_handler(update, context)  # переходим к обработке аудио


def log_transcribe_request(update: Update, context: CallbackContext, duration: float) -> None:
    user_id = update.effective_user.id
    time = datetime.now()
    cost = duration / 60 * 0.006
    message = f"Пользователь {user_id} начал транскрибацию в {time}. Стоимость составляет ${cost}."
    context.bot.send_message(chat_id=update.effective_chat.id, text=message)


def send_large_message(bot, chat_id, text, max_message_length=4096):
    app.log.info(f"send_large_message {text}")
    while text:
        part = text[:max_message_length]
        cut_pos = max_message_length

        # Если часть сообщения длиннее, чем максимально допустимая длина,
        # пытаемся обрезать по последнему пробелу или переносу строки.
        if len(part) == max_message_length:
            last_space_pos = part.rfind(' ')
            last_newline_pos = part.rfind('\n')

            cut_pos = max(last_space_pos, last_newline_pos)
            if cut_pos == -1:
                cut_pos = max_message_length  # Если нет пробелов или переносов строки, обрезаем по максимальной длине.

        # Отправляем часть сообщения и удаляем ее из исходного текста.
        bot.send_message(chat_id=chat_id, text=part[:cut_pos])
        text = text[cut_pos:].lstrip()  # Удаляем пробелы в начале следующей части текста.


def process_audio(url, start_time, end_time, update, context):
    app.log.info("process_audio")
    # Загружаем аудиофайл по URL
    try:
        # Сообщаем пользователю, что начался процесс транскрибирования
        context.bot.send_message(chat_id=update.effective_chat.id, text="Начался процесс транскрибирования. "
                                                                        "Пожалуйста, подождите")

        app.log.info("Before download_and_trim_audio")
        audio_file, duration = download_and_trim_audio(url, start_time, end_time)
        app.log.info("After download_and_trim_audio")

        # Отправляем аудиофайл пользователю
        with open(audio_file, 'rb') as audio_file_to_send:
            context.bot.send_document(chat_id=update.effective_chat.id, document=audio_file_to_send)

        # Транскрибируем аудиофайл
        transcript = transcribe(audio_file)

        # Отправляем транскрипцию пользователю
        send_large_message(context.bot, update.effective_chat.id,
                           text=f"Ваш запрос обработан! Результат:\n\n{transcript}")

        # Отправляем статистику вызова специальному боту
        log_transcribe_request(update, context, duration)
        # Удаляем временный аудиофайл
        # os.remove(audio_file)

    except Exception as e:
        app.log.error(f"Error in processing audio: {e}")

    finally:
        app.log.info("Finalize process audio")
        # Когда обработка завершена, устанавливаем событие
        processing_done_event.set()


def end_time_handler(update: Update, context: CallbackContext) -> int:
    app.log.info("end_time_handler")
    end_time_str = update.message.text.strip()
    context.user_data['end_time'] = end_time_str  # сохраняем время конца в пользовательских данных

    start_time_str = context.user_data.get('start_time', None)
    end_time_str = context.user_data.get('end_time', None)

    start_time = parse_time(start_time_str) if start_time_str else None
    end_time = parse_time(end_time_str) if end_time_str else None

    url = context.user_data['url']
    if not url:
        app.log.error("URL is missing in user data")
        return ConversationHandler.END

    processing_done_event.clear()

    threading.Thread(target=process_audio, args=(url, start_time, end_time, update, context)).start()

    return PROCESSING_AUDIO  # переходим в состояние обработки аудио


def processing_audio_handler(update: Update, context: CallbackContext) -> int:
    app.log.info("processing_audio_handler")
    # Если обработка завершена, завершаем диалог
    if not processing_done_event.is_set():
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
            CommandHandler('transcribe', transcribe_handler),
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


@app.route('/set_webhook', methods=['POST'])
def set_webhook():
    app.log.info("set_webhook")
    url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
    webhook_url = app.current_request.json_body['webhook_url']
    data = {"url": webhook_url}
    response = requests.post(url, data=data)
    return response.json()
