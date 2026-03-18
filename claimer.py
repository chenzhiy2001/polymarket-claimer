#!/usr/bin/env python3
"""
Auto-claimer for Polymarket — redeems winning positions gaslessly via Relayer API key.

Usage:
    python claimer.py                    # one-shot: claim all redeemable positions
    python claimer.py --batch 20         # claim up to 20 positions per run
    python claimer.py --loop 120         # poll every 120 s
    python claimer.py --loop 120 --batch 10
"""

import argparse
import logging
import os
import sys
import time
from json import dumps

import httpx
from dotenv import load_dotenv

from polymarket_apis.clients.data_client import PolymarketDataClient
from polymarket_apis.clients.web3_client import PolymarketGaslessWeb3Client
from polymarket_apis.types.web3_types import TransactionReceipt

# ── logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("claimer")

# ── constants ───────────────────────────────────────────────────────
CHAIN_ID = 137  # Polygon mainnet
CLAIM_DELAY = 5  # seconds between redemptions
MAX_RETRIES = 5  # per-position retry count
INITIAL_BACKOFF = 10  # seconds (doubles each attempt)


# ── Relayer API key client ──────────────────────────────────────────
class RelayerWeb3Client(PolymarketGaslessWeb3Client):
    """Gasless client using Relayer API key auth (no daily tx limit)."""

    def __init__(self, private_key, relayer_api_key, relayer_api_key_address,
                 signature_type=1, chain_id=137):
        super().__init__(private_key, signature_type=signature_type,
                         builder_creds=None, chain_id=chain_id)
        self._relayer_api_key = relayer_api_key
        self._relayer_api_key_address = relayer_api_key_address

    def _execute(self, to, data, operation_name, metadata=None):
        """Execute transaction via relay using Relayer API key headers."""
        match self.signature_type:
            case 1:
                body = self._build_proxy_relay_transaction(to, data, metadata or "")
            case 2:
                body = self._build_safe_relay_transaction(to, data, metadata or "")
            case _:
                raise ValueError(f"Invalid signature_type: {self.signature_type}")

        headers = {
            "RELAYER_API_KEY": self._relayer_api_key,
            "RELAYER_API_KEY_ADDRESS": self._relayer_api_key_address,
            "Content-Type": "application/json",
        }

        url = f"{self.relay_url}/submit"
        response = self.client.post(
            url, headers=headers, content=dumps(body).encode("utf-8")
        )
        response.raise_for_status()

        gasless_response = response.json()
        log.info("Gasless txn submitted: %s", gasless_response.get("transactionHash", "N/A"))
        log.info("Transaction ID: %s", gasless_response.get("transactionID", "N/A"))
        log.info("State: %s", gasless_response.get("state", "N/A"))

        tx_hash = gasless_response.get("transactionHash")
        if tx_hash:
            receipt_dict = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            receipt = TransactionReceipt.model_validate(receipt_dict)
            log.info(
                "%s succeeded" if receipt.status == 1 else "%s failed",
                operation_name,
            )
            return receipt
        msg = f"No transaction hash in response: {gasless_response}"
        raise ValueError(msg)


# ── env ─────────────────────────────────────────────────────────────
def load_env():
    """Load credentials from .env."""
    load_dotenv()
    private_key = os.getenv("PRIVATE_KEY")
    funder_address = os.getenv("FUNDER_ADDRESS")
    relayer_key = os.getenv("RELAYER_API_KEY")
    relayer_address = os.getenv("RELAYER_API_KEY_ADDRESS")

    missing = [
        name
        for name, val in [
            ("PRIVATE_KEY", private_key),
            ("FUNDER_ADDRESS", funder_address),
            ("RELAYER_API_KEY", relayer_key),
            ("RELAYER_API_KEY_ADDRESS", relayer_address),
        ]
        if not val
    ]
    if missing:
        log.error("Missing env vars: %s", ", ".join(missing))
        sys.exit(1)

    return private_key, funder_address, relayer_key, relayer_address


# ── data fetching ───────────────────────────────────────────────────
def fetch_redeemable(data_client: PolymarketDataClient, user: str):
    """Return all redeemable Position objects (paginated)."""
    all_positions = []
    offset = 0
    page = 500
    while True:
        positions = data_client.get_positions(
            user=user,
            redeemable=True,
            size_threshold=0,
            limit=page,
            offset=offset,
        )
        all_positions.extend(p for p in positions if p.redeemable)
        if len(positions) < page:
            break
        offset += page
    return all_positions


# ── single claim ────────────────────────────────────────────────────
def claim_position(
    web3_client: RelayerWeb3Client,
    condition_id: str,
    size: float,
    outcome_index: int,
    neg_risk: bool,
):
    """Redeem one position with retry / back-off."""
    amounts = [0.0, 0.0]
    amounts[outcome_index] = size

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            receipt = web3_client.redeem_position(
                condition_id=condition_id,
                amounts=amounts,
                neg_risk=neg_risk,
            )
            return receipt
        except Exception:
            wait = INITIAL_BACKOFF * (2 ** (attempt - 1))
            if attempt < MAX_RETRIES:
                log.warning(
                    "    Transient error. Retry %d/%d in %ds…",
                    attempt, MAX_RETRIES, wait,
                )
                time.sleep(wait)
            else:
                raise


# ── batch claim ─────────────────────────────────────────────────────
def claim_all(
    data_client: PolymarketDataClient,
    web3_client: RelayerWeb3Client,
    user: str,
    batch: int | None = None,
) -> tuple[int, int]:
    """
    Find and redeem redeemable positions.

    Returns (claimed, failed).
    """
    positions = fetch_redeemable(data_client, user)
    if not positions:
        log.info("No redeemable positions found.")
        return 0, 0

    total_size = sum(p.size for p in positions)
    log.info(
        "Found %d redeemable position(s)  (total shares ≈ %.2f)",
        len(positions),
        total_size,
    )

    if batch and batch < len(positions):
        log.info("Batch size %d — claiming first %d positions this run.", batch, batch)
        positions = positions[:batch]

    for p in positions:
        log.info(
            "  • %s | %s | size=%.4f | neg_risk=%s",
            p.title, p.outcome, p.size, p.negative_risk,
        )

    claimed = 0
    failed = 0

    for i, p in enumerate(positions):
        log.info(
            "[%d/%d] Redeeming %s — %s (%.4f shares)…",
            i + 1, len(positions), p.title, p.outcome, p.size,
        )
        try:
            receipt = claim_position(
                web3_client,
                condition_id=p.condition_id,
                size=p.size,
                outcome_index=p.outcome_index,
                neg_risk=p.negative_risk,
            )
            tx_hash = getattr(receipt, "tx_hash", None) or "?"
            log.info("  ✓ Claimed  tx=%s", tx_hash)
            claimed += 1

        except Exception:
            log.exception("  ✗ Failed to redeem %s (%s)", p.title, p.outcome)
            failed += 1

        # throttle between claims
        if i < len(positions) - 1:
            time.sleep(CLAIM_DELAY)

    log.info(
        "Batch done: %d claimed, %d failed, %d remaining.",
        claimed, failed, len(positions) - claimed - failed,
    )
    return claimed, failed


# ── main ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Polymarket auto-claimer")
    parser.add_argument(
        "--loop",
        type=int,
        default=0,
        metavar="SECS",
        help="Poll interval in seconds (0 = one-shot, default: 0)",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=None,
        metavar="N",
        help="Max positions to claim per run / cycle (default: all)",
    )
    args = parser.parse_args()

    private_key, funder_address, relayer_key, relayer_address = load_env()

    data_client = PolymarketDataClient()
    web3_client = RelayerWeb3Client(
        private_key=private_key,
        relayer_api_key=relayer_key,
        relayer_api_key_address=relayer_address,
        signature_type=1,  # Poly proxy wallets
        chain_id=CHAIN_ID,
    )

    log.info("Claimer started  (user=%s)", funder_address)

    if args.loop <= 0:
        claim_all(data_client, web3_client, funder_address, batch=args.batch)
    else:
        log.info("Polling every %ds. Ctrl+C to stop.", args.loop)
        try:
            while True:
                claim_all(
                    data_client, web3_client, funder_address, batch=args.batch
                )
                time.sleep(args.loop)
        except KeyboardInterrupt:
            log.info("Stopped by user.")


if __name__ == "__main__":
    main()
