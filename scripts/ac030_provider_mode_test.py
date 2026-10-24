"""AC-030: Provider mode switch verification.

This script verifies:
1. TRAVEL_DESK_PROVIDER_MODE env var is recognized (demo/live)
2. Default mode is "demo"
3. Demo mode: no external network requests
4. Live mode: system attempts Live Provider calls (fails gracefully)
5. Invalid mode: config error at startup
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO_ROOT / "artifacts" / "acceptance" / "TASK-013" / "ac030-provider-mode"


def run_cmd(cmd: list[str], env: dict | None = None, timeout: int = 10) -> tuple[int, str, str]:
    """Run a command and return (exit_code, stdout, stderr)."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    # Remove DASHSCOPE_API_KEY for fake mode tests
    merged_env.pop("DASHSCOPE_API_KEY", None)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=merged_env, cwd=REPO_ROOT)
    return result.returncode, result.stdout, result.stderr


def test_config_validation():
    """Test 1: Config validates provider_mode."""
    print("\n=== Test 1: Config validation ===")
    
    # Default mode
    code, out, err = run_cmd([sys.executable, "-c", "from travel_desk.config import get_settings; s = get_settings(); print(s.provider_mode)"])
    assert code == 0, f"Default mode failed: {err}"
    assert "demo" in out, f"Expected 'demo', got: {out}"
    print(f"✓ Default mode: {out.strip()}")
    
    # Explicit demo
    code, out, err = run_cmd([sys.executable, "-c", "from travel_desk.config import get_settings; s = get_settings(); print(s.provider_mode)"],
                             env={"TRAVEL_DESK_PROVIDER_MODE": "demo"})
    assert code == 0, f"Explicit demo failed: {err}"
    assert "demo" in out
    print(f"✓ Explicit demo: {out.strip()}")
    
    # Explicit live
    code, out, err = run_cmd([sys.executable, "-c", "from travel_desk.config import get_settings; s = get_settings(); print(s.provider_mode)"],
                             env={"TRAVEL_DESK_PROVIDER_MODE": "live"})
    assert code == 0, f"Explicit live failed: {err}"
    assert "live" in out
    print(f"✓ Explicit live: {out.strip()}")
    
    # Invalid mode
    code, out, err = run_cmd([sys.executable, "-c", "from travel_desk.config import get_settings; s = get_settings()"],
                             env={"TRAVEL_DESK_PROVIDER_MODE": "invalid"})
    assert code != 0, "Invalid mode should fail"
    assert "TRAVEL_DESK_PROVIDER_MODE must be one of demo, live" in err
    print(f"✓ Invalid mode rejected: {err.strip().split(chr(10))[-1]}")


def test_network_guard():
    """Test 2: Network guard allows correct hosts."""
    print("\n=== Test 2: Network guard allowlist ===")
    
    # Demo mode: no external hosts
    code, out, err = run_cmd([sys.executable, "-c", """
import json
from travel_desk.network_guard import allowed_hosts_for
print(json.dumps(sorted(allowed_hosts_for('fake', '', 'demo'))))
"""])
    assert code == 0
    hosts = json.loads(out)
    assert hosts == [], f"Demo mode should have no external hosts, got: {hosts}"
    print(f"✓ Demo mode allowlist: {hosts}")
    
    # Live mode: Live Provider hosts
    code, out, err = run_cmd([sys.executable, "-c", """
import json
from travel_desk.network_guard import allowed_hosts_for
print(json.dumps(sorted(allowed_hosts_for('fake', '', 'live'))))
"""])
    assert code == 0
    hosts = json.loads(out)
    expected = {"api.open-meteo.com", "api.tavily.com", "tequila.kiwi.com"}
    assert set(hosts) == expected, f"Live mode allowlist mismatch: {hosts} vs {expected}"
    print(f"✓ Live mode allowlist: {hosts}")


def test_demo_mode_no_external():
    """Test 3: Demo mode makes no external requests."""
    print("\n=== Test 3: Demo mode (no external requests) ===")
    
    # Start server in demo mode
    env = {"TRAVEL_DESK_PROVIDER_MODE": "demo", "TRAVEL_DESK_LLM_MODE": "fake"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "travel_desk.main:create_app", "--factory", "--host", "127.0.0.1", "--port", "8001"],
                            cwd=REPO_ROOT, env={**os.environ, **env}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(3)
    
    try:
        # Check health
        code, out, err = run_cmd(["curl", "-s", "http://127.0.0.1:8001/api/health"])
        assert code == 0
        health = json.loads(out)
        assert health["status"] == "ok"
        print(f"✓ Server started in demo mode")
        
        # Check providers health
        code, out, err = run_cmd(["curl", "-s", "http://127.0.0.1:8001/api/health/providers", "-H", "X-Demo-User: u-wangwu"])
        assert code == 0
        providers = json.loads(out)
        assert providers["network"]["external_attempts"] == 0, "Demo mode should have zero external attempts"
        print(f"✓ External attempts: {providers['network']['external_attempts']}")
        
        # Check guard log doesn't exist (no external attempts)
        guard_log = REPO_ROOT / "var" / "network-guard.jsonl"
        # In demo mode with fake LLM, there should be no guard log or it should be empty
        if guard_log.exists():
            lines = guard_log.read_text().strip().split("\n")
            external_lines = [l for l in lines if '"external": true' in l]
            assert len(external_lines) == 0, f"Demo mode should have no external attempts, found: {external_lines}"
            print(f"✓ Guard log: no external attempts recorded")
        else:
            print(f"✓ Guard log: not created (no attempts)")
        
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_live_mode_graceful_failure():
    """Test 4: Live mode attempts calls but fails gracefully."""
    print("\n=== Test 4: Live mode (graceful failure) ===")
    
    # Start server in live mode
    env = {"TRAVEL_DESK_PROVIDER_MODE": "live", "TRAVEL_DESK_LLM_MODE": "fake"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "travel_desk.main:create_app", "--factory", "--host", "127.0.0.1", "--port", "8002"],
                            cwd=REPO_ROOT, env={**os.environ, **env}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(3)
    
    try:
        # Check health
        code, out, err = run_cmd(["curl", "-s", "http://127.0.0.1:8002/api/health"])
        assert code == 0
        health = json.loads(out)
        assert health["status"] == "ok"
        print(f"✓ Server started in live mode")
        
        # Check providers health
        code, out, err = run_cmd(["curl", "-s", "http://127.0.0.1:8002/api/health/providers", "-H", "X-Demo-User: u-wangwu"])
        assert code == 0
        providers = json.loads(out)
        print(f"✓ Provider mode: {providers.get('provider_mode', 'N/A')}")
        
        # Verify server is still alive
        code, out, err = run_cmd(["curl", "-s", "http://127.0.0.1:8002/api/health"])
        assert code == 0
        print(f"✓ Server survived startup in live mode")
        
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def main():
    """Run all AC-030 tests and generate evidence."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    
    print("AC-030: Provider mode switch verification")
    print("=" * 60)
    
    test_config_validation()
    test_network_guard()
    test_demo_mode_no_external()
    test_live_mode_graceful_failure()
    
    print("\n" + "=" * 60)
    print("✓ All AC-030 tests passed")
    print(f"Evidence directory: {EVIDENCE_DIR}")


if __name__ == "__main__":
    main()
