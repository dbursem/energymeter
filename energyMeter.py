#!/usr/bin/python3

import datetime
from dateutil import easter
from dotenv import load_dotenv
import os
import requests
import RPi.GPIO as GPIO
import signal
import sys
import time
import logging

# is_low_tariff will return true on Easter monday, Ascension day, Pentecost monday, and the dates specified here.
LOW_TARIFF_DAYS = [
    [1, 1],  # new years day
    [4, 27],  # Kingsday
    [12, 25],  # Christmas
    [12, 26],  # Christmas
]
LOW_TARIFF_START = datetime.time(23, 00)
LOW_TARIFF_END = datetime.time(7, 00)

load_dotenv()
ENERGY_PER_PULSE = int(os.getenv("ENERGY_PER_PULSE"))
PULSE_METER_PIN = int(os.getenv("PULSE_METER_PIN"))
INTERRUPT_BUTTON_PIN = int(os.getenv("INTERRUPT_BUTTON_PIN"))
INFLUX_ADDRESS = os.getenv("INFLUX_ADDRESS")
INFLUX_SERIES = os.getenv("INFLUX_SERIES")
INFLUX_METER_HIGH = os.getenv("INFLUX_METER_HIGH")
INFLUX_METER_LOW = os.getenv("INFLUX_METER_LOW")

log_level = logging.INFO
try:
    if sys.argv[1] == 'debug':
        log_level = logging.DEBUG
except IndexError:
    pass

logging.basicConfig(filename="/var/log/energymeter.log", level=log_level)

last_pulse_time = 0
timestamps = []
message_body = ''


def shutdown(signal, frame=None):
    logging.debug("halting due to {} event".format(signal), True)
    GPIO.cleanup()
    global message_body
    file = open("message_body.txt", 'a')
    file.write(message_body)
    file.close()
    sys.exit(0)


def handle_interrupt(pin):
    global timestamps
    timestamps.append(time.time_ns())
    logging.debug("interrupt handled")


def send_message():
    global message_body
    if message_body == "":
        return

    logging.debug("Trying to send message body..")
    try:
        r = requests.post(INFLUX_ADDRESS, data=message_body)
    except requests.exceptions.RequestException as e:
        logging.error("Encountered exception during request: {:d}".format(e))
        return

    if r.status_code != 204:
        logging.error("Unexpected status code: {}".format(r.status_code))
        return

    logging.debug("Message body successfully sent")
    message_body = ""


def loop():
    global last_pulse_time, message_body
    try:
        pulse_time = timestamps.pop(0)
    except IndexError:
        send_message()
        time.sleep(10)
        return

    timestamp_date_time = datetime.datetime.fromtimestamp(int(pulse_time / 10 ** 9))

    meter = (INFLUX_METER_HIGH, INFLUX_METER_LOW)[is_low_tariff(timestamp_date_time)]

    interval = pulse_time - last_pulse_time
    power = 10 ** 9 * ENERGY_PER_PULSE / interval  # interval is in nanoseconds, hence the factor 10^9
    message = "{:s},meter={:s} value=1,power={:.2f} {:d}\n".format(INFLUX_SERIES, meter, power, pulse_time)
    message_body += message
    logging.debug("Added string to message body: {:d}".format(message))
    last_pulse_time = pulse_time


def is_low_tariff(datetime_to_check: datetime.datetime) -> bool:
    # check for low tariff hours
    if datetime_to_check.time() > LOW_TARIFF_START or datetime_to_check.time() < LOW_TARIFF_END:
        return True

    date_to_check = datetime_to_check.date()

    # check for weekend days
    if date_to_check.weekday() == 5 or date_to_check.weekday() == 6:
        return True

    # check for fixed low-tarif days
    for date in LOW_TARIFF_DAYS:
        if datetime.date(date_to_check.year, date[0], date[1]) == date_to_check:
            return True

    # check for easter monday
    easter_day = easter.easter(date_to_check.year)
    if easter_day + datetime.timedelta(days=1) == date_to_check:
        return True

    # check for ascension day
    ascension_day = easter_day + datetime.timedelta(days=39)
    if ascension_day == date_to_check:
        return True

    # check for pentecost day
    pentecost_day = ascension_day + datetime.timedelta(days=10)
    if pentecost_day + datetime.timedelta(days=1) == date_to_check:
        return True

    # all other cases
    return False


GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

GPIO.setup(PULSE_METER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(PULSE_METER_PIN, GPIO.FALLING, callback=handle_interrupt, bouncetime=50)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)
GPIO.setup(INTERRUPT_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(INTERRUPT_BUTTON_PIN, GPIO.FALLING, callback=shutdown, bouncetime=100)

while True:
    loop()
