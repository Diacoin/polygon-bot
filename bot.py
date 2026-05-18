"""
Bot USDT0 Polygon — servizio continuo su Railway.

Monitora in tempo reale il wallet configurato e invia notifica Telegram
per ogni trasferimento in entrata >= MIN_AMOUNT USDT0.

Stato (LAST_BLOCK) persistito via Railway Variables API.
"""

import requests
import os
import time
import urllib3
from datetime import datetime, timezone

urllib3.disable_warnings()

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

WALLET           = os.environ["WALLET"]
USDT0_CONTRACT   = "0xc2132d05d31c914a87c6611c10748aeb04b58e8f"
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_IDS = [
    cid.strip()
    for cid in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",")
    if cid.strip()
]
STATE_FILE = "/app/state.json"  # persiste tra restart sullo stesso volume Railway
POLL_INTERVAL    = int(os.environ.get("POLL_INTERVAL", "60"))
MIN_AMOUNT       = 1.0

RPC_NODES = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.meowrpc.com",
    "https://rpc.tornadoeth.cash/polygon",
    "https://gateway.tenderly.co/public/polygon",
]

BLOCKSCOUT = "https://polygon.blockscout.com/api/v2"


# ---------------------------------------------------------------------------
# State file — persiste LAST_BLOCK tra i riavvii (volume Railway)
# ---------------------------------------------------------------------------

import json as _json

def load_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return _json.load(f)
    except Exception:
        return {}

def save_state(state: dict):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            _json.dump(state, f)
        print(f"[state] salvato: {state}")
    except Exception as e:
        print(f"[state] errore scrittura: {e}")


# ---------------------------------------------------------------------------
# JSON-RPC Polygon
# ---------------------------------------------------------------------------

def _rpc(method: str, params: list):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for node in RPC_NODES:
        try:
            r = requests.post(node, json=payload, timeout=10, verify=False)
            data = r.json()
            if "result" in data:
                return data["result"]
        except Exception:
            continue
    return None


def get_current_block() -> int:
    res = _rpc("eth_blockNumber", [])
    return int(res, 16) if res else 0


def get_usdt0_balance() -> str:
    data = "0x70a08231" + "000000000000000000000000" + WALLET[2:].lower()
    res  = _rpc("eth_call", [{"to": USDT0_CONTRACT, "data": data}, "latest"])
    if res and res not in ("0x", "0x0"):
        return f"{int(res, 16) / 1_000_000:,.2f}"
    return "N/A"


# ---------------------------------------------------------------------------
# BlockScout — trasferimenti token in entrata
# ---------------------------------------------------------------------------

def get_latest_transfers(from_block: int) -> list:
    results = []
    params  = {
        "token":  USDT0_CONTRACT,
        "filter": "to",
        "type":   "ERC-20",
    }

    while True:
        try:
            r    = requests.get(
                f"{BLOCKSCOUT}/addresses/{WALLET}/token-transfers",
                params=params, timeout=15
            )
            data = r.json()
        except Exception as e:
            print(f"[blockscout] errore fetch: {e}")
            break

        items = data.get("items", [])
        stop  = False

        for item in items:
            block_num = int(item.get("block_number", 0))
            if block_num <= from_block:
                stop = True
                break

            try:
                ts_unix = int(
                    datetime.fromisoformat(
                        item.get("timestamp", "").replace("Z", "+00:00")
                    ).timestamp()
                )
            except Exception:
                ts_unix = 0

            total    = item.get("total", {})
            results.append({
                "blockNumber":  str(block_num),
                "timeStamp":    str(ts_unix),
                "hash":         item.get("transaction_hash", ""),
                "from":         item.get("from", {}).get("hash", ""),
                "value":        total.get("value", "0"),
                "tokenDecimal": total.get("decimals", "6"),
                "tokenSymbol":  item.get("token", {}).get("symbol", "USDT0"),
            })

        if stop or not data.get("next_page_params"):
            break

        params = {**params, **data["next_page_params"]}

    results.sort(key=lambda x: int(x["blockNumber"]))
    return results


def format_amount(value: str, decimals: str) -> str:
    try:
        return f"{int(value) / (10 ** int(decimals)):,.2f}"
    except Exception:
        return value


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    ok  = True
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


# ---------------------------------------------------------------------------
# Main — loop continuo
# ---------------------------------------------------------------------------

def main():
    print(f"[avvio] Bot USDT0 Polygon — wallet {WALLET}")
    print(f"[avvio] Polling ogni {POLL_INTERVAL}s — soglia minima {MIN_AMOUNT} USDT0")

    # Legge LAST_BLOCK dallo state file (persiste tra i riavvii)
    state = load_state()
    last_block = int(state.get("last_block", os.environ.get("LAST_BLOCK", "0")))

    if last_block == 0:
        last_block = get_current_block()
        print(f"[avvio] primo avvio — blocco iniziale: {last_block}")
        save_state({"last_block": last_block})

    print(f"[avvio] in ascolto dal blocco {last_block}")

    while True:
        try:
            current_block = get_current_block()
            if current_block <= last_block:
                time.sleep(POLL_INTERVAL)
                continue

            print(f"[check] dal blocco {last_block + 1} → {current_block}")
            transfers = get_latest_transfers(last_block)

            # Ricarica lo state da disco ad ogni ciclo — garantisce coerenza dopo riavvii
            state = load_state()
            notified_hashes: set = set(state.get("notified_hashes", []))

            for tx in transfers:
                block   = int(tx["blockNumber"])
                amount  = format_amount(tx["value"], tx["tokenDecimal"])
                token   = tx["tokenSymbol"]
                tx_hash = tx["hash"]

                # Salta TX già notificate (deduplicazione robusta)
                if tx_hash in notified_hashes:
                    print(f"[skip] già notificata — {tx_hash[:12]}... (blocco {block})")
                    continue

                if float(amount.replace(",", "")) < MIN_AMOUNT:
                    print(f"[skip] dust tx — {amount} {token} (blocco {block})")
                    continue

                dt_str  = datetime.fromtimestamp(
                    int(tx["timeStamp"]), tz=timezone.utc
                ).strftime("%d/%m/%Y %H:%M UTC")
                balance = get_usdt0_balance()

                msg = (
                    f"Nuova transazione in entrata!\n\n"
                    f"Importo: {amount} {token}\n"
                    f"Saldo attuale: {balance} {token}\n"
                    f"Data: {dt_str}\n"
                    f"Da: {tx['from'][:10]}...{tx['from'][-6:]}\n"
                    f"TX: https://polygonscan.com/tx/{tx_hash}\n"
                    f"Blocco: {block}"
                )

                send_telegram(msg)
                print(f"[notifica] {amount} {token} — blocco {block}")

                # Salva lo state SUBITO dopo la notifica — prima di proseguire
                notified_hashes.add(tx_hash)
                hashes_list = list(notified_hashes)[-500:]
                save_state({"last_block": block, "notified_hashes": hashes_list})
                last_block = block

            # Avanza il blocco fino al blocco corrente anche se non ci sono TX
            if current_block > last_block:
                state_now = load_state()
                hashes_now = state_now.get("notified_hashes", [])
                save_state({"last_block": current_block, "notified_hashes": hashes_now})
                last_block = current_block

        except Exception as e:
            print(f"[errore] ciclo principale: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
