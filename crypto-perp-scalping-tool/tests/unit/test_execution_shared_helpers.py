import unittest

from crypto_perp_tool.execution.fills import (
    entry_fill_price,
    entry_limit_fill_price,
    entry_limit_price,
    exit_fill_price,
    pending_entry_touched,
    position_pnl,
)
from crypto_perp_tool.execution.position_rules import (
    absorption_should_reduce,
    break_even_stop_price,
    kline_momentum_stop_price,
    max_holding_reduced_target_price,
    partial_take_profit_price,
    price_moves,
    should_close_for_orderflow_invalidation,
    trailing_stop_price,
    triggered_close,
)
from crypto_perp_tool.market_data import KlineEvent
from crypto_perp_tool.types import SignalSide, TradeSignal


def _signal(side: SignalSide) -> TradeSignal:
    return TradeSignal(
        id=f"sig-{side.value}",
        symbol="BTCUSDT",
        side=side,
        setup="test",
        entry_price=100,
        stop_price=90 if side == SignalSide.LONG else 110,
        target_price=130 if side == SignalSide.LONG else 70,
        confidence=0.8,
        reasons=("test",),
        invalidation_rules=("stop",),
        created_at=1_000,
    )


class ExecutionFillHelperTests(unittest.TestCase):
    def test_limit_price_and_touch_rules_are_side_aware(self):
        long_signal = _signal(SignalSide.LONG)
        short_signal = _signal(SignalSide.SHORT)

        self.assertEqual(entry_limit_price(long_signal, pullback_bps=100), 99)
        self.assertEqual(entry_limit_price(short_signal, pullback_bps=100), 101)
        self.assertTrue(pending_entry_touched(SignalSide.LONG, limit_price=99, price=98.9))
        self.assertFalse(pending_entry_touched(SignalSide.LONG, limit_price=99, price=99.1))
        self.assertTrue(pending_entry_touched(SignalSide.SHORT, limit_price=101, price=101.1))
        self.assertFalse(pending_entry_touched(SignalSide.SHORT, limit_price=101, price=100.9))

    def test_fill_prices_and_pnl_are_side_aware(self):
        long_signal = _signal(SignalSide.LONG)
        short_signal = _signal(SignalSide.SHORT)

        self.assertEqual(entry_limit_fill_price(long_signal, limit_price=99, event_price=98.5), 98.5)
        self.assertEqual(entry_limit_fill_price(short_signal, limit_price=101, event_price=101.5), 101.5)
        self.assertEqual(entry_fill_price(long_signal, slippage_bps=10), 100.1)
        self.assertEqual(entry_fill_price(short_signal, slippage_bps=10), 99.9)
        self.assertEqual(exit_fill_price(SignalSide.LONG, trigger_price=100, slippage_bps=10), 99.9)
        self.assertEqual(exit_fill_price(SignalSide.SHORT, trigger_price=100, slippage_bps=10), 100.1)
        self.assertEqual(position_pnl(SignalSide.LONG, entry_price=100, quantity=2, close_price=103), 6)
        self.assertEqual(position_pnl(SignalSide.SHORT, entry_price=100, quantity=2, close_price=97), 6)


class PositionRuleHelperTests(unittest.TestCase):
    def test_triggered_close_identifies_stop_and_target_without_time_stop(self):
        self.assertEqual(
            triggered_close(
                SignalSide.LONG,
                stop_price=95,
                target_price=110,
                opened_at=1_000,
                current_price=94,
                timestamp=2_000,
                max_holding_ms=10_000,
            ),
            (95, "stop_loss"),
        )
        self.assertEqual(
            triggered_close(
                SignalSide.LONG,
                stop_price=95,
                target_price=110,
                opened_at=1_000,
                current_price=94,
                timestamp=2_000,
                max_holding_ms=10_000,
                trail_stop_price=95,
            ),
            (95, "trailing_stop"),
        )
        self.assertEqual(
            triggered_close(
                SignalSide.SHORT,
                stop_price=105,
                target_price=90,
                opened_at=1_000,
                current_price=89,
                timestamp=2_000,
                max_holding_ms=10_000,
            ),
            (90, "target"),
        )
        self.assertEqual(
            triggered_close(
                SignalSide.LONG,
                stop_price=95,
                target_price=110,
                opened_at=1_000,
                current_price=100,
                timestamp=12_000,
                max_holding_ms=10_000,
            ),
            (None, None),
        )

    def test_max_holding_reduced_target_steps_down_to_breakeven_floor(self):
        self.assertEqual(
            max_holding_reduced_target_price(
                SignalSide.LONG,
                entry_price=100,
                initial_stop_price=90,
                current_target_r_multiple=3.0,
                elapsed_ms=20_000,
                max_holding_ms=10_000,
                completed_reductions=0,
                round_trip_cost=0.5,
            ),
            (110.0, 1.0, 2),
        )
        self.assertEqual(
            max_holding_reduced_target_price(
                SignalSide.SHORT,
                entry_price=100,
                initial_stop_price=110,
                current_target_r_multiple=1.0,
                elapsed_ms=30_000,
                max_holding_ms=10_000,
                completed_reductions=0,
                round_trip_cost=0.5,
            ),
            (99.5, 0.05, 3),
        )

    def test_position_management_calculations_are_side_aware(self):
        self.assertEqual(price_moves(SignalSide.LONG, entry_price=100, price=112), (12, -12))
        self.assertEqual(price_moves(SignalSide.SHORT, entry_price=100, price=88), (12, -12))
        self.assertEqual(
            partial_take_profit_price(
                SignalSide.LONG,
                entry_price=100,
                initial_stop_price=90,
                current_price=110,
                first_take_profit_r=1,
            ),
            110,
        )
        self.assertIsNone(
            partial_take_profit_price(
                SignalSide.LONG,
                entry_price=100,
                initial_stop_price=90,
                current_price=109.9,
                first_take_profit_r=1,
            )
        )
        self.assertEqual(
            break_even_stop_price(
                SignalSide.SHORT,
                entry_price=100,
                initial_stop_price=110,
                current_price=85,
                break_even_trigger_r=1.5,
                round_trip_cost=0.1,
            ),
            99.9,
        )
        self.assertEqual(
            trailing_stop_price(
                SignalSide.LONG,
                entry_price=100,
                initial_stop_price=90,
                current_stop_price=100,
                current_price=112,
                atr=4,
                trail_after_r=1,
                trail_atr_multiple=0.5,
            ),
            110,
        )
        self.assertIsNone(
            trailing_stop_price(
                SignalSide.LONG,
                entry_price=100,
                initial_stop_price=90,
                current_stop_price=111,
                current_price=112,
                atr=4,
                trail_after_r=1,
                trail_atr_multiple=0.5,
            )
        )

    def test_kline_momentum_stop_shift_waits_for_pullback_confirmation(self):
        long_bars = [
            KlineEvent(0, 59_999, "BTCUSDT", "1m", 98, 105, 97, 104, 10, 1_000, 5, True),
            KlineEvent(60_000, 119_999, "BTCUSDT", "1m", 100, 108, 99, 104, 10, 1_000, 5, True),
            KlineEvent(120_000, 179_999, "BTCUSDT", "1m", 104, 109, 102, 107, 10, 1_000, 5, True),
            KlineEvent(180_000, 239_999, "BTCUSDT", "1m", 107, 112, 105, 111, 10, 1_000, 5, True),
        ]
        self.assertIsNone(
            kline_momentum_stop_price(
                SignalSide.LONG,
                opened_at=30_000,
                current_stop_price=95,
                current_price=113,
                closed_klines=long_bars,
                consecutive_bars=3,
                reference_bars=2,
            )
        )
        self.assertIsNone(
            kline_momentum_stop_price(
                SignalSide.LONG,
                opened_at=30_000,
                current_stop_price=103,
                current_price=113,
                closed_klines=long_bars,
                consecutive_bars=3,
                reference_bars=2,
            )
        )

        short_bars = [
            KlineEvent(60_000, 119_999, "BTCUSDT", "1m", 110, 112, 106, 108, 10, 1_000, 5, True),
            KlineEvent(120_000, 179_999, "BTCUSDT", "1m", 108, 109, 103, 105, 10, 1_000, 5, True),
            KlineEvent(180_000, 239_999, "BTCUSDT", "1m", 105, 106, 100, 101, 10, 1_000, 5, True),
        ]
        self.assertIsNone(
            kline_momentum_stop_price(
                SignalSide.SHORT,
                opened_at=30_000,
                current_stop_price=115,
                current_price=99,
                closed_klines=short_bars,
                consecutive_bars=3,
                reference_bars=2,
            )
        )

    def test_kline_momentum_waits_for_supported_bearish_pullback_after_three_bull_bars(self):
        bull_run = [
            KlineEvent(60_000, 119_999, "BTCUSDT", "1m", 100, 106, 99, 105, 100, 10_500, 5, True),
            KlineEvent(120_000, 179_999, "BTCUSDT", "1m", 105, 110, 104, 109, 120, 12_900, 5, True),
            KlineEvent(180_000, 239_999, "BTCUSDT", "1m", 109, 113, 108, 112, 110, 12_210, 5, True),
        ]
        self.assertIsNone(
            kline_momentum_stop_price(
                SignalSide.LONG,
                opened_at=30_000,
                current_stop_price=95,
                current_price=113,
                closed_klines=bull_run,
                consecutive_bars=3,
                reference_bars=2,
            )
        )

        supported_pullback = [
            *bull_run,
            KlineEvent(240_000, 299_999, "BTCUSDT", "1m", 112, 113, 106, 110, 95, 10_450, 5, True),
        ]
        self.assertEqual(
            kline_momentum_stop_price(
                SignalSide.LONG,
                opened_at=30_000,
                current_stop_price=95,
                current_price=111,
                closed_klines=supported_pullback,
                consecutive_bars=3,
                reference_bars=2,
            ),
            99,
        )

    def test_kline_momentum_ignores_bearish_pullback_without_lower_wick_support(self):
        bars = [
            KlineEvent(60_000, 119_999, "BTCUSDT", "1m", 100, 106, 99, 105, 100, 10_500, 5, True),
            KlineEvent(120_000, 179_999, "BTCUSDT", "1m", 105, 110, 104, 109, 120, 12_900, 5, True),
            KlineEvent(180_000, 239_999, "BTCUSDT", "1m", 109, 113, 108, 112, 110, 12_210, 5, True),
            KlineEvent(240_000, 299_999, "BTCUSDT", "1m", 112, 113, 109.3, 110, 95, 10_450, 5, True),
        ]

        self.assertIsNone(
            kline_momentum_stop_price(
                SignalSide.LONG,
                opened_at=30_000,
                current_stop_price=95,
                current_price=111,
                closed_klines=bars,
                consecutive_bars=3,
                reference_bars=2,
            )
        )

    def test_kline_momentum_short_uses_supported_bullish_pullback_after_three_bear_bars(self):
        bars = [
            KlineEvent(60_000, 119_999, "BTCUSDT", "1m", 110, 112, 104, 105, 100, 10_500, 5, True),
            KlineEvent(120_000, 179_999, "BTCUSDT", "1m", 105, 106, 100, 101, 120, 12_120, 5, True),
            KlineEvent(180_000, 239_999, "BTCUSDT", "1m", 101, 102, 96, 98, 110, 10_890, 5, True),
            KlineEvent(240_000, 299_999, "BTCUSDT", "1m", 98, 104, 97, 100, 95, 9_500, 5, True),
        ]

        self.assertEqual(
            kline_momentum_stop_price(
                SignalSide.SHORT,
                opened_at=30_000,
                current_stop_price=115,
                current_price=99,
                closed_klines=bars,
                consecutive_bars=3,
                reference_bars=2,
            ),
            112,
        )

    def test_orderflow_and_absorption_rules_share_the_same_move_math(self):
        self.assertTrue(
            absorption_should_reduce(
                SignalSide.LONG,
                delta_30s=30,
                baseline=10,
                entry_price=100,
                current_price=100.05,
                atr=1,
            )
        )
        self.assertFalse(
            absorption_should_reduce(
                SignalSide.LONG,
                delta_30s=30,
                baseline=10,
                entry_price=100,
                current_price=102,
                atr=1,
            )
        )
        self.assertTrue(
            should_close_for_orderflow_invalidation(
                SignalSide.LONG,
                delta_30s=-30,
                baseline=10,
                entry_price=100,
                initial_stop_price=90,
                current_price=101,
            )
        )
        self.assertFalse(
            should_close_for_orderflow_invalidation(
                SignalSide.LONG,
                delta_30s=-30,
                baseline=10,
                entry_price=100,
                initial_stop_price=90,
                current_price=103,
            )
        )


if __name__ == "__main__":
    unittest.main()
