"""Minimal deepagents + MCP test script.

Run:  python mcp_agent.py
Needs: ANTHROPIC_API_KEY set in your terminal.
To swap in Vexcel later, set MCP_URL and MCP_TOKEN (no code changes needed).
"""
import asyncio
import os

from langchain_mcp_adapters.client import MultiServerMCPClient
from deepagents import create_deep_agent

# Default is DeepWiki, a free public MCP server that needs no token.
MCP_URL = os.getenv("MCP_URL", "https://mcp.deepwiki.com/mcp")
MCP_TOKEN = os.getenv("MCP_TOKEN")  # leave unset for DeepWiki

server = {"transport": "streamable_http", "url": MCP_URL}
if MCP_TOKEN:
    server["headers"] = {"Authorization": f"Bearer {MCP_TOKEN}"}


async def main():
    # 1. Connect to the MCP server and fetch its tools.
    client = MultiServerMCPClient({"remote": server})
    tools = await client.get_tools()
    print("Tools from MCP server:", [t.name for t in tools])

    # 2. Give those tools to the deep agent.
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        tools=tools,
        system_prompt="Use the MCP tools to answer.",
    )

    # 3. Ask a question that needs the tools.
    result = await agent.ainvoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "What does the langchain-ai/deepagents repo do? Use your tools.",
                }
            ]
        }
    )
    print("\nAnswer:\n", result["messages"][-1].content)


asyncio.run(main())
