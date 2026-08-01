import tomllib
import pathlib


def test_llm_extra_declares_llama_cpp():
    data = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
    extras = data["project"]["optional-dependencies"]
    assert "llm" in extras
    assert any("llama-cpp-python" in dep for dep in extras["llm"])


def test_core_deps_do_not_require_llama_cpp():
    data = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
    assert all("llama-cpp-python" not in d for d in data["project"]["dependencies"])
