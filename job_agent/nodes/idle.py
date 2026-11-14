from langchain_core.runnables import RunnableLambda

from job_agent.state import AgentState


def idle_logic(state: AgentState) -> dict:
    return {}


idle_chain = RunnableLambda(idle_logic).with_config({"run_name": "idle_runnable"})
