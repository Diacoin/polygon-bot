FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONFAULTHANDLER=1

WORKDIR /src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Verifica che il file esista e che python funzioni prima di avviare
RUN python -c "import requests, urllib3; print('imports OK')"

CMD ["python", "-u", "bot.py"]
