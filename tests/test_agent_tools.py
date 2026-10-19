"""lookup_patient / register_patient use the real WhatsApp sender's number, never
the model's copy of it (the model garbles it -- production already holds a
patient stored with phone "20196")."""
from app.models import Patient
from app.whatsapp_agent import tools


def test_register_uses_the_real_sender_number_not_the_models_copy(db, seed):
    token = tools.CURRENT_SENDER_PHONE.set("201099990004")
    try:
        result = tools.register_patient.invoke({"name": "نور أيمن", "phone": "20120999900004"})  # garbled by the model
        found = tools.lookup_patient.invoke({"phone": "2019"})
    finally:
        tools.CURRENT_SENDER_PHONE.reset(token)
    assert "patient_id=" in result and "patient_id=" in found
    assert db.query(Patient).filter(Patient.phone == "201099990004").count() == 1
    assert db.query(Patient).filter(Patient.phone == "20120999900004").count() == 0


def test_without_a_sender_the_typed_number_is_used(db, seed):
    tools.register_patient.invoke({"name": "Walk-in", "phone": "01155566677"})  # e.g. a script, no WhatsApp context
    assert db.query(Patient).filter(Patient.phone == "201155566677").count() == 1


def test_the_sender_number_reaches_tools_run_by_the_agent_graph(db, seed):
    """The agent's ToolNode runs tools in worker threads -- the number must still get there."""
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.graph import START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    builder = StateGraph(MessagesState)
    builder.add_node("tools", ToolNode(tools.tools))
    builder.add_edge(START, "tools")
    graph = builder.compile()
    call = AIMessage(content="", tool_calls=[{"name": "lookup_patient", "args": {"phone": "20999"}, "id": "c1"}])
    token = tools.CURRENT_SENDER_PHONE.set("201111111111")  # the seeded patient
    try:
        out = graph.invoke({"messages": [HumanMessage(content="x"), call]})
    finally:
        tools.CURRENT_SENDER_PHONE.reset(token)
    assert f"patient_id={seed['patient'].id}" in out["messages"][-1].content
