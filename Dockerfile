FROM python:3.11-slim

# Il codice sta in /src — il volume Railway monta /app per lo state
WORKDIR /src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

CMD ["python", "bot.py"]
