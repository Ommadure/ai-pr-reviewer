"""The keep-warm job for an API host that sleeps when idle (Render free)."""

import httpx
import respx

from app.workers.schedules import BEAT_SCHEDULE, KEEP_WARM_SECONDS, beat_schedule
from app.workers.tasks import keep_api_warm

URL = "https://reviewpilot-api.onrender.com/api/v1/health"


def test_scheduled_only_when_a_url_is_configured() -> None:
    assert beat_schedule("") == BEAT_SCHEDULE
    entry = beat_schedule(URL)["keep-api-warm"]
    assert entry["args"] == (URL,)
    assert entry["schedule"] < 15 * 60 == 900  # inside Render's 15-minute idle window
    assert entry["schedule"] == KEEP_WARM_SECONDS


@respx.mock
def test_ping_reports_status_and_never_raises() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200))
    assert keep_api_warm.apply(args=(URL,)).get() == 200
    route.mock(return_value=httpx.Response(503))
    assert keep_api_warm.apply(args=(URL,)).get() == 503
    route.mock(side_effect=httpx.ConnectTimeout("slow"))
    assert keep_api_warm.apply(args=(URL,)).get() is None
