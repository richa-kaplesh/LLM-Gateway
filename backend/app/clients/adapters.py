from app.clients import gemini_client, openai_compatible
from app.models.deployment import Deployment
from app.models.schemas import GatewayRequest, GatewayResponse

ADAPTERS = {
    "openai_compatible": openai_compatible.complete,
    "gemini": gemini_client.complete,
}


async def complete(request: GatewayRequest, deployment: Deployment) -> GatewayResponse:
    return await ADAPTERS[deployment.adapter](request, deployment)


async def close_adapters() -> None:
    await openai_compatible.close_clients()
