import asyncio
import logging
import sys
from datetime import date
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from until import *

load_dotenv()

configure_logging()
# Ensure local harness modules are importable when running from repo root.
BACKEND_ROOT = Path(__file__).resolve().parent / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.agents.lead_agent.agent import make_lead_agent

async def main():
    config = {
        "configurable":{
            "thread_id": "leetcode_assis",
            # "thread_id": "debug-thread-001",
            "thinking_enabled": False,
            "is_plan_mode": True,
            "model_name": "minimax-m2.5",
            "subagent_enabled": True,
            "tools_enabled": True,
            "agent_name": "leetcode-assis",
            # "title_enabled": True,
            
        }
    }
    agent = make_lead_agent(config)

    # 打印agent的流程图
    # graph = agent.get_graph()
    # print(graph.draw_mermaid())

    while True:
        try:
            user_input = input(f"{CYAN}{BOLD}You >> {RESET}")
            if not user_input:
                continue
            if user_input.lower() in ("q", "exit", ""):
                    print("Goodbye!")
                    break
            
            state = {"messages": [HumanMessage(content=user_input)]}
            result = await agent.ainvoke(state, config=config, context={"thread_id": "debug-thread-001"})

            if result.get("messages"):
                last_message = result["messages"][-1]
                print(f"\n{GREEN}{BOLD}Agent{RESET}: {last_message.content}")
            
        except KeyboardInterrupt:     
            print("Goodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")
            import traceback

            traceback.print_exc()
        
        

if __name__ == "__main__":
    asyncio.run(main())

