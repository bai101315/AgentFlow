import asyncio
import logging
import sys
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Ensure local harness modules are importable when running from repo root.
HARNESS_ROOT = Path(__file__).resolve().parent / "deer_flow"
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))

# Workaround for incompatible langgraph/langgraph-prebuilt builds where
# langgraph.runtime does not expose ExecutionInfo/ServerInfo.
try:
    import langgraph.runtime as _langgraph_runtime

    if not hasattr(_langgraph_runtime, "ExecutionInfo"):
        class _ExecutionInfo(TypedDict, total=False):
            pass

        _langgraph_runtime.ExecutionInfo = _ExecutionInfo  # type: ignore[attr-defined]

    if not hasattr(_langgraph_runtime, "ServerInfo"):
        class _ServerInfo(TypedDict, total=False):
            pass

        _langgraph_runtime.ServerInfo = _ServerInfo  # type: ignore[attr-defined]

    runtime_cls = getattr(_langgraph_runtime, "Runtime", None)
    if runtime_cls is not None:
        # Some langgraph-prebuilt builds expect these attrs on Runtime instances.
        if not hasattr(runtime_cls, "execution_info"):
            setattr(runtime_cls, "execution_info", None)
        if not hasattr(runtime_cls, "server_info"):
            setattr(runtime_cls, "server_info", None)
except Exception:
    # Keep startup resilient; downstream import errors will still surface.
    pass

from deer_flow.agents.lead_agent.agent import make_lead_agent
from deer_flow.agents.memory import get_memory_queue
from deer_flow.agents.middlewares.memory_middleware import detect_correction, detect_reinforcement

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"
BOLD = "\033[1m"
MAGENTA = "\033[35m"


async def main():
    config = {
        "configurable":{
            "thread_id": "debug-thread-001",
            "thinking_enabled": False,
            "is_plan_mode": True,
            "model_name": "minimax-m2.5",
            "subagent_enabled": True,
            "tools_enabled": True
        }
    }
    agent = make_lead_agent(config)
    
    # Get the memory queue for automatic memory updates
    memory_queue = get_memory_queue()

    while True:
        try:
             
            user_input = input(f"{CYAN}{BOLD}You >> {RESET}")
            if not user_input:
                continue
            if user_input.lower() in ("q", "exit", ""):
                    print("Goodbye!")
                    break
            
            # Invoke the agent
            state = {"messages": [HumanMessage(content=user_input)]}
            result = await agent.ainvoke(state, config=config, context={"thread_id": "debug-thread-001"})

            # Print the response
            if result.get("messages"):
                last_message = result["messages"][-1]
                print(f"\n{GREEN}{BOLD}Agent{RESET}: {last_message.content}")
            
            # ===== NEW: Queue memory update =====
            # After agent completes, queue the conversation for memory update
            messages = result.get("messages", [])
            
            # Detect signals
            correction_detected = detect_correction(messages)
            reinforcement_detected = detect_reinforcement(messages)
            
            # Add to memory queue (will be processed after debounce timeout)
            thread_id = config["configurable"].get("thread_id", "default-thread")
            memory_queue.add(
                thread_id=thread_id,
                messages=messages,
                agent_name=None,  # global memory
                correction_detected=correction_detected,
                reinforcement_detected=reinforcement_detected,
            )
            
            if correction_detected:
                print(f"{YELLOW}[Memory] 检测到修正信号，将更新内存{RESET}")
            elif reinforcement_detected:
                print(f"{YELLOW}[Memory] 检测到正向反馈，将更新内存{RESET}")
            else:
                print(f"{DIM}[Memory] 对话已加入队列，将在30秒后处理{RESET}")
            # ===== END: Memory queue integration =====

        except KeyboardInterrupt:
            print("\nInterrupted. Waiting for memory queue to finish...")
            # Give the memory queue time to process pending updates
            memory_queue.wait_for_processing(timeout=10)
            print("Goodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")
            import traceback

            traceback.print_exc()
        
        

if __name__ == "__main__":
    asyncio.run(main())

