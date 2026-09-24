#!/usr/bin/env python3
"""FORGE-003 — first protected OpenHands coding execution.

Runs ONE bounded agent session with every model request passing through the
ForgeOne ProtectedGateway and Resource Controller. The OpenHands SDK never
connects to the raw MLX-LM backend.

Approved limits (owner-approved; do not raise without fresh approval):
    total context        4096 tokens
    max admitted input   3072 tokens
    reserved output       128 tokens
    concurrency             1
    agent model requests  <= 8
    agent tool calls      <= 10
    wall clock            <= 5 minutes

Run under the **OpenHands venv** for the SDK, with the model venv's Python used
for the backend. Tokenizer access is via the model venv's site-packages on
PYTHONPATH.

Exit codes: 0 = PASS, 1 = FAIL, 2 = BLOCKED, 3 = ABORTED, 4 = PARTIAL
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

CHECKPOINT = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
REVISION = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"
SNAP = (REPO / "storage/cache/huggingface/hub"
        / f"models--{CHECKPOINT.replace('/', '--')}" / "snapshots" / REVISION)

BACKEND_PORT = 8082
GATEWAY_PORT = 8084
HOST = "127.0.0.1"

GB = 1024**3
MIB = 1024**2

# --- approved budget -------------------------------------------------------
MAX_CONTEXT = 4096
MAX_INPUT = 3072
RESERVED_OUTPUT = 128
MIN_AVAILABLE = 4 * GB
RETAINED_CACHE = 512 * MIB
TRANSIENT_RESERVE = 1 * GB
REQUEST_TIMEOUT_S = 180.0
COOLDOWN_S = 60.0
TELEMETRY_MAX_AGE_S = 5.0

WD_MIN_AVAILABLE = 2 * GB
WD_MAX_SWAP = 6 * GB
WD_MAX_PAGEOUT_RATE = 20000.0

MAX_MODEL_REQUESTS = 8
MAX_TOOL_CALLS = 10
MAX_WALL_CLOCK_S = 300.0

RUNS = REPO / "storage/runs"
TELEMETRY = RUNS / "openhands-telemetry.jsonl"
ADMISSION = RUNS / "openhands-admission.jsonl"
RESULT = RUNS / "openhands-result.json"
FIXTURE_SRC = Path("/tmp/forge002-fixture")
WORKSPACE = Path("/tmp/forge003-oh-workspace")

TASK = (
    "Work only inside this directory. It contains src/pricing.py and "
    "tests/test_pricing.py. Read both files, find the defect in tier_price(), "
    "apply the smallest correct fix, then run exactly: "
    "python3 -m unittest discover -s tests -v . "
    "Report the real test output and the exit code. Do not claim a test passed "
    "unless you ran it and saw it pass."
)


def emit(stage, outcome, **extra):
    rec = {"stage": stage, "outcome": outcome, "ts": time.time()}
    rec.update(extra)
    print(json.dumps(rec, sort_keys=True), flush=True)


def finish(code, outcome, results):
    results["outcome"] = outcome
    results["exit_code"] = code
    RUNS.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    emit("final", outcome, result_path=str(RESULT.relative_to(REPO)))
    return code


def main() -> int:
    results: dict = {"limits": {
        "max_context": MAX_CONTEXT, "max_input": MAX_INPUT,
        "reserved_output": RESERVED_OUTPUT,
        "max_model_requests": MAX_MODEL_REQUESTS,
        "max_tool_calls": MAX_TOOL_CALLS,
        "max_wall_clock_s": MAX_WALL_CLOCK_S,
    }}

    os.environ.setdefault("HF_HOME", str(REPO / "storage/cache/huggingface"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

    from services.resource_controller import (
        CacheBudget, GatewayHTTPServer, HttpRequestForwarder, MacOSTelemetrySource,
        ProcessSupervisor, ProtectedGateway, ProtectedServer, ProtectedServerConfig,
        RealPortProbe, ResourceController, ResourcePolicy, SubprocessAdapter,
        WatchdogThresholds, count_telemetry_samples, launch_watchdog_process,
        serving_path_is_bounded, supported_flags_from_help, verify_server_support,
    )
    from services.resource_controller.estimator import ModelMetadata
    from services.resource_controller.tokenization import HuggingFaceTokenCounter
    from services.resource_controller.watchdog import JsonlTelemetryWriter

    # ---------------------------------------------------------- policy
    policy = ResourcePolicy(
        max_context_tokens=MAX_CONTEXT, max_input_tokens=MAX_INPUT,
        reserved_output_tokens=RESERVED_OUTPUT,
        min_available_memory_bytes=MIN_AVAILABLE,
        max_retained_cache_bytes=RETAINED_CACHE,
        transient_reserve_bytes=TRANSIENT_RESERVE,
        request_timeout_s=REQUEST_TIMEOUT_S,
        cooldown_after_abnormal_exit_s=COOLDOWN_S,
        telemetry_max_age_s=TELEMETRY_MAX_AGE_S,
    )
    emit("policy", "OK", max_context=MAX_CONTEXT, max_input=MAX_INPUT,
         reserved_output=RESERVED_OUTPUT)

    from mlx_lm.utils import load_config, load_tokenizer
    cfg = load_config(SNAP)
    tokenizer = load_tokenizer(SNAP)          # tokenizer files only
    counter = HuggingFaceTokenCounter(tokenizer)

    observed = json.loads((REPO / "services/resource_controller/"
                           "observed-model-metadata.example.json").read_text())
    m = observed["measured"]
    metadata = ModelMetadata.from_hf_config(
        cfg, weight_bytes=int(m["weights_bytes_approx"]),
        runtime_overhead_bytes=int(m["runtime_overhead_bytes_approx"]),
        source_revision=REVISION,
    )

    # ------------------------------------------------- serving path check
    cache = CacheBudget(
        retained_cache_bytes=RETAINED_CACHE, max_sequences=2,
        active_kv_bytes=metadata.kv_bytes(MAX_CONTEXT),
        weights_bytes=metadata.weight_bytes,
        transient_reserve_bytes=TRANSIENT_RESERVE,
    )
    server_cfg = ProtectedServerConfig(
        executable=sys.executable, model=CHECKPOINT, port=BACKEND_PORT, cache=cache,
    )
    argv = server_cfg.build_argv()
    bounded, why = serving_path_is_bounded(argv)
    emit("serving_path", "OK" if bounded else "BLOCKED", bounded=bounded, why=why)
    if not bounded:
        return finish(2, "BLOCKED", results)

    _help = subprocess.run([sys.executable, "-m", "mlx_lm.server", "--help"],
                           capture_output=True, text=True, timeout=60)
    try:
        verify_server_support(_help.stdout + _help.stderr)
    except Exception as exc:
        emit("flag_support", "BLOCKED", error=str(exc))
        return finish(2, "BLOCKED", results)
    emit("flag_support", "OK")

    # ------------------------------------------------------ safety gate
    telemetry = MacOSTelemetrySource()
    snap = telemetry.snapshot()
    cold = metadata.weight_bytes + metadata.runtime_overhead_bytes + TRANSIENT_RESERVE
    reserve = snap.available_bytes - cold
    gate = {"available_gib": round(snap.available_bytes / GB, 2),
            "swap_used_gib": round(snap.swap_used_bytes / GB, 2),
            "projected_reserve_gib": round(reserve / GB, 2),
            "pageouts": snap.pageouts}
    results["safety_gate"] = gate
    emit("safety_gate", "OK", **gate)
    if snap.available_bytes < 8 * GB or reserve < 4 * GB:
        emit("safety_gate", "BLOCKED", reason="memory gate failed")
        return finish(2, "BLOCKED", results)

    # --------------------------------------------------------- workspace
    if not FIXTURE_SRC.is_dir():
        emit("fixture", "BLOCKED", reason=f"fixture missing at {FIXTURE_SRC}")
        return finish(2, "BLOCKED", results)
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    shutil.copytree(FIXTURE_SRC, WORKSPACE)
    for junk in (".git",):
        p = WORKSPACE / junk
        if p.exists():
            shutil.rmtree(p)
    subprocess.run(["git", "init", "-q"], cwd=WORKSPACE, check=False)
    subprocess.run(["git", "add", "-A"], cwd=WORKSPACE, check=False)
    subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=fixture",
                    "commit", "-q", "-m", "baseline"], cwd=WORKSPACE, check=False)

    baseline = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                              cwd=WORKSPACE, capture_output=True, text=True)
    results["baseline"] = {"exit_code": baseline.returncode,
                           "tail": baseline.stderr.strip().splitlines()[-1:]}
    emit("baseline", "EXPECTED FAILING BASELINE", exit_code=baseline.returncode)
    if baseline.returncode == 0:
        emit("baseline", "BLOCKED", reason="fixture baseline did not fail as expected")
        return finish(2, "BLOCKED", results)

    # --------------------------------------------------- watchdog + backend
    RUNS.mkdir(parents=True, exist_ok=True)
    if TELEMETRY.exists():
        TELEMETRY.unlink()

    wd_proc, readiness = launch_watchdog_process(
        telemetry_path=TELEMETRY, min_available_bytes=WD_MIN_AVAILABLE,
        max_swap_used_bytes=WD_MAX_SWAP, max_pageout_rate=WD_MAX_PAGEOUT_RATE,
        sample_interval_s=0.25, python_executable=sys.executable, cwd=str(REPO),
        extra_env={"HF_HOME": str(REPO / "storage/cache/huggingface"),
                   "PYTHONPATH": str(REPO)},
    )
    deadline = time.time() + 20
    while time.time() < deadline and not readiness.is_ready():
        time.sleep(0.25)
    results["watchdog"] = {"ready": readiness.is_ready(), "pid": wd_proc.pid}
    emit("watchdog", "READY" if readiness.is_ready() else "BLOCKED", **results["watchdog"])
    if not readiness.is_ready():
        wd_proc.terminate()
        return finish(2, "BLOCKED", results)

    writer = JsonlTelemetryWriter(ADMISSION)
    controller = ResourceController(
        policy, counter, metadata, telemetry,
        supervisor=ProcessSupervisor(SubprocessAdapter(), graceful_timeout_s=10.0),
        telemetry_writer=writer,
        watchdog_thresholds=WatchdogThresholds(
            min_available_bytes=WD_MIN_AVAILABLE, max_swap_used_bytes=WD_MAX_SWAP,
            max_pageout_rate=WD_MAX_PAGEOUT_RATE, sample_interval_s=0.25),
    )
    backend = ProtectedServer(
        controller, server_cfg, port_probe=RealPortProbe(),
        watchdog_readiness=readiness,
        forwarder=HttpRequestForwarder(f"http://{HOST}:{BACKEND_PORT}", model=CHECKPOINT),
        writer=writer,
    )
    startup = backend.start()
    results["cold_start"] = {"outcome": startup.outcome.value, "reason": startup.reason}
    emit("cold_start", startup.outcome.value, reason=startup.reason)
    if not startup.started:
        wd_proc.terminate()
        return finish(2, "BLOCKED", results)

    ready = False
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            if backend.forwarder.get_models(timeout_s=5.0).get("data"):
                ready = True
                break
        except Exception:
            time.sleep(2.0)
        if wd_proc.poll() is not None:
            break
    results["backend_ready"] = ready
    emit("backend_ready", "OK" if ready else "FAIL")
    if not ready:
        backend.stop(outcome=__import__("services.resource_controller.outcomes",
                                        fromlist=["Outcome"]).Outcome.ABORTED_MEMORY_PRESSURE)
        wd_proc.terminate()
        return finish(3, "ABORTED", results)

    # -------------------------------------------------------- gateway
    gateway = ProtectedGateway(backend)
    http = GatewayHTTPServer(gateway, GATEWAY_PORT)
    http.start()
    results["gateway"] = {"host": HOST, "port": GATEWAY_PORT, "running": http.running}
    emit("gateway", "OK", **results["gateway"])

    # ------------------------------------------------- OpenHands agent
    from openhands.sdk import LLM, Agent, Conversation
    from openhands.sdk.tool import Tool
    from openhands.sdk.workspace import LocalWorkspace
    from services.agent_profiles import FORGEONE_COMPACT_V1, validate_llm_kwargs

    llm_kwargs = {
        "model": "forgeone-protected",
        "base_url": f"http://{HOST}:{GATEWAY_PORT}/v1",
        "api_key": "not-needed-loopback",
        "api_mode": "chat",
        "stream": False,
        "num_retries": 0,
        "timeout": int(REQUEST_TIMEOUT_S),
        "max_output_tokens": RESERVED_OUTPUT,
    }
    validate_llm_kwargs(llm_kwargs)
    results["llm_config"] = {k: v for k, v in llm_kwargs.items() if k != "api_key"}
    emit("llm_config", "OK", **{k: v for k, v in llm_kwargs.items() if k != "api_key"})

    llm = LLM(**llm_kwargs)
    tools = [Tool(name=n) for n in FORGEONE_COMPACT_V1.tool_names]
    agent = Agent(llm=llm, tools=tools,
                  system_prompt=FORGEONE_COMPACT_V1.system_prompt)
    workspace = LocalWorkspace(working_dir=str(WORKSPACE))
    # max_iteration_per_run bounds the agent loop (SDK default is 500).
    conversation = Conversation(
        agent=agent, workspace=workspace, max_iteration_per_run=MAX_MODEL_REQUESTS
    )

    t0 = time.time()
    conv_error = None
    try:
        conversation.send_message(TASK)
        conversation.run()
    except Exception as exc:
        conv_error = f"{type(exc).__name__}: {exc}"
    elapsed = time.time() - t0
    results["agent"] = {"elapsed_s": round(elapsed, 2), "error": conv_error,
                        "wall_clock_within_limit": elapsed <= MAX_WALL_CLOCK_S}
    emit("agent", "DONE" if not conv_error else "ERROR", **results["agent"])

    # ------------------------------------------------- final verification
    final = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                           cwd=WORKSPACE, capture_output=True, text=True)
    results["final_tests"] = {"exit_code": final.returncode,
                              "summary": final.stderr.strip().splitlines()[-3:]}
    emit("final_tests", "PASS" if final.returncode == 0 else "FAIL",
         exit_code=final.returncode)

    diff = subprocess.run(["git", "diff"], cwd=WORKSPACE, capture_output=True, text=True)
    results["diff"] = diff.stdout[:4000]

    # ------------------------------------------------------- shutdown
    http.stop()
    stop = backend.stop()
    wd_proc.terminate()
    try:
        wd_proc.wait(timeout=10)
    except Exception:
        wd_proc.kill()
    results["shutdown"] = {
        "gateway_running": http.running, "backend_stop": stop.value,
        "reservations_held": controller.reservations.active_count,
        "owned_pids": controller.owned_pids(), "watchdog_exit": wd_proc.poll(),
        "telemetry_samples": count_telemetry_samples(TELEMETRY),
    }
    emit("shutdown", "OK", **results["shutdown"])

    after = telemetry.snapshot()
    results["safety_gate_after"] = {
        "available_gib": round(after.available_bytes / GB, 2),
        "swap_used_gib": round(after.swap_used_bytes / GB, 2),
        "pageouts": after.pageouts,
    }
    emit("safety_gate_after", "OK", **results["safety_gate_after"])

    if final.returncode == 0 and not conv_error:
        return finish(0, "PASS", results)
    if final.returncode != 0 and not conv_error:
        return finish(4, "PARTIAL", results)
    return finish(1, "FAIL", results)


if __name__ == "__main__":
    raise SystemExit(main())
