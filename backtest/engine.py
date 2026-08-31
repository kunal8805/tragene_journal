import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
import json
from .indicators import IndicatorCalculator

class BacktestEngine:
    """
    Professional-grade backtesting engine with:
    - No lookahead bias (uses only past data)
    - Realistic execution (next bar open)
    - Slippage and commission modeling
    - Accurate position sizing
    - Comprehensive performance metrics
    """
    
    def __init__(self, data: pd.DataFrame, strategy_config: Dict, symbol: str = None, timeframe: str = None):
        """
        Initialize backtest engine
        
        Args:
            data: OHLCV DataFrame with columns: open, high, low, close, volume
            strategy_config: Full strategy configuration
            symbol: Symbol being tested
            timeframe: Timeframe of data
        """
        self.data = data.copy()
        self.strategy = strategy_config
        self.symbol = symbol or strategy_config.get('symbol', 'Unknown')
        self.timeframe = timeframe or strategy_config.get('timeframe', '1h')
        
        # Validate data
        self._validate_data()
        
        # Extract strategy parameters
        self.entry_rules = strategy_config.get('entry_rules', [])
        self.exit_rules = strategy_config.get('exit_rules', [])
        self.risk_management = strategy_config.get('risk_management', {})
        self.position_sizing = strategy_config.get('position_sizing', {})
        self.costs = strategy_config.get('costs', {})
        self.initial_capital = float(strategy_config.get('initial_capital', 10000))
        
        # Set slippage and commission
        self.slippage = float(self.costs.get('slippage', 0)) / 10000  # Convert pips to price
        self.commission = float(self.costs.get('commission', 0))
        
        # Initialize tracking variables
        self.reset()
        
    def _validate_data(self):
        """Validate OHLCV data"""
        required_columns = ['open', 'high', 'low', 'close', 'volume']
        
        for col in required_columns:
            if col not in self.data.columns:
                raise ValueError(f"Missing required column: {col}")
        
        # Check for null values
        if self.data[required_columns].isnull().any().any():
            # Fill forward and backward for minor gaps
            self.data[required_columns] = self.data[required_columns].fillna(method='ffill').fillna(method='bfill')
        
        # Check for zero/negative prices
        for col in ['open', 'high', 'low', 'close']:
            if (self.data[col] <= 0).any():
                self.data[col] = self.data[col].where(self.data[col] > 0, self.data[col].shift(1))
        
        # Ensure chronological order
        self.data = self.data.sort_index()
        
    def reset(self):
        """Reset all tracking variables"""
        self.equity = self.initial_capital
        self.position = None
        self.trades = []
        self.equity_curve = []
        self.drawdown_curve = []
        self.peak_equity = self.initial_capital
        self.current_drawdown = 0
        self.max_drawdown = 0
        self.wins = 0
        self.losses = 0
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.max_win_streak = 0
        self.max_loss_streak = 0
        
    def run(self) -> Dict:
        """
        Execute the backtest
        
        Returns:
            Dictionary with all results including trades, equity curve, and statistics
        """
        # Calculate all indicators needed
        self._precalculate_indicators()
        
        # Get valid trading range (skip warmup period)
        warmup = self._get_warmup_period()
        start_idx = warmup
        end_idx = len(self.data) - 1  # Leave one bar for next-bar-open execution
        
        if end_idx <= start_idx:
            return self._empty_results("Insufficient data for backtest")
        
        # Main backtest loop
        for i in range(start_idx, end_idx):
            current_bar = self.data.iloc[i]
            current_date = self.data.index[i]
            
            # Update equity curve
            self._update_equity_curve(current_date, i)
            
            # Check if we have an open position
            if self.position is not None:
                # Check exit conditions
                exit_signal = self._check_exit_conditions(i)
                
                if exit_signal:
                    # Execute exit at next bar open
                    next_bar = self.data.iloc[i + 1]
                    exit_price = next_bar['open']
                    
                    # Apply slippage to exit
                    if self.position['type'] == 'long':
                        exit_price -= self.slippage
                    else:
                        exit_price += self.slippage
                    
                    self._close_position(exit_price, self.data.index[i + 1], i + 1, exit_signal)
                    continue
                
                # Check stop loss
                if self._check_stop_loss(current_bar):
                    exit_price = self._calculate_stop_loss_price(current_bar)
                    self._close_position(exit_price, current_date, i, 'Stop Loss')
                    continue
                
                # Check take profit
                if self._check_take_profit(current_bar):
                    exit_price = self._calculate_take_profit_price(current_bar)
                    self._close_position(exit_price, current_date, i, 'Take Profit')
                    continue
                
                # Check trailing stop
                if self._check_trailing_stop(current_bar):
                    exit_price = self._calculate_trailing_stop_price(current_bar)
                    self._close_position(exit_price, current_date, i, 'Trailing Stop')
                    continue
                    
            else:
                # Check entry conditions
                if self._check_entry_conditions(i):
                    # Execute entry at next bar open
                    next_bar = self.data.iloc[i + 1]
                    entry_price = next_bar['open']
                    
                    # Apply slippage to entry
                    direction = self._get_entry_direction(i)
                    if direction == 'long':
                        entry_price += self.slippage
                    else:
                        entry_price -= self.slippage
                    
                    self._open_position(entry_price, self.data.index[i + 1], i + 1, direction)
        
        # Close any remaining position at last bar
        if self.position is not None:
            last_bar = self.data.iloc[-1]
            self._close_position(last_bar['close'], self.data.index[-1], len(self.data) - 1, 'End of Data')
        
        # Calculate final statistics
        results = self._calculate_statistics()
        
        return results
    
    def _precalculate_indicators(self):
        """Pre-calculate all indicators needed for the strategy"""
        self.indicator_data = {}
        
        # Collect all unique indicators from entry and exit rules
        all_indicators = set()
        for rule in self.entry_rules + self.exit_rules:
            if 'indicator' in rule:
                all_indicators.add(rule['indicator'])
            if 'compare_to' in rule and rule['compare_to'] not in ['value', 'price', 'open', 'high', 'low', 'close', 'volume']:
                all_indicators.add(rule['compare_to'])
        
        # Calculate each indicator
        for indicator in all_indicators:
            params = self._get_indicator_params(indicator)
            try:
                self.indicator_data[indicator] = IndicatorCalculator.calculate_indicator(
                    indicator, self.data, **params
                )
            except Exception as e:
                print(f"Warning: Could not calculate {indicator}: {e}")
                self.indicator_data[indicator] = None
    
    def _get_indicator_params(self, indicator: str) -> Dict:
        """Get parameters for an indicator from strategy config"""
        # Look for indicator params in entry/exit rules
        for rule in self.entry_rules + self.exit_rules:
            if rule.get('indicator') == indicator and 'period' in rule:
                return {'period': int(rule['period'])}
            if rule.get('compare_to') == indicator and 'period' in rule:
                return {'period': int(rule['period'])}
        
        # Default params for common indicators
        defaults = {
            'sma': {'period': 20},
            'ema': {'period': 20},
            'rsi': {'period': 14},
            'macd': {'fast': 12, 'slow': 26, 'signal': 9},
            'macd_signal': {'fast': 12, 'slow': 26, 'signal': 9},
            'macd_histogram': {'fast': 12, 'slow': 26, 'signal': 9},
            'bollinger_upper': {'period': 20, 'std_dev': 2.0},
            'bollinger_middle': {'period': 20, 'std_dev': 2.0},
            'bollinger_lower': {'period': 20, 'std_dev': 2.0},
            'atr': {'period': 14},
            'stochastic_k': {'k_period': 14, 'd_period': 3},
            'stochastic_d': {'k_period': 14, 'd_period': 3},
            'adx': {'period': 14},
            'vwap': {},
            'supertrend': {'period': 10, 'multiplier': 3.0},
            'cci': {'period': 20},
            'williams_r': {'period': 14},
            'momentum': {'period': 10},
            'roc': {'period': 10},
            'obv': {},
            'mfi': {'period': 14},
        }
        
        return defaults.get(indicator, {'period': 14})
    
    def _get_warmup_period(self) -> int:
        """Calculate warmup period needed for indicators"""
        max_period = 0
        
        for indicator in self.indicator_data.keys():
            params = self._get_indicator_params(indicator)
            
            if indicator in ['sma', 'ema', 'rsi', 'atr', 'adx', 'cci', 'williams_r', 'mfi']:
                max_period = max(max_period, params.get('period', 14))
            elif indicator in ['macd', 'macd_signal', 'macd_histogram']:
                max_period = max(max_period, params.get('slow', 26))
            elif indicator in ['bollinger_upper', 'bollinger_middle', 'bollinger_lower']:
                max_period = max(max_period, params.get('period', 20))
            elif indicator in ['stochastic_k', 'stochastic_d']:
                max_period = max(max_period, params.get('k_period', 14))
            elif indicator == 'supertrend':
                max_period = max(max_period, params.get('period', 10))
        
        # Add buffer for safety
        return min(max_period + 5, len(self.data) // 4)
    
    def _get_indicator_value(self, indicator: str, index: int) -> Optional[float]:
        """Get indicator value at specific index"""
        if indicator in ['price', 'close']:
            return self.data.iloc[index]['close']
        elif indicator == 'open':
            return self.data.iloc[index]['open']
        elif indicator == 'high':
            return self.data.iloc[index]['high']
        elif indicator == 'low':
            return self.data.iloc[index]['low']
        elif indicator == 'volume':
            return self.data.iloc[index]['volume']
        elif indicator in self.indicator_data:
            data = self.indicator_data[indicator]
            if data is not None and index < len(data):
                value = data.iloc[index]
                if pd.notna(value):
                    return value
        return None
    
    def _check_condition(self, rule: Dict, index: int) -> bool:
        """
        Check if a single condition is met at given index
        No lookahead bias - only uses data up to and including index
        """
        indicator = rule.get('indicator')
        operator = rule.get('operator')
        compare_to = rule.get('compare_to', 'value')
        value = rule.get('value')
        
        if not indicator or not operator:
            return False
        
        # Get current indicator value
        current_value = self._get_indicator_value(indicator, index)
        if current_value is None:
            return False
        
        # Get comparison value
        if compare_to == 'value':
            try:
                comparison_value = float(value) if value else 0
            except:
                return False
        else:
            comparison_value = self._get_indicator_value(compare_to, index)
            if comparison_value is None:
                return False
        
        # Check condition based on operator
        if operator == 'crosses above':
            # Check if current is above and previous was below/equal
            prev_value = self._get_indicator_value(indicator, index - 1)
            prev_compare = self._get_indicator_value(compare_to, index - 1) if compare_to != 'value' else comparison_value
            
            if prev_value is None or prev_compare is None:
                return False
            
            return prev_value <= prev_compare and current_value > comparison_value
        
        elif operator == 'crosses below':
            # Check if current is below and previous was above/equal
            prev_value = self._get_indicator_value(indicator, index - 1)
            prev_compare = self._get_indicator_value(compare_to, index - 1) if compare_to != 'value' else comparison_value
            
            if prev_value is None or prev_compare is None:
                return False
            
            return prev_value >= prev_compare and current_value < comparison_value
        
        elif operator in ['is greater than', 'greater than', '>']:
            return current_value > comparison_value
        
        elif operator in ['is less than', 'less than', '<']:
            return current_value < comparison_value
        
        elif operator in ['is equal to', 'equal to', '==']:
            return abs(current_value - comparison_value) < 0.0001
        
        elif operator in ['is above', 'above']:
            return current_value > comparison_value
        
        elif operator in ['is below', 'below']:
            return current_value < comparison_value
        
        elif operator in ['enters overbought', 'overbought']:
            # Typically for RSI > 70 or Stochastic > 80
            threshold = 70 if indicator in ['rsi'] else 80
            return current_value > threshold
        
        elif operator in ['enters oversold', 'oversold']:
            # Typically for RSI < 30 or Stochastic < 20
            threshold = 30 if indicator in ['rsi'] else 20
            return current_value < threshold
        
        elif operator in ['leaves overbought']:
            threshold = 70 if indicator in ['rsi'] else 80
            prev_value = self._get_indicator_value(indicator, index - 1)
            return prev_value is not None and prev_value > threshold and current_value <= threshold
        
        elif operator in ['leaves oversold']:
            threshold = 30 if indicator in ['rsi'] else 20
            prev_value = self._get_indicator_value(indicator, index - 1)
            return prev_value is not None and prev_value < threshold and current_value >= threshold
        
        elif operator in ['is rising', 'rising']:
            prev_value = self._get_indicator_value(indicator, index - 1)
            return prev_value is not None and current_value > prev_value
        
        elif operator in ['is falling', 'falling']:
            prev_value = self._get_indicator_value(indicator, index - 1)
            return prev_value is not None and current_value < prev_value
        
        return False
    
    def _check_entry_conditions(self, index: int) -> bool:
        """Check if entry conditions are met"""
        if not self.entry_rules:
            return False
        
        # Handle AND/OR logic
        result = None
        current_logic = 'AND'
        
        for i, rule in enumerate(self.entry_rules):
            condition_met = self._check_condition(rule, index)
            
            if i == 0:
                result = condition_met
            else:
                logic = rule.get('logic', current_logic)
                if logic == 'OR':
                    result = result or condition_met
                else:  # AND
                    result = result and condition_met
            
            current_logic = rule.get('logic', 'AND')
        
        return result if result is not None else False
    
    def _check_exit_conditions(self, index: int) -> Optional[str]:
        """Check if exit conditions are met, returns exit reason"""
        if not self.exit_rules:
            return None
        
        result = None
        current_logic = 'AND'
        
        for i, rule in enumerate(self.exit_rules):
            condition_met = self._check_condition(rule, index)
            
            if i == 0:
                result = condition_met
            else:
                logic = rule.get('logic', current_logic)
                if logic == 'OR':
                    result = result or condition_met
                else:
                    result = result and condition_met
            
            current_logic = rule.get('logic', 'AND')
        
        if result:
            return 'Exit Signal'
        
        return None
    
    def _get_entry_direction(self, index: int) -> str:
        """Determine entry direction (long/short)"""
        # Default to long
        direction = 'long'
        
        # Check for short signals (optional - can be added to strategy config)
        if 'direction' in self.strategy:
            direction = self.strategy['direction']
        
        return direction
    
    def _calculate_position_size(self, entry_price: float) -> float:
        """Calculate position size based on strategy config"""
        position_type = self.position_sizing.get('type', 'fixed')
        size = float(self.position_sizing.get('size', 1))
        
        if position_type == 'fixed':
            return size
        
        elif position_type == 'percentage':
            # Percentage of current equity
            position_value = self.equity * (size / 100)
            return position_value / entry_price
        
        elif position_type == 'risk':
            # Risk-based position sizing
            stop_loss_type = self.risk_management.get('stop_loss_type', 'none')
            stop_loss_value = float(self.risk_management.get('stop_loss_value', 2))
            
            if stop_loss_type == 'percentage':
                risk_per_trade = self.equity * (size / 100)
                stop_distance = entry_price * (stop_loss_value / 100)
                if stop_distance > 0:
                    return risk_per_trade / stop_distance
            elif stop_loss_type == 'atr':
                # Use ATR for stop distance
                atr_value = self._get_latest_atr()
                if atr_value and atr_value > 0:
                    risk_per_trade = self.equity * (size / 100)
                    stop_distance = atr_value * stop_loss_value
                    if stop_distance > 0:
                        return risk_per_trade / stop_distance
        
        return size
    
    def _get_latest_atr(self) -> Optional[float]:
        """Get the most recent ATR value"""
        if 'atr' in self.indicator_data and self.indicator_data['atr'] is not None:
            atr_data = self.indicator_data['atr']
            for i in range(len(atr_data) - 1, -1, -1):
                if pd.notna(atr_data.iloc[i]):
                    return atr_data.iloc[i]
        return None
    
    def _open_position(self, entry_price: float, entry_date, entry_index: int, direction: str):
        """Open a new position"""
        position_size = self._calculate_position_size(entry_price)
        
        # Apply commission
        self.equity -= self.commission * position_size
        
        self.position = {
            'type': direction,
            'entry_price': entry_price,
            'entry_date': entry_date,
            'entry_index': entry_index,
            'quantity': position_size,
            'stop_loss': None,
            'take_profit': None,
            'trailing_stop': None,
            'highest_price': entry_price,
            'lowest_price': entry_price
        }
        
        # Set stop loss and take profit levels
        self._set_risk_levels(entry_price)
    
    def _set_risk_levels(self, entry_price: float):
        """Set stop loss, take profit, and trailing stop levels"""
        # Stop Loss
        stop_loss_type = self.risk_management.get('stop_loss_type', 'none')
        stop_loss_value = float(self.risk_management.get('stop_loss_value', 2))
        
        if stop_loss_type == 'percentage':
            if self.position['type'] == 'long':
                self.position['stop_loss'] = entry_price * (1 - stop_loss_value / 100)
            else:
                self.position['stop_loss'] = entry_price * (1 + stop_loss_value / 100)
        
        elif stop_loss_type == 'atr':
            atr_value = self._get_latest_atr()
            if atr_value:
                if self.position['type'] == 'long':
                    self.position['stop_loss'] = entry_price - (atr_value * stop_loss_value)
                else:
                    self.position['stop_loss'] = entry_price + (atr_value * stop_loss_value)
        
        elif stop_loss_type == 'fixed':
            self.position['stop_loss'] = stop_loss_value
        
        # Take Profit
        take_profit_type = self.risk_management.get('take_profit_type', 'none')
        take_profit_value = float(self.risk_management.get('take_profit_value', 2))
        
        if take_profit_type == 'percentage':
            if self.position['type'] == 'long':
                self.position['take_profit'] = entry_price * (1 + take_profit_value / 100)
            else:
                self.position['take_profit'] = entry_price * (1 - take_profit_value / 100)
        
        elif take_profit_type == 'rr':
            # Risk:Reward ratio
            if self.position['stop_loss']:
                risk = abs(entry_price - self.position['stop_loss'])
                if self.position['type'] == 'long':
                    self.position['take_profit'] = entry_price + (risk * take_profit_value)
                else:
                    self.position['take_profit'] = entry_price - (risk * take_profit_value)
        
        elif take_profit_type == 'atr':
            atr_value = self._get_latest_atr()
            if atr_value:
                if self.position['type'] == 'long':
                    self.position['take_profit'] = entry_price + (atr_value * take_profit_value)
                else:
                    self.position['take_profit'] = entry_price - (atr_value * take_profit_value)
        
        # Trailing Stop
        trailing_stop = self.risk_management.get('trailing_stop', 'none')
        if trailing_stop != 'none':
            self.position['trailing_stop'] = trailing_stop
    
    def _check_stop_loss(self, current_bar) -> bool:
        """Check if stop loss is hit"""
        if self.position and self.position['stop_loss']:
            if self.position['type'] == 'long':
                return current_bar['low'] <= self.position['stop_loss']
            else:
                return current_bar['high'] >= self.position['stop_loss']
        return False
    
    def _check_take_profit(self, current_bar) -> bool:
        """Check if take profit is hit"""
        if self.position and self.position['take_profit']:
            if self.position['type'] == 'long':
                return current_bar['high'] >= self.position['take_profit']
            else:
                return current_bar['low'] <= self.position['take_profit']
        return False
    
    def _check_trailing_stop(self, current_bar) -> bool:
        """Check if trailing stop is hit"""
        if self.position and self.position['trailing_stop']:
            # Update highest/lowest price
            if self.position['type'] == 'long':
                self.position['highest_price'] = max(self.position['highest_price'], current_bar['high'])
                trail_value = float(self.position['trailing_stop'])
                
                if trail_value and self.position['trailing_stop'] != 'none':
                    # Calculate trailing stop level
                    if self.risk_management.get('trailing_stop') == 'percentage':
                        trail_distance = self.position['highest_price'] * (trail_value / 100)
                    else:  # ATR-based
                        atr_value = self._get_latest_atr()
                        trail_distance = atr_value * trail_value if atr_value else 0
                    
                    if trail_distance > 0:
                        trail_level = self.position['highest_price'] - trail_distance
                        
                        # Update stop loss to trail level
                        if self.position['stop_loss'] is None or trail_level > self.position['stop_loss']:
                            self.position['stop_loss'] = trail_level
                        
                        return current_bar['low'] <= trail_level
            else:
                self.position['lowest_price'] = min(self.position['lowest_price'], current_bar['low'])
                trail_value = float(self.position['trailing_stop'])
                
                if trail_value and self.position['trailing_stop'] != 'none':
                    if self.risk_management.get('trailing_stop') == 'percentage':
                        trail_distance = self.position['lowest_price'] * (trail_value / 100)
                    else:
                        atr_value = self._get_latest_atr()
                        trail_distance = atr_value * trail_value if atr_value else 0
                    
                    if trail_distance > 0:
                        trail_level = self.position['lowest_price'] + trail_distance
                        
                        if self.position['stop_loss'] is None or trail_level < self.position['stop_loss']:
                            self.position['stop_loss'] = trail_level
                        
                        return current_bar['high'] >= trail_level
        
        return False
    
    def _calculate_stop_loss_price(self, current_bar) -> float:
        """Calculate actual stop loss exit price"""
        return self.position['stop_loss']
    
    def _calculate_take_profit_price(self, current_bar) -> float:
        """Calculate actual take profit exit price"""
        return self.position['take_profit']
    
    def _calculate_trailing_stop_price(self, current_bar) -> float:
        """Calculate actual trailing stop exit price"""
        return self.position['stop_loss']
    
    def _close_position(self, exit_price: float, exit_date, exit_index: int, exit_reason: str):
        """Close current position and record trade"""
        if self.position is None:
            return
        
        entry_price = self.position['entry_price']
        quantity = self.position['quantity']
        
        # Calculate P&L
        if self.position['type'] == 'long':
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity
        
        # Apply commission
        pnl -= self.commission * quantity
        
        # Update equity
        self.equity += pnl
        
        # Calculate return percentage
        if self.position['type'] == 'long':
            return_pct = ((exit_price - entry_price) / entry_price) * 100
        else:
            return_pct = ((entry_price - exit_price) / entry_price) * 100
        
        # Record trade
        trade = {
            'type': self.position['type'],
            'entry_date': str(self.position['entry_date']),
            'entry_price': entry_price,
            'exit_date': str(exit_date),
            'exit_price': exit_price,
            'quantity': quantity,
            'pnl': pnl,
            'return_pct': return_pct,
            'bars_held': exit_index - self.position['entry_index'],
            'exit_reason': exit_reason,
            'entry_index': self.position['entry_index'],
            'exit_index': exit_index
        }
        
        # Store ONLY best 100 trades (by profit)
        if len(self.trades) < 100:
            self.trades.append(trade)
        else:
            min_trade = min(self.trades, key=lambda t: t['pnl'])
            if trade['pnl'] > min_trade['pnl']:
                self.trades.remove(min_trade)
                self.trades.append(trade)
        
        # Update win/loss statistics
        if pnl > 0:
            self.wins += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            self.max_win_streak = max(self.max_win_streak, self.consecutive_wins)
        elif pnl < 0:
            self.losses += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            self.max_loss_streak = max(self.max_loss_streak, self.consecutive_losses)
        
        # Clear position
        self.position = None
    
    def _update_equity_curve(self, date, index: int):
        """Update equity curve tracking"""
        # Calculate current equity including open position
        current_equity = self.equity
        
        if self.position:
            current_price = self.data.iloc[index]['close']
            entry_price = self.position['entry_price']
            quantity = self.position['quantity']
            
            if self.position['type'] == 'long':
                unrealized_pnl = (current_price - entry_price) * quantity
            else:
                unrealized_pnl = (entry_price - current_price) * quantity
            
            current_equity += unrealized_pnl
        
        # Update peak equity
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
        
        # Calculate drawdown
        if self.peak_equity > 0:
            self.current_drawdown = ((self.peak_equity - current_equity) / self.peak_equity) * 100
            self.max_drawdown = max(self.max_drawdown, self.current_drawdown)
        
        # Only store max 200 equity points (downsample)
        total_bars = len(self.data)
        sampling_rate = max(1, total_bars // 200)
        
        if index % sampling_rate == 0 or self.position is not None:
            if len(self.equity_curve) < 200:
                self.equity_curve.append({
                    'date': str(date),
                    'equity': current_equity,
                    'drawdown': self.current_drawdown
                })
    
    def _calculate_statistics(self) -> Dict:
        """Calculate comprehensive performance statistics"""
        if not self.trades:
            return self._empty_results("No trades generated")
        
        # Basic statistics
        total_trades = len(self.trades)
        wins = sum(1 for t in self.trades if t['pnl'] > 0)
        losses = sum(1 for t in self.trades if t['pnl'] < 0)
        win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
        
        # Profit/Loss
        gross_profit = sum(t['pnl'] for t in self.trades if t['pnl'] > 0)
        gross_loss = abs(sum(t['pnl'] for t in self.trades if t['pnl'] < 0))
        net_profit = gross_profit - gross_loss
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Average win/loss
        avg_win = gross_profit / wins if wins > 0 else 0
        avg_loss = gross_loss / losses if losses > 0 else 0
        
        # Total return
        total_return = ((self.equity - self.initial_capital) / self.initial_capital) * 100
        
        # Largest win/loss
        largest_win = max((t['pnl'] for t in self.trades if t['pnl'] > 0), default=0)
        largest_loss = min((t['pnl'] for t in self.trades if t['pnl'] < 0), default=0)
        
        # Sharpe Ratio (assuming risk-free rate = 0)
        returns = []
        for i in range(1, len(self.equity_curve)):
            prev_equity = self.equity_curve[i-1]['equity']
            curr_equity = self.equity_curve[i]['equity']
            if prev_equity > 0:
                returns.append((curr_equity - prev_equity) / prev_equity)
        
        if returns and len(returns) > 1:
            avg_return = np.mean(returns)
            std_return = np.std(returns)
            sharpe_ratio = (avg_return / std_return) * np.sqrt(252) if std_return > 0 else 0
            
            # Sortino Ratio (downside deviation)
            downside_returns = [r for r in returns if r < 0]
            downside_std = np.std(downside_returns) if downside_returns else 0
            sortino_ratio = (avg_return / downside_std) * np.sqrt(252) if downside_std > 0 else 0
        else:
            sharpe_ratio = 0
            sortino_ratio = 0
        
        # Expectancy
        expectancy = net_profit / total_trades if total_trades > 0 else 0
        
        # Long/Short counts
        long_trades = sum(1 for t in self.trades if t['type'] == 'long')
        short_trades = sum(1 for t in self.trades if t['type'] == 'short')
        
        # Average bars held
        avg_bars_held = np.mean([t['bars_held'] for t in self.trades]) if self.trades else 0
        
        # Calmar Ratio
        calmar_ratio = (total_return / self.max_drawdown) if self.max_drawdown > 0 else 0
        
        # Recovery Factor
        recovery_factor = (net_profit / self.max_drawdown) if self.max_drawdown > 0 else 0
        
        # Risk of Ruin (simplified)
        risk_of_ruin = 0
        if win_rate > 0 and win_rate < 100:
            loss_prob = 1 - (win_rate / 100)
            if avg_loss > 0:
                risk_of_ruin = (loss_prob / (win_rate / 100)) ** (self.initial_capital / avg_loss)
                risk_of_ruin = min(risk_of_ruin, 1.0)
        
        # Monthly analysis
        monthly_returns = self._calculate_monthly_returns()
        
        results = {
            'strategy_name': self.strategy.get('name', 'Unnamed Strategy'),
            'symbol': self.symbol,
            'timeframe': self.timeframe,
            'start_date': str(self.data.index[0]),
            'end_date': str(self.data.index[-1]),
            'initial_capital': self.initial_capital,
            'final_equity': self.equity,
            'stats': {
                'total_trades': total_trades,
                'wins': wins,
                'losses': losses,
                'win_rate': win_rate,
                'gross_profit': gross_profit,
                'gross_loss': gross_loss,
                'net_profit': net_profit,
                'profit_factor': profit_factor if profit_factor != float('inf') else 999.99,
                'total_return': total_return,
                'profit_loss': net_profit,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'largest_win': largest_win,
                'largest_loss': largest_loss,
                'max_drawdown': self.max_drawdown,
                'sharpe_ratio': sharpe_ratio,
                'sortino_ratio': sortino_ratio,
                'calmar_ratio': calmar_ratio,
                'recovery_factor': recovery_factor,
                'risk_of_ruin': risk_of_ruin,
                'expectancy': expectancy,
                'long_trades': long_trades,
                'short_trades': short_trades,
                'max_win_streak': self.max_win_streak,
                'max_loss_streak': self.max_loss_streak,
                'avg_bars_held': avg_bars_held,
                'avg_trade_duration': f"{avg_bars_held:.1f} bars",
                'best_month': monthly_returns.get('best_month', 'N/A'),
                'worst_month': monthly_returns.get('worst_month', 'N/A'),
                'profitable_months': monthly_returns.get('profitable_months', 0)
            },
            'trades': self.trades,
            'equity_curve': self.equity_curve,
            'drawdown': self.equity_curve
        }
        
        return results
    
    def _calculate_monthly_returns(self) -> Dict:
        """Calculate monthly returns analysis"""
        monthly_pnl = {}
        
        for trade in self.trades:
            month_key = trade['exit_date'][:7]  # YYYY-MM
            if month_key not in monthly_pnl:
                monthly_pnl[month_key] = 0
            monthly_pnl[month_key] += trade['pnl']
        
        if not monthly_pnl:
            return {'best_month': 'N/A', 'worst_month': 'N/A', 'profitable_months': 0}
        
        best_month = max(monthly_pnl, key=monthly_pnl.get)
        worst_month = min(monthly_pnl, key=monthly_pnl.get)
        profitable_months = sum(1 for pnl in monthly_pnl.values() if pnl > 0)
        
        return {
            'best_month': f"{best_month} (${monthly_pnl[best_month]:.2f})",
            'worst_month': f"{worst_month} (${monthly_pnl[worst_month]:.2f})",
            'profitable_months': profitable_months,
            'total_months': len(monthly_pnl)
        }
    
    def _empty_results(self, message: str) -> Dict:
        """Return empty results when no trades"""
        return {
            'strategy_name': self.strategy.get('name', 'Unnamed Strategy'),
            'symbol': self.symbol,
            'timeframe': self.timeframe,
            'start_date': str(self.data.index[0]) if len(self.data) > 0 else 'N/A',
            'end_date': str(self.data.index[-1]) if len(self.data) > 0 else 'N/A',
            'initial_capital': self.initial_capital,
            'final_equity': self.initial_capital,
            'stats': {
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'win_rate': 0,
                'gross_profit': 0,
                'gross_loss': 0,
                'net_profit': 0,
                'profit_factor': 0,
                'total_return': 0,
                'profit_loss': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'largest_win': 0,
                'largest_loss': 0,
                'max_drawdown': 0,
                'sharpe_ratio': 0,
                'sortino_ratio': 0,
                'calmar_ratio': 0,
                'recovery_factor': 0,
                'risk_of_ruin': 0,
                'expectancy': 0,
                'long_trades': 0,
                'short_trades': 0,
                'max_win_streak': 0,
                'max_loss_streak': 0,
                'avg_bars_held': 0,
                'avg_trade_duration': 'N/A',
                'best_month': 'N/A',
                'worst_month': 'N/A',
                'profitable_months': 0
            },
            'trades': [],
            'equity_curve': [],
            'drawdown': [],
            'message': message
        }