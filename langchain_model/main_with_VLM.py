import base64
from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from tool import AVAILABLE_TOOLS


def setup_LLM_agent(VLM_response):
    
    llm = ChatOllama(model="llama3.2:3b", temperature=0.5)
    system_prompt = f"""
    You are a professional AI image editing assistant.
    Your task is to deeply analyze VLM's output and the user's prompt to understand their visual intent (e.g., lighting, color tone, or cinematic style).
    Based on this analysis, strategically call one or more defined tools with precise parameter values.
    Do not invent tools, and strictly follow the parameter ranges provided in the tool descriptions.
    CRITICAL: After completing all editing tasks, you must use the apply_xmp_and_export tool to generate and provide the final output.
    
    Here is the technical analysis of the RAW photo:
    {VLM_response}
    """
    agent = create_agent(model=llm, tools=AVAILABLE_TOOLS, system_prompt=system_prompt)
    return agent

def execute_LLM_agent(LLM_agent):
    
    print("=== Local AI Image Editing Assistant started (type 'exit' to quit) ===")  
    while True:
        user_input = input("Enter your photo editing request: ").strip().lower()
        if user_input in ["exit", "quit"]:
            print("Exiting the assistant. Goodbye!")
            break
        try:
            response = LLM_agent.invoke({"messages": [{"role": "user", "content": user_input}]})
            final_message = response["messages"][-1].content
            print(f"Assistant: {final_message}")
            
        except Exception as e:
            print(f"An error occurred: {e}")

def setup_VLM_model():
    vlm = ChatOllama(model="moondream:latest", temperature=0)
    return vlm
    

def execute_VLM_model(VLM_model):
    image_path = input("Please enter the path of the photo you want to edit (e.g., test_photo.jpg): ")

    def encode_image(path):
        with open(path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    image_base64 = encode_image(image_path)
    vlm_prompt = """
    ### ROLE
    You are a Professional Technical Image Analyst. Your task is to diagnose RAW photos for a post-processing pipeline and output in JSON format.

    ### INSTRUCTIONS
    1. Analyze the visual data (Luminance, Color, Structure).
    2. Output your findings STRICTLY in the following JSON format.
    3. Ensure each description is detailed (at least 15 words) to provide enough evidence for an AI Editor.
    4. If a value is unknown, provide your best technical estimate.

    ### JSON STRUCTURE EXAMPLE (Follow this exactly):
    {
        "lighting_diagnostics": {
            "exposure_status": "The image is under-exposed by roughly 1.5 EV; the histogram is heavily skewed towards the left with unused headroom in highlights.",
            "black_level_observation": "Shadows are slightly lifted and milky, suggesting a need to drop the black level to achieve pure blacks."
        },
        "structural_diagnostics": {
            "detail_clarity": "Micro-contrast is low, resulting in a hazy appearance across the landscape textures.",
            "shadow_texture_loss": "Significant detail loss in the dark areas, particularly in the subject's hair and background foliage.",
            "highlight_texture_loss": "No clipping detected in highlights; bright areas retain full structural integrity."
        },
        "color_and_tone_diagnostics": {
            "global_color_cast": "A strong green/yellow tint is present, likely caused by artificial lighting or incorrect white balance.",
            "vibrance_assessment": "Colors appear dull and washed out; the overall saturation is below professional standards.",
            "perceptual_contrast": "Lacks visual depth; the tonal range is too narrow to create a 3D feel.",
            "artistic_grading_suggestion": "Recommend a teal-orange split tone: cool down the shadows and warm up the highlights."
        },
        "summary_and_objective": {
            "primary_fix": "Increase overall exposure and perform a global color cast correction.",
            "recommended_workflow": "1. adjust_exposure, 2. adjust_color_balance_rgb, 3. adjust_local_contrast"
        }
    }
    
    """
    message = HumanMessage(
        content=[
            {"type": "text", "text": vlm_prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
        ]
    )

    response = VLM_model.invoke([message]).content
    print("=== VLM Analysis Result ===", response)

    return response

if __name__ == "__main__":
    VLM_model = setup_VLM_model()
    VLM_response = execute_VLM_model(VLM_model)
  
    LLM_agent = setup_LLM_agent(VLM_response)
    execute_LLM_agent(LLM_agent)
    
    
    