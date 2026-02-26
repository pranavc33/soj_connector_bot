import pandas as pd
import zipfile
import wave
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from google import genai
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import os

# Pull the keys from the environment variables instead of hardcoding them
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# Initialize Gemini Client using the variable
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is awake!")
        
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        
def load_gtfs():
    with zipfile.ZipFile('gtfs.zip') as z:
        routes = pd.read_csv(z.open('routes.txt'))
        trips = pd.read_csv(z.open('trips.txt'))
        stop_times = pd.read_csv(z.open('stop_times.txt'))
        stops = pd.read_csv(z.open('stops.txt'))
        calendar = pd.read_csv(z.open('calendar.txt'))
    return routes, trips, stop_times, stops, calendar

routes, trips, stop_times, stops, calendar = load_gtfs()

SOJ_ROUTE_ID = 76138

def get_next_bus(stop_name_query):
    matched_stops = stops[
        stops['stop_name'].str.contains(stop_name_query, case=False, na=False)
    ]

    if len(matched_stops) == 0:
        return f"Could not find a stop matching '{stop_name_query}'. Try: Burke, Blunt, Tuck Circle, Dartmouth Hall, DMS-Vail."

    stop_id = matched_stops.iloc[0]['stop_id']
    stop_name = matched_stops.iloc[0]['stop_name']

    now = datetime.now()
    current_time = now.strftime('%H:%M:%S')
    day_of_week = now.strftime('%A').lower()

    if day_of_week not in calendar.columns:
        return "Could not determine today's schedule"

    active_services = calendar[calendar[day_of_week] == 1]['service_id']

    soj_trips = trips[
        (trips['route_id'] == SOJ_ROUTE_ID) &
        (trips['service_id'].isin(active_services))
    ]

    if len(soj_trips) == 0:
        return "No buses running today"

    upcoming = stop_times[
        (stop_times['stop_id'] == stop_id) &
        (stop_times['trip_id'].isin(soj_trips['trip_id'])) &
        (stop_times['departure_time'] > current_time)
    ].sort_values('departure_time')

    if len(upcoming) == 0:
        return f"No more buses today from {stop_name}"

    next_buses = upcoming['departure_time'].unique()[:3].tolist()

    response = f"Next buses from {stop_name}:\n"

    for i, bus_time in enumerate(next_buses):
        try:
            hour, minute, second = bus_time.split(':')
            hour_int = int(hour) % 24
            period = 'AM' if hour_int < 12 else 'PM'
            display_hour = hour_int % 12 or 12
            formatted = f"{display_hour}:{minute} {period}"
        except:
            formatted = bus_time

        if i == 0:
            try:
                h = int(bus_time.split(':')[0]) % 24
                m = bus_time.split(':')[1]
                s = bus_time.split(':')[2]
                next_dt = datetime.strptime(f"{h}:{m}:{s}", '%H:%M:%S').replace(
                    year=now.year, month=now.month, day=now.day
                )
                minutes_away = int((next_dt - now).total_seconds() / 60)
                response += f"-> {formatted} ({minutes_away} min away)\n"
            except:
                response += f"-> {formatted}\n"
        else:
            response += f"-> {formatted}\n"

    return response

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    stop_keywords = ['burke', 'blunt', 'tuck', 'dartmouth', 'dhmc', 'webster',
                     'berry', 'vail', 'coop', 'brockway', 'sachem', 'maynard',
                     'irving', 'post office', 'transit hub', 'summit', 'juniper']

    matched_keyword = None
    for keyword in stop_keywords:
        if keyword in text.lower():
            matched_keyword = keyword
            break

    if matched_keyword:
        result = get_next_bus(matched_keyword)
        await update.message.reply_text(result)

        try:
            speech_text = result.replace('->', '').replace('\n', '. ')
            
            # Call Gemini's TTS model
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash-preview-tts",
                contents=speech_text,
                config={"response_modalities": ["AUDIO"]}
            )
            
            # Extract raw audio bytes
            audio_data = response.candidates[0].content.parts[0].inline_data.data
            audio_path = "reply.wav"
            
            # Save as a .wav file so Telegram can send it
            with wave.open(audio_path, "wb") as wf:
                wf.setnchannels(1)       # Mono
                wf.setsampwidth(2)       # 16-bit
                wf.setframerate(24000)   # 24kHz sample rate
                wf.writeframes(audio_data)
                
            await update.message.reply_voice(voice=open(audio_path, "rb"))
            
        except Exception as e:
            print(f"TTS error: {e}")
    else:
        await update.message.reply_text(
            "Tell me which stop you're at. For example:\n"
            "Burke\n"
            "Tuck Circle\n"
            "Dartmouth Hall\n"
            "Summit"
        )

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is awake!")

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), DummyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

def main():
    keep_alive()
    print("Bot is running...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    main()
