"""Cross-cutting: every layer's concern, no layer's private concern.

Identity, authorization, visibility, attribution, configuration and logging. Importable
from anywhere in the package and importing only itself — the half of the layer rule that
`tests/test_layers.py::test_cross_cutting_imports_only_cross_cutting` enforces.
"""
