"""
Bot USDT0 Polygon — esecuzione one-shot per GitHub Actions.
Lo stato (LAST_BLOCK, LAST_WEEKLY_CHECK) è persistito come variabile
del repository GitHub tramite API, così sopravvive tra un run e l'altro.
"""

import requests
import os
import time
import urllib3
from datetime import datetime, timezone

urllib3.disable_warnings()

# ---------------------------------------------------------------------------
# Configurazione — tutte le variabili obbligatorie arrivano dai secrets/vars GHA
# ---------------------------------------------------------------------------

API_KEY          = os.environ["POLYGONSCAN_API_KEY"]
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
REPO             = os.environ.get("REPO", "")   # es. "Diacoin/polygon-bot"
WEEKLY_SECONDS   = 7 * 24 * 3600


# ---------------------------------------------------------------------------
# GitHub Variables API  (persiste lo stato tra un run e l'altro)
# ---------------------------------------------------------------------------

def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def read_gh_var(name: str) -> str:
    """Legge una variabile del repository GitHub. Restituisce '' se non trovata."""
    if not GH_PAT or not REPO:
        return ""
    url = f"https://api.github.com/repos/{REPO}/actions/variables/{name}"
    try:
        r = requests.get(url, headers=_gh_headers(), timeout=10)
        if r.ok:
            return r.json().get("value", "")
        print(f"[gh] variabile {name} non trovata ({r.status_code})")
    except Exception as e:
        print(f"[gh] errore lettura {name}: {e}")
    return ""


def write_gh_var(name: str, value: str):
    """Crea o aggiorna una variabile del repository GitHub."""
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
            print(f"[gh] {name} aggiornato: {value}")
        else:
            print(f"[gh] errore scrittura {name}: {r.status_code} — {r.text}")
    except Exception as e:
        print(f"[gh] errore scrittura {name}: {e}")


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
# PolygonScan API
# ---------------------------------------------------------------------------

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
    # Priorità: env passato dal workflow → variabile GitHub → primo avvio
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

    # ── 2. Controlla nuovi trasferimenti ─────────────────────────────────────
    transfers    = get_latest_transfers(last_block + 1)
    print(f"[check] {len(transfers)} trasferimenti trovati")

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

    # ── 3. Aggiorna LAST_BLOCK ───────────────────────────────────────────────
    if new_max_block > last_block:
        write_gh_var("LAST_BLOCK", str(new_max_block))
    else:
        print(f"[stato] nessun nuovo blocco — last_block invariato: {last_block}")

    # ── 4. Report settimanale (se sono passati 7 giorni) ────────────────────
    raw_weekly = read_gh_var("LAST_WEEKLY_CHECK")
    last_weekly = int(raw_weekly) if raw_weekly.isdigit() else 0
    now_ts      = int(time.time())

    if now_ts - last_weekly >= WEEKLY_SECONDS:
        send_weekly_railway_report()
        write_gh_var("LAST_WEEKLY_CHECK", str(now_ts))


if __name__ == "__main__":
    main()
