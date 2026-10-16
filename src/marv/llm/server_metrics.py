"""Read live runtime state from a local mlx_vlm server.

The mlx_vlm server measures its own turns far more precisely than the agent
can from the outside: ``/v1/metrics`` reports per-request TTFT, prefill and
decode rates, and whether Automatic Prefix Caching (APC) is on. marv's wait
prediction previously started blind on a first-ever run (no ``state.toml``
calibration yet) and stayed pessimistic once APC made most of a turn's
prefill a cache hit, so the server's own numbers are used to seed and to
correct the prediction:

* ``recent`` request samples become :class:`~marv.llm.latency.LatencySample`
  values, giving the prefill-rate fit real measurements from the very first
  turn of a session — including turns served to other clients of the same
  server.
* ``apc_enabled`` says whether the next turn's prefill will mostly be a
  prefix-cache hit, which the prediction subtracts from the token count.

Everything here is best-effort: a server that does not speak these endpoints
(an older mlx-vlm, a cloud-compatible endpoint) yields ``None`` and the
caller falls back to the existing behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from marv.llm.latency import LatencySample

if TYPE_CHECKING:
    import httpx

#: Seconds to wait for the metrics endpoints. A local server answers in
#: milliseconds; anything slower is down and the prediction should not wait.
METRICS_TIMEOUT_S = 2.0


def _root_url(base_url: str) -> str:
    """Server root from an OpenAI-style base URL (drop a trailing ``/v1``)."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root


def _number(data: object, key: str) -> float | None:
    value = data.get(key) if isinstance(data, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if value > 0 else None


@dataclass(frozen=True, slots=True)
class ServerRequestSample:
    """One request the server measured, as reported by ``/v1/metrics``."""

    prompt_tokens: int = 0
    ttft_s: float = 0.0
    prefill_tok_s: float = 0.0
    decode_tok_s: float = 0.0
    completion_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ServerMetrics:
    """Live runtime state of the serving mlx_vlm process."""

    #: Whether Automatic Prefix Caching is enabled server-side. ``None`` when
    #: the server did not report it (endpoint missing or older server).
    apc_enabled: bool | None = None
    #: Recent per-request measurements, oldest first.
    samples: list[ServerRequestSample] = field(default_factory=list)
    #: The most recent request's decode rate, in tokens/second (0 unknown).
    decode_tok_s: float = 0.0


def parse_server_metrics(payload: object) -> ServerMetrics:
    """Extract the prediction-relevant fields from a ``/v1/metrics`` body."""
    if not isinstance(payload, dict):
        return ServerMetrics()

    server = payload.get("server")
    apc: bool | None = None
    if isinstance(server, dict):
        apc_info = server.get("apc")
        if isinstance(apc_info, dict) and isinstance(apc_info.get("enabled"), bool):
            apc = apc_info["enabled"]

    entries: list[dict[str, Any]] = []
    recent = payload.get("recent")
    if isinstance(recent, list):
        entries.extend(entry for entry in recent if isinstance(entry, dict))
    latest = payload.get("latest")
    if isinstance(latest, dict):
        entries.append(latest)

    samples: list[ServerRequestSample] = []
    seen: set[int] = set()
    decode_tok_s = 0.0
    for entry in entries:
        timestamp = entry.get("timestamp_unix")
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            continue
        if int(timestamp) in seen:
            continue
        seen.add(int(timestamp))
        prompt_tokens = int(_number(entry, "prompt_tokens") or 0)
        ttft_s = _number(entry, "ttft_s") or 0.0
        if prompt_tokens <= 0 or ttft_s <= 0:
            continue
        samples.append(
            ServerRequestSample(
                prompt_tokens=prompt_tokens,
                ttft_s=ttft_s,
                prefill_tok_s=_number(entry, "prefill_tok_s") or 0.0,
                decode_tok_s=_number(entry, "decode_tok_s") or 0.0,
                completion_tokens=int(_number(entry, "completion_tokens") or 0),
            )
        )
    if samples:
        decode_tok_s = samples[-1].decode_tok_s
    return ServerMetrics(apc_enabled=apc, samples=samples, decode_tok_s=decode_tok_s)


async def fetch_server_metrics(
    base_url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = METRICS_TIMEOUT_S,
) -> ServerMetrics | None:
    """Fetch ``/v1/metrics`` from the server at ``base_url`` (None if absent)."""
    import httpx

    url = f"{_root_url(base_url)}/v1/metrics"
    try:
        if client is not None:
            response = await client.get(url, timeout=timeout_s)
        else:
            async with httpx.AsyncClient(timeout=timeout_s) as own:
                response = await own.get(url)
        if response.status_code != 200:
            return None
        return parse_server_metrics(response.json())
    except Exception:  # noqa: BLE001 - a probe failure must never block a turn
        return None


def to_latency_samples(metrics: ServerMetrics) -> list[LatencySample]:
    """Convert server-measured requests into agent TTFT samples."""
    return [
        LatencySample(
            prompt_tokens=sample.prompt_tokens,
            ttft_ms=sample.ttft_s * 1000.0,
            streamed=True,
        )
        for sample in metrics.samples
    ]
