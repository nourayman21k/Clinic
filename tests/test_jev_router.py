"""The System 1 router (app/whatsapp_agent/router.py): JEV's tool scoping, plus
the multi-turn safety rules -- tested with a fake decider (no GPU, no Groq)."""
import sys
import uuid

import pytest
from langchain_core.messages import AIMessage

from app.whatsapp_agent import graph, router, sender

BOOK = set(router.TOOL_GROUPS["book_appointment"])


@pytest.fixture
def jev(monkeypatch):
    """answers: patient text -> (label, confidence). calls: what the router was asked."""
    answers, calls = {}, []

    def fake_classify(user_message, previous_reply=None):
        calls.append((user_message, previous_reply))
        label, confidence = answers.get(user_message, ("general_chat", 0.1))
        return {"label": label, "confidence": confidence}

    monkeypatch.setattr(router, "ENABLED", True)
    monkeypatch.setattr(router, "classify_with_jev", fake_classify)
    monkeypatch.setattr(router, "get_decider", lambda: object())
    router._classify_cached.cache_clear()
    yield answers, calls
    router._classify_cached.cache_clear()


# ------------------------------------------------------------------ route()

def test_confident_tool_binds_its_group(jev):
    jev[0]["عايز اغير ميعادي"] = ("reschedule_appointment", 0.92)
    d = router.route("عايز اغير ميعادي")
    assert d["selected_tools"] == router.TOOL_GROUPS["reschedule_appointment"] and not d["general_chat"]
    assert d["jev_label"] == "reschedule_appointment" and d["jev_used"]


def test_low_confidence_falls_back_to_all_tools(jev):
    jev[0]["ممكن"] = ("book_appointment", 0.5)
    d = router.route("ممكن")
    assert d["selected_tools"] == [] and d["general_chat"] is False  # [] + not general = all tools


def test_confident_general_chat_binds_only_identity_tools(jev):
    jev[0]["شكرا"] = ("general_chat", 0.97)
    d = router.route("شكرا")
    assert d["general_chat"] is True and d["selected_tools"] == router.GENERAL_CHAT_TOOLS


def test_general_chat_needs_the_stricter_threshold(jev):
    jev[0]["الدكتور قالي محتاج ايه؟"] = ("general_chat", 0.84)  # a real question the model misread
    d = router.route("الدكتور قالي محتاج ايه؟")
    assert d["general_chat"] is False and d["selected_tools"] == []  # -> all tools, not none


def test_treatment_lookup_can_finish_a_treatment_booking(jev):
    jev[0]["عايز اركب التاج"] = ("get_pending_treatments", 0.81)
    assert "book_appointment" in router.route("عايز اركب التاج")["selected_tools"]


def test_follow_up_never_loses_the_previous_turns_tools(jev):
    jev[0]["١٠:٣٠"] = ("general_chat", 0.98)
    d = router.route("١٠:٣٠", "تحب الساعة كام؟", tuple(router.TOOL_GROUPS["book_appointment"]))
    assert set(d["selected_tools"]) == BOOK and not d["general_chat"]


def test_follow_up_widens_scope_never_narrows(jev):
    jev[0]["والغي التاني"] = ("cancel_appointment", 0.95)
    d = router.route("والغي التاني", "تم الحجز", tuple(router.TOOL_GROUPS["book_appointment"]))
    assert set(d["selected_tools"]) == BOOK | set(router.TOOL_GROUPS["cancel_appointment"])


def test_after_an_all_tools_turn_general_chat_keeps_all_tools(jev):
    jev[0]["تمام"] = ("general_chat", 0.99)
    d = router.route("تمام", "عايز يوم ايه؟", tuple(router.ALL_TOOLS))
    assert set(d["selected_tools"]) == set(router.ALL_TOOLS)


def test_disabled_router_changes_nothing(monkeypatch):
    monkeypatch.setattr(router, "ENABLED", False)
    d = router.route("عايز احجز")
    assert d == {"selected_tools": [], "general_chat": False, "jev_label": "", "jev_confidence": 0.0, "jev_used": False}


def test_router_crash_falls_back_to_all_tools(jev, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(router, "classify_with_jev", boom)
    d = router.route("عايز احجز")
    assert d["selected_tools"] == [] and not d["general_chat"] and not d["jev_used"]


def test_model_load_failure_disables_router_once(monkeypatch, capsys):
    monkeypatch.setattr(router, "ENABLED", True)
    monkeypatch.setattr(router, "_decider", None)
    monkeypatch.setattr(router, "_load_error", None)
    monkeypatch.setitem(sys.modules, "so1", None)  # "package not installed"
    assert router.get_decider() is None and router.get_decider() is None
    assert capsys.readouterr().out.count("Router disabled") == 1
    router._classify_cached.cache_clear()
    assert router.route("عايز احجز")["jev_used"] is False


def test_every_label_maps_to_real_tools():
    assert set(router.TOOL_LABELS) == set(router.ALL_TOOLS) | {"general_chat"}
    for label, group in router.TOOL_GROUPS.items():
        assert label in router.ALL_TOOLS and set(group) <= set(router.ALL_TOOLS) and label in group


# ------------------------------------------------- inside the agent graph

class FakeModel:
    """Records which tools each Groq call would have had bound."""

    def __init__(self, log, bound=None):
        self.log, self.bound = log, bound

    def bind_tools(self, tools):
        return FakeModel(self.log, [t.name for t in tools])

    def invoke(self, _messages):
        self.log.append(self.bound)
        return AIMessage(content="تحب الساعة كام؟")


@pytest.fixture
def agent_calls(monkeypatch, seed):
    log = []
    monkeypatch.setattr(graph, "llm_mini", FakeModel(log))
    monkeypatch.setattr(graph, "llm_full", FakeModel(log))
    monkeypatch.setattr(graph, "llm_mini_with_tools", FakeModel(log, list(router.ALL_TOOLS)))
    monkeypatch.setattr(graph, "llm_full_with_tools", FakeModel(log, list(router.ALL_TOOLS)))
    monkeypatch.setattr(graph, "extract_booking_info", lambda _t: graph.BookingExtraction(intent="unclear"))
    monkeypatch.setattr(sender, "send_whatsapp_text_to_chat", lambda *_a: True)
    return log


def _chat():
    return f"2010{uuid.uuid4().int % 10**8:08d}@c.us"


def test_graph_binds_only_the_routed_tools_across_turns(jev, agent_calls, monkeypatch):
    def no_llm_routing(_t):
        raise AssertionError("the Groq routing call must be skipped when the System 1 router is available")

    monkeypatch.setattr(graph, "route_message", no_llm_routing)
    answers, calls = jev
    answers["عايز احجز كشف بكرة"] = ("book_appointment", 0.93)
    answers["١٠:٣٠"] = ("general_chat", 0.98)
    chat = _chat()

    graph.handle_whatsapp_message_v2(chat, chat.split("@")[0], "عايز احجز كشف بكرة")
    assert set(agent_calls[-1]) == BOOK
    assert calls[-1] == ("عايز احجز كشف بكرة", None)  # raw words, not the system-note-enriched text

    graph.handle_whatsapp_message_v2(chat, chat.split("@")[0], "١٠:٣٠")
    assert set(agent_calls[-1]) == BOOK  # still able to book mid-conversation
    assert calls[-1] == ("١٠:٣٠", "تحب الساعة كام؟")  # the router saw the bot's question


def test_graph_greeting_gets_no_tools_and_unsure_gets_all(jev, agent_calls, monkeypatch):
    monkeypatch.setattr(graph, "route_message", lambda _t: pytest.fail("LLM routing should be skipped"))
    jev[0]["السلام عليكم"] = ("general_chat", 0.96)
    jev[0]["عندي سؤال"] = ("get_patient_appointments", 0.4)
    graph.handle_whatsapp_message_v2(c := _chat(), c.split("@")[0], "السلام عليكم")
    assert agent_calls[-1] == router.GENERAL_CHAT_TOOLS  # identity lookups only, never zero tools
    graph.handle_whatsapp_message_v2(c := _chat(), c.split("@")[0], "عندي سؤال")
    assert set(agent_calls[-1]) == set(router.ALL_TOOLS)


def test_without_the_router_the_old_flow_is_unchanged(agent_calls, monkeypatch):
    routed = []
    monkeypatch.setattr(router, "ENABLED", False)
    monkeypatch.setattr(graph, "route_message",
                        lambda t: routed.append(t) or graph.RouteDecision(route="agent", complexity="simple"))
    graph.handle_whatsapp_message_v2(c := _chat(), c.split("@")[0], "عايز احجز")
    assert routed == ["عايز احجز"] and set(agent_calls[-1]) == set(router.ALL_TOOLS)


def test_empty_model_reply_is_never_sent(jev, agent_calls, monkeypatch):
    sent = []
    monkeypatch.setattr(sender, "send_whatsapp_text_to_chat", lambda chat, text: sent.append(text) or True)
    monkeypatch.setattr(FakeModel, "invoke", lambda self, _m: self.log.append(self.bound) or AIMessage(content=""))
    jev[0]["هاي"] = ("general_chat", 0.99)
    graph.handle_whatsapp_message_v2(c := _chat(), c.split("@")[0], "هاي")
    assert sent == [graph.EMPTY_REPLY_FALLBACK]


def test_too_narrow_scope_escalates_to_all_tools(jev, agent_calls, monkeypatch):
    """Groq rejects a call to an unbound tool; the agent must retry with every tool, not fail."""
    original = FakeModel.invoke

    def strict_invoke(self, messages):
        if self.bound is not None and set(self.bound) != set(router.ALL_TOOLS):
            self.log.append(self.bound)
            raise RuntimeError("Error code: 400 - Tool call validation failed: attempted to call tool "
                               "'get_available_slots' which was not in request.tools")
        return original(self, messages)

    monkeypatch.setattr(FakeModel, "invoke", strict_invoke)
    sent = []
    monkeypatch.setattr(sender, "send_whatsapp_text_to_chat", lambda chat, text: sent.append(text) or True)
    jev[0]["اسمي نور وعايز احجز بكرة"] = ("register_patient", 0.9)
    graph.handle_whatsapp_message_v2(c := _chat(), c.split("@")[0], "اسمي نور وعايز احجز بكرة")
    assert set(agent_calls[-2]) == set(router.TOOL_GROUPS["register_patient"])  # the scoped attempt
    assert set(agent_calls[-1]) == set(router.ALL_TOOLS)  # escalated, and it worked
    assert sent == ["تحب الساعة كام؟"]  # a real reply, not the technical-problem apology
