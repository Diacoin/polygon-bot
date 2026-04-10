import requests
import json
import os
import time
import urllib3
from datetime import datetime, timezone

urllib3.disable_warnings()

API_KEY          = os.environ.get("POLYGONSCAN_API_KEY", "D9BX98HE38P3RZQVCUU9NAKU1PI8RP53UN")
WALLET           = os.environ.get("WALLET", "0x0Cf18469b589973707B605785516EC4f0de35979")
USDT0_CONTRACT   = "0xc2132d05d31c914a87c6611c10748aeb04b58e8f"
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "8750629917:AAHfXl9Ovlkg-RJAu8g78B2F49AcSRbiFIY")
TELEGRAM_CHAT_IDS = [
    cid.strip()
    for cid in os.environ.get("TELEGRAM_CHAT_IDS", "540964914").split(",")
    if cid.strip()
]
POLL_INTERVAL    = int(os.environ.get("POLL_INTERVAL", "60"))   # secondi
RAILWAY_TOKEN    = os.environ.get("RAILWAY_TOKEN", "")
STATE_FILE       = "state.json"
WEEKLY_SECONDS   = 7 * 24 * 3600


# ---------------------------------------------------------------------------
# Stato persistente
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception as e:
            print(f"[stato] errore lettura {STATE_FILE}: {e}")
    return {}


def save_state(data: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[stato] errore scrittura {STATE_FILE}: {e}")


def load_last_block(state: dict) -> int:
    val = int(state.get("last_block", 0))
    if val > 0:
        print(f"[stato] last_block caricato dal file: {val}")
        return val

    env_val = int(os.environ.get("LAST_BLOCK", "0"))
    if env_val > 0:
        print(f"[stato] last_block caricato da env: {env_val}")
        return env_val

    current = get_current_block()
    print(f"[stato] primo avvio — parto dal blocco attuale: {current}")
    return current


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    ok = True
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            r = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
            if not r.ok:
                print(f"[telegram] errore chat {chat_id}: {r.text}")
                ok = False
        except Exception as e:
            print(f"[telegram] errore chat {chat_id}: {e}")
            ok = False
    return ok


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
# Check settimanale Railway
# ---------------------------------------------------------------------------

def get_railway_usage() -> dict | None:
    """Interroga l'API Railway e restituisce i dati di utilizzo del workspace."""
    if not RAILWAY_TOKEN:
        return None
    query = """
    { me { ... on User { workspaces {
        name plan
        customer {
            currentUsage
            hasExhaustedFreePlan
            remainingUsageCreditBalance
            state
        }
    } } } }
    """
    try:
        r = requests.post(
            "https://backboard.railway.app/graphql/v2",
            headers={"Authorization": f"Bearer {RAILWAY_TOKEN}", "Content-Type": "application/json"},
            json={"query": query},
            timeout=10,
        )
        workspaces = r.json()["data"]["me"]["workspaces"]
        return workspaces[0]["customer"] if workspaces else None
    except Exception as e:
        print(f"[railway] errore lettura usage: {e}")
        return None


def send_weekly_railway_report():
    usage = get_railway_usage()
    now_str = datetime.now(tz=timezone.utc).strftime("%d/%m/%Y")

    if usage is None:
        msg = (
            f"Report settimanale Railway ({now_str})\n\n"
            f"Impossibile leggere l'utilizzo (token mancante o scaduto).\n"
            f"Verifica su railway.com che il bot sia ancora attivo."
        )
    else:
        exhausted    = usage.get("hasExhaustedFreePlan", False)
        state        = usage.get("state", "UNKNOWN")
        current_usd  = usage.get("currentUsage", 0)
        remaining    = usage.get("remainingUsageCreditBalance", 0)
        free_total   = 5.0  # Piano Hobby: $5/mese

        if exhausted or state == "INACTIVE" and current_usd >= free_total:
            stato_emoji = "SOSPESO"
            stato_note  = "Credito esaurito — bot sospeso fino al mese successivo."
        elif remaining <= 1.0:
            stato_emoji = "ATTENZIONE"
            stato_note  = f"Credito quasi esaurito! Rimangono solo ${remaining:.2f}."
        else:
            stato_emoji = "ATTIVO"
            stato_note  = "Bot operativo regolarmente."

        msg = (
            f"Report settimanale Railway ({now_str})\n\n"
            f"Stato: {stato_emoji}\n"
            f"Credito usato: ${current_usd:.2f} / ${free_total:.2f}\n"
            f"Credito rimanente: ${remaining:.2f}\n\n"
            f"{stato_note}"
        )

    send_telegram(msg)
    print(f"[railway] report settimanale inviato")


# ---------------------------------------------------------------------------
# Ciclo principale
# ---------------------------------------------------------------------------

def check_once(last_block: int) -> int:
    transfers = get_latest_transfers(last_block + 1)
    print(f"[check] dal blocco {last_block + 1}: {len(transfers)} trasferimenti trovati")

    new_max_block = last_block
    for tx in transfers:
        block    = int(tx.get("blockNumber", 0))
        to_addr  = tx.get("to", "").lower()

        if to_addr == WALLET.lower():
            amount    = format_amount(tx.get("value", "0"), tx.get("tokenDecimal", "6"))
            from_addr = tx.get("from", "")
            tx_hash   = tx.get("hash", "")
            token     = tx.get("tokenSymbol", "USDT0")
            dt_str    = datetime.fromtimestamp(
                int(tx.get("timeStamp", 0)), tz=timezone.utc
            ).strftime("%d/%m/%Y %H:%M:%S UTC")
            balance   = get_usdt0_balance()

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

    state      = load_state()
    last_block = load_last_block(state)
    last_weekly_check = state.get("last_weekly_check", 0)

    while True:
        try:
            new_block = check_once(last_block)
            if new_block > last_block:
                last_block = new_block

            # Check settimanale
            now_ts = time.time()
            if now_ts - last_weekly_check >= WEEKLY_SECONDS:
                send_weekly_railway_report()
                last_weekly_check = now_ts

            save_state({"last_block": last_block, "last_weekly_check": last_weekly_check})

        except Exception as e:
            print(f"[errore] ciclo principale: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
