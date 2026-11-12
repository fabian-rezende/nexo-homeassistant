"""Carrega os módulos puros da integração sem o Home Assistant (pula o __init__.py)."""
import pathlib
import sys
import types

COMPONENT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "nexohub"
pkg = types.ModuleType("nexohub_pure")
pkg.__path__ = [str(COMPONENT)]
sys.modules.setdefault("nexohub_pure", pkg)
