FROM python:3.12-slim

# ffmpeg склеивает записи озвучки; шрифт с кириллицей нужен build.py для картинок азбуки
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# данные (bank.json, озвучка, база прогресса) и логи живут в томах: /app/data и /app/logs
CMD ["python", "bot.py"]
