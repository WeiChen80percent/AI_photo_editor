from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate
from tool import AVAILABLE_TOOLS, apply_xmp_and_export

def setup_agent():
    llm = ChatOllama(model="llama3.2:3b", temperature=0.5)
    system_prompt = """You are a professional AI image editing assistant. 
1. Deeply analyze the user's intent (lighting, color tone, style).
2. Strategically call available tools (adjust_exposure, adjust_local_contrast, adjust_color_balance_rgb) with precise parameters.
3. CRITICAL: You MUST finish your workflow by calling the `apply_xmp_and_export` tool.
   Use default paths if not specified: image_path="sample1.jpg", output_path="edited.jpg".
Do not invent tools."""
    agent = create_agent(model=llm, tools=AVAILABLE_TOOLS, system_prompt=system_prompt)
    return agent

if __name__ == "__main__":
    agent = setup_agent()
    print("=== Local AI Image Editing Assistant started (type 'exit' to quit) ===")  
    
    while True:
        user_input = input("\nEnter your photo editing request: ").strip()
        if user_input.lower() in ["exit", "quit"]:
            print("Exiting the assistant. Goodbye!")
            break
        
        try:
            response = agent.invoke({"messages": [{"role": "user", "content": user_input}]})
            last_msg = response["messages"][-1]
            has_exported = False
            
            if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                print("\n[AI 決定調用以下工具]:")
                for tool_call in last_msg.tool_calls:
                    print(f"- {tool_call['name']}: {tool_call['args']}")
                    if tool_call['name'] == 'apply_xmp_and_export':
                        has_exported = True
            
            if not has_exported:
                print("\n[系統介入]: AI 完成了參數設定，正在強制幫您輸出圖片...")
                # 強制執行
                apply_xmp_and_export.invoke({
                    "image_path": "sample1.jpg", 
                    "output_path": "edited.jpg"
                })
                print("系統已強制將圖片輸出為 edited.jpg")
                
            print(f"\nAssistant: {last_msg.content}")

        except Exception as e:
            print(f"An error occurred: {e}")
            import traceback
            traceback.print_exc()