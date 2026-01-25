from __future__ import annotations

from functools import lru_cache
from typing import List
import json
import random
from datetime import datetime, timedelta

from langchain_ollama.chat_models import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.tools import tool
from langchain.agents import create_agent

from src.ai.agents.datagen_agent_config import DataGenAgentConfig


# -------- Tools --------
@tool
def write_json(filepath: str, data: dict) -> str:
    """Write a Python dictionary as JSON to a file with pretty formatting."""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return (
            f"Successfully wrote JSON data to '{filepath}' "
            f"({len(json.dumps(data))} characters)."
        )
    except Exception as e:  # noqa: BLE001
        return f"Error writing JSON: {str(e)}"


@tool
def read_json(filepath: str) -> str:
    """Read and return the contents of a JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return json.dumps(data, indent=2)
    except FileNotFoundError:
        return f"Error: File '{filepath}' not found."
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON in file - {str(e)}"
    except Exception as e:  # noqa: BLE001
        return f"Error reading JSON: {str(e)}"


@tool
def generate_sample_users(
    first_names: List[str],
    last_names: List[str],
    domains: List[str],
    min_age: int,
    max_age: int,
) -> dict:
    """Generate sample user data.

    Count is determined by the length of first_names.
    """
    if not first_names:
        return {"error": "first_names list cannot be empty"}
    if not last_names:
        return {"error": "last_names list cannot be empty"}
    if not domains:
        return {"error": "domains list cannot be empty"}
    if min_age > max_age:
        return {
            "error": f"min_age ({min_age}) cannot be greater than max_age ({max_age})",
        }
    if min_age < 0 or max_age < 0:
        return {"error": "ages must be non-negative"}

    users = []
    count = len(first_names)

    for i in range(count):
        first = first_names[i]
        last = last_names[i % len(last_names)]
        domain = domains[i % len(domains)]
        email = f"{first.lower()}.{last.lower()}@{domain}"

        user = {
            "id": i + 1,
            "firstName": first,
            "lastName": last,
            "email": email,
            "username": f"{first.lower()}{random.randint(100, 999)}",
            "age": random.randint(min_age, max_age),
            "registeredAt": (
                datetime.now()
                - timedelta(days=random.randint(1, 365))
            ).isoformat(),
        }
        users.append(user)

    return {"users": users, "count": len(users)}


TOOLS = [write_json, read_json, generate_sample_users]

SYSTEM_MESSAGE = (
    "You are DataGen, a helpful assistant that generates sample data for applications. "
    "To generate users, you need: first_names (list), last_names (list), domains (list), min_age, max_age. "
    "Fill in these values yourself without asking for them. "
    "When asked to save users, first call the tool generate_sample_users with the required arguments. "
    "Then immediately call write_json with the dictionary returned by generate_sample_users. "
    "Ensure the parameter passed is a dictionary. "
    "If the user refers to 'those users' from a previous request, ask them to specify the details again."
)

@lru_cache(maxsize=8)
def _agent_for(model: str, base_url: str, temperature: float):
    llm = ChatOllama(
        model=model,
        base_url=base_url,
        temperature=temperature,
    )
    return create_agent(llm, TOOLS, system_prompt=SYSTEM_MESSAGE)


def _generate_mock_response(user_input: str) -> AIMessage:
    """Generate a mock response when Ollama is disabled."""
    # Extract number of users from input (default to 10)
    import re
    match = re.search(r'(\d+)\s*users?', user_input.lower())
    count = int(match.group(1)) if match else 10

    # Generate sample users with default parameters
    default_first_names = ["Alice", "Bob", "Charlie", "Diana", "Eva", "Frank", "Grace", "Henry", "Ivy", "Jack"]
    default_last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis"]
    default_domains = ["example.com", "test.com", "demo.com"]

    first_names = default_first_names * ((count // len(default_first_names)) + 1)
    first_names = first_names[:count]

    result = generate_sample_users(
        first_names=first_names,
        last_names=default_last_names,
        domains=default_domains,
        min_age=18,
        max_age=65
    )

    # Check if user wants to save to file
    save_match = re.search(r'save.*?to\s+([^\s]+\.json)', user_input.lower())
    if save_match:
        filepath = save_match.group(1)
        write_result = write_json(filepath, result)
        response = (
            f"Generated {count} sample users and saved to {filepath}.\n\n"
            f"**Note:** Ollama is disabled. Using mock data generator.\n\n"
            f"Summary: {result['count']} users created with random names, emails, and ages.\n\n"
            f"{write_result}"
        )
    else:
        response = (
            f"Generated {count} sample users.\n\n"
            f"**Note:** Ollama is disabled. Using mock data generator.\n\n"
            f"```json\n{json.dumps(result, indent=2)}\n```"
        )

    return AIMessage(content=response)


def run_agent(user_input: str, history: List[BaseMessage]) -> AIMessage:
    """Invoke the DataGen agent with a single user message.

    The history parameter is accepted for compatibility but is not
    currently forwarded to the underlying agent (stateless by design).
    """
    try:
        cfg = DataGenAgentConfig.load()

        # Check if Ollama is disabled - use mock data
        if not cfg.enabled:
            return _generate_mock_response(user_input)

        # Try to use Ollama agent
        try:
            agent = _agent_for(cfg.model, cfg.ollama_base_url, float(cfg.temperature))
            result = agent.invoke({"messages": [{"role": "user", "content": user_input}]})
            # Debug: see all messages including tool calls
            print("Full agent result:", result)
            return result["messages"][-1]
        except Exception as ollama_error:
            # Fallback to mock data if Ollama connection fails
            print(f"Ollama connection failed: {ollama_error}. Falling back to mock data.")
            fallback_response = _generate_mock_response(user_input)
            fallback_response.content = (
                f"**Warning:** Could not connect to Ollama ({str(ollama_error)}). "
                f"Using mock data generator instead.\n\n{fallback_response.content}"
            )
            return fallback_response

    except Exception as e:  # noqa: BLE001
        import traceback

        return AIMessage(
            content=f"Error: {repr(e)}\nTraceback:\n{traceback.format_exc()}",
        )
