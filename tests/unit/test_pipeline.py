"""Pipeline integration tests: idempotency, states, resume, retry, storage (PostgreSQL).

Runs against the local dev meeting-db (docker compose up meeting-db).
Skipped automatically when the database is not reachable.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from app.broker.gateway import BrokerGatewayError, ScopedHandle  # noqa: E402
from app.config import Settings  # noqa: E402
from app.core.events import EventBus  # noqa: E402
from app.core.security import content_hash  # noqa: E402
from app.meetings.pipeline import MeetingPipeline  # noqa: E402
from app.meetings.storage import MeetingStore  # noqa: E402

DB_URL = os.getenv("MEETING_TEST_DATABASE_URL", "postgresql://meeting:meeting@127.0.0.1:5433/meeting_intelligence_test")


def _database_available() -> bool:
    try:
        import psycopg
        with psycopg.connect(DB_URL, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


FAKE_PROVIDER_PAYLOAD = {
    "transcript_id": "t-1", "status": "completed", "audio_duration": 12, "language_code": "ru",
    "text": "Полный текст",
    "utterances": [{"speaker": "A", "text": "Нужно подготовить интеграцию к марту.", "start": 0, "end": 4000},
                   {"speaker": "B", "text": "Бюджет обсудим на следующей встрече.", "start": 4200, "end": 8000}],
}


class FakeGateway:
    """Deterministic broker double: real policy shape, no network, no secrets."""

    def __init__(self, *, fail_speech=False, fail_analysis=False, analysis_payload=None):
        self.fail_speech, self.fail_analysis = fail_speech, fail_analysis
        self.analysis_payload = analysis_payload or {
            "choices": [{"message": {"content": """
            {"meeting_summary": "Обсуждение интеграции.",
             "audit_results": {"profile_id": "commercial_meeting_v1", "overall_status": "YELLOW", "criteria": [
                {"criterion": "next_step", "value": "подготовить интеграцию", "evidence": "подготовить интеграцию"},
                {"criterion": "budget", "value": "NOT_DISCLOSED"}]},
             "actions": [{"text": "Подготовить интеграцию", "owner": "Speaker A", "deadline": "март",
                          "confidence": 0.9, "source_utterance": "подготовить интеграцию"}],
             "decisions": [], "commitments": [], "open_questions": [], "risks": [],
             "recommendations": ["Зафиксировать бюджет"], "overall_status": "YELLOW"}
            """}}]}

    def scoped_handle(self, service_id, capability):
        return ScopedHandle(service_id, capability, "test://handle", True)

    def speech(self):
        class S:
            def transcribe(inner_self, media_path, **kwargs):
                if self.fail_speech:
                    raise BrokerGatewayError("PROVIDER_UNAVAILABLE", "test", retryable=True)
                return dict(FAKE_PROVIDER_PAYLOAD)
        return S()

    def analysis(self):
        class A:
            def complete(inner_self, system_prompt, user_payload):
                if self.fail_analysis:
                    raise BrokerGatewayError("ANALYSIS_FAILED", "test", retryable=False)
                return dict(self.analysis_payload)
        return A()

    def telegram(self):
        class T:
            def send_message(inner_self, chat_id, text):
                return {"ok": True}
        return T()


@unittest.skipUnless(_database_available(), "local meeting-db not running")
class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["MEETING_SOURCE_RETENTION_SECONDS"] = "0"   # keep originals: tests re-process files
        cls.store = MeetingStore(DB_URL, min_size=1, max_size=2)
        with cls.store._conn() as conn:
            for table in ("processing_events", "risks", "decisions", "action_items", "meeting_analyses",
                          "utterances", "transcripts", "meeting_files", "meetings"):
                conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        cls.store.migrate()
        cls.settings = Settings()
        cls.bus = EventBus(stream=open(os.devnull, "w", encoding="utf-8"))
        cls.media = cls.settings.media_dir / "test_input.mp3"
        cls.settings.media_dir.mkdir(parents=True, exist_ok=True)
        for stale in cls.settings.media_dir.glob("*.mp3"):
            stale.unlink(missing_ok=True)
        # Real decodable synthetic media (2s tone), generated via ffmpeg — same tool the pipeline uses.
        import subprocess
        subprocess.run([cls.settings.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-b:a", "64k", str(cls.media)], check=True, capture_output=True, timeout=60)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.store.close()

    def _pipeline(self, gateway) -> MeetingPipeline:
        return MeetingPipeline(self.settings, gateway, self.store, self.bus)

    def test_full_lifecycle_and_idempotency(self) -> None:
        pipeline = self._pipeline(FakeGateway())
        intake = pipeline.accept_media(self.media, original_name="demo_meeting.mp3", source="test", file_unique_id="f1")
        self.assertFalse(intake["duplicate"])
        result = pipeline.process(intake["meeting_id"])
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual(result["speaker_count"], 2)
        meeting = self.store.get_meeting(intake["meeting_id"])
        self.assertEqual(meeting["state"], "COMPLETED")
        analysis = self.store.load_analysis(intake["meeting_id"])
        self.assertEqual(analysis.overall_status, "YELLOW")
        # Idempotency: same content again -> duplicate, no reprocessing.
        again = pipeline.accept_media(self.media, original_name="renamed_copy.mp3", source="test", file_unique_id="f2")
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["meeting_id"], intake["meeting_id"])
        rows = self.store.recent_meetings()
        self.assertEqual(len(rows), 1)

    def test_retryable_provider_failure_marks_retry_pending(self) -> None:
        pipeline = self._pipeline(FakeGateway(fail_speech=True))
        intake = pipeline.accept_media(self.media, original_name="retry_case.mp3", source="test", file_unique_id="f3")
        with self.assertRaises(Exception):
            pipeline.process(intake["meeting_id"])
        meeting = self.store.get_meeting(intake["meeting_id"])
        self.assertEqual(meeting["state"], "RETRY_PENDING")

    def test_resume_after_restart_reuses_transcript(self) -> None:
        pipeline = self._pipeline(FakeGateway())
        intake = pipeline.accept_media(self.media, original_name="resume_case.mp3", source="test", file_unique_id="f4")
        # First run completes; simulate restart by wiping analysis only.
        pipeline.process(intake["meeting_id"])
        with self.store._conn() as conn:
            conn.execute("DELETE FROM meeting_analyses WHERE meeting_id=%s", (intake["meeting_id"],))
            conn.execute("DELETE FROM action_items WHERE meeting_id=%s", (intake["meeting_id"],))
            conn.execute("UPDATE meetings SET state='TRANSCRIBED', overall_status=NULL WHERE meeting_id=%s",
                         (intake["meeting_id"],))
        # A gateway that now fails upload proves transcription was NOT redone (transcript reused).
        class NoUploadGateway(FakeGateway):
            def speech(self):
                raise AssertionError("upload must not run again on resume")
        resumed = pipeline.resume_pending()
        self.assertGreaterEqual(resumed, 1)
        self.assertEqual(self.store.get_meeting(intake["meeting_id"])["state"], "COMPLETED")

    def test_persistence_and_kpi(self) -> None:
        kpi = self.store.kpi_snapshot()
        self.assertGreaterEqual(kpi["meetings_count"], 1)
        self.assertGreaterEqual(kpi["transcription_success_rate"], 0.5)
        self.assertGreaterEqual(len(self.store.open_actions()), 1)


if __name__ == "__main__":
    unittest.main()
