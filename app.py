import time
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, jsonify, render_template_string


app = Flask(__name__)


# ============================================================
# COINDCX API URLS
# ============================================================

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


# ============================================================
# SETTINGS
# ============================================================

CANDLE_COUNT = 200

MAX_WORKERS = 12


session = requests.Session()

session.headers.update({
    "User-Agent": "CoinDCX-Futures-RSI-Scanner/1.1"
})


# ============================================================
# GET ACTIVE FUTURES
# ============================================================

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

    instruments = [
        x for x in instruments
        if isinstance(x, str)
    ]

    return sorted(instruments)


# ============================================================
# GET CANDLES
# ============================================================

def get_candles(pair, resolution):

    now = int(time.time())

    if resolution == 5:
        seconds_per_candle = 5 * 60

    elif resolution == 15:
        seconds_per_candle = 15 * 60

    else:
        raise ValueError(
            "Unsupported resolution"
        )

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

    candles = result.get(
        "data",
        []
    )

    candles.sort(
        key=lambda x: x.get(
            "time",
            0
        )
    )

    return candles


# ============================================================
# RSI CALCULATION
# ============================================================

def calculate_rsi(closes, period=14):

    if len(closes) < period + 1:
        return None

    changes = []

    for i in range(
        1,
        len(closes)
    ):
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

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            + losses[i]
        ) / period

    if avg_loss == 0:

        if avg_gain == 0:
            return 50.0

        return 100.0

    rs = avg_gain / avg_loss

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


# ============================================================
# GET RSI
# ============================================================

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

        except (
            KeyError,
            TypeError,
            ValueError
        ):

            continue

    return calculate_rsi(
        closes,
        period=14
    )


# ============================================================
# GET CURRENT PRICES
# ============================================================

def get_current_prices():

    response = session.get(
        CURRENT_PRICES_URL,
        timeout=15
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# CLEAN COIN NAME
# ============================================================

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


# ============================================================
# PROCESS ONE FUTURES PAIR
# ============================================================

def process_pair(pair, prices):

    try:

        price_info = prices.get(
            pair,
            {}
        )

        current_price = (
            price_info.get("ls")
        )

        if current_price is not None:

            current_price = float(
                current_price
            )

        # ----------------------------------------
        # EXACT SAME RSI LOGIC
        # ----------------------------------------

        rsi_5m = get_rsi(
            pair,
            5
        )

        rsi_15m = get_rsi(
            pair,
            15
        )

        print(
            f"{pair:25} "
            f"5m="
            f"{rsi_5m if rsi_5m is not None else '-':>6} "
            f"15m="
            f"{rsi_15m if rsi_15m is not None else '-':>6}"
        )

        return {
            "pair": pair,
            "coin": coin_name(pair),
            "price": current_price,
            "rsi_5m": rsi_5m,
            "rsi_15m": rsi_15m
        }

    except Exception as error:

        print(
            f"Error processing "
            f"{pair}: {error}"
        )

        return None


# ============================================================
# BUILD COMPLETE SCANNER
# ============================================================

def build_scanner():

    start_time = time.time()

    instruments = get_active_futures()

    prices = get_current_prices()

    rows = []

    print()
    print(
        f"Starting scan for "
        f"{len(instruments)} Futures coins..."
    )
    print()

    # ========================================================
    # PROCESS MULTIPLE COINS SIMULTANEOUSLY
    # ========================================================

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                process_pair,
                pair,
                prices
            ): pair
            for pair in instruments
        }

        for future in as_completed(
            futures
        ):

            pair = futures[future]

            try:

                result = future.result()

                if result is not None:
                    rows.append(result)

            except Exception as error:

                print(
                    f"Thread error "
                    f"{pair}: {error}"
                )

    # ========================================================
    # SORT BY 5-MINUTE RSI DESCENDING
    # ========================================================

    rows.sort(
        key=lambda x: (
            x["rsi_5m"]
            if x["rsi_5m"] is not None
            else -1
        ),
        reverse=True
    )

    elapsed = (
        time.time()
        - start_time
    )

    print()
    print(
        "=============================================="
    )

    print(
        f"Scanner completed: "
        f"{len(rows)} coins in "
        f"{elapsed:.2f} seconds"
    )

    print(
        "=============================================="
    )

    print()

    return rows


# ============================================================
# API
# ============================================================

@app.route("/api/data")
def api_data():

    try:

        rows = build_scanner()

        return jsonify({
            "success": True,
            "timestamp": int(
                time.time()
            ),
            "count": len(rows),
            "data": rows
        })

    except Exception as error:

        print(
            f"/api/data error: "
            f"{error}"
        )

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


# ============================================================
# HTML DASHBOARD
# ============================================================

HTML = r"""
<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>
CoinDCX Futures RSI Scanner
</title>


<style>

body {

    font-family:
        Arial,
        sans-serif;

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

    border-bottom:
        1px solid #ddd;

}


input {

    width: 250px;

    max-width: 80%;

    padding: 10px;

    border:
        1px solid #ccc;

    border-radius: 6px;

    font-size: 15px;

}


button {

    padding:
        10px 14px;

    margin-left: 8px;

    border: none;

    border-radius: 6px;

    cursor: pointer;

}


table {

    width: 100%;

    border-collapse:
        collapse;

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

    border-bottom:
        1px solid #eee;

    text-align: right;

}


.coin {

    font-weight: bold;

}


.status {

    padding:
        12px 15px;

    background: #f9fafb;

    font-size: 13px;

    color: #555;

}


.loading {

    color: #1d4ed8;

}


.error {

    color: #b91c1c;

}


.success {

    color: #166534;

}


@media(max-width:700px) {

    table {

        font-size: 13px;

    }


    th,
    td {

        padding:
            8px 6px;

    }


    .header h1 {

        font-size: 18px;

    }

}

</style>

</head>


<body>


<div class="header">

    <h1>
        CoinDCX Futures RSI Scanner
    </h1>

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

    <button
        onclick="manualRefresh()"
    >
        Refresh
    </button>

</div>


<div
    class="status"
    id="status"
>

    Loading...

</div>


<table>

<thead>

<tr>

<th>Coin</th>

<th>Price</th>

<th>RSI 5m</th>

<th>RSI 15m</th>

</tr>

</thead>


<tbody
    id="tableBody"
>
</tbody>


</table>


<script>


let allData = [];

let requestRunning = false;


function formatPrice(value) {

    if (
        value === null ||
        value === undefined
    ) {

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

    if (
        value === null ||
        value === undefined
    ) {

        return "-";

    }

    return Number(value)
        .toFixed(2);

}


function render() {

    const search =
        document
        .getElementById("search")
        .value
        .toUpperCase()
        .trim();


    const filtered =
        allData.filter(
            row =>
                row.coin
                .toUpperCase()
                .includes(search)
        );


    let html = "";


    for (
        const row of filtered
    ) {

        html += `
        <tr>

            <td class="coin">
                ${row.coin}
            </td>

            <td>
                ${formatPrice(row.price)}
            </td>

            <td>
                ${formatRSI(row.rsi_5m)}
            </td>

            <td>
                ${formatRSI(row.rsi_15m)}
            </td>

        </tr>
        `;

    }


    document
        .getElementById(
            "tableBody"
        )
        .innerHTML = html;

}


async function loadData() {

    if (requestRunning) {

        return;

    }


    requestRunning = true;


    const status =
        document.getElementById(
            "status"
        );


    status.className =
        "status loading";


    status.innerText =
        "Updating Futures data...";


    const startTime =
        Date.now();


    try {

        const response =
            await fetch(
                "/api/data",
                {
                    cache: "no-store"
                }
            );


        const contentType =
            response.headers.get(
                "content-type"
            );


        if (
            !contentType ||
            !contentType.includes(
                "application/json"
            )
        ) {

            const text =
                await response.text();


            throw new Error(
                "Server returned non-JSON response. "
                +
                text.substring(
                    0,
                    100
                )
            );

        }


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


        const seconds =
            (
                (
                    Date.now()
                    - startTime
                )
                / 1000
            )
            .toFixed(1);


        status.className =
            "status success";


        status.innerText =
            `Showing ${result.count} active Futures coins`
            +
            ` • Last update: ${now}`
            +
            ` • Scan: ${seconds}s`;

    }

    catch (error) {

        status.className =
            "status error";


        status.innerText =
            "Error: "
            + error.message;

    }

    finally {

        requestRunning = false;

    }

}


async function refreshLoop() {

    await loadData();


    setTimeout(
        refreshLoop,
        15000
    );

}


async function manualRefresh() {

    if (
        !requestRunning
    ) {

        await loadData();

    }

}


refreshLoop();


</script>


</body>

</html>
"""


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/")
def home():

    return render_template_string(
        HTML
    )


# ============================================================
# LOCAL START
# ============================================================

if __name__ == "__main__":

    print()

    print(
        "=============================================="
    )

    print(
        " CoinDCX Futures RSI Scanner"
    )

    print(
        " Version 1.1 - Concurrent"
    )

    print(
        "=============================================="
    )

    print()

    print(
        "Open this in your browser:"
    )

    print()

    print(
        "http://127.0.0.1:5000"
    )

    print()

    print(
        "Press CTRL+C to stop the scanner."
    )

    print()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )