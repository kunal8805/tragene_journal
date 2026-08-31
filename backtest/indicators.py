import pandas as pd
import numpy as np
from typing import Tuple, Optional, Union

class IndicatorCalculator:
    """
    Professional-grade technical indicator calculations.
    All indicators calculate using only past data (no lookahead bias).
    """
    
    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        """Simple Moving Average"""
        return series.rolling(window=period, min_periods=period).mean()
    
    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """Exponential Moving Average"""
        return series.ewm(span=period, adjust=False, min_periods=period).mean()
    
    @staticmethod
    def rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """
        Relative Strength Index (Wilder's smoothing)
        Returns values between 0-100
        """
        delta = close.diff()
        
        # Use Wilder's smoothing (EMA with alpha = 1/period)
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        
        avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        # Handle edge cases
        rsi = rsi.where(avg_loss != 0, 100.0)  # If no losses, RSI = 100
        rsi = rsi.where(avg_gain != 0, 0.0)    # If no gains, RSI = 0
        
        return rsi
    
    @staticmethod
    def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Moving Average Convergence Divergence
        Returns: (macd_line, signal_line, histogram)
        """
        ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
        ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    @staticmethod
    def bollinger_bands(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Bollinger Bands
        Returns: (upper_band, middle_band, lower_band)
        """
        middle = close.rolling(window=period, min_periods=period).mean()
        std = close.rolling(window=period, min_periods=period).std(ddof=0)
        
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        
        return upper, middle, lower
    
    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """
        Average True Range (Wilder's smoothing)
        Measures market volatility
        """
        high_low = high - low
        high_close = np.abs(high - close.shift())
        low_close = np.abs(low - close.shift())
        
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        
        # Wilder's smoothing
        atr = true_range.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
        
        return atr
    
    @staticmethod
    def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, 
                   k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
        """
        Stochastic Oscillator
        Returns: (%K, %D)
        """
        lowest_low = low.rolling(window=k_period, min_periods=k_period).min()
        highest_high = high.rolling(window=k_period, min_periods=k_period).max()
        
        # Handle division by zero
        denominator = (highest_high - lowest_low)
        k_percent = 100 * ((close - lowest_low) / denominator.where(denominator != 0, np.nan))
        
        d_percent = k_percent.rolling(window=d_period, min_periods=d_period).mean()
        
        return k_percent, d_percent
    
    @staticmethod
    def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """
        Average Directional Index (Trend Strength)
        Returns values between 0-100
        """
        # True Range
        tr = IndicatorCalculator.atr(high, low, close, period) * period
        
        # Directional Movement
        up_move = high.diff()
        down_move = -low.diff()
        
        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)
        
        # Smoothed averages
        plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / tr)
        minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / tr)
        
        # Directional Index
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        
        # Average Directional Index
        adx = dx.ewm(alpha=1/period, adjust=False).mean()
        
        return adx
    
    @staticmethod
    def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
        """
        Volume Weighted Average Price (Cumulative)
        """
        typical_price = (high + low + close) / 3
        vwap = (typical_price * volume).cumsum() / volume.cumsum()
        return vwap
    
    @staticmethod
    def supertrend(high: pd.Series, low: pd.Series, close: pd.Series, 
                   period: int = 10, multiplier: float = 3.0) -> Tuple[pd.Series, pd.Series]:
        """
        SuperTrend Indicator
        Returns: (supertrend_line, direction) where direction: 1=uptrend, -1=downtrend
        """
        atr = IndicatorCalculator.atr(high, low, close, period)
        
        # Basic bands
        upper_band = ((high + low) / 2) + (multiplier * atr)
        lower_band = ((high + low) / 2) - (multiplier * atr)
        
        # Initialize arrays
        supertrend = pd.Series(index=close.index, dtype=float)
        direction = pd.Series(index=close.index, dtype=int)
        
        for i in range(len(close)):
            if i < period:
                supertrend.iloc[i] = np.nan
                direction.iloc[i] = 0
                continue
            
            if pd.isna(atr.iloc[i]):
                supertrend.iloc[i] = np.nan
                direction.iloc[i] = 0
                continue
            
            # Current upper and lower bands
            current_upper = upper_band.iloc[i]
            current_lower = lower_band.iloc[i]
            
            # Previous bands
            prev_upper = upper_band.iloc[i-1] if i > 0 else current_upper
            prev_lower = lower_band.iloc[i-1] if i > 0 else current_lower
            
            # Previous supertrend and direction
            prev_supertrend = supertrend.iloc[i-1] if i > 0 else np.nan
            prev_direction = direction.iloc[i-1] if i > 0 else 0
            
            # Adjust bands
            if current_upper < prev_upper or close.iloc[i-1] > prev_upper:
                current_upper = current_upper
            else:
                current_upper = prev_upper
                
            if current_lower > prev_lower or close.iloc[i-1] < prev_lower:
                current_lower = current_lower
            else:
                current_lower = prev_lower
            
            # Determine direction
            if pd.isna(prev_supertrend):
                if close.iloc[i] > current_upper:
                    direction.iloc[i] = 1
                    supertrend.iloc[i] = current_lower
                else:
                    direction.iloc[i] = -1
                    supertrend.iloc[i] = current_upper
            else:
                if prev_direction == 1:
                    if close.iloc[i] < current_lower:
                        direction.iloc[i] = -1
                        supertrend.iloc[i] = current_upper
                    else:
                        direction.iloc[i] = 1
                        supertrend.iloc[i] = min(current_lower, prev_supertrend)
                else:
                    if close.iloc[i] > current_upper:
                        direction.iloc[i] = 1
                        supertrend.iloc[i] = current_lower
                    else:
                        direction.iloc[i] = -1
                        supertrend.iloc[i] = max(current_upper, prev_supertrend)
        
        return supertrend, direction
    
    @staticmethod
    def parabolic_sar(high: pd.Series, low: pd.Series, 
                      step: float = 0.02, max_step: float = 0.2) -> pd.Series:
        """
        Parabolic SAR
        Returns SAR values
        """
        length = len(high)
        sar = pd.Series(index=high.index, dtype=float)
        
        # Initialize
        trend_up = True
        ep = high.iloc[0]  # Extreme point
        sar.iloc[0] = low.iloc[0]
        af = step  # Acceleration factor
        
        for i in range(1, length):
            prev_sar = sar.iloc[i-1]
            
            if trend_up:
                sar.iloc[i] = prev_sar + af * (ep - prev_sar)
                
                # SAR cannot be above prior lows
                if i >= 2:
                    sar.iloc[i] = min(sar.iloc[i], low.iloc[i-1], low.iloc[i-2])
                
                # Check for reversal
                if low.iloc[i] < sar.iloc[i]:
                    trend_up = False
                    sar.iloc[i] = ep
                    ep = low.iloc[i]
                    af = step
                else:
                    if high.iloc[i] > ep:
                        ep = high.iloc[i]
                        af = min(af + step, max_step)
            else:
                sar.iloc[i] = prev_sar - af * (prev_sar - ep)
                
                # SAR cannot be below prior highs
                if i >= 2:
                    sar.iloc[i] = max(sar.iloc[i], high.iloc[i-1], high.iloc[i-2])
                
                # Check for reversal
                if high.iloc[i] > sar.iloc[i]:
                    trend_up = True
                    sar.iloc[i] = ep
                    ep = high.iloc[i]
                    af = step
                else:
                    if low.iloc[i] < ep:
                        ep = low.iloc[i]
                        af = min(af + step, max_step)
        
        return sar
    
    @staticmethod
    def cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
        """
        Commodity Channel Index
        """
        typical_price = (high + low + close) / 3
        sma = typical_price.rolling(window=period, min_periods=period).mean()
        mad = typical_price.rolling(window=period, min_periods=period).apply(
            lambda x: np.abs(x - x.mean()).mean()
        )
        cci = (typical_price - sma) / (0.015 * mad)
        return cci
    
    @staticmethod
    def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """
        Williams %R
        Returns values between -100 and 0
        """
        highest_high = high.rolling(window=period, min_periods=period).max()
        lowest_low = low.rolling(window=period, min_periods=period).min()
        
        wr = -100 * (highest_high - close) / (highest_high - lowest_low)
        return wr
    
    @staticmethod
    def momentum(close: pd.Series, period: int = 10) -> pd.Series:
        """Momentum Indicator"""
        return close.diff(period)
    
    @staticmethod
    def roc(close: pd.Series, period: int = 10) -> pd.Series:
        """Rate of Change"""
        return ((close - close.shift(period)) / close.shift(period)) * 100
    
    @staticmethod
    def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        """On-Balance Volume"""
        direction = np.sign(close.diff())
        direction.iloc[0] = 0
        obv = (direction * volume).cumsum()
        return obv
    
    @staticmethod
    def mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14) -> pd.Series:
        """
        Money Flow Index
        Returns values between 0-100
        """
        typical_price = (high + low + close) / 3
        money_flow = typical_price * volume
        
        positive_flow = pd.Series(index=close.index, dtype=float)
        negative_flow = pd.Series(index=close.index, dtype=float)
        
        price_change = typical_price.diff()
        positive_flow[price_change > 0] = money_flow[price_change > 0]
        negative_flow[price_change < 0] = money_flow[price_change < 0]
        
        positive_flow = positive_flow.fillna(0)
        negative_flow = negative_flow.fillna(0)
        
        positive_sum = positive_flow.rolling(window=period, min_periods=period).sum()
        negative_sum = negative_flow.rolling(window=period, min_periods=period).sum()
        
        money_ratio = positive_sum / negative_sum
        mfi = 100 - (100 / (1 + money_ratio))
        
        return mfi
    
    @staticmethod
    def keltner_channels(high: pd.Series, low: pd.Series, close: pd.Series, 
                         period: int = 20, multiplier: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Keltner Channels
        Returns: (upper, middle, lower)
        """
        middle = close.ewm(span=period, adjust=False).mean()
        atr = IndicatorCalculator.atr(high, low, close, period)
        
        upper = middle + (multiplier * atr)
        lower = middle - (multiplier * atr)
        
        return upper, middle, lower
    
    @staticmethod
    def donchian_channels(high: pd.Series, low: pd.Series, period: int = 20) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Donchian Channels
        Returns: (upper, middle, lower)
        """
        upper = high.rolling(window=period, min_periods=period).max()
        lower = low.rolling(window=period, min_periods=period).min()
        middle = (upper + lower) / 2
        
        return upper, middle, lower
    
    @staticmethod
    def detect_doji(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, 
                    threshold: float = 0.1) -> pd.Series:
        """
        Detect Doji candlestick pattern
        Returns boolean Series
        """
        body = np.abs(close - open_)
        range_ = high - low
        doji = body <= (range_ * threshold)
        return doji
    
    @staticmethod
    def detect_bullish_engulfing(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
        """
        Detect Bullish Engulfing pattern
        """
        prev_open = open_.shift(1)
        prev_close = close.shift(1)
        
        bullish_engulfing = (
            (prev_close < prev_open) &  # Previous is bearish
            (close > open_) &  # Current is bullish
            (close >= prev_open) &  # Current body engulfs previous
            (open_ <= prev_close)
        )
        
        return bullish_engulfing
    
    @staticmethod
    def detect_bearish_engulfing(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
        """
        Detect Bearish Engulfing pattern
        """
        prev_open = open_.shift(1)
        prev_close = close.shift(1)
        
        bearish_engulfing = (
            (prev_close > prev_open) &  # Previous is bullish
            (close < open_) &  # Current is bearish
            (close <= prev_open) &  # Current body engulfs previous
            (open_ >= prev_close)
        )
        
        return bearish_engulfing
    
    @staticmethod
    def detect_hammer(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series,
                      body_threshold: float = 0.3, wick_threshold: float = 2.0) -> pd.Series:
        """
        Detect Hammer pattern (bullish reversal)
        """
        body = np.abs(close - open_)
        range_ = high - low
        lower_wick = np.minimum(open_, close_) - low
        upper_wick = high - np.maximum(open_, close_)
        
        hammer = (
            (body <= range_ * body_threshold) &  # Small body
            (lower_wick >= body * wick_threshold) &  # Long lower wick
            (upper_wick <= body)  # Small upper wick
        )
        
        return hammer
    
    @staticmethod
    def detect_shooting_star(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series,
                             body_threshold: float = 0.3, wick_threshold: float = 2.0) -> pd.Series:
        """
        Detect Shooting Star pattern (bearish reversal)
        """
        body = np.abs(close - open_)
        range_ = high - low
        upper_wick = high - np.maximum(open_, close_)
        lower_wick = np.minimum(open_, close_) - low
        
        shooting_star = (
            (body <= range_ * body_threshold) &  # Small body
            (upper_wick >= body * wick_threshold) &  # Long upper wick
            (lower_wick <= body)  # Small lower wick
        )
        
        return shooting_star
    
    @staticmethod
    def calculate_indicator(name: str, data: pd.DataFrame, **params) -> pd.Series:
        """
        Calculate any indicator by name
        """
        name = name.lower().replace(' ', '_')
        
        indicator_map = {
            'sma': lambda: IndicatorCalculator.sma(data['close'], params.get('period', 20)),
            'ema': lambda: IndicatorCalculator.ema(data['close'], params.get('period', 20)),
            'rsi': lambda: IndicatorCalculator.rsi(data['close'], params.get('period', 14)),
            'atr': lambda: IndicatorCalculator.atr(data['high'], data['low'], data['close'], params.get('period', 14)),
            'adx': lambda: IndicatorCalculator.adx(data['high'], data['low'], data['close'], params.get('period', 14)),
            'vwap': lambda: IndicatorCalculator.vwap(data['high'], data['low'], data['close'], data['volume']),
            'cci': lambda: IndicatorCalculator.cci(data['high'], data['low'], data['close'], params.get('period', 20)),
            'williams_r': lambda: IndicatorCalculator.williams_r(data['high'], data['low'], data['close'], params.get('period', 14)),
            'momentum': lambda: IndicatorCalculator.momentum(data['close'], params.get('period', 10)),
            'roc': lambda: IndicatorCalculator.roc(data['close'], params.get('period', 10)),
            'obv': lambda: IndicatorCalculator.obv(data['close'], data['volume']),
            'mfi': lambda: IndicatorCalculator.mfi(data['high'], data['low'], data['close'], data['volume'], params.get('period', 14)),
            'price': lambda: data['close'],
            'close': lambda: data['close'],
            'open': lambda: data['open'],
            'high': lambda: data['high'],
            'low': lambda: data['low'],
            'volume': lambda: data['volume'],
        }
        
        # MACD special handling
        if name == 'macd':
            macd_line, _, _ = IndicatorCalculator.macd(
                data['close'], 
                params.get('fast', 12), 
                params.get('slow', 26), 
                params.get('signal', 9)
            )
            return macd_line
        elif name == 'macd_signal':
            _, signal_line, _ = IndicatorCalculator.macd(
                data['close'], 
                params.get('fast', 12), 
                params.get('slow', 26), 
                params.get('signal', 9)
            )
            return signal_line
        elif name == 'macd_histogram':
            _, _, histogram = IndicatorCalculator.macd(
                data['close'], 
                params.get('fast', 12), 
                params.get('slow', 26), 
                params.get('signal', 9)
            )
            return histogram
        
        # Bollinger Bands
        elif name in ['bollinger_upper', 'bollinger_middle', 'bollinger_lower']:
            upper, middle, lower = IndicatorCalculator.bollinger_bands(
                data['close'],
                params.get('period', 20),
                params.get('std_dev', 2.0)
            )
            return {'bollinger_upper': upper, 'bollinger_middle': middle, 'bollinger_lower': lower}[name]
        
        # Stochastic
        elif name in ['stochastic_k', 'stochastic_d']:
            k, d = IndicatorCalculator.stochastic(
                data['high'], data['low'], data['close'],
                params.get('k_period', 14),
                params.get('d_period', 3)
            )
            return k if name == 'stochastic_k' else d
        
        # SuperTrend
        elif name == 'supertrend':
            supertrend, _ = IndicatorCalculator.supertrend(
                data['high'], data['low'], data['close'],
                params.get('period', 10),
                params.get('multiplier', 3.0)
            )
            return supertrend
        
        # Candlestick patterns
        elif name == 'doji':
            return IndicatorCalculator.detect_doji(data['open'], data['high'], data['low'], data['close'])
        elif name == 'bullish_engulfing':
            return IndicatorCalculator.detect_bullish_engulfing(data['open'], data['high'], data['low'], data['close'])
        elif name == 'bearish_engulfing':
            return IndicatorCalculator.detect_bearish_engulfing(data['open'], data['high'], data['low'], data['close'])
        elif name == 'hammer':
            return IndicatorCalculator.detect_hammer(data['open'], data['high'], data['low'], data['close'])
        elif name == 'shooting_star':
            return IndicatorCalculator.detect_shooting_star(data['open'], data['high'], data['low'], data['close'])
        
        if name in indicator_map:
            return indicator_map[name]()
        
        return None