import requests
import json
import os
import urllib3
from datetime import datetime, timezone
urllib3.disable_warnings()

API_KEY = "D9BX98HE38P3RZQVCUU9NAKU1PI8RP53UN"
WALLET = "0x0Cf18469b589973707B605785516EC4f0de35979"
USDT0_CONTRACT = "0xc2132d05d31c914a87c6611c10748aeb04b58e8f"
TELEGRAM_TOKEN = "8750629917:AAHfXl9Ovlkg-RJAu8g78B2F49AcSRbiFIY"
TELEGRAM_CHAT_ID = "540964914"
GH_PAT = os.environ.get("GH_PAT", "")
REPO = os.environ.get("REPO", "Diacoin/polygon-bot")
LAST_BLOCK = int(os.environ.get("LAST_BLOCK", "0"))

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        r = requests.post(url, json=data, timeout=10)
        return r.ok
    except Exception as e:
        print(f"Errore Telegram: {e}")
        return False

def update_github_variable(value):
    url = f"https://api.github.com/repos/{REPO}/actions/variables/LAST_BLOCK"
    headers = {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json"
    }
    data = {"name": "LAST_BLOCK", "value": str(value)}
    r = requests.patch(url, headers=headers, json=data, timeout=10)
    print(f"Update LAST_BLOCK to {value}: {r.status_code}")
    return r.ok

def get_current_block():
    url = f"https://api.etherscan.io/v2/api?chainid=137&module=proxy&action=eth_blockNumber&apikey={API_KEY}"
    r = requests.get(url, verify=False, timeout=10)
    return int(r.json().get("result", "0x0"), 16)

def get_usdt0_balance():
    url = (f"https://api.etherscan.io/v2/api?chainid=137"
           f"&module=account&action=tokenbalance"
           f"&contractaddress={USDT0_CONTRACT}"
           f"&address={WALLET}"
           f"&tag=latest&apikey={API_KEY}")
    try:
        r = requests.get(url, verify=False, timeout=10)
        result = r.json().get("result", "0")
        balance = int(result) / 1_000_000
        return f"{balance:,.2f}"
    except:
        return "N/A"

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
    print(f"LAST_BLOCK: {LAST_BLOCK}")

    if LAST_BLOCK == 0:
        current_block = get_current_block()
        update_github_variable(current_block)
        print(f"Primo avvio - blocco attuale: {current_block}")
        exit(0)

    transfers = get_latest_transfers(LAST_BLOCK + 1)
    print(f"Trasferimenti trovati dal blocco {LAST_BLOCK+1}: {len(transfers)}")
    new_max_block = LAST_BLOCK

    for tx in transfers:
        block = int(tx.get("blockNumber", 0))
        to_addr = tx.get("to", "").lower()

        if to_addr == WALLET.lower():
            amount = format_amount(tx.get("value", "0"), tx.get("tokenDecimal", "6"))
            from_addr = tx.get("from", "")
            tx_hash = tx.get("hash", "")
            token = tx.get("tokenSymbol", "USDT0")
            timestamp = int(tx.get("timeStamp", 0))
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            dt_str = dt.strftime("%d/%m/%Y %H:%M:%S UTC")
            balance = get_usdt0_balance()

            msg = (f"Nuova transazione in entrata!\n\n"
                   f"Importo: {amount} {token}\n"
                   f"Saldo attuale: {balance} {token}\n"
                   f"Data: {dt_str}\n"
                   f"Da: {from_addr}\n"
                   f"TX: https://polygonscan.com/tx/{tx_hash}\n"
                   f"Blocco: {block}")

            sent = send_telegram(msg)
            if sent:
                print(f"Notifica inviata! Importo: {amount} {token}")
            else:
                print(f"Errore invio notifica per TX {tx_hash}")

        if block > new_max_block:
            new_max_block = block

    if new_max_block > LAST_BLOCK:
        update_github_variable(new_max_block)

    print(f"Controllo completato - ultimo blocco: {new_max_block}")