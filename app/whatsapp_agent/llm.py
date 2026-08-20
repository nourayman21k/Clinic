from langchain_groq import ChatGroq

llm = ChatGroq(model="openai/gpt-oss-120b", temperature=0)      # Layer 2 — full reasoning
llm_mini = ChatGroq(model="openai/gpt-oss-20b", temperature=0)  # Layer 1 — fast/cheap routing
llm_full = llm
