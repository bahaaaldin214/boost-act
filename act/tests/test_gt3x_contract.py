from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_acc_new_r_prefers_gt3x_over_csv():
    script = (PROJECT_ROOT / "act" / "core" / "acc_new.R").read_text(encoding="utf-8")

    assert 'pattern = "\\\\.(gt3x|csv)$"' in script
    assert "split(RelativeFiles, dirname(RelativeFiles))" in script
    assert "gt3x <- paths[grepl(\"\\\\.gt3x$\", paths, ignore.case = TRUE)]" in script
    assert "return(gt3x[1])" in script
    assert "csv <- paths[grepl(\"_accel\\\\.csv$\", paths, ignore.case = TRUE)]" in script
    assert "return(csv[1])" in script


def test_gg_command_uses_input_and_output_dirs(monkeypatch):
    commands = []
    qc_calls = []

    class FakeStdout:
        def __iter__(self):
            return iter(["line-1\n", "line-2\n"])

        def close(self):
            return None

    class FakeProcess:
        def __init__(self, command, shell, stdout, stderr, bufsize, universal_newlines):
            commands.append(command)
            self.stdout = FakeStdout()
            self.returncode = 0

        def wait(self):
            return 0

    class FakeQC:
        def __init__(self, project_type, system):
            qc_calls.append((project_type, system))

        def qc(self):
            qc_calls.append("qc")

    qc_mod = types.ModuleType("act.utils.qc")
    qc_mod.QC = FakeQC
    monkeypatch.setitem(sys.modules, "act.utils.qc", qc_mod)

    gg_mod = importlib.import_module("act.core.gg")
    monkeypatch.setattr(gg_mod.subprocess, "Popen", FakeProcess)

    runner = gg_mod.GG(
        matched={"8001": []},
        intdir="/tmp/int study",
        obsdir="/tmp/obs study",
        system="local",
        output_dir="/tmp/out study",
    )
    runner.run_gg()

    assert commands == [
        "Rscript act/core/acc_new.R --input_dir /tmp/int study/ --output_dir /tmp/out study/ --deriv_dir derivatives/GGIR-3.2.6/",
        "Rscript act/core/acc_new.R --input_dir /tmp/obs study/ --output_dir /tmp/out study/ --deriv_dir derivatives/GGIR-3.2.6/",
    ]
    assert qc_calls == [("int", "local"), "qc", ("obs", "local"), "qc"]
