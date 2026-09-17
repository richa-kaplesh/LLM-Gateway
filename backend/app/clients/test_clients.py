import asyncio
from app.models.schemas import GatewayRequest
from app.clients import groq_client, gemini_client


WEATHER_TOOL = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"}
            },
            "required": ["city"]
        }
    }
}]


async def test_plain_text(client_module, label):
    print(f"\n--- {label}: plain text, no tools ---")
    request = GatewayRequest(
        conversation_id="test_conv_1",
        user_id="test_user",
        messages=[{"role": "user", "content": "What is the capital of France? Answer in one word."}],
    )
    response = await client_module.complete(request)
    print("content:", response.content)
    print("tool_calls:", response.tool_calls)
    print("finish_reason:", response.finish_reason)
    print("cost_usd:", response.cost_usd)
    print("latency_ms:", response.latency_ms)


async def test_tool_calling(client_module, label):
    print(f"\n--- {label}: tool-calling ---")
    request = GatewayRequest(
        conversation_id="test_conv_2",
        user_id="test_user",
        messages=[{"role": "user", "content": "What's the weather in Mandi, India right now?"}],
        tools=WEATHER_TOOL,
        tool_choice="auto",
    )
    response = await client_module.complete(request)
    print("content:", response.content)
    print("tool_calls:", response.tool_calls)
    print("finish_reason:", response.finish_reason)


async def main():


    # await test_plain_text(gemini_client, "GEMINI")
    await test_tool_calling(gemini_client, "GEMINI")


if __name__ == "__main__":
    asyncio.run(main())