# app.py
import os
import logging
import time
import json
import uuid
import pytz
from datetime import datetime
import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
    Dispatcher
)
from flask import Flask, request, jsonify, send_file
import queue


load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

# Telegram Bot
bot = Bot(os.environ.get("TELEGRAM_BOT_TOKEN"))

# Connect to Database
def connect_db():
    try:
        conn = psycopg2.connect(
            os.environ.get("DATABASE_URL"), sslmode='require'
        )
        return conn
    except Exception as e:
        logging.error(f"Database connection error: {e}")
        return None

# State Variables for Conversation Handler
TASK_NAME, SCHEDULE_DATA, ADD_REMINDER_CONFIRM = range(3)

# Function to get User Data from the Database
def get_user_data(telegram_id):
    conn = connect_db()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
        user_data = cur.fetchone()
        cur.close()
        return user_data
    except Exception as e:
        logging.error(f"Error fetching user data: {e}")
        return None
    finally:
        conn.close()


# Function to create user
def create_user(telegram_id):
    conn = connect_db()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        secret_token = str(uuid.uuid4())
        cur.execute("INSERT INTO users (telegram_id, secret_token) VALUES (%s, %s)", (telegram_id, secret_token))
        conn.commit()
        cur.close()
        return secret_token
    except Exception as e:
        logging.error(f"Error creating user: {e}")
        return None
    finally:
        conn.close()
    

# Function to create reminder
def create_reminder(user_id, task_name, description, schedule_data, time_zone):
    conn = connect_db()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO reminders (user_id, task_name, description, schedule_type, schedule_data, time_zone) VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, task_name, description, 'once', json.dumps(schedule_data), time_zone),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        logging.error(f"Error creating reminder: {e}")
        return False
    finally:
        conn.close()

# Function to get reminders
def get_reminders(user_id):
    conn = connect_db()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM reminders WHERE user_id = %s", (user_id,))
        reminders = cur.fetchall()
        cur.close()
        return reminders
    except Exception as e:
        logging.error(f"Error fetching reminders: {e}")
        return None
    finally:
        conn.close()


def start(update: Update, context: CallbackContext):
    telegram_id = update.effective_user.id
    user_data = get_user_data(telegram_id)

    if not user_data:
        secret_token = create_user(telegram_id)
        update.message.reply_text(
            f"Welcome! A new profile has been created for you! Your secret access token is: {secret_token}. Use this token when you access the Web App."
        )
    else:
        update.message.reply_text("Welcome back!")

    context.user_data["user_id"] = telegram_id
    return ConversationHandler.END

def add_reminder_start(update: Update, context: CallbackContext):
    update.message.reply_text("Okay, let's create a reminder. What is the task name?")
    return TASK_NAME

def add_reminder_task_name(update: Update, context: CallbackContext):
    context.user_data["task_name"] = update.message.text
    update.message.reply_text("What is the date and time (YYYY-MM-DD HH:MM, e.g., 2024-09-15 14:30)?")
    return SCHEDULE_DATA

def add_reminder_schedule_data(update: Update, context: CallbackContext):
    schedule_data = update.message.text
    context.user_data["schedule_data"] = {"date_time": datetime.strptime(schedule_data, "%Y-%m-%d %H:%M").isoformat()}

    update.message.reply_text(f"Task Name: {context.user_data['task_name']}\nSchedule: {schedule_data}\n Confirm?")
    return ADD_REMINDER_CONFIRM

def add_reminder_confirmation(update: Update, context: CallbackContext):
    if update.message.text == "Yes":
        user_id = context.user_data["user_id"]
        task_name = context.user_data["task_name"]
        schedule_data = context.user_data["schedule_data"]
        user_data = get_user_data(user_id)
        if create_reminder(user_id, task_name, None, schedule_data, user_data[2] if user_data else "UTC"):
            update.message.reply_text("Reminder added.", reply_markup=ReplyKeyboardRemove())
        else:
            update.message.reply_text("Failed to add reminder.", reply_markup=ReplyKeyboardRemove())
    else:
        update.message.reply_text("Reminder creation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def error(update: Update, context: CallbackContext, bot_obj):
    """Log Errors caused by Updates."""
    logging.warning(f'Update "{update}" caused error "{context.error}"')
    try:
       bot_obj.send_message(chat_id=update.effective_chat.id, text=f"An error ocurred: {context.error}")
    except Exception as e:
        logging.warning(f"Error sending message about the error to user: {e}")

# Scheduler Functions
def get_all_reminders():
    conn = connect_db()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, task_name, description, schedule_type, schedule_data, time_zone FROM reminders WHERE status = 'active'")
        reminders = cur.fetchall()
        cur.close()
        return reminders
    except Exception as e:
        logging.error(f"Error fetching reminders for scheduler: {e}")
        return None
    finally:
        conn.close()


def update_reminder_status(reminder_id, status):
    conn = connect_db()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute("UPDATE reminders SET status = %s WHERE id = %s", (status, reminder_id))
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        logging.error(f"Error updating reminder status: {e}")
        return False
    finally:
        conn.close()


def send_telegram_notification(user_id, task_name, description):
    message = f"Reminder: {task_name}\n{description if description else ''}"
    try:
        bot.send_message(chat_id=user_id, text=message)
    except Exception as e:
        logging.error(f"Error sending message to Telegram: {e}")

def check_reminders():
    reminders = get_all_reminders()
    if not reminders:
        return
    for reminder_id, user_id, task_name, description, schedule_type, schedule_data, time_zone in reminders:
        try:
            schedule_data = json.loads(schedule_data)
            now_utc = datetime.now(pytz.utc)
            reminder_date_time = datetime.fromisoformat(schedule_data["date_time"]).astimezone(pytz.utc)
            if now_utc >= reminder_date_time:
                send_telegram_notification(user_id, task_name, description)
                update_reminder_status(reminder_id, 'completed')
        except Exception as e:
            logging.error(f"Error processing reminder {reminder_id}: {e}")

def start_scheduler():
    scheduler = BackgroundScheduler(timezone=pytz.utc)
    scheduler.add_job(check_reminders, 'interval', minutes=1)
    scheduler.start()
    logging.info("Scheduler started in Main Thread")

    try:
        while True:
            time.sleep(10)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


# Flask API Setup
app = Flask(__name__)

@app.route('/')
def index():
    return send_file("index.html")

@app.route('/api/reminders', methods=['GET'])
def get_user_reminders():
    telegram_id = request.headers.get('X-Telegram-ID')
    secret_token = request.headers.get('X-Secret-Token')

    if not telegram_id or not secret_token:
        return jsonify({'error': 'Missing headers'}), 400

    user_data = get_user_data_flask(telegram_id)
    if not user_data:
        return jsonify({'error': 'User not found'}), 404

    if user_data[1] != secret_token:
        return jsonify({'error': 'Unauthorized'}), 401

    reminders = get_reminders_flask(telegram_id)
    if reminders:
        reminders_list = []
        for reminder in reminders:
            reminders_list.append({
                'id': reminder[0],
                'task_name': reminder[2],
                'description': reminder[3],
                'schedule_type': json.loads(reminder[4]),
                'schedule_data': json.loads(reminder[5]),
                'status': reminder[6],
                'created_at': reminder[7].isoformat()
            })
        return jsonify({'reminders': reminders_list}), 200
    else:
        return jsonify({'message': 'No reminders found'}), 404


def main():
    # Start Scheduler
    start_scheduler()

    # Start Telegram Bot
    updater = Updater(os.environ.get("TELEGRAM_BOT_TOKEN"))
    dispatcher = Dispatcher(updater, None)

    # Conversation handler for adding reminders
    add_reminder_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add', add_reminder_start)],
        states={
            TASK_NAME: [MessageHandler(Filters.text & ~Filters.command, add_reminder_task_name)],
            SCHEDULE_DATA: [MessageHandler(Filters.text & ~Filters.command, add_reminder_schedule_data)],
            ADD_REMINDER_CONFIRM: [MessageHandler(Filters.text & ~Filters.command, add_reminder_confirmation)],
        },
        fallbacks=[CommandHandler('cancel', lambda update, context: update.message.reply_text('Cancelled', reply_markup=ReplyKeyboardRemove()))],
    )
    dispatcher.add_handler(add_reminder_conv_handler)

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_error_handler(lambda update, context, bot_obj: error(update, context, bot_obj))

    updater.start_polling()

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True, use_reloader=False)

if __name__ == '__main__':
    main()
