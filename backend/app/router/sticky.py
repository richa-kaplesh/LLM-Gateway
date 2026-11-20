from collections import OrderedDict

MAX_TRACKED_CONVERSATIONS = 5000

ModelGroup = tuple[str, str]

_pins: OrderedDict[str, ModelGroup] = OrderedDict()


def pinned_group(conversation_id: str) -> ModelGroup | None:
    return _pins.get(conversation_id)


def pin(conversation_id: str, group: ModelGroup) -> None:
    _pins.pop(conversation_id, None)
    _pins[conversation_id] = group
    while len(_pins) > MAX_TRACKED_CONVERSATIONS:
        _pins.popitem(last=False)


def reset_pins() -> None:
    _pins.clear()
