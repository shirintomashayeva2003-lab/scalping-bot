import asyncio
import aiohttp
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

# Топ ликвидные монеты — только с объёмом > $100M/день
# Исключены: мем-коины без реальной ликвидности, новые листинги
WHITELIST = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "LTCUSDT", "LINKUSDT",
    "DOTUSDT", "MATICUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT"
]

BINANCE_BASE = "https://api.binance.com/api/v3"


class ScalpingAnalyzer:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def fetch_klines(self, symbol: str, interval: str, limit: int = 100) -> Optional[pd.DataFrame]:
        session = await self._get_session()
        url = f"{BINANCE_BASE}/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                df = pd.DataFrame(data, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                    'taker_buy_quote', 'ignore'
                ])
                for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume']:
                    df[col] = df[col].astype(float)
                return df
        except Exception as e:
            logger.error(f"Error fetching {symbol}: {e}")
            return None

    async def fetch_24h_volume(self, symbol: str) -> float:
        session = await self._get_session()
        url = f"{BINANCE_BASE}/ticker/24hr"
        try:
            async with session.get(url, params={"symbol": symbol}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return 0
                data = await resp.json()
                return float(data.get('quoteVolume', 0))
        except:
            return 0

    def calc_rsi(self, closes: pd.Series, period: int = 14) -> float:
        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]

    def calc_ema(self, closes: pd.Series, period: int) -> pd.Series:
        return closes.ewm(span=period, adjust=False).mean()

    def calc_macd(self, closes: pd.Series):
        ema12 = self.calc_ema(closes, 12)
        ema26 = self.calc_ema(closes, 26)
        macd_line = ema12 - ema26
        signal_line = self.calc_ema(macd_line, 9)
        histogram = macd_line - signal_line
        return macd_line.iloc[-1], signal_line.iloc[-1], histogram.iloc[-1]

    def calc_bollinger(self, closes: pd.Series, period: int = 20):
        sma = closes.rolling(period).mean()
        std = closes.rolling(period).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        price = closes.iloc[-1]
        bb_position = (price - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1])
        return upper.iloc[-1], lower.iloc[-1], bb_position

    def calc_volume_spike(self, volumes: pd.Series) -> float:
        avg_vol = volumes.iloc[-20:-1].mean()
        current_vol = volumes.iloc[-1]
        return current_vol / avg_vol if avg_vol > 0 else 1.0

    def analyze_symbol(self, symbol: str, df_5m: pd.DataFrame, df_15m: pd.DataFrame) -> Optional[Dict]:
        """Анализ монеты по 5м и 15м свечам"""
        closes_5m = df_5m['close']
        closes_15m = df_15m['close']
        current_price = closes_5m.iloc[-1]

        # RSI
        rsi_5m = self.calc_rsi(closes_5m)
        rsi_15m = self.calc_rsi(closes_15m)

        # MACD
        macd_5m, signal_5m, hist_5m = self.calc_macd(closes_5m)
        macd_15m, signal_15m, hist_15m = self.calc_macd(closes_15m)

        # EMA
        ema9_5m = self.calc_ema(closes_5m, 9).iloc[-1]
        ema21_5m = self.calc_ema(closes_5m, 21).iloc[-1]
        ema9_15m = self.calc_ema(closes_15m, 9).iloc[-1]
        ema21_15m = self.calc_ema(closes_15m, 21).iloc[-1]

        # Bollinger
        bb_upper_5m, bb_lower_5m, bb_pos_5m = self.calc_bollinger(closes_5m)

        # Объём
        vol_spike = self.calc_volume_spike(df_5m['volume'])

        # ATR для стоп-лосса
        atr = self._calc_atr(df_5m)

        # Определение сигнала
        long_score = 0
        short_score = 0
        reasons_long = []
        reasons_short = []

        # RSI сигналы
        if rsi_5m < 35:
            long_score += 25
            reasons_long.append(f"RSI(5m)={rsi_5m:.0f} — перепродан")
        elif rsi_5m > 65:
            short_score += 25
            reasons_short.append(f"RSI(5m)={rsi_5m:.0f} — перекуплен")

        if rsi_15m < 40:
            long_score += 15
            reasons_long.append(f"RSI(15m)={rsi_15m:.0f} — слабость подтверждена")
        elif rsi_15m > 60:
            short_score += 15
            reasons_short.append(f"RSI(15m)={rsi_15m:.0f} — сила подтверждена")

        # MACD сигналы
        if hist_5m > 0 and hist_5m > df_5m['close'].std() * 0.001:
            long_score += 20
            reasons_long.append("MACD(5m) — бычий импульс")
        elif hist_5m < 0 and abs(hist_5m) > df_5m['close'].std() * 0.001:
            short_score += 20
            reasons_short.append("MACD(5m) — медвежий импульс")

        if hist_15m > 0:
            long_score += 15
            reasons_long.append("MACD(15m) — тренд вверх")
        elif hist_15m < 0:
            short_score += 15
            reasons_short.append("MACD(15m) — тренд вниз")

        # EMA crossover
        if ema9_5m > ema21_5m and closes_5m.iloc[-2] < self.calc_ema(closes_5m, 21).iloc[-2]:
            long_score += 20
            reasons_long.append("EMA 9 пересекла EMA 21 вверх")
        elif ema9_5m < ema21_5m and closes_5m.iloc[-2] > self.calc_ema(closes_5m, 21).iloc[-2]:
            short_score += 20
            reasons_short.append("EMA 9 пересекла EMA 21 вниз")
        elif ema9_5m > ema21_5m:
            long_score += 10
            reasons_long.append("EMA 9 > EMA 21 (бычий порядок)")
        elif ema9_5m < ema21_5m:
            short_score += 10
            reasons_short.append("EMA 9 < EMA 21 (медвежий порядок)")

        # Bollinger Bands
        if bb_pos_5m < 0.2:
            long_score += 15
            reasons_long.append("Цена у нижней полосы Боллинджера")
        elif bb_pos_5m > 0.8:
            short_score += 15
            reasons_short.append("Цена у верхней полосы Боллинджера")

        # Объём
        if vol_spike > 1.5:
            if long_score > short_score:
                long_score += 10
                reasons_long.append(f"Объём x{vol_spike:.1f} — подтверждение")
            else:
                short_score += 10
                reasons_short.append(f"Объём x{vol_spike:.1f} — подтверждение")

        # Итог
        if long_score >= 60 and long_score > short_score + 15:
            direction = "LONG"
            score = min(long_score, 97)
            reasons = reasons_long
            entry = current_price
            stop_loss = round(entry - atr * 1.5, _get_precision(current_price))
            take_profit = round(entry + atr * 2.5, _get_precision(current_price))
        elif short_score >= 60 and short_score > long_score + 15:
            direction = "SHORT"
            score = min(short_score, 97)
            reasons = reasons_short
            entry = current_price
            stop_loss = round(entry + atr * 1.5, _get_precision(current_price))
            take_profit = round(entry - atr * 2.5, _get_precision(current_price))
        else:
            return None

        sl_pct = round(abs(entry - stop_loss) / entry * 100, 2)
        tp_pct = round(abs(entry - take_profit) / entry * 100, 2)

        # Выбор таймфрейма
        timeframe = "5м" if abs(rsi_5m - 50) > abs(rsi_15m - 50) else "15м"

        return {
            "symbol": symbol.replace("USDT", "/USDT"),
            "direction": direction,
            "timeframe": timeframe,
            "hold_time": "5-10 минут",
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "strength": score,
            "reason": "\n".join(f"• {r}" for r in reasons[:4]),
            "rr_ratio": round(tp_pct / sl_pct, 2) if sl_pct > 0 else 0,
        }

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high = df['high']
        low = df['low']
        close = df['close']
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean().iloc[-1]

    async def get_signals(self) -> List[Dict]:
        """Основная функция — сканирование всех монет"""
        signals = []
        tasks = []

        for symbol in WHITELIST:
            tasks.append(self._analyze_symbol_async(symbol))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, dict) and result is not None:
                # Фильтр: только R/R >= 1.5 и сила >= 65%
                if result['rr_ratio'] >= 1.5 and result['strength'] >= 65:
                    signals.append(result)

        # Сортировка по силе сигнала
        signals.sort(key=lambda x: x['strength'], reverse=True)
        return signals[:5]

    async def _analyze_symbol_async(self, symbol: str) -> Optional[Dict]:
        try:
            # Проверка объёма
            volume_24h = await self.fetch_24h_volume(symbol)
            if volume_24h < 50_000_000:  # меньше $50M — пропускаем
                return None

            df_5m, df_15m = await asyncio.gather(
                self.fetch_klines(symbol, "5m", 100),
                self.fetch_klines(symbol, "15m", 100)
            )

            if df_5m is None or df_15m is None:
                return None
            if len(df_5m) < 30 or len(df_15m) < 30:
                return None

            return self.analyze_symbol(symbol, df_5m, df_15m)
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return None


def _get_precision(price: float) -> int:
    if price >= 1000:
        return 1
    elif price >= 100:
        return 2
    elif price >= 1:
        return 3
    elif price >= 0.1:
        return 4
    else:
        return 6
