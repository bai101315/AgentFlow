
def init_agent(agent, x: int = 0):
    agent.x = x
    print(agent)
    print(agent.x)
    
    

class AIAgent:
    def __init__(self, x) -> None:
        init_agent(self, x=x)
        print(self.x)
        


agent = AIAgent(x=200)
