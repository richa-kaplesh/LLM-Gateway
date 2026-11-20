from app.state.store import KEY_PREFIX, get_store

ModelGroup = tuple[str, str]

PIN_TTL_SECONDS = 6 * 3600


def pin_key(conversation_id: str) -> str:
    return f"{KEY_PREFIX}pin:{conversation_id}"


async def pinned_group(conversation_id: str) -> ModelGroup | None:
    value = (await get_store().get_json_many([pin_key(conversation_id)]))[0]
    return (value["provider"], value["model"]) if value else None


async def pin(conversation_id: str, group: ModelGroup) -> None:
    provider, model = group
    await get_store().set_json(pin_key(conversation_id), {"provider": provider, "model": model}, PIN_TTL_SECONDS)
