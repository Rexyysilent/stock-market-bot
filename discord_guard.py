"""Bounded, dependency-free controls for the optional Discord surface."""

from collections import OrderedDict, deque
import time


class RequestGate:
    """Per-user cooldown with bounded bookkeeping."""

    def __init__(self, cooldown_seconds, max_users=2048, clock=None):
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")
        if max_users < 1:
            raise ValueError("max_users must be positive")
        self.cooldown_seconds = float(cooldown_seconds)
        self.max_users = int(max_users)
        self._clock = clock or time.monotonic
        self._last_accepted = OrderedDict()

    def allow(self, user_id):
        """Return ``(allowed, retry_after_seconds)`` for one user."""
        now = float(self._clock())
        last = self._last_accepted.get(user_id)
        if last is not None:
            retry_after = self.cooldown_seconds - (now - last)
            self._last_accepted.move_to_end(user_id)
            if retry_after > 0:
                return False, retry_after

        self._last_accepted[user_id] = now
        self._last_accepted.move_to_end(user_id)
        while len(self._last_accepted) > self.max_users:
            self._last_accepted.popitem(last=False)
        return True, 0.0

    @property
    def tracked_users(self):
        return len(self._last_accepted)


class BoundedChatHistory:
    """LRU channel history with fixed per-channel and channel-count bounds."""

    def __init__(self, max_channels=100, max_entries_per_channel=20):
        if max_channels < 1 or max_entries_per_channel < 1:
            raise ValueError("history bounds must be positive")
        self.max_channels = int(max_channels)
        self.max_entries_per_channel = int(max_entries_per_channel)
        self._channels = OrderedDict()

    def _history(self, channel_id, create=False):
        history = self._channels.get(channel_id)
        if history is not None:
            self._channels.move_to_end(channel_id)
            return history
        if not create:
            return None
        history = deque(maxlen=self.max_entries_per_channel)
        self._channels[channel_id] = history
        while len(self._channels) > self.max_channels:
            self._channels.popitem(last=False)
        return history

    def append(self, channel_id, role, content):
        self._history(channel_id, create=True).append(
            {"role": role, "content": content}
        )

    def recent(self, channel_id, limit):
        history = self._history(channel_id)
        if history is None or limit <= 0:
            return []
        return list(history)[-limit:]

    @property
    def channel_count(self):
        return len(self._channels)
