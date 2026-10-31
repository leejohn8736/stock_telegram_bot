import math

def calculate_sma(prices, period=20):
    """
    Calculates Simple Moving Average (SMA) of a list of prices.
    Returns a list of SMAs. The first (period - 1) elements will be None.
    """
    if len(prices) < period:
        return [None] * len(prices)
        
    sma = [None] * len(prices)
    current_sum = sum(prices[:period])
    sma[period - 1] = current_sum / period
    
    for i in range(period, len(prices)):
        current_sum = current_sum - prices[i - period] + prices[i]
        sma[i] = current_sum / period
        
    return sma


def calculate_ema(prices, period=20):
    """
    Calculates Exponential Moving Average (EMA) of a list of prices.
    Returns a list of EMAs. The first (period - 1) elements will be None.
    
    Handles None values in the input list (e.g., from MACD calculation).
    """
    if len(prices) < period:
        return [None] * len(prices)
    
    ema = [None] * len(prices)
    multiplier = 2 / (period + 1)
    
    # Seed the EMA from the first 'period' non-None values.
    valid_indices = [i for i, p in enumerate(prices) if p is not None]
    if len(valid_indices) < period:
        return [None] * len(prices)
    
    first_valid_idx = valid_indices[period - 1]
    ema[first_valid_idx] = sum(prices[i] for i in valid_indices[:period]) / period
    
    # Calculate EMA for remaining indices
    previous = ema[first_valid_idx]
    for idx in valid_indices[period:]:
        previous = (prices[idx] - previous) * multiplier + previous
        ema[idx] = previous
    
    return ema


def calculate_bollinger_bands(prices, period=20, num_std=2):
    """
    Calculates Bollinger Bands (Upper, Middle/SMA, Lower) of a list of prices.
    Returns three lists: (upper_band, middle_band, lower_band).
    First (period - 1) elements will be None.
    """
    if len(prices) < period:
        return [None] * len(prices), [None] * len(prices), [None] * len(prices)
        
    sma = calculate_sma(prices, period)
    upper_band = [None] * len(prices)
    lower_band = [None] * len(prices)
    
    for i in range(period - 1, len(prices)):
        mean = sma[i]
        # Calculate standard deviation for the period
        variance = sum((prices[j] - mean) ** 2 for j in range(i - period + 1, i + 1)) / period
        std_dev = math.sqrt(variance)
        
        upper_band[i] = mean + (num_std * std_dev)
        lower_band[i] = mean - (num_std * std_dev)
        
    return upper_band, sma, lower_band


def calculate_rsi(prices, period=14):
    """
    Calculates the Relative Strength Index (RSI) using Wilder's Smoothing.
    Returns a list of RSI values. First 'period' elements will be None.
    """
    if len(prices) < period + 1:
        return [None] * len(prices)
        
    rsi = [None] * len(prices)
    
    # Calculate daily price changes
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    
    # Separate gains and losses
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    
    # Calculate first average gain and loss (SMA)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    if avg_loss == 0:
        rsi[period] = 100
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100 - (100 / (1 + rs))
        
    # Apply Wilder's smoothing technique for the remaining periods
    for i in range(period + 1, len(prices)):
        gain = gains[i - 1]
        loss = losses[i - 1]
        
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        
        if avg_loss == 0:
            rsi[i] = 100
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100 - (100 / (1 + rs))
            
    return rsi


def calculate_macd(prices, fast=12, slow=26, signal=9):
    """
    Calculates MACD (Moving Average Convergence Divergence).
    Returns (macd_line, signal_line, histogram).
    First (slow - 1 + signal - 1) elements will be None.
    """
    if len(prices) < slow + signal:
        return [None] * len(prices), [None] * len(prices), [None] * len(prices)
    
    fast_ema = calculate_ema(prices, fast)
    slow_ema = calculate_ema(prices, slow)
    
    macd_line = [None] * len(prices)
    for i in range(len(prices)):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            macd_line[i] = fast_ema[i] - slow_ema[i]
    
    # Signal line = EMA of MACD line
    signal_line = calculate_ema(macd_line, signal)
    
    # Histogram = MACD - Signal
    histogram = [None] * len(prices)
    for i in range(len(prices)):
        if macd_line[i] is not None and signal_line[i] is not None:
            histogram[i] = macd_line[i] - signal_line[i]
    
    return macd_line, signal_line, histogram


def calculate_atr(highs, lows, closes, period=14):
    """
    Calculates Average True Range (ATR) using Wilder's Smoothing.
    ATR measures market volatility - essential for stop-loss/target price setting.
    Returns a list of ATR values. First 'period' elements will be None.
    """
    if len(closes) < period + 1:
        return [None] * len(closes)
    
    atr = [None] * len(closes)
    
    # True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        true_ranges.append(tr)
    
    # First ATR = SMA of first 'period' true ranges
    atr_value = sum(true_ranges[:period]) / period
    atr[period] = atr_value
    
    # Wilder's Smoothing
    for i in range(period, len(true_ranges)):
        atr_value = (atr_value * (period - 1) + true_ranges[i]) / period
        atr[i + 1] = atr_value
    
    return atr


def calculate_volume_trend(volumes, period=20):
    """
    Analyzes volume trend. Returns the average volume ratio.
    > 1.0 means recent volume is higher than average (increased activity)
    < 1.0 means recent volume is lower than average (decreased activity)
    """
    if len(volumes) < period + 5:
        return 1.0
    
    recent = volumes[-5:]  # last 5 days
    historical = volumes[-(period + 5):-5]  # previous 'period' days
    
    avg_recent = sum(recent) / len(recent)
    avg_historical = sum(historical) / len(historical)
    
    if avg_historical == 0:
        return 1.0
    
    return avg_recent / avg_historical


def find_support_resistance(highs, lows, closes, period=20, min_touch=2):
    """
    Finds support and resistance levels using pivot point detection.
    Unlike simple min/max, this identifies price levels that have been
    tested (touched) multiple times, making them more reliable.

    Returns (support_level, resistance_level) or (None, None).
    
    - Uses fractal pivots: a high is a pivot if it's the highest of the
      surrounding 'window' bars; a low is a pivot if it's the lowest.
    - Support = strongest pivot low (most touches weighted by recency)
    - Resistance = strongest pivot high (most touches weighted by recency)
    """
    if len(highs) < period or len(lows) < period:
        return None, None
    
    # Look back window
    window = min(period, len(highs) - 1)
    pivot_window = 2  # bars on each side to confirm a pivot
    
    pivot_highs = []
    pivot_lows = []
    
    # Detect local pivots (fractals)
    for i in range(pivot_window, len(highs) - pivot_window):
        is_high = True
        is_low = True
        for j in range(1, pivot_window + 1):
            if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                is_high = False
            if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                is_low = False
        if is_high:
            pivot_highs.append((i, highs[i]))
        if is_low:
            pivot_lows.append((i, lows[i]))
    
    if not pivot_highs and not pivot_lows:
        # Fallback to simple min/max
        recent_highs = highs[-period:]
        recent_lows = lows[-period:]
        return min(recent_lows), max(recent_highs)
    
    # Score each pivot by how many times price returned to that level (touches)
    def _score_level(level, prices, tolerance_pct=0.01):
        """Count how many times price came within tolerance_pct of the level."""
        touches = 0
        tolerance = level * tolerance_pct
        for p in prices:
            if abs(p - level) <= tolerance:
                touches += 1
        return touches
    
    # Consider only pivots within the lookback window
    start_idx = max(0, len(highs) - window * 3)  # look back 3x period for pivots
    
    # Score and pick strongest support (pivot low)
    best_support = None
    best_support_score = -1
    for idx, level in pivot_lows:
        if idx < start_idx:
            continue
        # Recency + touch count weighting
        recency_weight = (idx - start_idx + 1) / (len(highs) - start_idx + 1)
        touch_score = _score_level(level, lows[idx:])
        total = touch_score + recency_weight * 2
        if total > best_support_score:
            best_support_score = total
            best_support = level
    
    # Score and pick strongest resistance (pivot high)
    best_resistance = None
    best_resistance_score = -1
    for idx, level in pivot_highs:
        if idx < start_idx:
            continue
        recency_weight = (idx - start_idx + 1) / (len(highs) - start_idx + 1)
        touch_score = _score_level(level, highs[idx:])
        total = touch_score + recency_weight * 2
        if total > best_resistance_score:
            best_resistance_score = total
            best_resistance = level
    
    # Ensure support < resistance; if inverted, fallback to min/max
    if best_support is not None and best_resistance is not None:
        if best_support < best_resistance:
            return best_support, best_resistance
    
    # Fallback
    recent_highs = highs[-period:]
    recent_lows = lows[-period:]
    return min(recent_lows), max(recent_highs)


def detect_market_regime(closes, sma_20, sma_50, period=20):
    """
    Detects the current market regime (trending vs ranging).
    
    Returns a string: 'TRENDING_UP', 'TRENDING_DOWN', or 'RANGING'.
    
    Method: Uses the ADX-like concept - when the price moves strongly
    in one direction relative to the moving average spread, it's trending.
    When the spread oscillates without direction, it's ranging.
    """
    if len(closes) < period * 2:
        return 'RANGING'
    
    # Get valid SMA values
    valid_sma20 = [v for v in sma_20 if v is not None]
    valid_sma50 = [v for v in sma_50 if v is not None]
    if len(valid_sma20) < period or len(valid_sma50) < period:
        return 'RANGING'
    
    # Directional movement: percentage of closes above/below SMA50
    sma50 = sma_50
    
    # Use last 'period' bars for regime detection
    lookback = closes[-period:]
    sma50_recent = sma50[-period:]
    
    above_count = 0
    below_count = 0
    valid_pairs = 0
    
    for c, s in zip(lookback, sma50_recent):
        if s is None:
            continue
        valid_pairs += 1
        if c > s:
            above_count += 1
        elif c < s:
            below_count += 1
    
    if valid_pairs == 0:
        return 'RANGING'
    
    above_pct = above_count / valid_pairs
    below_pct = below_count / valid_pairs
    
    # Slope of SMA20 (trend strength)
    if len(valid_sma20) >= 10:
        slope = (valid_sma20[-1] - valid_sma20[-10]) / valid_sma20[-10] * 100
    else:
        slope = 0
    
    # Regime classification
    # Strong trend: >70% of bars on one side of SMA50 with meaningful slope
    # Weak/range: mixed positioning or flat slope
    if above_pct >= 0.7 and slope > 0.5:
        return 'TRENDING_UP'
    elif below_pct >= 0.7 and slope < -0.5:
        return 'TRENDING_DOWN'
    else:
        return 'RANGING'
