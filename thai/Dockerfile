FROM python:3.12-slim

# ffmpeg склеивает записи озвучки и сдвигает высоту голоса для «разных дикторов» (фильтр rubberband:
# у ffmpeg из Debian он есть, отдельный пакет не нужен)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# данные (course.json, озвучка, база прогресса) и логи живут в томах: /app/data и /app/logs
CMD ["python", "bot.py"]
