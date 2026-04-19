"""
Bot USDT0 Polygon — esecuzione one-shot per GitHub Actions.

Sorgenti dati (nessuna API key richiesta):
  - Saldo:        ERC-20 balanceOf via JSON-RPC su nodi pubblici Polygon
  - Transazioni:  BlockScout API (polygon.blockscout.com)
  - Blocco attuale: eth_blockNumber via JSON-RPC

Stato (LAST_BLOCK, LAST_WEEKLY_CHECK) persistito su GitHub Variables API.
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
RAILWAY_TOKEN    = os.environ.get("RAILWAY_TOKEN", "")
GH_PAT           = os.environ.get("GH_PAT", "")
REPO             = os.environ.get("REPO", "")
WEEKLY_SECONDS   = 7 * 24 * 3600

# Nodi RPC pubblici Polygon (fallback automatico)
RPC_NODES = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.meowrpc.com",
    "https://rpc.tornadoeth.cash/polygon",
    "https://gateway.tenderly.co/public/polygon",
]

BLOCKSCOUT = "https://polygon.blockscout.com/api/v2"


# ---------------------------------------------------------------------------
# GitHub Variables API
# ---------------------------------------------------------------------------

def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def read_gh_var(name: str) -> str:
    if not GH_PAT or not REPO:
        return ""
    try:
        r = requests.get(
            f"https://api.github.com/repos/{REPO}/actions/variables/{name}",
            headers=_gh_headers(), timeout=10
        )
        return r.json().get("value", "") if r.ok else ""
    except Exception as e:
        print(f"[gh] errore lettura {name}: {e}")
        return ""


def write_gh_var(name: str, value: str):
    if not GH_PAT or not REPO:
        print(f"[gh] GH_PAT/REPO mancanti — {name} non aggiornato")
        return
    base    = f"https://api.github.com/repos/{REPO}/actions/variables"
    payload = {"name": name, "value": value}
    try:
        r = requests.patch(f"{base}/{name}", headers=_gh_headers(), json=payload, timeout=10)
        if r.status_code == 404:
            r = requests.post(base, headers=_gh_headers(), json=payload, timeout=10)
        if r.ok:
            print(f"[gh] {name} = {value}")
        else:
            print(f"[gh] errore scrittura {name}: {r.status_code} — {r.text}")
    except Exception as e:
        print(f"[gh] errore scrittura {name}: {e}")


# ---------------------------------------------------------------------------
# JSON-RPC Polygon (con fallback automatico tra i nodi)
# ---------------------------------------------------------------------------

def _rpc(method: str, params: list):
    """Chiama un metodo JSON-RPC provando i nodi in ordine fino al primo che risponde."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for node in RPC_NODES:
        try:
            r = requests.post(node, json=payload, timeout=10)
            data = r.json()
            if "result" in data:
                return data["result"]
            print(f"[rpc] {node} errore: {data.get('error', {}).get('message', '?')}")
        except Exception as e:
            print(f"[rpc] {node} non raggiungibile: {e}")
    return None


def get_current_block() -> int:
    res = _rpc("eth_blockNumber", [])
    return int(res, 16) if res else 0


def get_usdt0_balance() -> str:
    """Legge il saldo USDT0 direttamente dal contratto via eth_call (sempre accurato)."""
    # balanceOf(address) selector = 0x70a08231
    data = "0x70a08231" + "000000000000000000000000" + WALLET[2:].lower()
    res  = _rpc("eth_call", [{"to": USDT0_CONTRACT, "data": data}, "latest"])
    if res and res not in ("0x", "0x0"):
        balance = int(res, 16) / 1_000_000
        return f"{balance:,.2f}"
    return "N/A"


# ---------------------------------------------------------------------------
# BlockScout API — trasferimenti token
# ---------------------------------------------------------------------------

def get_latest_transfers(from_block: int) -> list:
    """
    Recupera i trasferimenti in entrata USDT0 con block_number > from_block.
    BlockScout restituisce i dati dal più recente al meno recente; paginato.
    """
    results = []
    params  = {
        "token":  USDT0_CONTRACT,
        "filter": "to",
        "type":   "ERC-20",
    }

    while True:
        try:
            r    = requests.get(f"{BLOCKSCOUT}/addresses/{WALLET}/token-transfers",
                                params=params, timeout=15)
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

            ts_raw = item.get("timestamp", "")
            try:
                ts_unix = int(
                    datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
                )
            except Exception:
                ts_unix = 0

            total    = item.get("total", {})
            value    = total.get("value", "0")
            decimals = total.get("decimals", "6")

            results.append({
                "blockNumber":  str(block_num),
                "timeStamp":    str(ts_unix),
                "hash":         item.get("transaction_hash", ""),
                "from":         item.get("from", {}).get("hash", ""),
                "to":           item.get("to", {}).get("hash", ""),
                "value":        value,
                "tokenDecimal": decimals,
                "tokenSymbol":  item.get("token", {}).get("symbol", "USDT0"),
            })

        if stop or not data.get("next_page_params"):
            break

        # Aggiorna i parametri per la pagina successiva
        params = {**params, **data["next_page_params"]}

    # Restituisce in ordine cronologico (blocco crescente)
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
# Report settimanale Railway
# ---------------------------------------------------------------------------

def get_railway_usage() -> dict | None:
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
            headers={"Authorization": f"Bearer {RAILWAY_TOKEN}",
                     "Content-Type": "application/json"},
            json={"query": query},
            timeout=10,
        )
        workspaces = r.json()["data"]["me"]["workspaces"]
        return workspaces[0]["customer"] if workspaces else None
    except Exception as e:
        print(f"[railway] errore lettura usage: {e}")
        return None


def send_weekly_railway_report():
    usage   = get_railway_usage()
    now_str = datetime.now(tz=timezone.utc).strftime("%d/%m/%Y")

    if usage is None:
        msg = (
            f"Report settimanale Railway ({now_str})\n\n"
            f"Impossibile leggere l'utilizzo (token mancante o scaduto).\n"
            f"Verifica su railway.com che il bot sia ancora attivo."
        )
    else:
        exhausted   = usage.get("hasExhaustedFreePlan", False)
        state       = usage.get("state", "UNKNOWN")
        current_usd = usage.get("currentUsage", 0)
        remaining   = usage.get("remainingUsageCreditBalance", 0)
        free_total  = 5.0

        if exhausted or (state == "INACTIVE" and current_usd >= free_total):
            stato = "SOSPESO"
            note  = "Credito esaurito — bot sospeso fino al mese successivo."
        elif remaining <= 1.0:
            stato = "ATTENZIONE"
            note  = f"Credito quasi esaurito! Rimangono solo ${remaining:.2f}."
        else:
            stato = "ATTIVO"
            note  = "Bot operativo regolarmente."

        msg = (
            f"Report settimanale Railway ({now_str})\n\n"
            f"Stato: {stato}\n"
            f"Credito usato: ${current_usd:.2f} / ${free_total:.2f}\n"
            f"Credito rimanente: ${remaining:.2f}\n\n"
            f"{note}"
        )

    send_telegram(msg)
    print("[railway] report settimanale inviato")


# ---------------------------------------------------------------------------
# Main — one-shot
# ---------------------------------------------------------------------------

def main():
    print(f"[avvio] Bot USDT0 Polygon — wallet {WALLET}")

    # ── 1. Leggi LAST_BLOCK ──────────────────────────────────────────────────
    last_block = int(os.environ.get("LAST_BLOCK", "0"))
    if last_block == 0:
        raw = read_gh_var("LAST_BLOCK")
        last_block = int(raw) if raw.isdigit() else 0

    if last_block == 0:
        current = get_current_block()
        print(f"[stato] primo avvio — salvo blocco attuale: {current}")
        write_gh_var("LAST_BLOCK", str(current))
        write_gh_var("LAST_WEEKLY_CHECK", str(int(time.time())))
        return

    print(f"[stato] scansione dal blocco: {last_block + 1}")

    # ── 2. Controlla nuovi trasferimenti in entrata ──────────────────────────
    transfers = get_latest_transfers(last_block)
    print(f"[check] {len(transfers)} nuovi trasferimenti trovati")

    new_max_block = last_block
    for tx in transfers:
        block     = int(tx.get("blockNumber", 0))
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

    # ── 3. Aggiorna LAST_BLOCK ───────────────────────────────────────────────
    if new_max_block > last_block:
        write_gh_var("LAST_BLOCK", str(new_max_block))
    else:
        print(f"[stato] nessun nuovo blocco — last_block invariato: {last_block}")

    # ── 4. Report settimanale ────────────────────────────────────────────────
    raw_weekly  = read_gh_var("LAST_WEEKLY_CHECK")
    last_weekly = int(raw_weekly) if raw_weekly.isdigit() else 0
    now_ts      = int(time.time())

    if now_ts - last_weekly >= WEEKLY_SECONDS:
        send_weekly_railway_report()
        write_gh_var("LAST_WEEKLY_CHECK", str(now_ts))


if __name__ == "__main__":
    main()
