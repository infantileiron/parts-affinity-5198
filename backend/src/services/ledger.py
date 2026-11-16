"""领退流水：按作业登记领用/退料，结清后形成净用件证据。

关键约束：
- 流水号全局唯一；同号同文幂等重试不重复记账，同号异文冲突。
- 退料必须引用原领用；原领用未到先暂挂，到齐后按登记顺序补算。
- 跨作业引用、备件不一致、累计超退一律拒绝入账（保留拒绝痕）。
- 作业状态 manual -> open（核算中，排除出新挖掘）-> settled（结清，净用量正件进入新挖掘）。
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import case, update
from sqlalchemy.exc import IntegrityError, OperationalError

from src.models.entities import Audit, Job, LedgerEntry
from src.services.business import dump

MSG_CROSS_JOB = "跨作业引用：原领用不属于本作业，拒绝入账"
MSG_OVER = "累计退料数量超过原领用数量，拒绝入账"
MSG_PART = "退料备件与原领用备件不一致，拒绝入账"
MSG_NOT_ISSUE = "引用的流水不是领用，拒绝入账"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _audit_add(s, actor, action):
    """只排队审计行，交给业务事务一起提交，避免半份状态。"""
    s.add(Audit(actor=actor, action=action, created=_now()))


def _same_content(entry, job, data, ref_no):
    return (
        entry.job_id == job.id
        and entry.direction == data.direction
        and entry.part == data.part
        and entry.qty == data.qty
        and (entry.ref_entry_no or None) == ref_no
    )


def _new_entry(actor, job, data, ref_no, status="booked", reason=""):
    return LedgerEntry(
        entry_no=data.entry_no,
        job_id=job.id,
        direction=data.direction,
        part=data.part,
        qty=data.qty,
        returned_qty=0,
        ref_entry_no=ref_no,
        status=status,
        reject_reason=reason,
        created_by=actor,
        created_at=_now(),
    )


def _touch_open(s, job):
    """把作业原子置为核算中并递增流水版本；已结清则拒绝，避免结清后仍写入新流水。"""
    applied = s.execute(
        update(Job)
        .where(Job.id == job.id, Job.ledger_state != "settled")
        .values(
            ledger_version=Job.ledger_version + 1,
            ledger_state=case(
                (Job.ledger_state == "manual", "open"), else_=Job.ledger_state
            ),
            settled_at=case((Job.ledger_state == "manual", None), else_=Job.settled_at),
        )
    )
    if applied.rowcount != 1:
        s.rollback()
        raise HTTPException(409, "作业已结清，新增流水须先在流水页重开核算")
    s.refresh(job)


def _insert_entry(s, entry):
    """先落流水行；同号并发在这里撞唯一键，交由调用方判定幂等或冲突。"""
    s.add(entry)
    try:
        s.flush()
    except IntegrityError:
        s.rollback()
        return False
    return True


def _consume_issue(s, issue, qty):
    """原子占用原领用的可退数量；并发退料只有一笔能越过上限。"""
    result = s.execute(
        update(LedgerEntry)
        .where(
            LedgerEntry.id == issue.id,
            LedgerEntry.direction == "issue",
            LedgerEntry.returned_qty + qty <= LedgerEntry.qty,
        )
        .values(returned_qty=LedgerEntry.returned_qty + qty)
    )
    return result.rowcount == 1


def _bump_version(s, job_id):
    """流水状态变化后递增版本，让并发结清的条件提交失效并重算。"""
    s.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(ledger_version=Job.ledger_version + 1)
    )
    s.expire_all()


def _refresh_job_state(s, job_id):
    """作业没有已入账/暂挂流水时退回 manual，避免空账占住新挖掘。"""
    job = s.get(Job, job_id)
    if not job:
        return
    s.refresh(job)
    if job.ledger_state != "open":
        return
    accepted = (
        s.query(LedgerEntry)
        .filter(
            LedgerEntry.job_id == job_id,
            LedgerEntry.status.in_(["booked", "pending"]),
        )
        .count()
    )
    if accepted:
        return
    applied = s.execute(
        update(Job)
        .where(Job.id == job_id, Job.ledger_state == "open")
        .values(
            ledger_state="manual",
            settled_at=None,
            ledger_version=Job.ledger_version + 1,
        )
    )
    if applied.rowcount == 1:
        s.refresh(job)


def _claim_pending(s, row_id):
    """把暂挂退料原子地标记为已入账；并发补算同一笔时只有一方能成功。"""
    result = s.execute(
        update(LedgerEntry)
        .where(LedgerEntry.id == row_id, LedgerEntry.status == "pending")
        .values(status="booked", reject_reason="")
    )
    return result.rowcount == 1


def _reject_row(s, row_id, reason):
    """原子地拒绝一笔仍暂挂的退料，返回是否由本事务完成。"""
    result = s.execute(
        update(LedgerEntry)
        .where(LedgerEntry.id == row_id, LedgerEntry.status == "pending")
        .values(status="rejected", reject_reason=reason)
    )
    return result.rowcount == 1


def _resolve_pending(s, issue):
    """原领用到齐后，按登记顺序补算早到的退料。"""
    pending_rows = (
        s.query(LedgerEntry)
        .filter(
            LedgerEntry.direction == "return",
            LedgerEntry.status == "pending",
            LedgerEntry.ref_entry_no == issue.entry_no,
        )
        .order_by(LedgerEntry.id)
        .all()
    )
    touched = set()
    for row in pending_rows:
        if row.job_id != issue.job_id:
            if _reject_row(s, row.id, MSG_CROSS_JOB):
                touched.add(row.job_id)
                _audit_add(s, "system", f"补算退料 {row.entry_no}（引用 {issue.entry_no}）：rejected")
            continue
        if row.part != issue.part:
            if _reject_row(s, row.id, MSG_PART):
                touched.add(row.job_id)
                _audit_add(s, "system", f"补算退料 {row.entry_no}（引用 {issue.entry_no}）：rejected")
            continue
        if not _claim_pending(s, row.id):
            # 已被并发补算处理。
            continue
        booked = _consume_issue(s, issue, row.qty)
        if booked:
            _resolve_refs_to_non_issue(s, [row.entry_no])
        else:
            _reject_claimed(s, row.id, MSG_OVER)
            touched.add(row.job_id)
        _audit_add(
            s,
            "system",
            f"补算退料 {row.entry_no}（引用 {issue.entry_no}）："
            + ("booked" if booked else "rejected"),
        )
    for job_id in touched:
        _refresh_job_state(s, job_id)
    changed = {row.job_id for row in pending_rows}
    for job_id in changed:
        _bump_version(s, job_id)


def _reject_claimed(s, row_id, reason):
    """撤销本事务刚认领的暂挂退料（占用失败时回到拒绝态）。"""
    s.execute(
        update(LedgerEntry)
        .where(LedgerEntry.id == row_id, LedgerEntry.status == "booked")
        .values(status="rejected", reject_reason=reason)
    )
    s.expire_all()


def _resolve_refs_to_non_issue(s, entry_nos):
    """以非领用身份落账的流水（退料/拒绝/作废）不能作为退料依据，逐层拒绝引用它的暂挂退料。"""
    queue = list(entry_nos)
    seen = set()
    touched = set()
    while queue:
        no = queue.pop(0)
        if no in seen:
            continue
        seen.add(no)
        rows = (
            s.query(LedgerEntry)
            .filter(
                LedgerEntry.direction == "return",
                LedgerEntry.status == "pending",
                LedgerEntry.ref_entry_no == no,
            )
            .all()
        )
        for row in rows:
            if not _reject_row(s, row.id, MSG_NOT_ISSUE):
                # 已被并发补算或撤回处理。
                continue
            touched.add(row.job_id)
            queue.append(row.entry_no)
            _audit_add(s, "system", f"补算退料 {row.entry_no}（引用 {no}）：rejected")
    for job_id in touched:
        _refresh_job_state(s, job_id)
        _bump_version(s, job_id)


def _existing_result(entry):
    return {
        "outcome": "rejected" if entry.status == "rejected" else "duplicate",
        "entry": dump(entry),
        "reason": entry.reject_reason or "",
    }


def _reject(s, actor, job, data, ref_no, reason):
    """拒绝也留痕，但不把作业拉进核算中：拒绝笔不算有效流水。"""
    if not _insert_entry(
        s, _new_entry(actor, job, data, ref_no, status="rejected", reason=reason)
    ):
        existing = s.query(LedgerEntry).filter_by(entry_no=data.entry_no).first()
        if existing and existing.status == "rejected":
            raise HTTPException(409, existing.reject_reason or reason)
        raise HTTPException(409, f"流水号 {data.entry_no} 已存在且内容不同")
    _audit_add(s, actor, f"拒绝流水 {data.entry_no}：{reason}")
    _resolve_refs_to_non_issue(s, [data.entry_no])
    _refresh_job_state(s, job.id)
    try:
        s.commit()
    except OperationalError:
        s.rollback()
        raise HTTPException(409, "并发冲突，请刷新后重试")
    raise HTTPException(409, reason)


def _duplicate_result(s, job_id, data, ref_no):
    """并发下同号已落账：同文幂等返回，异文报冲突。"""
    existing = s.query(LedgerEntry).filter_by(entry_no=data.entry_no).first()
    if existing and _same_content(existing, s.get(Job, job_id), data, ref_no):
        result = _existing_result(existing)
        if result["outcome"] == "rejected":
            raise HTTPException(409, result["reason"])
        return result
    raise HTTPException(409, f"流水号 {data.entry_no} 已存在且内容不同")


def _commit(s, actor, job, data, ref_no, fallback):
    job_id = job.id
    try:
        s.commit()
    except IntegrityError:
        # 并发下同号同时提交：回滚本笔，按已落账的那笔判定幂等或冲突。
        s.rollback()
        return _duplicate_result(s, job_id, data, ref_no)
    except OperationalError:
        s.rollback()
        raise HTTPException(409, "并发冲突，请刷新后重试")
    if fallback["outcome"] == "rejected":
        raise HTTPException(409, fallback["reason"])
    return fallback


def _reconcile_pending(s, entry_no):
    """暂挂刚提交时原领用可能并发落账；用新事务再查一次，补算并返回最新状态。"""
    s.rollback()
    entry = s.query(LedgerEntry).filter_by(entry_no=entry_no).first()
    if not entry or entry.status != "pending":
        return None
    issue = (
        s.query(LedgerEntry)
        .filter_by(entry_no=entry.ref_entry_no, status="booked", direction="issue")
        .with_for_update()
        .first()
    )
    if not issue:
        s.rollback()
        return None
    _resolve_pending(s, issue)
    try:
        s.commit()
    except OperationalError:
        s.rollback()
        return None
    return s.query(LedgerEntry).filter_by(entry_no=entry_no).first()


def _conflict_guard(fn):
    """并发写库（锁等待、死锁）统一转为 409，避免半份状态或 500。"""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except OperationalError:
            args[0].rollback()
            raise HTTPException(409, "并发冲突，请刷新后重试")

    wrapper.__name__ = fn.__name__
    return wrapper


@_conflict_guard
def post_entry(s, actor, data):
    job = s.query(Job).filter_by(code=data.job_code).with_for_update().first()
    if not job:
        raise HTTPException(404, "维保作业不存在")
    ref_no = data.ref_entry_no or None

    existing = s.query(LedgerEntry).filter_by(entry_no=data.entry_no).first()
    if existing:
        if _same_content(existing, job, data, ref_no):
            result = _existing_result(existing)
            if result["outcome"] == "rejected":
                raise HTTPException(409, result["reason"])
            return result
        raise HTTPException(409, f"流水号 {data.entry_no} 已存在且内容不同")

    if job.ledger_state == "settled":
        raise HTTPException(409, "作业已结清，新增流水须先在流水页重开核算")

    if data.direction == "issue":
        entry = _new_entry(actor, job, data, None)
        _touch_open(s, job)
        if not _insert_entry(s, entry):
            return _duplicate_result(s, job.id, data, ref_no)
        _resolve_pending(s, entry)
        s.refresh(entry)
        _audit_add(s, actor, f"登记领用 {entry.entry_no}（{data.part}×{data.qty}）")
        return _commit(
            s, actor, job, data, ref_no, {"outcome": "booked", "entry": dump(entry)}
        )

    issue = s.query(LedgerEntry).filter_by(entry_no=ref_no).with_for_update().first()
    if not issue:
        entry = _new_entry(
            actor,
            job,
            data,
            ref_no,
            status="pending",
            reason="暂挂：原领用流水尚未登记，待到齐后补算",
        )
        _touch_open(s, job)
        if not _insert_entry(s, entry):
            return _duplicate_result(s, job.id, data, ref_no)
        _audit_add(s, actor, f"暂挂退料 {entry.entry_no}（引用 {ref_no}）")
        _resolve_refs_to_non_issue(s, [entry.entry_no])
        result = _commit(
            s, actor, job, data, ref_no, {"outcome": "pending", "entry": dump(entry)}
        )
        if result["outcome"] == "pending":
            latest = _reconcile_pending(s, entry.entry_no)
            if latest is not None:
                if latest.status == "rejected":
                    raise HTTPException(409, latest.reject_reason)
                return {"outcome": latest.status, "entry": dump(latest)}
        return result

    if issue.status != "booked":
        _reject(
            s,
            actor,
            job,
            data,
            ref_no,
            "引用的流水尚未入账（"
            + issue.status
            + "），不能作为退料依据",
        )
    if issue.direction != "issue":
        _reject(s, actor, job, data, ref_no, MSG_NOT_ISSUE)
    if issue.job_id != job.id:
        _reject(s, actor, job, data, ref_no, MSG_CROSS_JOB)
    if issue.part != data.part:
        _reject(s, actor, job, data, ref_no, MSG_PART)
    if not _consume_issue(s, issue, data.qty):
        _reject(s, actor, job, data, ref_no, MSG_OVER)

    entry = _new_entry(actor, job, data, ref_no)
    _touch_open(s, job)
    if not _insert_entry(s, entry):
        return _duplicate_result(s, job.id, data, ref_no)
    _audit_add(
        s, actor, f"登记退料 {entry.entry_no}（{data.part}×{data.qty}，引用 {ref_no}）"
    )
    _resolve_refs_to_non_issue(s, [entry.entry_no])
    return _commit(
        s, actor, job, data, ref_no, {"outcome": "booked", "entry": dump(entry)}
    )


@_conflict_guard
def void_pending(s, actor, entry_no):
    entry = s.query(LedgerEntry).filter_by(entry_no=entry_no).first()
    if not entry:
        raise HTTPException(404, "流水不存在")
    if entry.status != "pending":
        raise HTTPException(409, "只能撤回仍暂挂的退料流水")
    if not _reject_row(s, entry.id, "作废：暂挂退料已撤回，不参与补算"):
        raise HTTPException(409, "该退料已被补算或撤回，请刷新")
    _audit_add(s, actor, f"作废暂挂退料 {entry_no}")
    _resolve_refs_to_non_issue(s, [entry_no])
    _refresh_job_state(s, entry.job_id)
    _bump_version(s, entry.job_id)
    s.commit()
    s.expire_all()
    entry = s.query(LedgerEntry).filter_by(entry_no=entry_no).first()
    return {"outcome": "rejected", "entry": dump(entry), "reason": entry.reject_reason}


def _job_or_404(s, code, lock=False):
    query = s.query(Job).filter_by(code=code)
    if lock:
        query = query.with_for_update()
    job = query.first()
    if not job:
        raise HTTPException(404, "维保作业不存在")
    return job


@_conflict_guard
def settle_job(s, actor, code):
    job = _job_or_404(s, code, lock=True)
    if job.ledger_state == "settled":
        raise HTTPException(409, "作业已结清，不可重复结清")
    if job.ledger_state == "manual":
        raise HTTPException(409, "作业尚未接入领退流水，不能结清")
    entries = (
        s.query(LedgerEntry).filter_by(job_id=job.id).with_for_update().all()
    )
    # 兜底：并发下原领用可能在本作业暂挂之后才落账，结清前先补算一次。
    for ref in {e.ref_entry_no for e in entries if e.status == "pending" and e.ref_entry_no}:
        issue = (
            s.query(LedgerEntry)
            .filter_by(entry_no=ref, status="booked", direction="issue")
            .with_for_update()
            .first()
        )
        if issue:
            _resolve_pending(s, issue)
    s.flush()
    entries = (
        s.query(LedgerEntry).filter_by(job_id=job.id).with_for_update().all()
    )
    pending = [e.entry_no for e in entries if e.status == "pending"]
    if pending:
        raise HTTPException(
            409,
            "存在暂挂退料（"
            + "、".join(sorted(pending))
            + "），待原领用到齐补算后再结清",
        )
    booked_issues = [
        e for e in entries if e.status == "booked" and e.direction == "issue"
    ]
    if not booked_issues:
        raise HTTPException(409, "没有已入账的领用流水，不能结清")
    net = {}
    for entry in entries:
        if entry.status != "booked":
            continue
        delta = entry.qty if entry.direction == "issue" else -entry.qty
        net[entry.part] = net.get(entry.part, 0) + delta
    parts = sorted(part for part, qty in net.items() if qty > 0)
    settled_at = _now()
    # 以流水版本为条件原子提交：若期间有并发领退（版本已变），整笔结清不落账，
    # 避免把旧净用量当作已结清用件发布。
    applied = s.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.ledger_state == "open",
            Job.ledger_version == job.ledger_version,
        )
        .values(
            parts=parts,
            ledger_state="settled",
            settled_at=settled_at,
            ledger_version=Job.ledger_version + 1,
        )
    )
    if applied.rowcount != 1:
        s.rollback()
        raise HTTPException(409, "结清期间流水发生变化，请刷新后重试")
    _audit_add(s, actor, f"结清作业 {job.code}（净用件 {', '.join(parts) or '无'}）")
    try:
        s.commit()
    except OperationalError:
        s.rollback()
        raise HTTPException(409, "并发冲突，请刷新后重试")
    s.expire_all()
    return job_ledger(s, code)


@_conflict_guard
def reopen_job(s, actor, code):
    job = _job_or_404(s, code, lock=True)
    if job.ledger_state != "settled":
        raise HTTPException(409, "仅已结清作业可以重开核算")
    applied = s.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.ledger_state == "settled",
            Job.ledger_version == job.ledger_version,
        )
        .values(
            ledger_state="open",
            settled_at=None,
            ledger_version=job.ledger_version + 1,
        )
    )
    if applied.rowcount != 1:
        s.rollback()
        raise HTTPException(409, "重开期间流水发生变化，请刷新后重试")
    _audit_add(s, actor, f"重开核算 {job.code}（结清后补料）")
    try:
        s.commit()
    except OperationalError:
        s.rollback()
        raise HTTPException(409, "并发冲突，请刷新后重试")
    s.expire_all()
    return job_ledger(s, code)


def job_ledger(s, code):
    job = _job_or_404(s, code)
    entries = (
        s.query(LedgerEntry).filter_by(job_id=job.id).order_by(LedgerEntry.id).all()
    )
    summary = []
    for part in sorted({e.part for e in entries}):
        issued = sum(
            e.qty
            for e in entries
            if e.part == part and e.status == "booked" and e.direction == "issue"
        )
        returned = sum(
            e.qty
            for e in entries
            if e.part == part and e.status == "booked" and e.direction == "return"
        )
        summary.append(
            {
                "part": part,
                "issued": issued,
                "returned": returned,
                "net": issued - returned,
                "pending": sum(
                    1 for e in entries if e.part == part and e.status == "pending"
                ),
                "rejected": sum(
                    1 for e in entries if e.part == part and e.status == "rejected"
                ),
                "in_mining": job.ledger_state == "settled" and issued - returned > 0,
            }
        )
    issues = []
    for issue in entries:
        if issue.direction != "issue" or issue.status != "booked":
            continue
        refs = [
            e
            for e in entries
            if e.direction == "return" and e.ref_entry_no == issue.entry_no
        ]
        issues.append(
            {
                "entry_no": issue.entry_no,
                "part": issue.part,
                "qty": issue.qty,
                "returned": issue.returned_qty,
                "remaining": issue.qty - issue.returned_qty,
                "returns": [
                    {
                        "entry_no": r.entry_no,
                        "qty": r.qty,
                        "status": r.status,
                        "reason": r.reject_reason or "",
                    }
                    for r in refs
                ],
            }
        )
    return {
        "job": dump(job),
        "entries": [dump(e) for e in entries],
        "summary": summary,
        "issues": issues,
        "counts": {
            "booked": sum(1 for e in entries if e.status == "booked"),
            "pending": sum(1 for e in entries if e.status == "pending"),
            "rejected": sum(1 for e in entries if e.status == "rejected"),
        },
    }


def recent(s, limit=200):
    rows = (
        s.query(LedgerEntry, Job.code)
        .join(Job, LedgerEntry.job_id == Job.id)
        .order_by(LedgerEntry.id.desc())
        .limit(limit)
        .all()
    )
    return [{**dump(entry), "job_code": code} for entry, code in rows]
