import requests
import json
import os
import urllib3
urllib3.disable_warnings()

API_KEY = os.environ.get("API_KEY", "D9BX98HE38P3RZQVCUU9NAKU1PI8RP53UN")
WALLET = "0x0Cf18469b589973707B605785516EC4f0de35979"
USDT0_CONTRACT = "0xc2132d05d31c914a87c6611c10748aeb04b58e8f"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8750629917:AAHfXl9Ovlkg-RJAu8g78B2F49AcSRbiFIY")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "540964914")
STATE_FILE = "state.json"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        r = requests.post(url, json=data, timeout=10)
        return r.ok
    except Exception as e:
        print(f"Errore Telegram: {e}")
        return False

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_block": 0}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def get_current_block():
    url = f"https://api.etherscan.io/v2/api?chainid=137&module=proxy&action=eth_blockNumber&apikey={API_KEY}"
    r = requests.get(url, verify=False, timeout=10)
    return int(r.json().get("result", "0x0"), 16)

def get_latest_transfers(from_block):
    url = (f"https://api.etherscan.io/v2/api?chainid=137"
           f"&module=account&action=tokentx"
           f"&address={WALLET}"
           f"&contractaddress={USDT0_CONTRACT}"
           f"&startblock={from_block}"
           f"&endblock=99999999"
           f"&sort=asc&apikey={API_KEY}")
    try:
        r = requests.get(url, verify=False, timeout=15)
        data = r.json()
        result = data.get("result", [])
        if not isinstance(result, list):
            return []
        return result
    except Exception as e:
        print(f"Errore API: {e}")
        return []

def format_amount(value, decimals):
    try:
        amount = int(value) / (10 ** int(decimals))
        return f"{amount:,.2f}"
    except:
        return value

if __name__ == "__main__":
    state = load_state()

    if state["last_block"] == 0:
        current_block = get_current_block()
        state["last_block"] = current_block
        save_state(state)
        print(f"Primo avvio - blocco attuale: {current_block}")
        exit(0)

    transfers = get_latest_transfers(state["last_block"] + 1)
    new_max_block = state["last_block"]

    for tx in transfers:
        block = int(tx.get("blockNumber", 0))
        to_addr = tx.get("to", "").lower()

        if to_addr == WALLET.lower():
            amount = format_amount(tx.get("value", "0"), tx.get("tokenDecimal", "6"))
            from_addr = tx.get("from", "")
            tx_hash = tx.get("hash", "")
            token = tx.get("tokenSymbol", "USDT0")

            msg = (f"Nuova transazione in entrata!\n\n"
                   f"Importo: {amount} {token}\n"
                   f"Da: {from_addr}\n"
                   f"A: {WALLET}\n"
                   f"TX: https://polygonscan.com/tx/{tx_hash}\n"
                   f"Blocco: {block}")

            sent = send_telegram(msg)
            if sent:
                print(f"Notifica inviata! Importo: {amount} {token}")
            else:
                print(f"Errore invio notifica per TX {tx_hash}")

        if block > new_max_block:
            new_max_block = block

    if new_max_block > state["last_block"]:
        state["last_block"] = new_max_block
        save_state(state)

    print(f"Controllo completato - ultimo blocco: {state['last_block']}")