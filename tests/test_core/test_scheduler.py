"""主动调度器测试"""

import asyncio

from paperwise.core.scheduler import Scheduler
from paperwise.tools.collab_tools import SetTimerTool


def test_timer_fires_callback():
    sched = Scheduler()
    events = []

    async def run():
        sched.start()
        sched.add_timer(0.1, "hello", "sess1",
                        callback=lambda ev: events.append(ev))
        await asyncio.sleep(0.7)
        await sched.stop()

    asyncio.run(run())
    assert len(events) == 1
    assert events[0]["message"] == "hello"
    assert events[0]["session_id"] == "sess1"


def test_monitor_event_fires():
    sched = Scheduler()
    events = []

    async def run():
        sched.start()
        sched.add_monitor("done", "sess1",
                          callback=lambda ev: events.append(ev), task_id="m1")
        sched.fire_monitor("m1", "output-ok")
        await asyncio.sleep(0.7)
        await sched.stop()

    asyncio.run(run())
    assert events and events[0]["output"] == "output-ok"
    assert events[0]["type"] == "monitor"


async def test_set_timer_tool_registers_with_scheduler(tmp_path):
    sched = Scheduler()
    sched.start()
    try:
        tool = SetTimerTool(tmp_path)
        tool._scheduler = sched
        tool._session_id = "sess-1"
        fired = []
        tool._scheduler_callback = lambda ev: fired.append(ev)

        out = await tool.execute(0.1, "检查进度")
        assert "系统调度器" in out
        assert sched.pending() == 1

        await asyncio.sleep(0.7)
        assert fired and fired[0]["message"] == "检查进度"
    finally:
        await sched.stop()
