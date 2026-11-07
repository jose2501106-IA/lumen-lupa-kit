"""El marcador `live` (N8) está declarado y excluido por defecto (docs/pruebas.md)."""


def test_marcador_live_declarado(pytestconfig):
    assert any(m.startswith("live:") for m in pytestconfig.getini("markers"))


def test_live_excluido_por_defecto(pytestconfig):
    assert "not live" in " ".join(pytestconfig.getini("addopts"))
