import requests
import json
import os
import time
import urllib3
from datetime import datetime, timezone

urllib3.disable_warnings()

API_KEY      = os.environ.get("POLYGONSCAN_API_KEY", "D9BX98HE38P3RZQVCUU9NAKU1PI8RP53UN")
WALLET       = os.environ.get("WALLET", "0x0Cf18469b589973707B605785516EC4f0de35979")
USDT0_CONTRACT = "0xc2132d05d31c914a87c6611c10748aeb04b58e8f"
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "8750629917:AAHfXl9Ovlkg-RJAu8g78B2F49AcSRbiFIY")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "540964914")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))  # secondi
STATE_FILE    = "state.json"


# ---------------------------------------------------------------------------
# Stato persistente
# ---------------------------------------------------------------------------

def load_last_block() -> int:
    """Carica last_block da file locale, poi da env var, poi usa il blocco attuale."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
                val = int(data.get("last_block", 0))
                if val > 0:
                    print(f"[stato] last_block caricato dal file: {val}")
                    return val
        except Exception as e:
            print(f"[stato] errore lettura {STATE_FILE}: {e}")

    env_val = int(os.environ.get("LAST_BLOCK", "0"))
    if env_val > 0:
        print(f"[stato] last_block caricato da env: {env_val}")
        return env_val

    current = get_current_block()
    print(f"[stato] primo avvio — parto dal blocco attuale: {current}")
    save_last_block(current)
    return current


def save_last_block(block: int):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"last_block": block}, f)
    except Exception as e:
        print(f"[stato] errore scrittura {STATE_FILE}: {e}")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
        return r.ok
    except Exception as e:
        print(f"[telegram] errore: {e}")
        return False


def get_current_block() -> int:
    url = (f"https://api.etherscan.io/v2/api?chainid=137"
           f"&module=proxy&action=eth_blockNumber&apikey={API_KEY}")
    try:
        r = requests.get(url, verify=False, timeout=10)
        return int(r.json().get("result", "0x0"), 16)
    except Exception as e:
        print(f"[api] errore get_current_block: {e}")
        return 0


def get_usdt0_balance() -> str:
    url = (f"https://api.etherscan.io/v2/api?chainid=137"
           f"&module=account&action=tokenbalance"
           f"&contractaddress={USDT0_CONTRACT}"
           f"&address={WALLET}&tag=latest&apikey={API_KEY}")
    try:
        r = requests.get(url, verify=False, timeout=10)
        balance = int(r.json().get("result", "0")) / 1_000_000
        return f"{balance:,.2f}"
    except Exception as e:
        print(f"[api] errore get_usdt0_balance: {e}")
        return "N/A"


def get_latest_transfers(from_block: int) -> list:
    url = (f"https://api.etherscan.io/v2/api?chainid=137"
           f"&module=account&action=tokentx"
           f"&address={WALLET}"
           f"&contractaddress={USDT0_CONTRACT}"
           f"&startblock={from_block}&endblock=99999999"
           f"&sort=asc&apikey={API_KEY}")
    try:
        r = requests.get(url, verify=False, timeout=15)
        result = r.json().get("result", [])
        return result if isinstance(result, list) else []
    except Exception as e:
        print(f"[api] errore get_latest_transfers: {e}")
        return []


def format_amount(value: str, decimals: str) -> str:
    try:
        return f"{int(value) / (10 ** int(decimals)):,.2f}"
    except Exception:
        return value


# ---------------------------------------------------------------------------
# Ciclo principale
# ---------------------------------------------------------------------------

def check_once(last_block: int) -> int:
    transfers = get_latest_transfers(last_block + 1)
    print(f"[check] dal blocco {last_block + 1}: {len(transfers)} trasferimenti trovati")

    new_max_block = last_block
    for tx in transfers:
        block = int(tx.get("blockNumber", 0))
        to_addr = tx.get("to", "").lower()

        if to_addr == WALLET.lower():
            amount   = format_amount(tx.get("value", "0"), tx.get("tokenDecimal", "6"))
            from_addr = tx.get("from", "")
            tx_hash  = tx.get("hash", "")
            token    = tx.get("tokenSymbol", "USDT0")
            dt_str   = datetime.fromtimestamp(
                int(tx.get("timeStamp", 0)), tz=timezone.utc
            ).strftime("%d/%m/%Y %H:%M:%S UTC")
            balance  = get_usdt0_balance()

            msg = (
                f"Nuova transazione in entrata!\n\n"
                f"Importo: {amount} {token}\n"
                f"Saldo attuale: {balance} {token}\n"
                f"Data: {dt_str}\n"
                f"Da: {from_addr}\n"
                f"TX: https://polygonscan.com/tx/{tx_hash}\n"
                f"Blocco: {block}"
            )
            if send_telegram(msg):
                print(f"[telegram] notifica inviata — {amount} {token} (blocco {block})")
            else:
                print(f"[telegram] errore invio per TX {tx_hash}")

        if block > new_max_block:
            new_max_block = block

    return new_max_block


def main():
    print(f"[avvio] Bot USDT0 Polygon — wallet {WALLET}")
    print(f"[avvio] Polling ogni {POLL_INTERVAL}s")
    send_telegram("Bot USDT0 avviato su Railway.")

    last_block = load_last_block()

    while True:
        try:
            new_block = check_once(last_block)
            if new_block > last_block:
                last_block = new_block
                save_last_block(last_block)
                print(f"[stato] last_block aggiornato: {last_block}")
        except Exception as e:
            print(f"[errore] ciclo principale: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
