from indicators import (
    calculate_bollinger_bands, calculate_rsi, find_support_resistance,
    calculate_macd, calculate_volume_trend, calculate_sma, calculate_atr,
    detect_market_regime
)

def predict_buy_sell_prices(stock_data):
    """
    Analyze stock data with technical indicators and return buy/sell recommendation.
    
    Uses 8 indicators: RSI, MACD, Bollinger Bands, SMA 20/50, Volume, S/R, ATR, Market Regime.
    Market regime adjusts weights: trend indicators in trends, mean-reversion in ranges.
    
    Returns: { recommendation, confidence, indicators, score, signals, ... }
    """
    closes = stock_data["closes"]
    highs = stock_data["highs"]
    lows = stock_data["lows"]
    volumes = stock_data.get("volumes", [0] * len(closes))
    current_price = stock_data["current_price"]
    
    # Calculate all indicators
    upper_bands, middle_bands, lower_bands = calculate_bollinger_bands(closes, period=20)
    rsi_list = calculate_rsi(closes, period=14)
    macd_line, signal_line, histogram = calculate_macd(closes, fast=12, slow=26, signal=9)
    sma_20 = middle_bands
    sma_50 = calculate_sma(closes, period=50)
    atr_list = calculate_atr(highs, lows, closes, period=14)
    support, resistance = find_support_resistance(highs, lows, closes, period=20)
    volume_ratio = calculate_volume_trend(volumes, period=20)
    market_regime = detect_market_regime(closes, sma_20, sma_50, period=20)
    
    # Extract latest valid values
    def _last_valid(lst, default=None):
        for i in range(len(lst) - 1, -1, -1):
            if lst[i] is not None:
                return lst[i]
        return default
    
    bb_upper = _last_valid(upper_bands)
    bb_middle = _last_valid(middle_bands)
    bb_lower = _last_valid(lower_bands)
    rsi = _last_valid(rsi_list)
    macd_val = _last_valid(macd_line)
    signal_val = _last_valid(signal_line)
    hist_val = _last_valid(histogram)
    sma20_val = _last_valid(sma_20)
    sma50_val = _last_valid(sma_50, sma20_val)
    atr_val = _last_valid(atr_list)
    
    # Safety check
    required = [bb_upper, bb_middle, bb_lower, rsi, support, resistance, macd_val, signal_val, atr_val]
    if None in required:
        return {
            "current_price": current_price,
            "error": "Insufficient historical data to calculate technical indicators."
        }
    
    bandwidth = (bb_upper - bb_lower) / bb_middle if bb_middle != 0 else 0
    bb_position = (current_price - bb_lower) / (bb_upper - bb_lower) if (bb_upper != bb_lower) else 0.5
    
    # Market regime adaptive weights
    if market_regime == "TRENDING_UP" or market_regime == "TRENDING_DOWN":
        trend_weight = 1.3
        meanrev_weight = 0.8
    else:
        trend_weight = 0.8
        meanrev_weight = 1.3
    
    # Bollinger Band squeeze detection
    is_squeeze = bandwidth < 0.05
    squeeze_weight = 0.5
    score = 0.0
    signals = []
    
    # 1. RSI Score (평균회귀 지표, 가중치 2.0 * meanrev_weight)
    rsi_weight = 2.0 * meanrev_weight
    if rsi <= 25:
        score += rsi_weight
        signals.append("RSI 극단적 과매도 (강한 매수 신호)")
    elif rsi <= 30:
        score += rsi_weight * 0.75
        signals.append("RSI 과매도 (매수 신호)")
    elif rsi <= 40:
        score += rsi_weight * 0.25
        signals.append("RSI 저평가 구간 (약한 매수 신호)")
    elif rsi < 45:
        score += 0.0
        signals.append("RSI 중립 (약한 저평가)")
    elif rsi <= 55:
        score += 0.0
        signals.append("RSI 중립")
    elif rsi < 60:
        score += 0.0
        signals.append("RSI 중립 (약한 고평가)")
    elif rsi < 70:
        score -= rsi_weight * 0.25
        signals.append("RSI 고평가 구간 (약한 매도 신호)")
    elif rsi < 75:
        score -= rsi_weight * 0.75
        signals.append("RSI 과매수 (매도 신호)")
    else:
        score -= rsi_weight
        signals.append("RSI 극단적 과매수 (강한 매도 신호)")
    
    # 스퀴즈 상태에서는 RSI 신호 강화 (수정 5)
    if is_squeeze:
        if rsi <= 30:
            score += squeeze_weight
        elif rsi >= 70:
            score -= squeeze_weight
    
    # 2. MACD Score (추세 지표, 가중치 2.0 * trend_weight)
    macd_weight = 2.0 * trend_weight
    if hist_val > 0 and macd_val > 0:
        score += macd_weight * 0.75
        signals.append("MACD 상승 추세 강화 (매수 신호)")
    elif hist_val > 0 and macd_val < 0:
        score += macd_weight * 0.5
        signals.append("MACD 반등 시도 (약한 매수 신호)")
    elif hist_val < 0 and macd_val < 0:
        score -= macd_weight * 0.75
        signals.append("MACD 하락 추세 강화 (매도 신호)")
    elif hist_val < 0 and macd_val > 0:
        score -= macd_weight * 0.5
        signals.append("MACD 하락 반전 (약한 매도 신호)")
    else:
        score += 0.0
        signals.append("MACD 중립")
    
    # 3. Bollinger Band Position Score (평균회귀 지표, 가중치 1.5 * meanrev_weight)
    bb_weight = 1.5 * meanrev_weight
    if bb_position <= 0.05:
        score += bb_weight
        signals.append("볼린저 하단 하회")
    elif bb_position <= 0.2:
        score += bb_weight * 0.67
        signals.append("볼린저 하단 근접")
    elif bb_position <= 0.35:
        score += bb_weight * 0.33
        signals.append("볼린저 하단 부근")
    elif 0.4 <= bb_position <= 0.6:
        score += 0.0
        signals.append("볼린저 중앙")
    elif bb_position >= 0.95:
        score -= bb_weight
        signals.append("볼린저 상단 상회")
    elif bb_position >= 0.8:
        score -= bb_weight * 0.67
        signals.append("볼린저 상단 근접")
    elif bb_position >= 0.65:
        score -= bb_weight * 0.33
        signals.append("볼린저 상단 부근")
    
    # 4. SMA 20 vs 50 Golden/Dead Cross (추세 지표, 가중치 1.5 * trend_weight)
    sma_weight = 1.5 * trend_weight
    if sma20_val is not None and sma50_val is not None:
        # 이전 봉의 SMA 값으로 실제 크로스 판정 (버그 수정)
        prev_sma20 = None
        prev_sma50 = None
        for i in range(len(sma_20) - 1, -1, -1):
            if sma_20[i] is not None and sma_50[i] is not None:
                prev_sma20 = sma_20[i]
                prev_sma50 = sma_50[i]
                break
        
        if prev_sma20 is not None and prev_sma50 is not None:
            # 실제 골든크로스: 이전 SMA20 <= 이전 SMA50, 현재 SMA20 > 현재 SMA50
            if prev_sma20 <= prev_sma50 and sma20_val > sma50_val:
                score += sma_weight
                signals.append("골든크로스 발생! (SMA20이 SMA50 상향 돌파)")
            # 실제 데드크로스: 이전 SMA20 >= 이전 SMA50, 현재 SMA20 < 현재 SMA50
            elif prev_sma20 >= prev_sma50 and sma20_val < sma50_val:
                score -= sma_weight
                signals.append("데드크로스 발생! (SMA20이 SMA50 하향 돌파)")
            elif sma20_val > sma50_val:
                score += sma_weight * 0.5
                signals.append("SMA20이 SMA50 위 (단기 우상향 추세)")
            elif sma20_val < sma50_val:
                score -= sma_weight * 0.5
                signals.append("SMA20이 SMA50 아래 (단기 우하향 추세)")
            else:
                score += 0.0
    
    # 5. Volume Trend (독립적 확인, 가중치 1.0)
    # 거래량은 기존 점수 방향을 증폭시키는 것이 아니라
    # 추세 전환/돌파 시 거래량 동반 여부를 독립적으로 확인
    volume_weight = 1.0
    if volume_ratio > 1.5:
        # 거래량 급증: 추세 전환 확인용
        if hist_val > 0 and macd_val > 0:
            score += volume_weight
            signals.append("거래량 급증 + MACD 상승 (매수세 확인)")
        elif hist_val < 0 and macd_val < 0:
            score -= volume_weight
            signals.append("거래량 급증 + MACD 하락 (매도세 확인)")
        else:
            score += 0.0
            signals.append("거래량 급증 (방향성 확인 필요)")
    elif volume_ratio > 1.2:
        if hist_val > 0 and macd_val > 0:
            score += volume_weight * 0.5
            signals.append("거래량 증가 + MACD 상승 (매수세 동반)")
        elif hist_val < 0 and macd_val < 0:
            score -= volume_weight * 0.5
            signals.append("거래량 증가 + MACD 하락 (매도세 동반)")
    
    # 6. Support/Resistance Proximity (가중치 1.0)
    sr_weight = 1.0
    if support is not None and resistance is not None:
        dist_to_support = abs(current_price - support) / current_price * 100
        dist_to_resistance = abs(current_price - resistance) / current_price * 100
        
        if dist_to_support < 1.5:  # 지지선 1.5% 이내
            score += sr_weight
            signals.append("지지선 근접 (반등 가능)")
        elif dist_to_resistance < 1.5:  # 저항선 1.5% 이내
            score -= sr_weight
            signals.append("저항선 근접 (조정 가능)")
    
    # ============================================================
    # FINAL RECOMMENDATION
    # ============================================================
    # 최대 점수: RSI(2.0) + MACD(2.0) + BB(1.5) + SMA(1.5) + Volume(1.0) + S/R(1.0) = 9.0
    # 최소 점수: -9.0
    # 시장 국면에 따라 가중치가 조정되므로 실제 범위는 ±9.0 ~ ±11.7
    
    recommendation = "HOLD"
    confidence = 50
    
    # 수정 2: 추세장에서 매도/매수 임계값 조정
    # 상승 추세장에서는 매도 임계값을 더 엄격하게 (잘못된 매도 신호 감소)
    # 하락 추세장에서는 매도 임계값을 더 민감하게 (빠른 매도 감지)
    if market_regime == "TRENDING_UP":
        sell_threshold = -2.5   # 상승 추세에서는 더 엄격한 매도 기준
        buy_threshold = 1.0     # 상승 추세에서는 더 민감한 매수 기준
    elif market_regime == "TRENDING_DOWN":
        sell_threshold = -1.0   # 하락 추세에서는 더 민감한 매도 기준
        buy_threshold = 2.5     # 하락 추세에서는 더 엄격한 매수 기준
    else:  # RANGING
        sell_threshold = -1.5
        buy_threshold = 1.5
    
    if score >= 3.5:
        recommendation = "STRONG BUY"
        confidence = min(95, 70 + int(abs(score) * 2.5))
    elif score >= buy_threshold:
        recommendation = "BUY"
        confidence = min(90, 55 + int(abs(score) * 5))
    elif score <= -3.5:
        recommendation = "STRONG SELL"
        confidence = min(95, 70 + int(abs(score) * 2.5))
    elif score <= sell_threshold:
        recommendation = "SELL"
        confidence = min(90, 55 + int(abs(score) * 5))
    else:
        recommendation = "HOLD"
        # 중립 구간에서도 점수 방향성 표시
        if score > 0:
            confidence = 55 + int(score * 5)
        elif score < 0:
            confidence = 55 + int(abs(score) * 5)
        else:
            confidence = 50
    
    # 수정 3: 신뢰도 계산 개선 - 지표 일치도 반영
    # 모든 지표가 같은 방향이면 신뢰도 +10, 충돌이 있으면 -10
    positive_signals = sum(1 for s in signals if "매수" in s or "상승" in s or "골든" in s or "반등" in s or "지지" in s)
    negative_signals = sum(1 for s in signals if "매도" in s or "하락" in s or "데드" in s or "저항" in s)
    
    if recommendation in ("STRONG BUY", "BUY"):
        if positive_signals >= 3 and negative_signals == 0:
            confidence += 10  # 모든 지표가 매수 방향
        elif negative_signals >= 2:
            confidence -= 10  # 지표 충돌
    elif recommendation in ("STRONG SELL", "SELL"):
        if negative_signals >= 3 and positive_signals == 0:
            confidence += 10  # 모든 지표가 매도 방향
        elif positive_signals >= 2:
            confidence -= 10  # 지표 충돌
    
    # 수정 6: 매도 신호에 추세 확인 조건 추가
    if recommendation in ("SELL", "STRONG SELL"):
        if sma20_val is not None and sma50_val is not None:
            if sma20_val > sma50_val:  # 상승 추세인데 매도 신호
                confidence -= 5  # 신뢰도 낮춤
            elif sma20_val < sma50_val:  # 하락 추세인데 매도 신호
                confidence += 5  # 신뢰도 높임
    
    # 스퀴즈 상태에서는 신뢰도 조정 (수정 5)
    if is_squeeze:
        confidence += 5  # 스퀴즈 후 방향성 돌파 가능성
    
    # Limit confidence
    confidence = min(95, max(25, round(confidence)))
    
    # ============================================================
    # PRICE TARGETS (ATR 기반)
    # ============================================================
    # ATR 기반 목표가: 변동성을 고려한 현실적인 목표가
    # - 매수 목표가: 현재가 - 0.5 * ATR (볼린저 하단과 지지선 사이)
    # - 매도 목표가: 현재가 + 1.0 * ATR (볼린저 상단과 저항선 사이)
    # - 손절가: 현재가 - 1.5 * ATR
    
    # ATR 기반 목표가 (1차)
    atr_buy_target = current_price - 0.5 * atr_val
    atr_sell_target = current_price + 1.0 * atr_val
    
    # 볼린저/지지저항 기반 목표가 (2차)
    bb_sr_buy_target = (0.6 * bb_lower) + (0.4 * support)
    bb_sr_sell_target = (0.6 * bb_upper) + (0.4 * resistance)
    
    # 최종 목표가: ATR 기반과 볼린저/지지저항 기반의 평균
    # ATR이 너무 크면(변동성 높음) 볼린저/지지저항 비중을 높임
    atr_pct = atr_val / current_price * 100 if current_price > 0 else 0
    
    if atr_pct > 5.0:  # 변동성 매우 높음
        buy_target = (0.3 * atr_buy_target) + (0.7 * bb_sr_buy_target)
        sell_target = (0.3 * atr_sell_target) + (0.7 * bb_sr_sell_target)
    elif atr_pct > 2.0:  # 변동성 보통
        buy_target = (0.5 * atr_buy_target) + (0.5 * bb_sr_buy_target)
        sell_target = (0.5 * atr_sell_target) + (0.5 * bb_sr_sell_target)
    else:  # 변동성 낮음
        buy_target = (0.7 * atr_buy_target) + (0.3 * bb_sr_buy_target)
        sell_target = (0.7 * atr_sell_target) + (0.3 * bb_sr_sell_target)
    
    # 손절가 (ATR 기반)
    stop_loss = current_price - 1.5 * atr_val
    
    # 목표가가 현재가보다 높으면 (매수 목표가가 현재가 이상이면) 조정
    if buy_target >= current_price:
        buy_target = current_price * 0.98  # 현재가 대비 2% 하락 시 매수
    
    # 목표가가 현재가보다 낮으면 (매도 목표가가 현재가 이하이면) 조정
    if sell_target <= current_price:
        sell_target = current_price * 1.02  # 현재가 대비 2% 상승 시 매도
    
    result = {
        "ticker": stock_data["ticker"],
        "currency": stock_data["currency"],
        "current_price": round(current_price, 2),
        "buy_target": round(buy_target, 2),
        "sell_target": round(sell_target, 2),
        "stop_loss": round(stop_loss, 2),
        "recommendation": recommendation,
        "confidence": confidence,
        "score": round(score, 2),
        "signals": signals[:7],  # 최대 7개 신호 표시
        "market_regime": market_regime,
        "indicators": {
            "rsi": round(rsi, 2),
            "macd": round(macd_val, 4),
            "macd_histogram": round(hist_val, 4),
            "bb_upper": round(bb_upper, 2),
            "bb_middle": round(bb_middle, 2),
            "bb_lower": round(bb_lower, 2),
            "sma_20": round(sma20_val, 2) if sma20_val else 0,
            "sma_50": round(sma50_val, 2) if sma50_val else 0,
            "support": round(support, 2),
            "resistance": round(resistance, 2),
            "atr": round(atr_val, 2),
            "atr_pct": round(atr_pct, 2),
            "bandwidth": round(bandwidth, 4),
            "volume_ratio": round(volume_ratio, 2)
        }
    }
    
    # 캔들 정보 (timeframe)이 있으면 결과에 추가
    if "candle_name" in stock_data:
        result["candle_name"] = stock_data["candle_name"]
    if "timeframe" in stock_data:
        result["timeframe"] = stock_data["timeframe"]
    
    return result
