from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate
from tool import AVAILABLE_TOOLS


def setup_agent():
    
    llm = ChatOllama(model="llama3.2:3b", temperature=0.5)
    system_prompt = "You are a professional AI image editing assistant. Your task is to deeply analyze the user's prompt to understand their visual intent (e.g., lighting, color tone, or cinematic style). Based on this analysis, strategically call one or more defined tools with precise parameter values. Do not invent tools, and strictly follow the parameter ranges provided in the tool descriptions."
    agent = create_agent(model=llm, tools=AVAILABLE_TOOLS, system_prompt=system_prompt)
    return agent

if __name__ == "__main__":
    agent = setup_agent()
    
    print("=== Local AI Image Editing Assistant started (type 'exit' to quit) ===")  
    while True:
        user_input = input("\nEnter your photo editing request: ").strip().lower()
        if user_input in ["exit", "quit"]:
            print("Exiting the assistant. Goodbye!")
            break
        try:
            response = agent.invoke({"messages": [{"role": "user", "content": user_input}]})
            final_message = response["messages"][-1].content
            print(f"Assistant: {final_message}")
            
        except Exception as e:
            print(f"An error occurred: {e}")
    
    