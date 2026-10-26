"""Where the local models run: the NVIDIA card only when asked for and usable."""

from job_radar.embed import CPU, providers


def test_cpu_unless_the_gpu_is_asked_for_and_allowed(monkeypatch):
    assert providers(gpu=False) == CPU
    monkeypatch.setenv("RADAR_DEVICE", "cpu")
    assert providers(gpu=True) == CPU
    monkeypatch.delenv("RADAR_DEVICE")
    assert providers(gpu=True)[-1] == "CPUExecutionProvider"  # the CPU always stays as fallback
