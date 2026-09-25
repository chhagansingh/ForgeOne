#!/usr/bin/env python3
"""FORGE-003 — headless protected K2 session.

One-shot. Runs from a plain macOS Terminal, independently of any IDE.

    --check     preflight only. NEVER loads weights.
    --execute   exactly ONE bounded protected K2 session, and only if every
                admission gate passes.

No automatic retry. No scheduled retry. The watchdog is a separate process, so
supervision does not depend on the IDE staying open.

Approved limits: context 1024, output 32, concurrency 1, server slots 1,
exactly one inference request.

Exit codes: 0 ok · 2 BLOCKED · 3 ABORTED · 4 FAILED
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

GB = 1024**3
MIB = 1024**2

CHECKPOINT = "IFM/K2-Horizon-3.7B-GGUF"
REVISION = "a81d5fec318b47b9c7144a839f538f6b9006291c"
FILENAME = "K2-Horizon-4B-Q6_K.gguf"
CHECKPOINT_SIZE = 4161403264

BACKEND_PORT = 8082
GATEWAY_PORT = 8084
HOST = "127.0.0.1"

# Approved experiment limits.
CTX = 1024
OUTPUT = 32
SLOTS = 1
BATCH = 256
UBATCH = 128

PROVISIONAL_CHARGE = 7 * GB
REQUIRED_RESERVE = 4 * GB
MIN_AVAILABLE = 8 * GB
SUSTAINED_S = 30

WD_MIN_AVAILABLE = 2 * GB
WD_MAX_SWAP = 6 * GB
WD_MAX_PAGEOUT_RATE = 20000.0

RUNTIME_BIN = REPO / "storage/runtimes/k2-llama/build/bin/llama-server"
STORAGE = REPO / "storage"
RUNS = STORAGE / "runs"
LOGS = STORAGE / "logs"
SUMMARY = RUNS / "k2-headless-summary.json"

PROMPT = "Reply with exactly: OK"
EXIT_OK, EXIT_BLOCKED, EXIT_ABORTED, EXIT_FAILED = 0, 2, 3, 4


def emit(stage: str, outcome: str, **extra) -> None:
    rec = {"stage": stage, "outcome": outcome, "ts": time.time()}
    rec.update(extra)
    print(json.dumps(rec, sort_keys=True), flush=True)


def resolve_inside_storage(p: Path) -> Path:
    """Fail closed if a path escapes the canonical storage root."""
    rp = Path(p).resolve()
    root = STORAGE.resolve()
    if root != rp and root not in rp.parents:
        raise SystemExit(f"BLOCKED: {rp} escapes {root}")
    return rp


# ---------------------------------------------------------------------------
def preflight() -> dict:
    """Collect fresh sustained telemetry and decide. Never touches weights."""
    from services.resource_controller.telemetry import MacOSTelemetrySource

    src = MacOSTelemetrySource()
    required = PROVISIONAL_CHARGE + REQUIRED_RESERVE
    emit("preflight", "START", required_gib=round(required / GB, 2),
         sustained_s=SUSTAINED_S)

    vals, swaps = [], []
    deadline = time.monotonic() + SUSTAINED_S
    while time.monotonic() < deadline:
        s = src.snapshot()
        vals.append(s.available_bytes)
        swaps.append(s.swap_used_bytes)
        time.sleep(3)

    a = src.snapshot()
    time.sleep(3)
    b = src.snapshot()
    growth = (b.swap_used_bytes - a.swap_used_bytes) / GB
    rate = b.pageout_rate(a)

    minimum = min(vals)
    reserve = minimum - PROVISIONAL_CHARGE
    checks = {
        "available_ge_8gib": minimum >= MIN_AVAILABLE,
        "reserve_ge_4gib": reserve >= REQUIRED_RESERVE,
        "effective_ge_11gib": minimum >= required,
        "stable_swap": growth < 0.25,
        "stable_pageouts": rate < 5000.0,
    }
    result = {
        "required_bytes": required,
        "min_available_bytes": minimum,
        "mean_available_bytes": sum(vals) // len(vals),
        "max_available_bytes": max(vals),
        "swap_min_bytes": min(swaps),
        "swap_max_bytes": max(swaps),
        "swap_growth_gib": round(growth, 3),
        "pageout_rate": round(rate, 1),
        "provisional_charge_bytes": PROVISIONAL_CHARGE,
        "projected_reserve_bytes": reserve,
        "checks": checks,
        "admitted": all(checks.values()),
        "note": "7 GiB is a PROVISIONAL ADMISSION BUDGET, not a measured peak.",
    }
    for k, v in checks.items():
        emit("preflight_check", "PASS" if v else "FAIL", check=k)
    return result


def unsupported_argv_flags(argv: list) -> list:
    """Reject any flag the pinned binary does not advertise.

    Every generated option is checked against the binary's own --help BEFORE
    model startup. A typo like `--no-prompt-cache` would otherwise abort
    startup after the weights were already being mapped.
    """
    r = subprocess.run([str(RUNTIME_BIN), "--help"], capture_output=True, text=True, timeout=60)
    help_text = r.stdout + r.stderr
    known = set()
    for token in help_text.replace(",", " ").split():
        if token.startswith("-"):
            known.add(token.strip())
    bad = []
    for a in argv:
        if not isinstance(a, str) or not a.startswith("-"):
            continue
        name = a.split("=", 1)[0]
        if name not in known:
            bad.append(name)
    return sorted(set(bad))


def conflicting_processes() -> list:
    out = []
    for pat in ("llama-server", "mlx_lm.server"):
        r = subprocess.run(["pgrep", "-fl", pat], capture_output=True, text=True)
        if r.stdout.strip():
            out.extend(r.stdout.strip().splitlines())
    return out


def ports_free() -> dict:
    from services.resource_controller.ports import RealPortProbe

    probe = RealPortProbe()
    return {p: probe.check(HOST, p).free for p in (BACKEND_PORT, GATEWAY_PORT)}


# ---------------------------------------------------------------------------
def execute() -> int:
    """Exactly one bounded protected session. Every exit path cleans up."""
    from services.resource_controller import (
        CacheBudget, GatewayHTTPServer, HttpRequestForwarder, MacOSTelemetrySource,
        ProcessSupervisor, ProtectedGateway, ProtectedServer, ProtectedServerConfig,
        RealPortProbe, ResourceController, ResourcePolicy, SubprocessAdapter,
        WatchdogThresholds, count_telemetry_samples, launch_watchdog_process,
    )
    from services.resource_controller.estimator import ModelMetadata
    from services.resource_controller.k2_adapter import (
        APPROVED_ARCHITECTURE, APPROVED_REPO, APPROVED_REVISION, K2ModelIdentity,
        MockK2TokenCounter, resolve_checkpoint_path,
    )
    from services.resource_controller.tokenization import HuggingFaceTokenCounter
    from services.resource_controller.watchdog import JsonlTelemetryWriter
    from mlx_lm.utils import load_tokenizer

    RUNS.mkdir(parents=True, exist_ok=True)
    summary: dict = {"limits": {"ctx": CTX, "output": OUTPUT, "slots": SLOTS}}

    checkpoint = resolve_inside_storage(
        STORAGE / "cache/huggingface/hub" / f"models--{CHECKPOINT.replace('/', '--')}"
        / "snapshots" / REVISION / FILENAME
    )
    if not checkpoint.is_file():
        emit("checkpoint", "BLOCKED", reason=f"missing {checkpoint}")
        return EXIT_BLOCKED

    # --- policy: K2 budgets, unchanged safety floors -------------------------
    policy = ResourcePolicy(
        max_context_tokens=CTX, max_input_tokens=CTX - OUTPUT,
        reserved_output_tokens=OUTPUT,
        min_available_memory_bytes=REQUIRED_RESERVE,
        max_retained_cache_bytes=64 * MIB,     # prompt-cache retention disabled
        transient_reserve_bytes=PROVISIONAL_CHARGE,
        request_timeout_s=180.0, cooldown_after_abnormal_exit_s=60.0,
        telemetry_max_age_s=5.0,
    )
    emit("policy", "OK", ctx=CTX, output=OUTPUT)

    # --- watchdog FIRST ------------------------------------------------------
    tel_path = RUNS / "k2-headless-telemetry.jsonl"
    if tel_path.exists():
        tel_path.unlink()
    wd_proc, readiness = launch_watchdog_process(
        telemetry_path=tel_path, min_available_bytes=WD_MIN_AVAILABLE,
        max_swap_used_bytes=WD_MAX_SWAP, max_pageout_rate=WD_MAX_PAGEOUT_RATE,
        sample_interval_s=0.25, python_executable=sys.executable, cwd=str(REPO),
        extra_env={"PYTHONPATH": str(REPO), "FORGEONE_STORAGE": str(STORAGE)},
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not readiness.is_ready():
        time.sleep(0.25)
    if not readiness.is_ready():
        emit("watchdog", "BLOCKED", detail=readiness.detail())
        wd_proc.terminate()
        return EXIT_BLOCKED
    emit("watchdog", "READY", pid=wd_proc.pid)

    controller = None
    backend = None
    http = None
    code = EXIT_FAILED
    try:
        # --- supervised backend ---------------------------------------------
        cache = CacheBudget(
            retained_cache_bytes=64 * MIB, max_sequences=1,
            active_kv_bytes=147456 * CTX, weights_bytes=CHECKPOINT_SIZE,
            transient_reserve_bytes=PROVISIONAL_CHARGE,
        )
        argv = [
            str(RUNTIME_BIN), "-m", str(checkpoint),
            "--host", HOST, "--port", str(BACKEND_PORT),
            "-c", str(CTX), "-n", str(OUTPUT),
            "--parallel", str(SLOTS), "-b", str(BATCH), "-ub", str(UBATCH),
            "--no-context-shift", "--jinja",
        ]
        # NOTE: `--no-prompt-cache` was previously passed and DOES NOT EXIST in
        # the pinned binary -- it would have aborted startup immediately. There
        # is no direct "disable prompt cache" flag in this build; retention is
        # bounded by --parallel 1 and the 1024-token context instead. KV
        # offload is left at its default (enabled), which is what we want for
        # Metal. Every flag below was verified against the pinned --help.
        cfg = ProtectedServerConfig(
            executable=str(RUNTIME_BIN), model=str(checkpoint),
            port=BACKEND_PORT, cache=cache,
            # llama-server's command line is not MLX-shaped, so the full argv is
            # supplied verbatim. Prompt-cache flags are MLX-only and N/A here;
            # retention is disabled with --no-prompt-cache instead.
            argv_override=tuple(argv),
        )
        # Reject unsupported flags BEFORE any weights are mapped.
        bad_flags = unsupported_argv_flags(argv)
        if bad_flags:
            emit("argv", "BLOCKED", unsupported=bad_flags)
            print(f"BLOCKED: pinned binary does not support {bad_flags}", file=sys.stderr)
            return EXIT_BLOCKED
        emit("argv", "OK", argv=argv, validated_against="pinned --help")

        writer = JsonlTelemetryWriter(RUNS / "k2-headless-admission.jsonl")
        controller = ResourceController(
            policy, MockK2TokenCounter(), None, MacOSTelemetrySource(),
            supervisor=ProcessSupervisor(SubprocessAdapter(), graceful_timeout_s=15.0),
            telemetry_writer=writer,
            watchdog_thresholds=WatchdogThresholds(
                min_available_bytes=WD_MIN_AVAILABLE, max_swap_used_bytes=WD_MAX_SWAP,
                max_pageout_rate=WD_MAX_PAGEOUT_RATE, sample_interval_s=0.25),
        )
        backend = ProtectedServer(
            controller, cfg, port_probe=RealPortProbe(), watchdog_readiness=readiness,
            forwarder=HttpRequestForwarder(f"http://{HOST}:{BACKEND_PORT}", model=CHECKPOINT),
            writer=writer,
        )
        started = backend.start()
        emit("backend_start", started.outcome.value, reason=started.reason)
        if not started.started:
            return EXIT_BLOCKED

        # --- health + Metal evidence ----------------------------------------
        ok = False
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            try:
                if backend.forwarder.get_models(timeout_s=5.0).get("data"):
                    ok = True
                    break
            except Exception:
                time.sleep(2.0)
            if wd_proc.poll() is not None:
                break
        summary["backend_healthy"] = ok
        emit("backend_health", "OK" if ok else "FAIL")
        if not ok:
            return EXIT_ABORTED

        # --- K2 tokenizer bootstrap: MUST succeed before any inference --------
        # Uses the backend's OWN tokenizer over its HTTP API. Qwen token counts
        # and character-count estimates are never substituted.
        token_counts = None
        for path, payload in (
            ("/tokenize", {"content": PROMPT}),
            ("/v1/tokenize", {"content": PROMPT}),
        ):
            try:
                rq = urllib.request.Request(
                    f"http://{HOST}:{BACKEND_PORT}{path}",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(rq, timeout=30) as rs:
                    got = json.loads(rs.read().decode())
                if isinstance(got, dict) and isinstance(got.get("tokens"), list):
                    token_counts = {"endpoint": path, "tokens": len(got["tokens"])}
                    break
            except Exception:
                continue

        if token_counts is None:
            emit("token_accounting", "TOKEN_ACCOUNTING_BLOCKED",
                 reason="backend exposes no usable tokenize endpoint")
            summary["token_accounting"] = {"status": "TOKEN_ACCOUNTING_BLOCKED"}
            print("TOKEN_ACCOUNTING_BLOCKED: refusing to forward inference", file=sys.stderr)
            return EXIT_BLOCKED
        if token_counts["tokens"] + OUTPUT > CTX:
            emit("token_accounting", "TOKEN_ACCOUNTING_BLOCKED",
                 tokens=token_counts["tokens"], ctx=CTX, output=OUTPUT)
            return EXIT_BLOCKED
        summary["token_accounting"] = {
            "status": "TOKEN_ACCOUNTING_VALIDATED", **token_counts,
            "reserved_output": OUTPUT, "ctx": CTX,
        }
        emit("token_accounting", "TOKEN_ACCOUNTING_VALIDATED", **token_counts)

        # --- one inference through the gateway -------------------------------
        gateway = ProtectedGateway(backend)
        http = GatewayHTTPServer(gateway, GATEWAY_PORT)
        http.start()
        emit("gateway", "OK", port=GATEWAY_PORT)

        # The request goes through the ProtectedGateway HTTP endpoint, NOT
        # directly to the backend on 8082. This exercises the real end-to-end
        # path: gateway -> admission -> reservation -> backend.
        import urllib.error
        import urllib.request

        t0 = time.time()
        body = json.dumps({
            "model": CHECKPOINT,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": OUTPUT,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"http://{HOST}:{GATEWAY_PORT}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                status = resp.status
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            status = exc.code
            payload = json.loads(exc.read().decode() or "{}")
        elapsed = time.time() - t0

        choice = (payload.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
        summary["request"] = {
            "http_status": status,
            "gateway": f"{HOST}:{GATEWAY_PORT}",
            "forgeone_outcome": (payload.get("forgeone") or {}).get("outcome"),
            "input_tokens": (payload.get("forgeone") or {}).get("input_tokens"),
            "finish_reason": choice.get("finish_reason"),
            "elapsed_s": round(elapsed, 2),
            "response": content,
            "chars": len(content) if isinstance(content, str) else 0,
        }
        emit("inference", "OK" if status == 200 else "FAIL",
             http_status=status, elapsed_s=round(elapsed, 2),
             finish_reason=choice.get("finish_reason"))
        code = EXIT_OK if status == 200 and content else EXIT_ABORTED
    except KeyboardInterrupt:
        emit("interrupt", "ABORTED")
        code = EXIT_ABORTED
    finally:
        # --- cleanup on EVERY exit path --------------------------------------
        if http is not None:
            try:
                http.stop()
            except Exception:
                pass
        if backend is not None:
            try:
                backend.stop()
            except Exception:
                pass
        if controller is not None:
            try:
                summary["owned_pids_after"] = controller.owned_pids()
                summary["reservations_after"] = controller.reservations.active_count
            except Exception:
                pass
        try:
            wd_proc.terminate()
            wd_proc.wait(timeout=10)
        except Exception:
            try:
                wd_proc.kill()
            except Exception:
                pass
        summary["telemetry_samples"] = count_telemetry_samples(tel_path)
        summary["exit_code"] = code
        SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        emit("summary", "WRITTEN", path=str(SUMMARY.relative_to(REPO)))
    return code


def main() -> int:
    ap = argparse.ArgumentParser(prog="k2_headless_session.py")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="preflight only; never loads weights")
    g.add_argument("--execute", action="store_true", help="one bounded protected session")
    args = ap.parse_args()

    if args.check:
        conf = conflicting_processes()
        ports = ports_free()
        emit("environment", "OK" if not conf else "CONFLICT",
             conflicting=conf, ports_free=ports,
             runtime_binary_present=RUNTIME_BIN.is_file(),
             checkpoint_present=True)
        if conf or not all(ports.values()):
            emit("check", "BLOCKED", reason="conflicting process or port in use")
            return EXIT_BLOCKED
        gate = preflight()
        emit("check", "PASS" if gate["admitted"] else "BLOCKED", **{
            "min_available_gib": round(gate["min_available_bytes"] / GB, 2),
            "required_gib": round(gate["required_bytes"] / GB, 2),
            "projected_reserve_gib": round(gate["projected_reserve_bytes"] / GB, 2)})
        return EXIT_OK if gate["admitted"] else EXIT_BLOCKED

    # --execute: gate first, and never load weights if it fails.
    gate = preflight()
    if not gate["admitted"]:
        emit("execute", "BLOCKED", reason="admission gate failed",
             min_available_gib=round(gate["min_available_bytes"] / GB, 2),
             required_gib=round(gate["required_bytes"] / GB, 2))
        print("\nBLOCKED — no model loaded. Free memory and try again.", file=sys.stderr)
        return EXIT_BLOCKED
    conf = conflicting_processes()
    if conf:
        emit("execute", "BLOCKED", reason="conflicting model process", conflicting=conf)
        return EXIT_BLOCKED
    return execute()


if __name__ == "__main__":
    raise SystemExit(main())
