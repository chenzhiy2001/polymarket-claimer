# polymarket-claimer

An auto-claimer for Polymarket that redeems winning positions gaslessly via a Relayer API key — no builder credit limits.

## Prerequisites

- Python 3.10+
- A Polymarket account with redeemable positions
- A Relayer API key and the corresponding address

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/chenzhiy2001/polymarket-claimer.git
cd polymarket-claimer
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure credentials

Create a `.env` file in the project root (it is already listed in `.gitignore`, so it will not be committed):

```
PRIVATE_KEY=0xyour_wallet_private_key
FUNDER_ADDRESS=0xyour_wallet_address
RELAYER_API_KEY=your_relayer_api_key
RELAYER_API_KEY_ADDRESS=0xyour_relayer_api_key_address
```

| Variable | Description |
|---|---|
| `PRIVATE_KEY` | Private key of the wallet that holds the winning positions |
| `FUNDER_ADDRESS` | Public address of that wallet |
| `RELAYER_API_KEY` | API key obtained from the Polymarket Relayer |
| `RELAYER_API_KEY_ADDRESS` | Address associated with the Relayer API key |

## Usage

```bash
# Claim all redeemable positions (one-shot)
python claimer.py

# Claim up to 20 positions per run
python claimer.py --batch 20

# Poll every 120 seconds, claiming all redeemable positions each cycle
python claimer.py --loop 120

# Poll every 120 seconds, claiming up to 10 positions per cycle
python claimer.py --loop 120 --batch 10
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--batch N` | all | Maximum number of positions to claim per run / cycle |
| `--loop SECS` | 0 (one-shot) | If > 0, repeat every `SECS` seconds until interrupted with Ctrl+C |

## Deactivating the virtual environment

When you are done, deactivate the virtual environment:

```bash
deactivate
```
