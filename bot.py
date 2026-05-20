"""
Bot USDT0 Polygon — Railway.

Monitora il wallet e invia una notifica Telegram per ogni
trasferimento in entrata >= MIN_AMOUNT USDT0.

Stato: puramente in memoria. LAST_BLOCK è letto all'avvio
dall'env var e non viene mai riscritto — nessuna chiamata
Railway API, nessun trigger di redeploy automatico.
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

WALLET        = os.environ["WALLET"]
USDT0         = "0xc2132d05d31c914a87c6611c10748aeb04b58e8f"
TG_TOKEN      = os.environ["TELEGRAM_TOKEN"]
TG_CHAT_IDS   = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS","").split(",") if c.strip()]
POLL          = int(os.environ.get("POLL_INTERVAL", "60"))
MIN_AMOUNT    = 1.0

RPC_NODES = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.meowrpc.com",
    "https://rpc.tornadoeth.cash/polygon",
    "https://gateway.tenderly.co/public/polygon",
]
BLOCKSCOUT = "https://polygon.blockscout.com/api/v2"


# ---------------------------------------------------------------------------
# RPC helpers
# ---------------------------------------------------------------------------

def rpc(method, params):
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

def current_block():
    res = rpc("eth_blockNumber", [])
    return int(res, 16) if res else 0

def usdt0_balance():
    data = "0x70a08231" + "000000000000000000000000" + WALLET[2:].lower()
    res  = rpc("eth_call", [{"to": USDT0, "data": data}, "latest"])
    if res and res not in ("0x", "0x0"):
        return f"{int(res, 16) / 1_000_000:,.2f}"
    return "N/A"


# ---------------------------------------------------------------------------
# BlockScout
# ---------------------------------------------------------------------------

def get_transfers(from_block):
    results = []
    params  = {"token": USDT0, "filter": "to", "type": "ERC-20"}

    while True:
        try:
            r    = requests.get(f"{BLOCKSCOUT}/addresses/{WALLET}/token-transfers",
                                params=params, timeout=15)
            data = r.json()
        except Exception as e:
            print(f"[blockscout] errore: {e}")
            break

        stop = False
        for item in data.get("items", []):
            blk = int(item.get("block_number", 0))
            if blk <= from_block:
                stop = True
                break
            try:
                ts = int(datetime.fromisoformat(
                    item.get("timestamp","").replace("Z","+00:00")).timestamp())
            except Exception:
                ts = 0
            total = item.get("total", {})
            results.append({
                "block":   blk,
                "ts":      ts,
                "hash":    item.get("transaction_hash", ""),
                "from":    item.get("from", {}).get("hash", ""),
                "value":   total.get("value", "0"),
                "dec":     total.get("decimals", "6"),
                "symbol":  item.get("token", {}).get("symbol", "USDT0"),
            })

        if stop or not data.get("next_page_params"):
            break
        params = {**params, **data["next_page_params"]}

    results.sort(key=lambda x: x["block"])
    return results

def fmt(value, dec):
    try:
        return f"{int(value) / (10 ** int(dec)):,.2f}"
    except Exception:
        return value


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def notify(msg):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for cid in TG_CHAT_IDS:
        try:
            requests.post(url, json={"chat_id": cid, "text": msg}, timeout=10)
        except Exception as e:
            print(f"[telegram] errore {cid}: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"[avvio] wallet {WALLET}")
    print(f"[avvio] poll {POLL}s — min {MIN_AMOUNT} USDT0")

    # LAST_BLOCK: letto una sola volta all'avvio, mai riscritto su Railway
    last_block = int(os.environ.get("LAST_BLOCK", "0"))
    if last_block == 0:
        last_block = current_block()
        print(f"[avvio] blocco iniziale da chain: {last_block}")

    print(f"[avvio] in ascolto dal blocco {last_block + 1}")

    # Deduplicazione in memoria — se il bot si riavvia, riparte da LAST_BLOCK
    # (impostato manualmente a un valore sicuro) e non troverà TX già passate
    seen: set = set()

    while True:
        try:
            blk = current_block()
            if blk <= last_block:
                time.sleep(POLL)
                continue

            print(f"[check] {last_block + 1} → {blk}")
            txs = get_transfers(last_block)

            for tx in txs:
                h = tx["hash"]
                if h in seen:
                    print(f"[skip] già vista {h[:12]}...")
                    continue

                amount = fmt(tx["value"], tx["dec"])
                if float(amount.replace(",","")) < MIN_AMOUNT:
                    print(f"[skip] dust {amount}")
                    continue

                seen.add(h)

                dt  = datetime.fromtimestamp(tx["ts"], tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
                bal = usdt0_balance()

                msg = (
                    f"Nuova transazione in entrata!\n\n"
                    f"Importo: {amount} {tx['symbol']}\n"
                    f"Saldo attuale: {bal} {tx['symbol']}\n"
                    f"Data: {dt}\n"
                    f"Da: {tx['from'][:10]}...{tx['from'][-6:]}\n"
                    f"TX: https://polygonscan.com/tx/{h}\n"
                    f"Blocco: {tx['block']}"
                )
                notify(msg)
                print(f"[notifica] {amount} {tx['symbol']} blocco {tx['block']}")

            last_block = blk

        except Exception as e:
            print(f"[errore] {e}")

        time.sleep(POLL)


if __name__ == "__main__":
    main()
