import time
import math
import requests
from flask import Flask, jsonify, render_template_string

# ============================================================
# COINDCX FUTURES RSI SCANNER - VERSION 1
# ============================================================

app = Flask(__name__)

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

ACTIVE_INSTRUMENTS_URL = (
    "https://api.coindcx.com/exchange/v1/derivatives/"
    "futures/data/active_instruments"
)

CANDLES_URL = (
    "https://public.coindcx.com/market_data/candlesticks"
)

CURRENT_PRICES_URL = (
    "https://public.coindcx.com/market_data/v3/current_prices/futures/rt"
)

# How many candles we request for RSI calculation.
# 200 gives RSI enough historical data to stabilize.
CANDLE_COUNT = 200

# Refresh interval for the browser, in milliseconds.
REFRESH_INTERVAL = 15000  # 15 seconds


# ------------------------------------------------------------
# HTTP session
# ------------------------------------------------------------

session = requests.Session()

session.headers.update({
    "User-Agent": "CoinDCX-Futures-RSI-Scanner/1.0"
})


# ------------------------------------------------------------
# Get active USDT Futures instruments
# ------------------------------------------------------------

def get_active_futures():

    params = {
        "margin_currency_short_name[]": "USDT"
    }

    response = session.get(
        ACTIVE_INSTRUMENTS_URL,
        params=params,
        timeout=15
    )

    response.raise_for_status()

    instruments = response.json()

    # Keep only valid strings
    instruments = [
        x for x in instruments
        if isinstance(x, str)
    ]

    return sorted(instruments)


# ------------------------------------------------------------
# Get Futures candles
#
# CoinDCX:
# resolution = 5  -> 5 minute
# resolution = 15 -> 15 minute
# pcode = f        -> Futures
# ------------------------------------------------------------

def get_candles(pair, resolution):

    now = int(time.time())

    # Approximate number of seconds required.
    if resolution == 5:
        seconds_per_candle = 5 * 60
    elif resolution == 15:
        seconds_per_candle = 15 * 60
    else:
        raise ValueError("Unsupported resolution")

    # Ask for substantially more history than RSI requires.
    from_time = now - (
        CANDLE_COUNT * seconds_per_candle
    )

    params = {
        "pair": pair,
        "from": from_time,
        "to": now,
        "resolution": str(resolution),
        "pcode": "f"
    }

    response = session.get(
        CANDLES_URL,
        params=params,
        timeout=15
    )

    response.raise_for_status()

    result = response.json()

    if not isinstance(result, dict):
        return []

    candles = result.get("data", [])

    # Sort oldest -> newest
    candles.sort(
        key=lambda x: x.get("time", 0)
    )

    return candles


# ------------------------------------------------------------
# RSI(14) - Wilder's RSI
# ------------------------------------------------------------

def calculate_rsi(closes, period=14):

    if len(closes) < period + 1:
        return None

    # Calculate price changes
    changes = []

    for i in range(1, len(closes)):
        changes.append(
            closes[i] - closes[i - 1]
        )

    gains = [
        max(change, 0)
        for change in changes
    ]

    losses = [
        max(-change, 0)
        for change in changes
    ]

    # Initial averages
    avg_gain = sum(
        gains[:period]
    ) / period

    avg_loss = sum(
        losses[:period]
    ) / period

    # Wilder smoothing
    for i in range(period, len(gains)):

        avg_gain = (
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

    # Handle special cases
    if avg_loss == 0:

        if avg_gain == 0:
            return 50.0

        return 100.0

    rs = avg_gain / avg_loss

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


# ------------------------------------------------------------
# Get RSI from candles
# ------------------------------------------------------------

def get_rsi(pair, resolution):

    candles = get_candles(
        pair,
        resolution
    )

    if len(candles) < 20:
        return None

    closes = []

    for candle in candles:

        try:
            close = float(
                candle["close"]
            )

            closes.append(close)

        except (KeyError, TypeError, ValueError):
            continue

    return calculate_rsi(
        closes,
        period=14
    )


# ------------------------------------------------------------
# Get current Futures prices
# ------------------------------------------------------------

def get_current_prices():

    response = session.get(
        CURRENT_PRICES_URL,
        timeout=15
    )

    response.raise_for_status()

    return response.json()


# ------------------------------------------------------------
# Convert pair into readable coin name
#
# Example:
# B-BTC_USDT -> BTC
# ------------------------------------------------------------

def coin_name(pair):

    try:
        value = pair

        if value.startswith("B-"):
            value = value[2:]

        if value.endswith("_USDT"):
            value = value[:-5]

        return value

    except Exception:
        return pair


# ------------------------------------------------------------
# Signal based on 5-minute RSI
# ------------------------------------------------------------

def get_signal(rsi):

    if rsi is None:
        return ""

    if rsi >= 94:
        return "SHORT ZONE"

    if rsi >= 93:
        return "APPROACHING"

    if rsi >= 87:
        return "WATCH"

    return ""


# ------------------------------------------------------------
# Build scanner data
# ------------------------------------------------------------

def build_scanner():

    instruments = get_active_futures()

    prices = get_current_prices()

    rows = []

    for pair in instruments:

        try:

            # Current price
            price_info = prices.get(pair, {})

            current_price = price_info.get(
                "ls"
            )

            if current_price is not None:
                current_price = float(
                    current_price
                )

            # 5-minute RSI
            rsi_5m = get_rsi(
                pair,
                5
            )

            # 15-minute RSI
            rsi_15m = get_rsi(
                pair,
                15
            )

            rows.append({
                "pair": pair,
                "coin": coin_name(pair),
                "price": current_price,
                "rsi_5m": rsi_5m,
                "rsi_15m": rsi_15m,
                "signal": get_signal(rsi_5m)
            })

            print(
                f"{pair:25} "
                f"5m={rsi_5m if rsi_5m is not None else '-':>6} "
                f"15m={rsi_15m if rsi_15m is not None else '-':>6}"
            )

        except Exception as error:

            print(
                f"Error processing {pair}: {error}"
            )

    # Highest 5m RSI first
    rows.sort(
        key=lambda x: (
            x["rsi_5m"]
            if x["rsi_5m"] is not None
            else -1
        ),
        reverse=True
    )

    return rows


# ------------------------------------------------------------
# API endpoint
# ------------------------------------------------------------

@app.route("/api/data")
def api_data():

    try:

        rows = build_scanner()

        return jsonify({
            "success": True,
            "timestamp": int(time.time()),
            "count": len(rows),
            "data": rows
        })

    except Exception as error:

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


# ------------------------------------------------------------
# Web interface
# ------------------------------------------------------------

HTML = r"""
<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>CoinDCX Futures RSI Scanner</title>

<style>

body {
    font-family: Arial, sans-serif;
    margin: 0;
    padding: 0;
    background: #f4f5f7;
    color: #222;
}

.header {
    background: #111827;
    color: white;
    padding: 18px;
}

.header h1 {
    margin: 0 0 8px 0;
    font-size: 22px;
}

.header small {
    color: #cbd5e1;
}

.controls {
    padding: 15px;
    background: white;
    position: sticky;
    top: 0;
    z-index: 10;
    border-bottom: 1px solid #ddd;
}

input {
    width: 250px;
    max-width: 80%;
    padding: 10px;
    border: 1px solid #ccc;
    border-radius: 6px;
    font-size: 15px;
}

button {
    padding: 10px 14px;
    margin-left: 8px;
    border: none;
    border-radius: 6px;
    cursor: pointer;
}

table {
    width: 100%;
    border-collapse: collapse;
    background: white;
}

th {
    background: #e5e7eb;
    padding: 12px;
    text-align: right;
    position: sticky;
    top: 74px;
}

th:first-child,
td:first-child {
    text-align: left;
}

td {
    padding: 11px;
    border-bottom: 1px solid #eee;
    text-align: right;
}

.coin {
    font-weight: bold;
}

.rsi-high {
    font-weight: bold;
}

.rsi-short {
    background: #fee2e2;
    color: #991b1b;
    font-weight: bold;
}

.rsi-approaching {
    background: #ffedd5;
    color: #9a3412;
    font-weight: bold;
}

.rsi-watch {
    background: #fef9c3;
    color: #854d0e;
    font-weight: bold;
}

.status {
    padding: 12px 15px;
    background: #f9fafb;
    font-size: 13px;
    color: #555;
}

@media(max-width:700px) {

    table {
        font-size: 13px;
    }

    th, td {
        padding: 8px 6px;
    }

    .header h1 {
        font-size: 18px;
    }

}

</style>

</head>

<body>

<div class="header">

    <h1>CoinDCX Futures RSI Scanner</h1>

    <small>
        Futures only • RSI(14) • 5m + 15m
    </small>

</div>


<div class="controls">

    <input
        id="search"
        type="text"
        placeholder="Search coin..."
        oninput="render()"
    >

    <button onclick="loadData()">
        Refresh
    </button>

</div>


<div class="status" id="status">
    Loading...
</div>


<table>

<thead>

<tr>

<th>Coin</th>

<th>Price</th>

<th>RSI 5m</th>

<th>RSI 15m</th>

<th>Signal</th>

</tr>

</thead>

<tbody id="tableBody">

</tbody>

</table>


<script>

let allData = [];


function formatPrice(value) {

    if (value === null ||
        value === undefined) {

        return "-";

    }

    return Number(value)
        .toLocaleString(
            undefined,
            {
                maximumFractionDigits: 8
            }
        );
}


function formatRSI(value) {

    if (value === null ||
        value === undefined) {

        return "-";

    }

    return Number(value)
        .toFixed(2);
}


function rsiClass(value) {

    if (value === null ||
        value === undefined) {

        return "";

    }

    if (value >= 94) {

        return "rsi-short";

    }

    if (value >= 93) {

        return "rsi-approaching";

    }

    if (value >= 87) {

        return "rsi-watch";

    }

    return "";

}


function render() {

    const search =
        document
        .getElementById("search")
        .value
        .toUpperCase()
        .trim();

    const filtered =
        allData.filter(row =>
            row.coin
            .toUpperCase()
            .includes(search)
        );


    let html = "";


    for (const row of filtered) {

        html += `

        <tr>

            <td class="coin">
                ${row.coin}
            </td>

            <td>
                ${formatPrice(row.price)}
            </td>

            <td class="${rsiClass(row.rsi_5m)}">
                ${formatRSI(row.rsi_5m)}
            </td>

            <td>
                ${formatRSI(row.rsi_15m)}
            </td>

            <td>
                ${row.signal}
            </td>

        </tr>

        `;

    }


    document
        .getElementById("tableBody")
        .innerHTML = html;

}


async function loadData() {

    document
        .getElementById("status")
        .innerText =
        "Updating Futures data...";


    try {

        const response =
            await fetch("/api/data");


        const result =
            await response.json();


        if (!result.success) {

            throw new Error(
                result.error
            );

        }


        allData =
            result.data;


        render();


        const now =
            new Date()
            .toLocaleTimeString();


        document
            .getElementById("status")
            .innerText =
            `Showing ${result.count} active Futures coins • Last update: ${now}`;


    } catch (error) {

        document
            .getElementById("status")
            .innerText =
            "Error: " + error.message;

    }

}


// Initial load
loadData();


// Automatic refresh
setInterval(
    loadData,
    15000
);

</script>

</body>

</html>
"""


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

@app.route("/")
def home():

    return render_template_string(
        HTML
    )


if __name__ == "__main__":

    print()
    print("==============================================")
    print(" CoinDCX Futures RSI Scanner")
    print(" Version 1")
    print("==============================================")
    print()
    print("Open this in your browser:")
    print()
    print("http://127.0.0.1:5000")
    print()
    print("Press CTRL+C to stop the scanner.")
    print()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )