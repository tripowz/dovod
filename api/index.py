"""Вход для Vercel: всё API «Довода» — одна Python-функция.

Vercel перенаправляет сюда любой запрос на /api/... (см. vercel.json), а
FastAPI сам разбирает путь. Сайт Vercel раздаёт отдельно, из miniapp/dist.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.main import app  # noqa: E402,F401
