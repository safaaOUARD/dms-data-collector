import time
from datetime import datetime, timezone

class SyncClock:
    _t0_perf = None

    @classmethod
    def start(cls):
        cls._t0_perf = time.perf_counter()

    @classmethod
    def elapsed(cls) -> float:
        if cls._t0_perf is None:
            return 0.0
        return round(time.perf_counter() - cls._t0_perf, 4)

    @classmethod
    def now(cls) -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def reset(cls):
        cls._t0_perf = None