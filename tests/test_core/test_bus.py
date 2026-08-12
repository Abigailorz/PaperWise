"""Agent 消息总线测试"""

import asyncio

from paperwise.core.bus import AgentBus
from paperwise.tools.collab_tools import SendMessageTool, ReceiveMessageTool


def test_bus_send_receive_pending():
    bus = AgentBus.instance()
    bus.register("worker")
    try:
        assert bus.send("worker", {"from": "manager", "message": "hi"})
        assert bus.pending("worker") == 1
        msg = asyncio.run(bus.receive("worker", timeout=1))
        assert msg["message"] == "hi"
        assert bus.pending("worker") == 0
        # 未注册目标 → 投递失败
        assert not bus.send("ghost_agent", {"message": "x"})
    finally:
        bus.unregister("worker")


async def test_send_message_tool_delivers_to_bus(tmp_path):
    bus = AgentBus.instance()
    bus.register("analyst")
    try:
        sender = SendMessageTool(tmp_path)
        sender._agent_name = "manager"
        receiver = ReceiveMessageTool(tmp_path, agent_name="analyst")

        out = await sender.execute("analyst", "请检查表格数据")
        assert "已投递" in out

        out2 = await receiver.execute(timeout=0)
        assert "请检查表格数据" in out2
        assert "manager" in out2
    finally:
        bus.unregister("analyst")
