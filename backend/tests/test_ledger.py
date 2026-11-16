"""领退流水领域测试：python -m unittest discover -s tests

使用临时 SQLite，不依赖 MySQL/Redis；只测服务层与挖掘快照口径。
"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="affinity-test-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'test.db'}")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from src.config.database import Base, Session, engine  # noqa: E402
from src.models.entities import Job, LedgerEntry, MiningRun  # noqa: E402
from src.schemas.business import EntryInput  # noqa: E402
from src.services.analysis import mine  # noqa: E402
from src.services.jobs import save_job  # noqa: E402
from src.services.ledger import job_ledger, post_entry, reopen_job, settle_job, void_pending  # noqa: E402
from src.schemas.business import JobInput  # noqa: E402


class LedgerTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with Session() as s:
            for code in ("J-1", "J-2", "J-3"):
                s.add(Job(code=code, equipment="泵组", parts=["轴承"], note=""))
            s.commit()

    def post(self, **kw):
        with Session() as s:
            return post_entry(s, "tester", EntryInput(**kw))

    def ledger(self, code):
        with Session() as s:
            return job_ledger(s, code)

    def test_return_before_issue_pends_then_backfills(self):
        out = self.post(entry_no="R1", job_code="J-1", direction="return", part="轴承", qty=1, ref_entry_no="I1")
        self.assertEqual(out["outcome"], "pending")
        with self.assertRaises(HTTPException):
            settle_job(Session(), "tester", "J-1")
        self.post(entry_no="I1", job_code="J-1", direction="issue", part="轴承", qty=3)
        detail = self.ledger("J-1")
        self.assertEqual(detail["entries"][0]["status"], "booked")
        self.assertEqual(detail["summary"][0]["net"], 2)

    def test_over_return_rejected_with_trace(self):
        self.post(entry_no="I2", job_code="J-1", direction="issue", part="轴承", qty=2)
        self.post(entry_no="R2", job_code="J-1", direction="return", part="轴承", qty=2, ref_entry_no="I2")
        with self.assertRaises(HTTPException):
            self.post(entry_no="R3", job_code="J-1", direction="return", part="轴承", qty=1, ref_entry_no="I2")
        detail = self.ledger("J-1")
        rejected = [e for e in detail["entries"] if e["entry_no"] == "R3"][0]
        self.assertEqual(rejected["status"], "rejected")
        self.assertIn("超过", rejected["reject_reason"])

    def test_cross_job_reference_rejected(self):
        self.post(entry_no="I3", job_code="J-1", direction="issue", part="轴承", qty=1)
        with self.assertRaises(HTTPException):
            self.post(entry_no="R4", job_code="J-2", direction="return", part="轴承", qty=1, ref_entry_no="I3")
        detail = self.ledger("J-2")
        self.assertEqual(detail["entries"][0]["status"], "rejected")
        self.assertIn("跨作业", detail["entries"][0]["reject_reason"])

    def test_retry_idempotent_and_conflict(self):
        first = self.post(entry_no="I4", job_code="J-1", direction="issue", part="轴承", qty=1)
        again = self.post(entry_no="I4", job_code="J-1", direction="issue", part="轴承", qty=1)
        self.assertEqual(first["outcome"], "booked")
        self.assertEqual(again["outcome"], "duplicate")
        with self.assertRaises(HTTPException):
            self.post(entry_no="I4", job_code="J-1", direction="issue", part="轴承", qty=2)
        self.assertEqual(len(self.ledger("J-1")["entries"]), 1)

    def test_settle_net_positive_only_and_reopen(self):
        self.post(entry_no="I5", job_code="J-1", direction="issue", part="轴承", qty=2)
        self.post(entry_no="I6", job_code="J-1", direction="issue", part="润滑脂", qty=1)
        self.post(entry_no="R5", job_code="J-1", direction="return", part="轴承", qty=2, ref_entry_no="I5")
        detail = settle_job(Session(), "tester", "J-1")
        self.assertEqual(detail["job"]["parts"], ["润滑脂"])
        self.assertEqual(detail["job"]["ledger_state"], "settled")
        with self.assertRaises(HTTPException):
            self.post(entry_no="I7", job_code="J-1", direction="issue", part="垫片", qty=1)
        reopened = reopen_job(Session(), "tester", "J-1")
        self.assertEqual(reopened["job"]["ledger_state"], "open")
        self.post(entry_no="I7", job_code="J-1", direction="issue", part="垫片", qty=1)
        self.assertEqual(settle_job(Session(), "tester", "J-1")["job"]["parts"], ["垫片", "润滑脂"])

    def test_locked_job_rejects_manual_part_edit(self):
        self.post(entry_no="I8", job_code="J-1", direction="issue", part="轴承", qty=1)
        with Session() as s:
            with self.assertRaises(HTTPException):
                save_job(s, JobInput(code="J-1", equipment="泵组", parts=["垫片"], note="x"), 1)

    def test_void_pending_return(self):
        self.post(entry_no="R6", job_code="J-1", direction="return", part="轴承", qty=1, ref_entry_no="MISSING")
        out = void_pending(Session(), "tester", "R6")
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(self.ledger("J-1")["job"]["ledger_state"], "manual")

    def test_pending_cross_job_and_over_return_rejected_on_backfill(self):
        # 引用尚不存在的流水先暂挂；原领用在别处落账后补算为跨作业拒绝。
        self.post(entry_no="R8", job_code="J-2", direction="return", part="轴承", qty=1, ref_entry_no="I15")
        self.post(entry_no="R9", job_code="J-2", direction="return", part="轴承", qty=9, ref_entry_no="I16")
        self.post(entry_no="I15", job_code="J-1", direction="issue", part="轴承", qty=1)
        self.post(entry_no="I16", job_code="J-2", direction="issue", part="轴承", qty=2)
        detail = self.ledger("J-2")
        status = {e["entry_no"]: e for e in detail["entries"]}
        self.assertEqual(status["R8"]["status"], "rejected")
        self.assertIn("跨作业", status["R8"]["reject_reason"])
        self.assertEqual(status["R9"]["status"], "rejected")
        self.assertIn("超过", status["R9"]["reject_reason"])
        self.assertEqual(detail["summary"][0]["net"], 2)

    def test_pending_returns_share_issue_quota(self):
        self.post(entry_no="R10", job_code="J-1", direction="return", part="轴承", qty=2, ref_entry_no="I17")
        self.post(entry_no="R11", job_code="J-1", direction="return", part="轴承", qty=2, ref_entry_no="I17")
        self.post(entry_no="I17", job_code="J-1", direction="issue", part="轴承", qty=3)
        detail = self.ledger("J-1")
        status = {e["entry_no"]: e for e in detail["entries"]}
        self.assertEqual(status["R10"]["status"], "booked")
        self.assertEqual(status["R11"]["status"], "rejected")
        self.assertIn("超过", status["R11"]["reject_reason"])

    def test_concurrent_returns_never_exceed_issue(self):
        self.post(entry_no="I20", job_code="J-1", direction="issue", part="轴承", qty=3)
        results = []

        def worker(i):
            try:
                with Session() as s:
                    post_entry(
                        s,
                        "tester",
                        EntryInput(
                            entry_no=f"CR-{i}",
                            job_code="J-1",
                            direction="return",
                            part="轴承",
                            qty=1,
                            ref_entry_no="I20",
                        ),
                    )
                results.append("ok")
            except HTTPException:
                results.append("rejected")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        detail = self.ledger("J-1")
        booked = [e for e in detail["entries"] if e["direction"] == "return" and e["status"] == "booked"]
        self.assertEqual(len(booked), 3, results)
        self.assertEqual(detail["issues"][0]["returned"], 3)
        self.assertEqual(detail["issues"][0]["remaining"], 0)

    def test_concurrent_same_entry_no_books_once(self):
        results = []

        def worker(i):
            try:
                with Session() as s:
                    post_entry(
                        s,
                        "tester",
                        EntryInput(
                            entry_no="DUP-1",
                            job_code="J-1",
                            direction="issue",
                            part="轴承",
                            qty=2,
                        ),
                    )
                results.append("ok")
            except HTTPException:
                results.append("rejected")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        with Session() as s:
            self.assertEqual(s.query(LedgerEntry).filter_by(entry_no="DUP-1").count(), 1)
        self.assertEqual(self.ledger("J-1")["summary"][0]["issued"], 2)

    def test_entry_schema(self):
        with self.assertRaises(ValidationError):
            EntryInput(entry_no="R7", job_code="J-1", direction="return", part="轴承", qty=1)
        with self.assertRaises(ValidationError):
            EntryInput(entry_no="I9", job_code="J-1", direction="issue", part="轴承", qty=1, ref_entry_no="I8")
        with self.assertRaises(ValidationError):
            EntryInput(entry_no="I10", job_code="J-1", direction="issue", part="轴承", qty=0)
        with self.assertRaises(ValidationError):
            EntryInput(entry_no="   ", job_code="J-1", direction="issue", part="轴承", qty=1)

    def test_mining_snapshot_uses_settled_parts_and_skips_open(self):
        self.post(entry_no="I11", job_code="J-1", direction="issue", part="轴承", qty=1)
        self.post(entry_no="I12", job_code="J-1", direction="issue", part="润滑脂", qty=1)
        self.post(entry_no="R7", job_code="J-1", direction="return", part="轴承", qty=1, ref_entry_no="I11")
        settle_job(Session(), "tester", "J-1")
        self.post(entry_no="I13", job_code="J-2", direction="issue", part="密封圈", qty=1)
        with Session() as s:
            scoped = s.query(Job).all()
            snapshot = [
                {"parts": j.parts}
                for j in scoped
                if j.ledger_state != "open"
            ]
        self.assertEqual([r["parts"] for r in snapshot], [["润滑脂"], ["轴承"]])
        # J-3 仍为手工用件，正常进入；J-2 核算中被排除。
        result = mine(snapshot, 0.4, 0.5, 0)
        self.assertEqual(result["valid_jobs"], 2)
        self.assertEqual(result["excluded"], 0)

    def test_frozen_snapshot_not_changed_by_later_settlement(self):
        with Session() as s:
            run = MiningRun(name="冻结", result={"snapshot": [{"code": "J-1", "parts": ["轴承"]}]})
            s.add(run)
            s.commit()
            run_id = run.id
        self.post(entry_no="I14", job_code="J-1", direction="issue", part="垫片", qty=1)
        settle_job(Session(), "tester", "J-1")
        with Session() as s:
            self.assertEqual(s.get(MiningRun, run_id).result["snapshot"][0]["parts"], ["轴承"])


if __name__ == "__main__":
    unittest.main()
