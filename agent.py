from typing import List
import json
import random
from datetime import datetime, timedelta
from langchain_ollama.chat_models import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain.agents.middleware import ShellToolMiddleware, HostExecutionPolicy


@tool
def read_json(filepath: str) -> str:
    """Read and return the contents of a JSON file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return json.dumps(data, indent=2)
    except FileNotFoundError:
        return f"Error: File '{filepath}' not found."
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON in file - {str(e)}"
    except Exception as e:
        return f"Error reading JSON: {str(e)}"


TOOLS = [read_json]

# ---- Use Ollama instead of OpenAI ----
llm = ChatOllama(
    model="llama3.1:8b",  # or any Ollama model you pulled
    base_url="http://ef-nexus-02.efinance.com.eg:8081",
    temperature=0
)

SYSTEM_MESSAGE = (
    "You are DataGen, a helpful assistant that generates sample data for applications. "
    "To generate users, you need: first_names (list), last_names (list), domains (list), min_age, max_age. "
    "Fill in these values yourself without asking for them. "
    "When asked to save users, first call the tool generate_sample_users with the required arguments. "
    "Then immediately call write_json with the dictionary returned by generate_sample_users. "
    "Ensure the parameter passed is a dictionary."
    "If the user refers to 'those users' from a previous request, ask them to specify the details again."
)

agent = create_agent(
    llm,
    TOOLS,
    system_prompt=SYSTEM_MESSAGE,
    middleware=ShellToolMiddleware(
        workspace_root="/app",
        execution_policy=HostExecutionPolicy(),
    ),
)


def run_agent(user_input: str, history: List[BaseMessage]) -> AIMessage:
    try:
        result = agent.invoke({"messages": [{"role": "user", "content": user_input}]})

        # Debug: see all messages including tool calls
        print("Full agent result:", result)

        return result["messages"][-1]
    except Exception as e:
        import traceback
        return AIMessage(content=f"Error: {repr(e)}\nTraceback:\n{traceback.format_exc()}")
