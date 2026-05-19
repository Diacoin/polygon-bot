FROM python:3.11-slim

# Forza output non bufferizzato — i log appaiono subito in Railway
ENV PYTHONUNBUFFERED=1

# Il codice sta in /src
WORKDIR /src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

CMD ["python", "-u", "bot.py"]
