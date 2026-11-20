import asyncio
import dataclasses
import json
import logging
import time

import tiktoken

from app.cache.cache import get_cache_for
from app.clients import adapters
from app.clients.exceptions import (
    AllInvalidToolCallsError, AllProvidersRateLimitedError, AllProvidersUnavailableError,
    AllQuotaExhaustedError, CircuitOpenError, GatewayRejection, InvalidRequestError, InvalidToolCallError,
    LocalCapacityError, ProviderConnectionError, ProviderKeyError, ProviderOverloadedError,
    ProviderRateLimitedError, QuotaExhaustedError, TooLongError,
)
from app.core.config import get_settings
from app.core.registry import get_deployments
from app.models.deployment import Deployment
from app.models.schemas import GatewayRequest, GatewayResponse
from app.router import attempt_log, circuit_breaker, cooldowns, health, limiter, selection, sticky
from app.router.admission import get_admission
from app.router.circuit_breaker import group_label
from app.router.selection import Need
from app.tracker.tracker import tracker

log = logging.getLogger(__name__)
settings = get_settings()
_encoder = tiktoken.get_encoding("cl100k_base")

DB_TIMEOUT_SECONDS = 2.0
MAX_EXTRA_ATTEMPTS = 2
MAX_TOTAL_ATTEMPTS = 6
QUEUE_WAKE_MARGIN_SECONDS = 0.05


def _content_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content)


def estimate_tokens(messages: list[dict]) -> int:
    text = " ".join(_content_text(m.get("content")) for m in messages)
    return len(_encoder.encode(text))


def estimate_request_tokens(request: GatewayRequest) -> int:
    total = estimate_tokens(request.messages)
    if request.tools:
        total += len(_encoder.encode(json.dumps(request.tools)))
    return total + settings.OUTPUT_TOKEN_ALLOWANCE


def _has_tool_history(messages: list[dict]) -> bool:
    return any(m.get("role") == "tool" or m.get("tool_calls") for m in messages)


async def _safely(what: str, coro) -> None:
    try:
        await coro
    except Exception as e:
        log.warning("could not %s: %s: %s", what, type(e).__name__, e)


async def _record_transition(deployment: Deployment, before: str, after: str) -> None:
    if before == after:
        return
    label = group_label(deployment.model_group)
    try:
        await asyncio.wait_for(tracker.log_breaker_transition(label, before, after), DB_TIMEOUT_SECONDS)
    except Exception as e:
        log.warning("could not persist breaker transition %s %s->%s: %s: %s",
                    label, before, after, type(e).__name__, e)


async def _breaker_event(view: selection.DeploymentView, event: str) -> None:
    try:
        before, after = await circuit_breaker.record(view.deployment.model_group, event, view.breaker)
    except Exception as e:
        log.warning("could not update breaker for %s: %s: %s", view.id, type(e).__name__, e)
        return
    await _record_transition(view.deployment, before, after)


async def _after_rate_limit(view: selection.DeploymentView, e: ProviderRateLimitedError) -> None:
    deployment = view.deployment
    if isinstance(e, QuotaExhaustedError):
        await cooldowns.record_quota_exhausted(deployment.id, e.retry_after)
    else:
        await cooldowns.record_rate_limited(deployment.id, e.retry_after, view.cooldown)
    if e.rate_limit:
        await limiter.apply_rate_limit(deployment, e.rate_limit)
    else:
        await limiter.drain_minute_meters(deployment)


async def _attempt(view: selection.DeploymentView, request: GatewayRequest, need: Need) -> GatewayResponse:
    deployment = view.deployment
    reservation, meters = await limiter.reserve(deployment, need.tokens, need.day_fraction)
    if reservation is None:
        raise LocalCapacityError(f"{deployment.id} has no local capacity", retry_after=meters.wait_s)

    allowed, before, after = await circuit_breaker.allow(deployment.model_group, view.breaker)
    await _record_transition(deployment, before, after)
    if not allowed:
        await limiter.release(reservation)
        raise CircuitOpenError(f"{group_label(deployment.model_group)} circuit is {after}",
                               retry_after=circuit_breaker.retry_after(view.breaker))

    started = time.perf_counter()
    try:
        response = await adapters.complete(request, deployment)
    except InvalidRequestError as e:
        await _safely("release reservation", limiter.release(reservation))
        await _breaker_event(view, circuit_breaker.INCONCLUSIVE)
        log.info("deployment %s rejected the request as invalid: %s", deployment.id, e)
        raise
    except ProviderRateLimitedError as e:
        await _safely("release reservation", limiter.release(reservation))
        await _breaker_event(view, circuit_breaker.REACHABLE)
        await _safely("record rate limit", _after_rate_limit(view, e))
        await _safely("record health", health.record(deployment.id, attempt_log.outcome_for(e), None))
        raise
    except ProviderKeyError:
        await _safely("release reservation", limiter.release(reservation))
        await _breaker_event(view, circuit_breaker.REACHABLE)
        await _safely("record bad key", cooldowns.record_bad_key(deployment.id))
        raise
    except InvalidToolCallError as e:
        await _breaker_event(view, circuit_breaker.REACHABLE)
        await _safely("record health", health.record(deployment.id, "invalid_tool_call", None))
        log.info("deployment %s produced an invalid tool call; trying another sample: %s", deployment.id, e)
        raise
    except asyncio.CancelledError:
        await _safely("release reservation", limiter.release(reservation))
        await _breaker_event(view, circuit_breaker.INCONCLUSIVE)
        raise
    except Exception as e:
        if isinstance(e, (ProviderConnectionError, ProviderOverloadedError)):
            await _safely("release reservation", limiter.release(reservation))
        await _breaker_event(view, circuit_breaker.FAILURE)
        await _safely("record health", health.record(deployment.id, attempt_log.outcome_for(e), None))
        log.warning("deployment %s failed: %s: %s", deployment.id, type(e).__name__, e)
        raise

    await _breaker_event(view, circuit_breaker.SUCCESS)
    await _safely("reset cooldown streak", cooldowns.record_success(deployment.id, view.cooldown))
    await _safely("record health", health.record(deployment.id, "success", response.latency_ms))
    await _safely("apply rate-limit headers", limiter.apply_rate_limit(deployment, response.rate_limit))
    log.info("provider call ok: deployment=%s model=%s latency_ms=%.0f total_ms=%.0f cost_usd=%.6f",
             deployment.id, deployment.model, response.latency_ms,
             (time.perf_counter() - started) * 1000, response.cost_usd)
    return response


async def _tracked_attempt(view: selection.DeploymentView, request: GatewayRequest, need: Need) -> GatewayResponse:
    started = time.perf_counter()
    try:
        response = await _attempt(view, request, need)
    except BaseException as e:
        called_provider = not isinstance(e, (LocalCapacityError, CircuitOpenError))
        latency = (time.perf_counter() - started) * 1000 if called_provider else None
        attempt_log.current().record(view.deployment, latency, e)
        raise
    attempt_log.current().record(view.deployment, (time.perf_counter() - started) * 1000)
    return response


def _check_servable(need: Need, deployments: tuple[Deployment, ...]) -> None:
    if any(selection.can_serve(d, need) for d in deployments):
        return
    if not any(limiter.can_ever_fit(d.limits, need.tokens, need.day_fraction) for d in deployments):
        raise TooLongError(f"Prompt is too long ({need.tokens} tokens) for any configured deployment")
    if need.tier is not None and not any(d.tier == need.tier for d in deployments):
        raise InvalidRequestError(f"No configured deployment serves tier '{need.tier}'")
    raise InvalidRequestError("No configured deployment supports tool calling for this request")


async def _give_up(deployments, need: Need, views: list[selection.DeploymentView], hard_failed: set[str],
                   failures: list[str], hard_errors: list[Exception]) -> GatewayRejection:
    log_ = attempt_log.current()
    try:
        unpinned = dataclasses.replace(need, required_group=None)
        log_.capacity_existed = any(v.available for v in await selection.build_view(deployments, unpinned))
        views = await selection.build_view(deployments, need)
    except Exception as e:
        log.warning("could not compute capacity_existed: %s: %s", type(e).__name__, e)
    detail = "; ".join(failures) or "no deployment has capacity right now"
    if need.required_group is not None:
        detail += (f" (conversation has tool history and must stay on "
                   f"{group_label(need.required_group)})")
    attempts = log_.public()

    if hard_errors and all(isinstance(e, InvalidToolCallError) for e in hard_errors):
        return AllInvalidToolCallsError(
            f"The model kept producing invalid tool calls. {detail}", retry_after=None, attempts=attempts)

    remaining = [v for v in views if v.id not in hard_failed and not v.is_bad_key]
    rate_waits = [v.rate_wait for v in remaining if v.blocker == selection.BLOCK_RATE and v.rate_wait is not None]
    if rate_waits:
        return AllProvidersRateLimitedError(f"All deployments are busy. {detail}",
                                            retry_after=min(rate_waits), attempts=attempts)
    quota = [v for v in remaining if v.blocker == selection.BLOCK_QUOTA]
    if quota:
        waits = [v.rate_wait for v in quota if v.rate_wait is not None]
        return AllQuotaExhaustedError(f"Daily quota used up on every deployment. {detail}",
                                      retry_after=min(waits) if waits else None, attempts=attempts)
    return AllProvidersUnavailableError(f"No deployment is available. {detail}",
                                        retry_after=selection.soonest_breaker_wait(views, hard_failed),
                                        attempts=attempts)


async def _serve(request: GatewayRequest, need: Need, deployments: tuple[Deployment, ...],
                 preferred) -> GatewayResponse:
    deadline = time.monotonic() + settings.MAX_QUEUE_WAIT_SECONDS
    failed: set[str] = set()
    hard_failed: set[str] = set()
    failures: list[str] = []
    hard_errors: list[Exception] = []
    first_group = None
    attempts = 0
    while True:
        views = await selection.build_view(deployments, need)
        candidates = selection.available(views, failed)
        if not candidates:
            wait = selection.soonest_rate_wait(views, failed)
            if wait is not None and time.monotonic() + wait <= deadline:
                log.info("all deployments busy; waiting %.1f s for capacity", wait)
                await asyncio.sleep(wait + QUEUE_WAKE_MARGIN_SECONDS)
                continue
            raise await _give_up(deployments, need, views, hard_failed, failures, hard_errors)

        view = selection.pick(candidates, preferred)
        first_group = first_group or view.deployment.model_group
        attempts += 1
        log.info("routing request: deployment=%s attempt=%d", view.id, attempts)
        try:
            response = await _tracked_attempt(view, request, need)
        except InvalidRequestError:
            raise
        except QuotaExhaustedError as e:
            failed.add(view.id)
            failures.append(f"{view.id}: {e}")
        except (ProviderRateLimitedError, CircuitOpenError, LocalCapacityError) as e:
            failures.append(f"{view.id}: {e}")
        except Exception as e:
            failed.add(view.id)
            hard_failed.add(view.id)
            hard_errors.append(e)
            failures.append(f"{view.id}: {type(e).__name__}: {e}")
        else:
            response.attempts = attempts
            response.was_fallback = view.deployment.model_group != first_group
            return response

        if len(hard_errors) > MAX_EXTRA_ATTEMPTS or attempts >= MAX_TOTAL_ATTEMPTS:
            raise await _give_up(deployments, need, views, hard_failed, failures, hard_errors)


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
    preferred = await sticky.pinned_group(request.conversation_id)
    served_groups = {d.model_group for d in deployments}
    required = preferred if tool_history and preferred in served_groups else None
    need = Need(tokens=estimate_request_tokens(request),
                needs_tools=bool(request.tools) or tool_history,
                required_group=required,
                tier=request.tier or settings.DEFAULT_TIER,
                priority=tool_history)
    _check_servable(need, deployments)

    async with get_admission().slot():
        response = await _serve(request, need, deployments, preferred)
    await _safely("pin conversation", sticky.pin(request.conversation_id,
                                                 (response.provider_used, response.model_used)))

    if cache and lookup and lookup.embedding:
        emb = lookup.embedding
        cache.set(request, response.model_copy(deep=True), emb)
        response.embed_latency_ms = emb.latency_ms
        response.embed_cost_usd = emb.cost_usd
        response.latency_ms += emb.latency_ms
        response.cost_usd += emb.cost_usd
    return response
