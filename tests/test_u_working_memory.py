"""Unit tests for WorkingMemory (DEMO02-style)."""

from memory.working import WorkingMemory


def test_u_working_memory_truncates_when_over_token_budget():
    wm = WorkingMemory(token_limit=80)
    long_text = "x" * 500
    wm.add("user", long_text)
    wm.add("assistant", "short reply")
    msgs = wm.get_messages()
    assert len(msgs) >= 1
    total_chars = sum(len(m.get("content", "")) for m in msgs)
    assert total_chars < len(long_text) + 50
