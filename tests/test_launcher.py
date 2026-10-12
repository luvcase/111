import json

import httpx

from screenbridge.launcher import AppLock, RuntimeController, child_command, package_smoke


def test_app_lock_prevents_two_instances(tmp_path):
    first = AppLock(tmp_path)
    second = AppLock(tmp_path)
    assert first.acquired and not second.acquired
    first.close()
    third = AppLock(tmp_path)
    assert third.acquired
    third.close()


def test_runtime_starts_authenticated_workbench_and_shuts_down(tmp_path):
    runtime = RuntimeController(tmp_path, demo=True, port=0)
    try:
        runtime.start(native=False)
        with httpx.Client(timeout=3, trust_env=False) as client:
            assert client.get(runtime.base_url + "/").status_code == 200
            assert client.get(runtime.base_url + "/api/jobs").status_code == 401
            headers = {"Authorization": "Bearer " + runtime.token}
            assert client.get(runtime.base_url + "/api/status", headers=headers).json()["demo"]
        assert (tmp_path / "phone-qr.png").exists()
        assert json.loads((tmp_path / "instance.json").read_text())["port"] == runtime.port
    finally:
        runtime.stop()
    assert not (tmp_path / "instance.json").exists()


def test_workbench_restores_native_account_session(tmp_path):
    runtime = RuntimeController(tmp_path, demo=True, port=0)
    try:
        runtime.start(native=False)
        with httpx.Client(timeout=3, trust_env=False) as client:
            response = client.post(
                runtime.base_url + "/api/auth/register",
                headers={"Authorization": "Bearer " + runtime.token},
                json={"username": "app-user", "password": "test-password-123"},
            )
            assert response.status_code == 200
            account_token = response.json()["token"]
        (tmp_path / "desktop-session.txt").write_text(account_token)
        assert runtime.active_token() == account_token
        assert account_token in runtime.workbench_url
        (tmp_path / "desktop-session.txt").write_text("expired-session")
        assert runtime.active_token() == runtime.token
    finally:
        runtime.stop()


def test_packaged_child_uses_own_executable(monkeypatch, tmp_path):
    monkeypatch.setattr("screenbridge.launcher.sys.frozen", True, raising=False)
    monkeypatch.setattr("screenbridge.launcher.sys.executable", "ScreenBridge.exe")
    command = child_command(tmp_path, 8765)
    assert command[0] == "ScreenBridge.exe" and "--native" in command
    assert "8765" in command


def test_source_package_smoke(tmp_path):
    # CI runs the actual frozen native Windows smoke separately.
    result_file = tmp_path / "smoke.json"
    assert package_smoke(result_file, native=False) == 0
    result = json.loads(result_file.read_text())
    assert result["ok"] and not result["native_checked"]


def test_package_smoke_cleans_up_failed_start(monkeypatch, tmp_path):
    original = RuntimeController.start

    def fail_start(self, native=True):
        original(self, native=False)
        raise RuntimeError("startup-check-failed")

    monkeypatch.setattr(RuntimeController, "start", fail_start)
    result_file = tmp_path / "failed-smoke.json"
    assert package_smoke(result_file, native=False) == 1
    result = json.loads(result_file.read_text())
    assert result["error"] == "startup-check-failed" and not result["ok"]
