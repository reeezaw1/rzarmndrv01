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
from telegram import Bot, Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackContext,
    Filters,
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
            host=os.environ.get("DB_HOST"),
            database=os.environ.get("DB_NAME"),
            user=os.environ.get("DB_USER"),
            password=os.environ.get("DB_PASSWORD")
        )
        return conn
    except Exception as e:
        logging.error(f"Database connection error: {e}")
        return None

# State Variables for Conversation Handler
TASK_NAME, SCHEDULE_TYPE, SCHEDULE_DATA, ADD_REMINDER_CONFIRM = range(4)
SET_TIMEZONE = 5

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
def create_reminder(user_id, task_name, description, schedule_type, schedule_data, time_zone):
    conn = connect_db()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO reminders (user_id, task_name, description, schedule_type, schedule_data, time_zone) VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, task_name, description, json.dumps(schedule_type), json.dumps(schedule_data), time_zone),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        logging.error(f"Error creating reminder: {e}")
        return False
    finally:
        conn.close()

    def delete_reminder(reminder_id):
        conn = connect_db()
        if not conn:
            return None
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM reminders WHERE id = %s", (reminder_id,))
            conn.commit()
            cur.close()
            return True
        except Exception as e:
            logging.error(f"Error deleting reminder: {e}")
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
    
    def set_timezone(update: Update, context: CallbackContext):
        timezones = pytz.all_timezones
        keyboard = [
            [InlineKeyboardButton(timezone, callback_data=timezone)]
            for timezone in timezones
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(
           "Select your time zone:",
           reply_markup=reply_markup
        )
        return SET_TIMEZONE
    
    def set_user_timezone(update: Update, context: CallbackContext):
         query = update.callback_query
         query.answer()
         timezone = query.data
         telegram_id = update.effective_user.id
         if update_user_timezone(telegram_id, timezone):
             query.edit_message_text(f"Time zone set to {timezone}")
         else:
             query.edit_message_text("There was an error setting your timezone")
         return ConversationHandler.END
    

    def start(update: Update, context: CallbackContext):
        telegram_id = update.effective_user.id
        user_data = get_user_data(telegram_id)

        if not user_data:
            secret_token = create_user(telegram_id)
            update.message.reply_text(
                f"Welcome! A new profile has been created for you! Your secret access token is: {secret_token}. Use this token when you access the Web App."
            )
        else:
            keyboard = [
                ["Create Reminder", "List Reminders"],
                ["Help", "Language"]
            ]
            update.message.reply_text("Welcome back!",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
        if not user_data or user_data[2] is None:
            return set_timezone(update, context)

        return ConversationHandler.END


    def add_reminder_start(update: Update, context: CallbackContext):
        if update.message.text == "Create Reminder":
           update.message.reply_text("Okay, let's create a reminder. What is the task name?")
           return TASK_NAME
        else:
             return ConversationHandler.END

    def add_reminder_task_name(update: Update, context: CallbackContext):
        context.user_data["task_name"] = update.message.text
        reply_keyboard = [["Once", "Daily", "Weekly", "Interval"]]
        update.message.reply_text(
            "What kind of reminder is it?",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        )
        return SCHEDULE_TYPE


    def add_reminder_schedule_type(update: Update, context: CallbackContext):
        schedule_type = update.message.text.lower()
        context.user_data["schedule_type"] = schedule_type

        if schedule_type == "once":
            update.message.reply_text("What is the date and time (YYYY-MM-DD HH:MM, e.g., 2024-09-15 14:30)?")
        elif schedule_type == "daily":
            update.message.reply_text("What time of the day (HH:MM, e.g., 14:30)?")
        elif schedule_type == "weekly":
            update.message.reply_text("What days of the week (comma-separated, e.g., Mon,Wed,Fri) and time of the day (HH:MM, e.g., 14:30)?")
        elif schedule_type == "interval":
            update.message.reply_text("How many days in between (number) and time of the day (HH:MM, e.g., 2, 14:30)?")
        else:
            update.message.reply_text("Invalid schedule type.")
            return ConversationHandler.END

        return SCHEDULE_DATA

    def add_reminder_schedule_data(update: Update, context: CallbackContext):
        schedule_type = context.user_data["schedule_type"]
        schedule_data_text = update.message.text
        schedule_data = {}
        try:
           user_id = update.effective_user.id
           user_data = get_user_data(user_id)
           timezone = pytz.timezone(user_data[2])
           if schedule_type == "once":
               reminder_date_time = datetime.strptime(schedule_data_text, "%Y-%m-%d %H:%M")
               localized_reminder_date_time = timezone.localize(reminder_date_time)
               schedule_data = {"date_time": localized_reminder_date_time.astimezone(pytz.utc).isoformat()}
           elif schedule_type == "daily":
               schedule_data = {"time": datetime.strptime(schedule_data_text, "%H:%M").strftime("%H:%M")}
           elif schedule_type == "weekly":
               days, time = schedule_data_text.split(",")
               schedule_data = {"days": [day.strip().capitalize() for day in days.split(",")], "time": datetime.strptime(time.strip(), "%H:%M").strftime("%H:%M")}
           elif schedule_type == "interval":
               interval_number, time = schedule_data_text.split(",")
               schedule_data = {"interval": int(interval_number.strip()), "time": datetime.strptime(time.strip(), "%H:%M").strftime("%H:%M")}

        except Exception as e:
           update.message.reply_text("Invalid schedule data format.")
           return ConversationHandler.END
    
        context.user_data["schedule_data"] = schedule_data

        reply_keyboard = [["Yes", "No"]]
        update.message.reply_text(f"Task Name: {context.user_data['task_name']}\nType: {context.user_data['schedule_type']}\nSchedule: {schedule_data_text}\n Confirm?",
           reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return ADD_REMINDER_CONFIRM

    def add_reminder_confirmation(update: Update, context: CallbackContext):
        if update.message.text == "Yes":
            user_id = context.user_data["user_id"]
            task_name = context.user_data["task_name"]
            schedule_type = context.user_data["schedule_type"]
            schedule_data = context.user_data["schedule_data"]
            user_data = get_user_data(user_id)
            if create_reminder(user_id, task_name, None, schedule_type, schedule_data, user_data[2]):
                update.message.reply_text("Reminder added.", reply_markup=ReplyKeyboardRemove())
            else:
                update.message.reply_text("Failed to add reminder.", reply_markup=ReplyKeyboardRemove())
        else:
            update.message.reply_text("Reminder creation cancelled.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    

    def list_reminders(update: Update, context: CallbackContext):
      if update.message.text == "List Reminders":
            user_id = update.effective_user.id
            reminders = get_reminders(user_id)

            if reminders:
                message = "Your reminders:\n"
                for reminder in reminders:
                    message += f"- {reminder[2]} ({json.loads(reminder[4])}, {json.dumps(json.loads(reminder[5]))})\n"
                update.message.reply_text(message)
            else:
                update.message.reply_text("You don't have any reminders yet.")
            return ConversationHandler.END
        else:
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
            schedule_type = json.loads(schedule_type)
            schedule_data = json.loads(schedule_data)
            now_utc = datetime.now(pytz.utc)
            user_timezone = pytz.timezone(time_zone)

            if schedule_type == "once":
                 reminder_date_time = datetime.fromisoformat(schedule_data["date_time"]).astimezone(pytz.utc)
                 if now_utc >= reminder_date_time:
                     send_telegram_notification(user_id, task_name, description)
                     update_reminder_status(reminder_id, 'completed')
            elif schedule_type == "daily":
                reminder_time = datetime.strptime(schedule_data['time'], "%H:%M").time()
                current_time = now_utc.astimezone(user_timezone).time()
                if (current_time.hour == reminder_time.hour and current_time.minute == reminder_time.minute):
                    send_telegram_notification(user_id, task_name, description)
            elif schedule_type == "weekly":
                 current_day = now_utc.strftime("%a").capitalize()
                 reminder_days = schedule_data.get('days')
                 reminder_time = datetime.strptime(schedule_data['time'], "%H:%M").time()
                 current_time = now_utc.astimezone(user_timezone).time()
                 if current_day in reminder_days and current_time.hour == reminder_time.hour and current_time.minute == reminder_time.minute:
                     send_telegram_notification(user_id, task_name, description)
            elif schedule_type == "interval":
                reminder_time = datetime.strptime(schedule_data['time'], "%H:%M").time()
                current_time = now_utc.astimezone(user_timezone).time()
                if (current_time.hour == reminder_time.hour and current_time.minute == reminder_time.minute):
                    send_telegram_notification(user_id, task_name, description)
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

# Helper function to check if a user exists
def get_user_data_flask(telegram_id):
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
        return None
    finally:
        conn.close()


# Helper function to get the reminders of the user
def get_reminders_flask(user_id):
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
        return None
    finally:
        conn.close()

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
        entry_points=[MessageHandler(Filters.text & ~Filters.command, add_reminder_start)],
        states={
            TASK_NAME: [MessageHandler(Filters.text & ~Filters.command, add_reminder_task_name)],
            SCHEDULE_TYPE: [MessageHandler(Filters.text & ~Filters.command, add_reminder_schedule_type)],
            SCHEDULE_DATA: [MessageHandler(Filters.text & ~Filters.command, add_reminder_schedule_data)],
            ADD_REMINDER_CONFIRM: [MessageHandler(Filters.text & ~Filters.command, add_reminder_confirmation)],
        },
        fallbacks=[CommandHandler('cancel', lambda update, context: update.message.reply_text('Cancelled', reply_markup=ReplyKeyboardRemove()))],
    )
    dispatcher.add_handler(add_reminder_conv_handler)
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command,list_reminders))
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("language", lambda update, context: context.bot.send_message(chat_id=update.effective_chat.id, text="This functionality is still not implemented.")))
    dispatcher.add_error_handler(lambda update, context, bot_obj: error(update, context, bot_obj))
    
    updater.start_polling()

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True, use_reloader=False)

if __name__ == '__main__':
    main()
