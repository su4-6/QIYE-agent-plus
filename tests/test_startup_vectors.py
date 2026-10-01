import struct
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.config import settings
from app.database import get_connection, init_database
from app.knowledge import import_document
from app.main import app
from app.embeddings import local_dimension
from app.retrieval_health import vector_health, verify_local_vector_runtime
from evaluation.run import isolated_directory


class PersistedVectorStartupTest(unittest.TestCase):
    def setUp(self):
        self.original = dict(vars(settings))
        self.workspace = Path(__file__).resolve().parents[1]
        self.temp = isolated_directory()
        self.directory = self.temp.__enter__()
        for key, value in {"database_url":str(self.directory/"restart.db"),
                           "app_env":"test", "llm_provider":"disabled",
                           "embedding_provider":"local", "embedding_model":"BAAI/bge-small-zh-v1.5"}.items():
            object.__setattr__(settings, key, value)
        init_database()
        import_document("demo", "重启测试知识", "md", "# 网络\n办公网络连接异常时检查客户端配置。", with_embedding=False)
        dimension = local_dimension(settings.embedding_model)
        self.vector = struct.pack(f"{dimension}f", *([1.0]+[0.0]*(dimension-1)))
        with get_connection() as db:
            db.execute("UPDATE knowledge_chunks SET embedding=?, embedding_model=?",
                       (self.vector, settings.embedding_model))

    def tearDown(self):
        for key, value in self.original.items():
            object.__setattr__(settings, key, value)
        self.directory.resolve().relative_to(self.workspace.resolve())
        self.temp.__exit__(None, None, None)

    def test_restart_verifies_existing_index_without_reindex_or_llm(self):
        self.assertEqual(vector_health()["state"], "index_ready_unverified")
        with patch("app.retrieval_health.embed", return_value=self.vector) as embed:
            with TestClient(app) as client:
                health = client.get("/health").json()
                self.assertEqual(health["status"], "ok")
                self.assertEqual(health["vector"]["state"], "ready")
                self.assertEqual(health["vector"]["coverage"], 1)
            embed.assert_called_once()

    def test_failed_runtime_probe_preserves_fallback_state(self):
        with patch("app.retrieval_health.embed", side_effect=RuntimeError("offline probe")):
            verify_local_vector_runtime()
        self.assertEqual(vector_health()["state"], "runtime_failed")

    def test_other_embedding_modes_do_not_make_new_startup_calls(self):
        object.__setattr__(settings, "embedding_provider", "gemini")
        with patch("app.retrieval_health.embed") as embed:
            verify_local_vector_runtime()
            embed.assert_not_called()
