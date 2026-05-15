from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Any

from crypto_perp_tool.journal import JsonlJournal
from crypto_perp_tool.service import TradingService


class TelegramCommandHandler:
    def __init__(self, service: TradingService, allowed_chat_ids: set[int], store: Any = None) -> None:
        self.service = service
        self.allowed_chat_ids = allowed_chat_ids
        self._store = store

    def handle(self, chat_id: int, text: str) -> str:
        if chat_id not in self.allowed_chat_ids:
            self.service.journal.write("telegram_command_rejected", {"chat_id": chat_id, "text": text})
            return "unauthorized chat id"

        command = text.strip().split()[0].lower() if text.strip() else ""
        self.service.journal.write("telegram_command", {"chat_id": chat_id, "text": command})

        if command == "/status":
            return self.service.status()
        if command == "/pause":
            return self.service.pause(actor=f"telegram:{chat_id}")
        if command == "/resume":
            return self.service.resume(actor=f"telegram:{chat_id}")
        if command == "/risk":
            risk = self.service.risk()
            return " ".join(f"{key}={value}" for key, value in risk.items())
        if command == "/journal":
            return str(self.service.recent_journal(limit=3))
        if command == "/positions":
            return self._format_positions()
        if command == "/circuit":
            return self._format_circuit()
        if command == "/set":
            return self._handle_set(text)
        if command == "/config":
            return self._format_config()
        return "unknown command"

    def _format_positions(self) -> str:
        if self._store is None:
            return "not connected to trading engine"
        view = self._store.view()
        pos = view.get("summary", {}).get("open_position")
        if pos is None:
            details = view.get("details", {})
            paper = details.get("paper", {})
            pnl_24h = paper.get("pnl_by_range", {}).get("24h", 0)
            closed_count = len(paper.get("closed_positions", []))
            return (
                f"No open position\n"
                f"PnL 24h: {pnl_24h:.2f}\n"
                f"Closed positions: {closed_count}"
            )
        return (
            f"{pos.get('symbol','?')} {pos.get('side','?')} {pos.get('setup','?')}\n"
            f"Entry: {pos.get('entry_price',0):.2f}  Stop: {pos.get('stop_price',0):.2f}  Target: {pos.get('target_price',0):.2f}\n"
            f"Qty: {pos.get('quantity',0)}  BE shifted: {pos.get('break_even_shifted',False)}  Absorb: {pos.get('absorption_reduced',False)}"
        )

    def _handle_set(self, text: str) -> str:
        parts = text.strip().split()
        if len(parts) < 3:
            return ("usage: /set <key> <value>\n"
                    "risk keys: risk_per_trade, daily_loss_limit, max_consecutive_losses, max_leverage, max_symbol_notional\n"
                    "store keys: equity, cooldown_ms, flash_atr_mult, flash_pct\n"
                    "strategy keys: reward_risk, dynamic_reward_risk_enabled, reward_risk_min, reward_risk_max, "
                    "atr_stop_mult, min_stop_cost_mult, min_target_cost_mult, max_holding_min")
        key = parts[1]
        value = parts[2]
        return self.service.update_setting(key, value)

    def _format_config(self) -> str:
        risk = self.service.risk()
        lines = ["Risk settings:"]
        lines.append(f"  risk_per_trade={risk.get('risk_per_trade','?')}")
        lines.append(f"  daily_loss_limit={risk.get('daily_loss_limit','?')}")
        lines.append(f"  max_consecutive_losses={risk.get('max_consecutive_losses','?')}")
        lines.append(f"  max_leverage={risk.get('max_leverage','?')}")
        lines.append(f"  max_symbol_notional={risk.get('max_symbol_notional_equity_multiple','?')}")
        if self._store is not None:
            settings = getattr(self._store, 'settings', None)
            if settings is not None:
                exec_s = settings.execution
                lines.append("")
                lines.append("Strategy settings:")
                lines.append(f"  reward_risk={exec_s.reward_risk}")
                lines.append(f"  dynamic_reward_risk_enabled={exec_s.dynamic_reward_risk_enabled}")
                lines.append(f"  reward_risk_min={exec_s.reward_risk_min}")
                lines.append(f"  reward_risk_max={exec_s.reward_risk_max}")
                lines.append(f"  atr_stop_mult={exec_s.atr_stop_mult}")
                lines.append(f"  break_even_trigger_r=position_target_r_multiple / 2")
                lines.append(f"  min_stop_cost_mult={exec_s.min_stop_cost_mult}")
                lines.append(f"  min_target_cost_mult={exec_s.min_target_cost_mult}")
                lines.append(f"  max_holding_min={exec_s.max_holding_ms // 60_000}")
            store_lines = ["Store settings:"]
            store_lines.append(f"  equity={getattr(self._store, 'equity', '?')}")
            cb = getattr(self._store, '_circuit_breaker', None)
            if cb is not None:
                store_lines.append(f"  cooldown_ms={cb.hard_cooldown_ms}")
            fcd = getattr(self._store, '_flash_crash_detector', None)
            if fcd is not None:
                store_lines.append(f"  flash_atr_mult={fcd.atr_multiplier}")
                store_lines.append(f"  flash_pct={fcd.pct_threshold}")
            if len(store_lines) > 1:
                lines.append("")
                lines.extend(store_lines)
        return "\n".join(lines)

    def _format_circuit(self) -> str:
        if self._store is None:
            return "not connected to trading engine"
        summary = self._store.view().get("summary", {})
        state = summary.get("circuit_state", "unknown")
        reason = summary.get("circuit_reason")
        cooldown = summary.get("cooldown_until")
        parts = [f"Circuit: {state}"]
        if reason:
            parts.append(f"Reason: {reason}")
        if cooldown:
            parts.append(f"Cooldown until: {cooldown}")
        return "\n".join(parts)


class TelegramPoller:
    """Long-polls the Telegram Bot API for commands and dispatches them to the handler."""

    def __init__(
        self,
        handler: TelegramCommandHandler,
        token: str | None = None,
        poll_interval: float = 2.0,
        journal: JsonlJournal | None = None,
    ) -> None:
        self.handler = handler
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.poll_interval = poll_interval
        self.journal = journal
        self._offset: int | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._error_count = 0
        self._max_errors = 10

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        if not self.token:
            self._log("telegram_poller", {"error": "no TELEGRAM_BOT_TOKEN configured"})
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="telegram-poller")
        self._thread.start()
        self._log("telegram_poller_started", {"token_prefix": self.token[:8] + "..." if len(self.token) > 8 else self.token})

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._log("telegram_poller_stopped", {})

    def is_running(self) -> bool:
        return self._running and (self._thread is not None and self._thread.is_alive())

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        while self._running:
            try:
                updates = self._get_updates()
                if updates is None:
                    self._error_count += 1
                    if self._error_count >= self._max_errors:
                        self._log("telegram_poller_max_errors", {"error_count": self._error_count})
                        self._running = False
                        return
                    time.sleep(min(self.poll_interval * 2, 30.0))
                    continue
                self._error_count = 0
                for update in updates:
                    self._process_update(update)
                time.sleep(self.poll_interval)
            except Exception as exc:
                self._error_count += 1
                self._log("telegram_poller_error", {"error": str(exc)})
                if self._error_count >= self._max_errors:
                    self._running = False
                    return
                time.sleep(self.poll_interval * 2)

    # ------------------------------------------------------------------
    # Telegram API
    # ------------------------------------------------------------------

    def _get_updates(self) -> list[dict[str, Any]] | None:
        params = f"timeout={int(self.poll_interval + 5)}"
        if self._offset is not None:
            params += f"&offset={self._offset}"
        url = f"https://api.telegram.org/bot{self.token}/getUpdates?{params}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crypto-perp-tool"})
            with urllib.request.urlopen(req, timeout=self.poll_interval + 10) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            return None
        if not data.get("ok"):
            return None
        return data.get("result", [])

    def _process_update(self, update: dict[str, Any]) -> None:
        update_id = int(update.get("update_id", 0))
        self._offset = max(self._offset or 0, update_id + 1)

        message = update.get("message") or update.get("channel_post")
        if message is None:
            return
        chat = message.get("chat", {})
        chat_id = int(chat.get("id", 0))
        text = message.get("text", "")
        if not text or not chat_id:
            return

        reply = self.handler.handle(chat_id, text)
        self._send_message(chat_id, reply)

    def _send_message(self, chat_id: int, text: str) -> bool:
        payload = json.dumps({"chat_id": chat_id, "text": text[:4096]}).encode()
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "crypto-perp-tool"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            return bool(data.get("ok"))
        except Exception:
            return False

    def _log(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.journal is not None:
            try:
                self.journal.write(event_type, payload)
            except Exception:
                pass


def parse_allowed_chat_ids(raw: str) -> set[int]:
    """Parse TELEGRAM_ALLOWED_CHAT_IDS env var: comma-separated integers."""
    if not raw or not raw.strip():
        return set()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return ids
