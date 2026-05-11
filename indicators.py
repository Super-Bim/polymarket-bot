import pandas as pd
import numpy as np
from typing import List, Tuple
import config

def calculate_indicators(candles: List, symbol: str = "") -> pd.DataFrame:
    """
    Converts Candle list to Pandas DataFrame and calculates standard indicators.
    """
    if not candles:
        return pd.DataFrame()

    # Extract candle data
    data = {
        "open":  [c.open for c in candles],
        "high":  [c.high for c in candles],
        "low":   [c.low for c in candles],
        "close": [c.close for c in candles],
        "volume": [c.volume for c in candles],
    }
    df = pd.DataFrame(data)

    # --- EMA ---
    df["ema_short"] = df["close"].ewm(span=config.FILTER_EMA_PERIOD_SHORT, adjust=False).mean()
    df["ema_long"]  = df["close"].ewm(span=config.FILTER_EMA_PERIOD_LONG, adjust=False).mean()

    # --- RSI ---
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=config.FILTER_RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=config.FILTER_RSI_PERIOD).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # --- MACD ---
    exp1 = df["close"].ewm(span=config.FILTER_MACD_FAST, adjust=False).mean()
    exp2 = df["close"].ewm(span=config.FILTER_MACD_SLOW, adjust=False).mean()
    df["macd_line"]   = exp1 - exp2
    df["macd_signal"] = df["macd_line"].ewm(span=config.FILTER_MACD_SIGNAL, adjust=False).mean()
    df["macd_hist"]   = df["macd_line"] - df["macd_signal"]

    # --- Bollinger Bands ---
    df["bb_mid"] = df["close"].rolling(window=config.FILTER_BB_PERIOD).mean()
    std_dev = df["close"].rolling(window=config.FILTER_BB_PERIOD).std()
    df["bb_up"]   = df["bb_mid"] + (std_dev * config.FILTER_BB_STDDEV)
    df["bb_down"] = df["bb_mid"] - (std_dev * config.FILTER_BB_STDDEV)

    return df

def evaluate_filters(candles: List, entry_direction: str, logger=None) -> Tuple[bool, str]:
    """
    Evaluates all ENABLED filters in config.py.
    Returns (AllPassed: bool, LogMessage: str)
    """
    if not candles:
        return False, "No historical data available"

    # We need enough data for the longest period (EMA_LONG or BB_PERIOD)
    min_req = max(config.FILTER_EMA_PERIOD_LONG, config.FILTER_BB_PERIOD, config.FILTER_RSI_PERIOD, config.FILTER_MACD_SLOW) + 5
    if len(candles) < min_req:
        return False, f"Insufficient historical candles (need {min_req}, got {len(candles)})"

    try:
        df = calculate_indicators(candles)
        latest = df.iloc[-1]
        
        # High-integrity log feedback: ONLY show stats that the user actually activated
        debug_parts = [f"Close: {latest['close']:.2f}"]
        if config.FILTER_ENABLE_RSI:    debug_parts.append(f"RSI:{latest['rsi']:.1f}")
        if config.FILTER_ENABLE_EMA:    debug_parts.append(f"EMA(S/L):{latest['ema_short']:.2f}/{latest['ema_long']:.2f}")
        if config.FILTER_ENABLE_MACD:   debug_parts.append(f"MACD_H:{latest['macd_hist']:.4f}")
        if config.FILTER_ENABLE_BBANDS: debug_parts.append(f"BB:{latest['bb_up']:.2f}/{latest['bb_down']:.2f}")
        
        if logger:
             logger.info(f"[Indicators] " + " | ".join(debug_parts))

        # 1. EMA Filter Logic
        # Confirms the directional trend. 
        # If entry "UP" => Short EMA > Long EMA
        if config.FILTER_ENABLE_EMA:
            if entry_direction == "UP" and latest["ema_short"] <= latest["ema_long"]:
                return False, f"EMA Filter BLOCKED: Short EMA ({latest['ema_short']:.2f}) <= Long EMA ({latest['ema_long']:.2f}) for UP entry."
            if entry_direction == "DOWN" and latest["ema_short"] >= latest["ema_long"]:
                return False, f"EMA Filter BLOCKED: Short EMA ({latest['ema_short']:.2f}) >= Long EMA ({latest['ema_long']:.2f}) for DOWN entry."

        # 2. RSI Filter Logic
        # Standard Overbought/Oversold conditions
        if config.FILTER_ENABLE_RSI:
            if entry_direction == "UP" and latest["rsi"] > config.FILTER_RSI_OVERSOLD:
                 return False, f"RSI Filter BLOCKED: RSI ({latest['rsi']:.1f}) above Oversold threshold ({config.FILTER_RSI_OVERSOLD}) for UP entry."
            if entry_direction == "DOWN" and latest["rsi"] < config.FILTER_RSI_OVERBOUGHT:
                 return False, f"RSI Filter BLOCKED: RSI ({latest['rsi']:.1f}) below Overbought threshold ({config.FILTER_RSI_OVERBOUGHT}) for DOWN entry."

        # 3. MACD Filter Logic
        # Checks if the Histogram confirms the direction of our entry
        if config.FILTER_ENABLE_MACD:
            if entry_direction == "UP" and latest["macd_hist"] < 0:
                 return False, f"MACD Filter BLOCKED: Histogram is negative ({latest['macd_hist']:.4f}) for UP entry."
            if entry_direction == "DOWN" and latest["macd_hist"] > 0:
                 return False, f"MACD Filter BLOCKED: Histogram is positive ({latest['macd_hist']:.4f}) for DOWN entry."

        # 4. Bollinger Bands Logic
        # Checks for extension outside the bands
        if config.FILTER_ENABLE_BBANDS:
            if entry_direction == "UP" and latest["close"] > latest["bb_down"]:
                 return False, f"BBands Filter BLOCKED: Price {latest['close']:.2f} is above lower band {latest['bb_down']:.2f}."
            if entry_direction == "DOWN" and latest["close"] < latest["bb_up"]:
                 return False, f"BBands Filter BLOCKED: Price {latest['close']:.2f} is below upper band {latest['bb_up']:.2f}."

        # 5. Fibonacci Filter Logic
        # Simple check against dynamic lookback levels
        if config.FILTER_ENABLE_FIBO:
            window = df.tail(config.FILTER_FIBO_LOOKBACK)
            highest = window["high"].max()
            lowest  = window["low"].min()
            diff = highest - lowest
            
            if diff > 0:
                # For a DOWN sequence leading to UP entry, verify we hit/exceeded retracement level from top
                fibo_level = highest - (diff * config.FILTER_FIBO_LEVEL)
                
                if entry_direction == "UP" and latest["close"] > fibo_level:
                    return False, f"FIBO Filter BLOCKED: Price {latest['close']:.2f} above target Level ({config.FILTER_FIBO_LEVEL:.3f} -> {fibo_level:.2f})."
                
                # For an UP sequence leading to DOWN entry, verify we rose above the target retracement from bottom
                fibo_inv_level = lowest + (diff * config.FILTER_FIBO_LEVEL)
                if entry_direction == "DOWN" and latest["close"] < fibo_inv_level:
                    return False, f"FIBO Filter BLOCKED: Price {latest['close']:.2f} below target Level ({config.FILTER_FIBO_LEVEL:.3f} -> {fibo_inv_level:.2f})."

        # If we got here, all ENABLED filters passed!
        return True, "All active technical filters PASSED."

    except Exception as e:
        return False, f"Internal indicator evaluation error: {e}"
