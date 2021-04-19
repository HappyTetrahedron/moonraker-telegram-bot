import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
import sys

from numpy import random

from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

import websocket

try:
    import thread
except ImportError:
    import _thread as thread
import time

import json
import urllib.request

from urllib.request import urlopen
from PIL import Image
from io import BytesIO
import cv2
from pyhocon import ConfigFactory
import emoji
import threading

# Enable logging
logging.basicConfig(
    handlers=[
        RotatingFileHandler(os.path.join('/tmp/', 'telegram.log'), maxBytes=100000, backupCount=1),
        logging.StreamHandler(sys.stdout)
    ],
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# some global params
myId = random.randint(300000)
host = "localhost"
cameraEnabled = True
cameraHost = "localhost:8080"
chatId = 12341234
notify_percent = 5
notify_heigth = 5
flipVertically = False
flipHorisontally = False
gifDuration = 5
reduceGif = 2
poweroff_device: str
debug = False

klippy_connected: bool = False
klippy_printing: bool = False
ws: websocket.WebSocketApp

last_notify_heigth: int = 0
last_notify_percent: int = 0


# Define a few command handlers. These usually take the two arguments update and
# context. Error handlers also receive the raised TelegramError object in error.
def help_command(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('The following commands are known:\n\n'
                              '/status - send klipper status\n'
                              '/pause - pause printing\n'
                              '/resume - resume printing\n'
                              '/cancel - cancel printing\n'
                              '/photo - capture & send me a photo\n'
                              '/gif - let\'s make some gif from printer cam\n'
                              '/video - will take mp4 video from camera\n'
                              '/poweroff - turn off moonraker power device from config')


def echo(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(f"unknown command: {update.message.text}")


def info(update: Update, context: CallbackContext) -> None:
    response = urllib.request.urlopen(f"http://{host}/printer/info")
    update.message.reply_text(json.loads(response.read()))


def reset_notifications() -> None:
    global last_notify_percent
    last_notify_percent = 0
    global last_notify_heigth
    last_notify_heigth = 0


def get_status() -> str:
    response = urllib.request.urlopen(
        f"http://{host}/printer/objects/query?webhooks&print_stats=filename,total_duration,print_duration,filament_used,state,message")
    resp = json.loads(response.read())
    print_stats = resp['result']['status']['print_stats']
    webhook = resp['result']['status']['webhooks']
    total_time = time.strftime("%H:%M:%S", time.gmtime(print_stats['total_duration']))
    message = emoji.emojize(':robot: Printer status: ') + f"{webhook['state']} \n"
    if 'state_message' in webhook:
        message += f"State message: {webhook['state_message']}\n"
    message += emoji.emojize(':mechanical_arm: Printing process status: ') + f"{print_stats['state']} \n"
    if print_stats['state'] in ('printing', 'paused', 'complete'):
        message += f"Print time: {total_time} \n" \
                   f"Printing filename: {print_stats['filename']} \n" \
                   f"Used filament: {round(print_stats['filament_used'] / 1000, 2)}m"
    return message


def status(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(get_status())


def notify(bot, message):
    if not klippy_printing:
        return
    if cameraEnabled:
        bot.send_photo(chatId, photo=take_photo(), caption=message)
    else:
        bot.send_message(chatId, text=message)


def take_photo() -> BytesIO:
    cap = cv2.VideoCapture(cameraHost)
    success, image = cap.read()

    if not success:
        img = Image.open(urlopen('http://r.ddmcdn.com/s_f/o_1/APL/uploads/2014/10/nyan-cat-01-625x450.jpg', timeout=5))
    else:
        img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        if flipVertically:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        if flipHorisontally:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)

    cap.release()
    cv2.destroyAllWindows()

    bio = BytesIO()
    bio.name = 'status.jpeg'
    img.save(bio, 'JPEG')
    bio.seek(0)
    return bio


def process_frame(frame, width, height) -> Image:
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    if flipVertically:
        image = image.transpose(Image.FLIP_TOP_BOTTOM)
    if flipHorisontally:
        image = image.transpose(Image.FLIP_LEFT_RIGHT)
    if reduceGif > 0:
        image = image.resize((int(width / reduceGif), int(height / reduceGif)))
    return image


def get_photo(update: Update, context: CallbackContext) -> None:
    update.message.reply_photo(photo=take_photo())


def get_gif(update: Update, context: CallbackContext) -> None:
    if not cameraEnabled:
        update.message.reply_text("camera is disabled")
        return

    gif = []
    cap = cv2.VideoCapture(cameraHost)
    success, image = cap.read()

    if not success:
        update.message.reply_text("camera connection failed!")
        return

    height, width, channels = image.shape
    gif.append(process_frame(image, width, height))

    fps = 0
    t_end = time.time() + gifDuration
    # TOdo: calc frame count
    while success and time.time() < t_end:
        prev_frame_time = time.time()
        success, image_inner = cap.read()
        new_frame_time = time.time()
        gif.append(process_frame(image_inner, width, height))
        fps = 1 / (new_frame_time - prev_frame_time)

    cap.release()
    cv2.destroyAllWindows()

    bio = BytesIO()
    bio.name = 'image.gif'
    gif[0].save(bio, format='GIF', save_all=True, optimize=True, append_images=gif[1:], duration=int(1000 / int(fps)),
                loop=0)
    bio.seek(0)
    update.message.reply_animation(animation=bio, width=width, height=height, timeout=60, disable_notification=True,
                                   caption=get_status())

    if debug:
        update.message.reply_text(f"measured fps is {fps}", disable_notification=True)


def process_video_frame(frame):
    if flipVertically and flipHorisontally:
        frame = cv2.flip(frame, -1)
    elif flipHorisontally:
        frame = cv2.flip(frame, 1)
    elif flipVertically:
        frame = cv2.flip(frame, 0)

    return frame


def get_video(update: Update, context: CallbackContext) -> None:
    if not cameraEnabled:
        update.message.reply_text("camera is disabled")
        return

    cap = cv2.VideoCapture(cameraHost)
    success, frame = cap.read()

    if not success:
        update.message.reply_text("camera connection failed!")
        return

    height, width, channels = frame.shape
    fps_video = cap.get(cv2.CAP_PROP_FPS)
    fps = 10
    filepath = os.path.join('/tmp/', 'video.mp4')
    out = cv2.VideoWriter(filepath, fourcc=cv2.VideoWriter_fourcc(*'mp4v'), fps=fps_video, frameSize=(width, height))
    t_end = time.time() + gifDuration * 2
    while success and time.time() < t_end:
        prev_frame_time = time.time()
        success, frame_inner = cap.read()
        out.write(process_video_frame(frame_inner))
        fps = 1 / (time.time() - prev_frame_time)

    cap.release()
    out.set(cv2.CAP_PROP_FPS, fps)
    out.release()
    cv2.destroyAllWindows()

    bio = BytesIO()
    bio.name = 'video.mp4'
    with open(filepath, 'rb') as fh:
        bio.write(fh.read())

    os.remove(filepath)
    bio.seek(0)
    update.message.reply_video(video=bio, width=width, height=height)
    if debug:
        update.message.reply_text(f"measured fps is {fps}, video fps {fps_video}", disable_notification=True)


def manage_printing(command: str) -> None:
    ws.send(json.dumps({"jsonrpc": "2.0", "method": f"printer.print.{command}", "id": myId}))


def pause_printing(update: Update, context: CallbackContext) -> None:
    manage_printing('pause')


def resume_printing(update: Update, context: CallbackContext) -> None:
    manage_printing('resume')


def cancel_printing(update: Update, context: CallbackContext) -> None:
    manage_printing('cancel')


def power_off(update: Update, context: CallbackContext) -> None:
    if poweroff_device:
        ws.send(json.dumps({"jsonrpc": "2.0", "method": "machine.device_power.off", "id": myId,
                            "params": {f"{poweroff_device}": None}}))
    else:
        update.message.reply_text("No power device in config!")


def start_bot(token):
    # Create the Updater and pass it your bot's token.
    updater = Updater(token, workers=1)  # we have too small ram on oPi zero...

    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher

    # on different commands - answer in Telegram
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("status", status))
    dispatcher.add_handler(CommandHandler("photo", get_photo))
    dispatcher.add_handler(CommandHandler("gif", get_gif))
    dispatcher.add_handler(CommandHandler("video", get_video))
    dispatcher.add_handler(CommandHandler("pause", pause_printing))
    dispatcher.add_handler(CommandHandler("resume", resume_printing))
    dispatcher.add_handler(CommandHandler("cancel", cancel_printing))
    dispatcher.add_handler(CommandHandler("poweroff", power_off))

    # on noncommand i.e message - echo the message on Telegram
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))

    # Start the Bot
    updater.start_polling()

    return updater


def on_error(ws, error):
    logger.error(error)


def on_close(ws):
    logger.info("### ws closed ###")


def subscribe(ws):
    ws.send(
        json.dumps({"jsonrpc": "2.0",
                    "method": "printer.objects.subscribe",
                    "params": {
                        "objects": {
                            "print_stats": ["filename", "state"],
                            "display_status": ['progress', 'message'],
                            'toolhead': ['position'],
                            'gcode_move': ['absolute_coordinates', 'position', 'gcode_position']
                        }
                    },
                    "id": myId}))


def on_open(ws):
    ws.send(
        json.dumps({"jsonrpc": "2.0",
                    "method": "printer.info",
                    "id": myId}))


def reshedule():
    while True:
        if not klippy_connected and ws.keep_running is True:
            on_open(ws)
        time.sleep(1)


def websocket_to_message(ws_message, botUpdater):
    json_message = json.loads(ws_message)
    if debug:
        print(ws_message)
        logger.debug(ws_message)

    # логика таймлапсы:
    # делаем фото по событию и потом отсылаем видос. фото делаем по команде/по изменению высоты на констатнту/по увелечинею высоты. параметр надо в конфиг кинуть.
    # получить высоту слоя из слайсера. в чём проблема делать фото только когда дельта равна 0.2
    # {"jsonrpc": "2.0", "method": "notify_gcode_response", "params": ["prefix message"]} - RESPOND PREFIX=prefix MSG=message
    # "gcode_move": {"position": [18.0, 42.0, 0.0, 0.0], "gcode_position": [18.0, 42.0, 0.0, 0.0]}}]}
    if 'error' in json_message:
        return

    if 'id' in json_message and 'result' in json_message:
        if 'status' in json_message['result']:
            return
        if 'state' in json_message['result']:
            global klippy_connected
            if json_message['result']['state'] == 'ready':
                klippy_connected = True
                subscribe(ws)
            else:
                klippy_connected = False
            return
        botUpdater.bot.send_message(chatId, text=f"{json_message['result']}")
    if 'id' in json_message and 'error' in json_message:
        botUpdater.bot.send_message(chatId, text=f"{json_message['error']['message']}")

    # if json_message["method"] == "notify_gcode_response":
    #     val = ws_message["params"][0]
    #     # Todo: add global state for mcu disconnects!
    #     if 'Lost communication with MCU' not in ws_message["params"][0]:
    #         botUpdater.dispatcher.bot.send_message(chatId, ws_message["params"])
    #
    if json_message["method"] in ["notify_klippy_shutdown", "notify_klippy_disconnected"]:
        logger.warning(f"klippy disconnect detected with message: {json_message['method']}")
        klippy_connected = False

    if json_message["method"] == "notify_status_update":
        if 'display_status' in json_message["params"][0]:
            progress = int(json_message["params"][0]['display_status']['progress'] * 100)
            global last_notify_percent
            if progress < last_notify_percent - notify_percent:
                last_notify_percent = progress
            if notify_percent != 0 and progress % notify_percent == 0 and progress > last_notify_percent:
                notify(botUpdater.bot, f"Printed {progress}%")
                last_notify_percent = progress
        if 'toolhead' in json_message["params"][0] and 'position' in json_message["params"][0]['toolhead']:
            position = json_message["params"][0]['toolhead']['position'][2]
            # TOdo: detect not printing moves. maybe near homming position!
            global last_notify_heigth
            if int(position) < last_notify_heigth - notify_heigth:
                last_notify_heigth = int(position)
            if notify_heigth != 0 and int(position) % notify_heigth == 0 and int(position) > last_notify_heigth:
                notify(botUpdater.bot, f"Printed {round(position, 2)}mm")
                last_notify_heigth = int(position)
        if 'print_stats' in json_message['params'][0]:
            message = ""
            state = ""
            filename = ""
            if 'filename' in json_message['params'][0]['print_stats']:
                filename = json_message['params'][0]['print_stats']['filename']
            if 'state' in json_message['params'][0]['print_stats']:
                state = json_message['params'][0]['print_stats']['state']
            # Fixme: reset notify percent & heigth on finish/caancel/start
            global klippy_printing
            if state == "printing":
                klippy_printing = True
                if state and filename:
                    message += f"Printer started printing: {filename} \n"
                    reset_notifications()
            elif state:
                klippy_printing = False
                message += f"Printer state change: {json_message['params'][0]['print_stats']['state']} \n"

            if message:
                botUpdater.bot.send_message(chatId, text=message)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Moonraker Telegram Bot")
    parser.add_argument(
        "-c", "--configfile", default="application.conf",
        metavar='<configfile>',
        help="Location of moonraker tlegram bot configuration file")
    system_args = parser.parse_args()

    conf = ConfigFactory.parse_file(system_args.configfile)
    host = conf.get_string('server', 'localhost')
    token = conf.get_string('bot_token')
    chatId = conf.get_string('chat_id')
    notify_percent = conf.get_int('notify.percent', 5)
    notify_heigth = conf.get_int('notify.heigth', 5)
    cameraEnabled = conf.get_bool('camera.enabled', True)
    flipHorisontally = conf.get_bool('camera.flipHorisontally', False)
    flipVertically = conf.get_bool('camera.flipVertically', False)
    gifDuration = conf.get_int('camera.gifDuration', 5)
    reduceGif = conf.get_int('camera.reduceGif', 0)
    cameraHost = conf.get_string('camera.host', f"http://{host}:8080/?action=stream")
    poweroff_device = conf.get_string('poweroff_device', "")
    debug = conf.get_bool('debug', False)

    botUpdater = start_bot(token)


    # websocket communication
    def on_message(ws, message):
        websocket_to_message(message, botUpdater)


    if debug:
        websocket.enableTrace(True)

    ws = websocket.WebSocketApp(f"ws://{host}/websocket", on_message=on_message, on_error=on_error, on_close=on_close)
    ws.on_open = on_open

    botUpdater.bot.send_message(chatId, text=get_status())

    threading.Thread(target=reshedule, daemon=True).start()

    ws.run_forever(ping_interval=10, ping_timeout=2)
    logger.info("Exiting! Moonraker connection lost!")
    botUpdater.stop()
