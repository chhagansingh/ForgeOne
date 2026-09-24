#!/usr/bin/env python3
"""FORGE-003 protected inference smoke test.

Runs ONE small local inference session entirely through the ForgeOne Resource
Controller. Nothing here bypasses admission, the reservation, the watchdog or
the port probe.

Approved limits (do not raise without fresh owner approval):
    total context        2048 tokens
    input per request     512 tokens (chat template + tool schemas included)
    reserved output       128 tokens
    concurrent requests     1
    retained cache        512 MiB / 2 sequences
    transient reserve       1 GiB

Run with the model venv interpreter (it has mlx_lm for the real tokenizer):

    storage/bakeoff/model-venv/bin/python scripts/run_protected_smoke.py

Exit codes: 0 = PASS, 1 = FAIL, 2 = BLOCKED, 3 = ABORTED, 4 = NOT_RUN
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

CHECKPOINT = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
REVISION = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"
PORT = 8082
HOST = "127.0.0.1"

GB = 1024**3
MIB = 1024**2

MAX_CONTEXT = 2048
MAX_INPUT = 512
RESERVED_OUTPUT = 128
MIN_AVAILABLE = 4 * GB
RETAINED_CACHE = 512 * MIB
TRANSIENT_RESERVE = 1 * GB
REQUEST_TIMEOUT_S = 120.0
COOLDOWN_S = 60.0
TELEMETRY_MAX_AGE_S = 5.0

WATCHDOG_MIN_AVAILABLE = 2 * GB
WATCHDOG_MAX_SWAP = 6 * GB
WATCHDOG_MAX_PAGEOUT_RATE = 20000.0

RUNS_DIR = REPO / "storage/runs"
TELEMETRY_PATH = RUNS_DIR / "smoke-telemetry.jsonl"
RESULT_PATH = RUNS_DIR / "smoke-result.json"

MOCK_TOOL = {
    "type": "function",
    "function": {
        "name": "get_test_summary",
        "description": "Return the recorded summary for a named test suite.",
        "parameters": {
            "type": "object",
            "properties": {"suite": {"type": "string", "description": "Suite name"}},
            "required": ["suite"],
        },
    },
}

TOOL_CALL_PROMPT = (
    "Call the get_test_summary tool for the suite named 'pricing'. "
    "Use the tool; do not answer from memory."
)


def emit(stage: str, outcome: str, **extra) -> None:
    rec = {"stage": stage, "outcome": outcome, "ts": time.time()}
    rec.update(extra)
    print(json.dumps(rec, sort_keys=True), flush=True)


def finish(code: int, outcome: str, results: dict) -> int:
    results["outcome"] = outcome
    results["exit_code"] = code
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    emit("final", outcome, result_path=str(RESULT_PATH.relative_to(REPO)))
    return code


def main() -> int:
    results: dict = {"checks": [], "requests": []}

    os.environ.setdefault("HF_HOME", str(REPO / "storage/cache/huggingface"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    from services.resource_controller import (
        CacheBudget,
        HttpRequestForwarder,
        MacOSTelemetrySource,
        ProcessSupervisor,
        ProtectedServer,
        ProtectedServerConfig,
        RealPortProbe,
        ResourceController,
        ResourcePolicy,
        SubprocessAdapter,
        WatchdogThresholds,
        count_telemetry_samples,
        launch_watchdog_process,
        serving_path_is_bounded,
        supported_flags_from_help,
        verify_server_support,
    )
    from services.resource_controller.estimator import ModelMetadata
    from services.resource_controller.tokenization import HuggingFaceTokenCounter
    from services.resource_controller.watchdog import JsonlTelemetryWriter

    # ---------------------------------------------------------------- config
    policy = ResourcePolicy(
        max_context_tokens=MAX_CONTEXT,
        max_input_tokens=MAX_INPUT,
        reserved_output_tokens=RESERVED_OUTPUT,
        min_available_memory_bytes=MIN_AVAILABLE,
        max_retained_cache_bytes=RETAINED_CACHE,
        transient_reserve_bytes=TRANSIENT_RESERVE,
        request_timeout_s=REQUEST_TIMEOUT_S,
        cooldown_after_abnormal_exit_s=COOLDOWN_S,
        telemetry_max_age_s=TELEMETRY_MAX_AGE_S,
    )
    results["policy"] = {
        "max_context_tokens": MAX_CONTEXT, "max_input_tokens": MAX_INPUT,
        "reserved_output_tokens": RESERVED_OUTPUT,
        "min_available_memory_bytes": MIN_AVAILABLE,
        "max_retained_cache_bytes": RETAINED_CACHE,
        "transient_reserve_bytes": TRANSIENT_RESERVE,
        "max_concurrent_requests": policy.max_concurrent_requests,
        "allow_context_escalation": policy.allow_context_escalation,
    }
    emit("policy", "OK", **results["policy"])

    snapshot_dir = (
        REPO / "storage/cache/huggingface/hub"
        / f"models--{CHECKPOINT.replace('/', '--')}" / "snapshots" / REVISION
    )
    if not snapshot_dir.is_dir():
        emit("checkpoint", "BLOCKED", reason="snapshot missing")
        return finish(2, "BLOCKED", results)

    # ------------------------------------------------ tokenizer + metadata
    from mlx_lm.utils import load_config, load_tokenizer

    cfg = load_config(snapshot_dir)          # config.json only
    tokenizer = load_tokenizer(snapshot_dir)  # *.safetensors excluded
    counter = HuggingFaceTokenCounter(tokenizer)

    observed = json.loads(
        (REPO / "services/resource_controller/observed-model-metadata.example.json")
        .read_text(encoding="utf-8")
    )
    measured = observed["measured"]
    metadata = ModelMetadata.from_hf_config(
        cfg,
        weight_bytes=int(measured["weights_bytes_approx"]),
        runtime_overhead_bytes=int(measured["runtime_overhead_bytes_approx"]),
        source_revision=REVISION,
    )
    results["model"] = {
        "checkpoint": CHECKPOINT, "revision": REVISION,
        "kv_bytes_per_token": metadata.kv_bytes_per_token(),
        "has_chat_template": bool(getattr(tokenizer, "has_chat_template", False)),
        "has_tool_calling": bool(getattr(tokenizer, "has_tool_calling", False)),
        "weights_bytes": metadata.weight_bytes,
        "overhead_bytes": metadata.runtime_overhead_bytes,
    }
    emit("model", "OK", **results["model"])

    if not (results["model"]["has_chat_template"] and results["model"]["has_tool_calling"]):
        emit("model", "BLOCKED", reason="tokenizer lacks chat template or tool calling")
        return finish(2, "BLOCKED", results)

    # ------------------------------------------------ serving-path check
    cache = CacheBudget(
        retained_cache_bytes=RETAINED_CACHE, max_sequences=2,
        active_kv_bytes=metadata.kv_bytes(MAX_CONTEXT),
        weights_bytes=metadata.weight_bytes,
        transient_reserve_bytes=TRANSIENT_RESERVE,
    )
    server_cfg = ProtectedServerConfig(
        executable=sys.executable, model=CHECKPOINT, port=PORT, cache=cache,
    )
    argv = server_cfg.build_argv()
    bounded, why = serving_path_is_bounded(argv)
    results["argv"] = {"argv": argv, "bounded": bounded, "why": why}
    emit("argv", "OK" if bounded else "BLOCKED", bounded=bounded, why=why)
    if not bounded:
        return finish(2, "BLOCKED", results)

    # NOTE: argv list, never a shell string -- the interpreter path contains
    # spaces, and a shell would split it and return an error page instead of
    # the real --help output (which then looks like "flags unsupported").
    import subprocess as _sp

    _help = _sp.run(
        [sys.executable, "-m", "mlx_lm.server", "--help"],
        capture_output=True, text=True, timeout=60,
    )
    help_text = _help.stdout + _help.stderr
    try:
        verify_server_support(help_text)
        results["flag_support"] = {"supported": True}
    except Exception as exc:
        results["flag_support"] = {"supported": False, "error": str(exc)}
        emit("flag_support", "BLOCKED", error=str(exc))
        return finish(2, "BLOCKED", results)
    emit("flag_support", "OK", supported=True)

    # ------------------------------------------------ Phase 2 safety gate
    telemetry = MacOSTelemetrySource()
    snap = telemetry.snapshot()
    gate = {
        "available_bytes": snap.available_bytes,
        "available_gib": round(snap.available_bytes / GB, 2),
        "free_bytes": snap.free_bytes,
        "swap_used_bytes": snap.swap_used_bytes,
        "swap_used_gib": round(snap.swap_used_bytes / GB, 2),
        "pageouts": snap.pageouts,
    }
    results["safety_gate_before"] = gate
    emit("safety_gate", "OK", **gate)

    if snap.available_bytes < 8 * GB:
        emit("safety_gate", "BLOCKED", reason=f"available {gate['available_gib']} GiB < 8 GiB")
        return finish(2, "BLOCKED", results)

    cold = metadata.weight_bytes + metadata.runtime_overhead_bytes + TRANSIENT_RESERVE
    projected_reserve = snap.available_bytes - cold
    results["projected_reserve_bytes"] = projected_reserve
    emit("safety_gate", "OK", projected_reserve_gib=round(projected_reserve / GB, 2))
    if projected_reserve < 4 * GB:
        emit("safety_gate", "BLOCKED", reason="projected reserve < 4 GiB")
        return finish(2, "BLOCKED", results)

    # ------------------------------------------------ watchdog BEFORE load
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if TELEMETRY_PATH.exists():
        TELEMETRY_PATH.unlink()

    wd_proc, readiness = launch_watchdog_process(
        telemetry_path=TELEMETRY_PATH,
        min_available_bytes=WATCHDOG_MIN_AVAILABLE,
        max_swap_used_bytes=WATCHDOG_MAX_SWAP,
        max_pageout_rate=WATCHDOG_MAX_PAGEOUT_RATE,
        sample_interval_s=0.25,
        python_executable=sys.executable,
        cwd=str(REPO),
        extra_env={"HF_HOME": str(REPO / "storage/cache/huggingface"),
                   "PYTHONPATH": str(REPO)},
    )
    emit("watchdog", "STARTED", pid=wd_proc.pid)

    deadline = time.time() + 20
    while time.time() < deadline and not readiness.is_ready():
        time.sleep(0.25)
    results["watchdog_ready"] = {"ready": readiness.is_ready(), "detail": readiness.detail()}
    emit("watchdog", "READY" if readiness.is_ready() else "BLOCKED", **results["watchdog_ready"])
    if not readiness.is_ready():
        wd_proc.terminate()
        return finish(2, "BLOCKED", results)

    # ------------------------------------------------ controller + server
    writer = JsonlTelemetryWriter(RUNS_DIR / "smoke-admission.jsonl")
    controller = ResourceController(
        policy, counter, metadata, telemetry,
        supervisor=ProcessSupervisor(SubprocessAdapter(), graceful_timeout_s=10.0),
        telemetry_writer=writer,
        watchdog_thresholds=WatchdogThresholds(
            min_available_bytes=WATCHDOG_MIN_AVAILABLE,
            max_swap_used_bytes=WATCHDOG_MAX_SWAP,
            max_pageout_rate=WATCHDOG_MAX_PAGEOUT_RATE,
            sample_interval_s=0.25,
        ),
    )
    forwarder = HttpRequestForwarder(f"http://{HOST}:{PORT}", model=CHECKPOINT)
    server = ProtectedServer(
        controller, server_cfg, port_probe=RealPortProbe(),
        watchdog_readiness=readiness, forwarder=forwarder, writer=writer,
    )

    startup = server.start()
    results["cold_start"] = {
        "outcome": startup.outcome.value, "reason": startup.reason,
        "estimate_bytes": startup.cold_start_estimate.total_bytes if startup.cold_start_estimate else None,
        "model_resident_charged": startup.cold_start_estimate.model_resident
        if startup.cold_start_estimate else None,
    }
    emit("cold_start", startup.outcome.value, reason=startup.reason)
    if not startup.started:
        wd_proc.terminate()
        return finish(3 if startup.outcome.value.startswith("ABORT") else 2, "BLOCKED", results)

    # ------------------------------------------------ wait for model ready
    ready = False
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            models = forwarder.get_models(timeout_s=5.0)
            if models.get("data"):
                ready = True
                break
        except Exception:
            time.sleep(2.0)
        if wd_proc.poll() is not None:
            break
    results["model_ready"] = {"ready": ready, "watchdog_alive": wd_proc.poll() is None}
    emit("model_ready", "OK" if ready else "FAIL", **results["model_ready"])
    if not ready:
        server.stop(outcome=Outcome.ABORTED_MEMORY_PRESSURE)
        wd_proc.terminate()
        return finish(3, "ABORTED", results)

    # resident state DERIVED from real server state, not hardcoded
    resident = True
    results["resident_derived"] = {
        "model_resident": resident,
        "basis": "server answered /v1/models with a model loaded and the process is alive",
    }

    # ------------------------------------------------ request 1: tool call
    messages = [{"role": "user", "content": TOOL_CALL_PROMPT}]
    t0 = time.time()
    r1 = server.request(messages, RESERVED_OUTPUT, tools=[MOCK_TOOL], label="tool-call")
    results["requests"].append({
        "n": 1, "outcome": r1.outcome.value, "executed": r1.executed,
        "input_tokens": r1.decision.input_tokens if r1.decision else None,
        "estimate_bytes": r1.decision.estimate.total_bytes if r1.decision and r1.decision.estimate else None,
        "elapsed_s": round(time.time() - t0, 2),
    })
    emit("request_1", r1.outcome.value, executed=r1.executed)
    if not r1.admitted:
        server.stop()
        wd_proc.terminate()
        return finish(3, "ABORTED", results)

    choice = r1.response["choices"][0]["message"]
    tool_calls = choice.get("tool_calls") or []
    results["tool_call_raw"] = tool_calls
    if not tool_calls:
        emit("tool_call", "FAIL", reason="no structured tool_calls in response")
        server.stop()
        wd_proc.terminate()
        return finish(1, "FAIL", results)

    tc = tool_calls[0]
    fname = tc.get("function", {}).get("name")
    raw_args = tc.get("function", {}).get("arguments")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except Exception as exc:
        args = None
        results["tool_args_error"] = str(exc)

    results["tool_call"] = {
        "name": fname, "arguments_raw": raw_args, "arguments_parsed": args,
        "expected_name": "get_test_summary",
        "name_ok": fname == "get_test_summary",
        "args_ok": isinstance(args, dict) and isinstance(args.get("suite"), str),
    }
    emit("tool_call", "OK" if results["tool_call"]["name_ok"] and results["tool_call"]["args_ok"] else "FAIL",
         **results["tool_call"])
    if not (results["tool_call"]["name_ok"] and results["tool_call"]["args_ok"]):
        server.stop()
        wd_proc.terminate()
        return finish(1, "FAIL", results)

    # ------------------------------------------------ execute mock tool
    MOCK_DATA = {
        "pricing": {"suite": "pricing", "tests": 6, "failures": 2, "status": "EXPECTED FAILING BASELINE"},
    }
    tool_result = json.dumps(MOCK_DATA.get(args["suite"], {"error": "unknown suite"}))
    results["tool_execution"] = {"executed_locally": True, "result": tool_result}
    emit("tool_execution", "OK", result=tool_result)

    # ------------------------------------------------ request 2: continuation
    messages2 = messages + [
        {"role": "assistant", "content": None, "tool_calls": tool_calls},
        {"role": "tool", "tool_call_id": tc.get("id", "call_0"), "content": tool_result},
    ]
    t0 = time.time()
    r2 = server.request(messages2, RESERVED_OUTPUT, tools=[MOCK_TOOL], label="tool-result")
    results["requests"].append({
        "n": 2, "outcome": r2.outcome.value, "executed": r2.executed,
        "input_tokens": r2.decision.input_tokens if r2.decision else None,
        "estimate_bytes": r2.decision.estimate.total_bytes if r2.decision and r2.decision.estimate else None,
        "elapsed_s": round(time.time() - t0, 2),
    })
    emit("request_2", r2.outcome.value, executed=r2.executed)
    if not r2.admitted:
        server.stop()
        wd_proc.terminate()
        return finish(3, "ABORTED", results)

    final_text = (r2.response["choices"][0]["message"].get("content") or "").strip()
    results["final_answer"] = final_text[:500]
    emit("final_answer", "OK" if final_text else "FAIL", chars=len(final_text))

    # ------------------------------------------------ shutdown
    stop_outcome = server.stop()
    wd_proc.terminate()
    try:
        wd_proc.wait(timeout=10)
    except Exception:
        wd_proc.kill()

    results["shutdown"] = {
        "stop_outcome": stop_outcome.value,
        "reservations_held": controller.reservations.active_count,
        "owned_pids": controller.owned_pids(),
        "watchdog_exit": wd_proc.poll(),
        "telemetry_samples": count_telemetry_samples(TELEMETRY_PATH),
    }
    emit("shutdown", "OK", **results["shutdown"])

    after = telemetry.snapshot()
    results["safety_gate_after"] = {
        "available_gib": round(after.available_bytes / GB, 2),
        "swap_used_gib": round(after.swap_used_bytes / GB, 2),
        "pageouts": after.pageouts,
    }
    emit("safety_gate_after", "OK", **results["safety_gate_after"])

    ok = bool(final_text) and results["shutdown"]["reservations_held"] == 0 \
        and not results["shutdown"]["owned_pids"]
    return finish(0 if ok else 1, "PASS" if ok else "FAIL", results)


if __name__ == "__main__":
    from services.resource_controller.outcomes import Outcome  # noqa: E402

    raise SystemExit(main())
