import asyncio
import dataclasses
import json
import logging
import time

import tiktoken

from app.cache.cache import get_cache_for
from app.clients import adapters
from app.clients.exceptions import (
    AllProvidersRateLimitedError, AllProvidersUnavailableError, CircuitOpenError,
    InvalidRequestError, LocalCapacityError, ProviderConnectionError, ProviderKeyError,
    ProviderRateLimitedError, TooLongError,
)
from app.core.config import get_settings
from app.core.registry import get_deployments
from app.models.deployment import Deployment
from app.models.schemas import GatewayRequest, GatewayResponse
from app.router import attempt_log, cooldowns, sticky
from app.router.circuit_breaker import breaker_for, group_label
from app.router.limiter import limiter_for
from app.router.selection import (
    Need, available, can_serve, pick, soonest_breaker_wait, soonest_rate_limit_wait,
)
from app.tracker.tracker import tracker

log = logging.getLogger(__name__)
settings = get_settings()
_encoder = tiktoken.get_encoding("cl100k_base")

DB_TIMEOUT_SECONDS = 2.0
MAX_EXTRA_ATTEMPTS = 2
QUEUE_WAKE_MARGIN_SECONDS = 0.05


def estimate_tokens(messages: list[dict]) -> int:
    text = " ".join(m.get("content", "") or "" for m in messages)
    return len(_encoder.encode(text))


def estimate_request_tokens(request: GatewayRequest) -> int:
    total = estimate_tokens(request.messages)
    if request.tools:
        total += len(_encoder.encode(json.dumps(request.tools)))
    return total + settings.OUTPUT_TOKEN_ALLOWANCE


def _has_tool_history(messages: list[dict]) -> bool:
    return any(m.get("role") == "tool" or m.get("tool_calls") for m in messages)


async def _record_transition(deployment: Deployment, before: str, after: str) -> None:
    label = group_label(deployment.model_group)
    try:
        await asyncio.wait_for(tracker.log_breaker_transition(label, before, after), DB_TIMEOUT_SECONDS)
    except Exception as e:
        log.warning("could not persist breaker transition %s %s->%s: %s: %s",
                    label, before, after, type(e).__name__, e)


async def _attempt(deployment: Deployment, request: GatewayRequest, tokens: int) -> GatewayResponse:
    limiter = limiter_for(deployment)
    reservation = limiter.reserve(tokens)
    if reservation is None:
        raise LocalCapacityError(f"{deployment.id} has no local capacity",
                                 retry_after=limiter.seconds_until_fits(tokens))

    breaker = breaker_for(deployment.model_group)
    state_before = breaker.state
    if not breaker.allow_request():
        limiter.release(reservation)
        raise CircuitOpenError(f"{group_label(deployment.model_group)} circuit is {breaker.state}",
                               retry_after=breaker.retry_after())

    started = time.perf_counter()
    try:
        if breaker.state != state_before:
            await _record_transition(deployment, state_before, breaker.state)
            state_before = breaker.state
        response = await adapters.complete(request, deployment)
        breaker.record_success()
        cooldowns.record_success(deployment.id)
        log.info("provider call ok: deployment=%s model=%s latency_ms=%.0f total_ms=%.0f cost_usd=%.6f",
                 deployment.id, deployment.model, response.latency_ms,
                 (time.perf_counter() - started) * 1000, response.cost_usd)
        return response
    except InvalidRequestError as e:
        breaker.record_inconclusive()
        limiter.release(reservation)
        log.info("deployment %s rejected the request as invalid: %s", deployment.id, e)
        raise
    except ProviderRateLimitedError as e:
        breaker.record_reachable()
        limiter.release(reservation)
        cooldowns.record_rate_limited(deployment.id, e.retry_after)
        raise
    except ProviderKeyError:
        breaker.record_reachable()
        limiter.release(reservation)
        cooldowns.record_bad_key(deployment.id)
        raise
    except asyncio.CancelledError:
        breaker.record_inconclusive()
        limiter.release(reservation)
        raise
    except Exception as e:
        breaker.record_failure()
        if isinstance(e, ProviderConnectionError):
            limiter.release(reservation)
        log.warning("deployment %s failed (breaker=%s, consecutive_failures=%d): %s",
                    deployment.id, breaker.state, breaker.consecutive_failures, e)
        raise
    finally:
        if breaker.state != state_before:
            await _record_transition(deployment, state_before, breaker.state)


async def _tracked_attempt(deployment: Deployment, request: GatewayRequest, tokens: int) -> GatewayResponse:
    started = time.perf_counter()
    try:
        response = await _attempt(deployment, request, tokens)
    except BaseException as e:
        called_provider = not isinstance(e, (LocalCapacityError, CircuitOpenError))
        latency = (time.perf_counter() - started) * 1000 if called_provider else None
        attempt_log.current().record(deployment, latency, e)
        raise
    attempt_log.current().record(deployment, (time.perf_counter() - started) * 1000)
    return response


def _capacity_existed(deployments: tuple[Deployment, ...], need: Need) -> bool:
    unpinned = dataclasses.replace(need, required_group=None)
    return bool(available(deployments, unpinned, exclude=set()))


def _need_for(request: GatewayRequest, tool_history: bool, tokens: int,
              deployments: tuple[Deployment, ...]) -> Need:
    pinned = sticky.pinned_group(request.conversation_id)
    served_groups = {d.model_group for d in deployments}
    required = pinned if tool_history and pinned in served_groups else None
    return Need(tokens=tokens, needs_tools=bool(request.tools) or tool_history, required_group=required)


def _check_servable(need: Need, deployments: tuple[Deployment, ...]) -> None:
    if any(can_serve(d, need) for d in deployments):
        return
    if not any(limiter_for(d).can_ever_fit(need.tokens) for d in deployments):
        raise TooLongError(f"Prompt is too long ({need.tokens} tokens) for any configured deployment")
    raise InvalidRequestError("No configured deployment supports tool calling for this request")


def _give_up(deployments, need: Need, failed: set[str], failures: list[str]) -> Exception:
    attempt_log.current().capacity_existed = _capacity_existed(deployments, need)
    detail = "; ".join(failures) or "no deployment has capacity right now"
    if need.required_group is not None:
        detail += (f" (conversation has tool history and must stay on "
                   f"{group_label(need.required_group)})")
    wait = soonest_rate_limit_wait(deployments, need, failed)
    if wait is not None:
        return AllProvidersRateLimitedError(f"All deployments are busy. {detail}", retry_after=wait)
    return AllProvidersUnavailableError(f"No deployment is available. {detail}",
                                        retry_after=soonest_breaker_wait(deployments, need, failed))


async def _serve(request: GatewayRequest, need: Need,
                 deployments: tuple[Deployment, ...]) -> GatewayResponse:
    preferred = sticky.pinned_group(request.conversation_id)
    deadline = time.monotonic() + settings.MAX_QUEUE_WAIT_SECONDS
    failed: set[str] = set()
    failures: list[str] = []
    first_group = None
    attempts = 0
    while True:
        candidates = available(deployments, need, failed)
        if not candidates:
            wait = soonest_rate_limit_wait(deployments, need, failed)
            if wait is not None and time.monotonic() + wait <= deadline:
                log.info("all deployments busy; waiting %.1f s for capacity", wait)
                await asyncio.sleep(wait + QUEUE_WAKE_MARGIN_SECONDS)
                continue
            raise _give_up(deployments, need, failed, failures)

        deployment = pick(candidates, preferred)
        first_group = first_group or deployment.model_group
        attempts += 1
        log.info("routing request: deployment=%s attempt=%d", deployment.id, attempts)
        try:
            response = await _tracked_attempt(deployment, request, need.tokens)
        except InvalidRequestError:
            raise
        except (ProviderRateLimitedError, CircuitOpenError) as e:
            failures.append(f"{deployment.id}: {e}")
        except Exception as e:
            failed.add(deployment.id)
            failures.append(f"{deployment.id}: {type(e).__name__}: {e}")
        else:
            response.attempts = attempts
            response.was_fallback = deployment.model_group != first_group
            return response

        if attempts > MAX_EXTRA_ATTEMPTS:
            raise _give_up(deployments, need, failed, failures)


async def route(request: GatewayRequest) -> GatewayResponse:
    tool_history = _has_tool_history(request.messages)

    use_cache = not (request.tools or request.is_tool_related or tool_history)
    cache = get_cache_for(request) if use_cache else None
    lookup = None
    if cache:
        try:
            lookup = await cache.get(request)
        except Exception as e:
            log.warning("cache lookup failed, continuing without cache: %s: %s", type(e).__name__, e)
            cache = None
        else:
            if lookup.hit:
                return lookup.hit

    deployments = get_deployments()
    need = _need_for(request, tool_history, estimate_request_tokens(request), deployments)
    _check_servable(need, deployments)

    response = await _serve(request, need, deployments)
    sticky.pin(request.conversation_id, (response.provider_used, response.model_used))

    if cache and lookup and lookup.embedding:
        emb = lookup.embedding
        cache.set(request, response.model_copy(deep=True), emb)
        response.embed_latency_ms = emb.latency_ms
        response.embed_cost_usd = emb.cost_usd
        response.latency_ms += emb.latency_ms
        response.cost_usd += emb.cost_usd
    return response
