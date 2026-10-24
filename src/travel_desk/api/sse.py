"""SSE framing (docs/api.md §1.5): one frame per event, `id`/`event` from the envelope."""
from __future__ import annotations

import json

from sse_starlette.sse import EventSourceResponse

from travel_desk.workflow.engine import SubmitRun


def sse_response(run: SubmitRun) -> EventSourceResponse:
    async def frames():
        async for event in run.events():
            yield {"id": event["event_id"], "event": event["type"],
                   "data": json.dumps(event, ensure_ascii=False)}

    # The run is a background task: a client disconnect stops the frames, never the business write.
    return EventSourceResponse(frames(), ping=15)
