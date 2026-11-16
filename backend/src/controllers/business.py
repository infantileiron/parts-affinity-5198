from fastapi import HTTPException
from src.models.entities import MiningRun,AssociationRule,Kit
def ensure_run(s,i):
 r=s.get(MiningRun,i)
 if not r:raise HTTPException(404,'挖掘批次不存在')
 return r
def ensure_rule(s,i):
 r=s.get(AssociationRule,i)
 if not r:raise HTTPException(404,'来源规则不存在')
 return r
def ensure_kit(s,i):
 r=s.get(Kit,i)
 if not r:raise HTTPException(404,'备件包不存在')
 return r
def ensure_open_job(s,i):
 from src.models.entities import Job
 r=s.get(Job,i)
 if not r:raise HTTPException(404,'维保作业不存在')
 if r.ledger_state=='settled':raise HTTPException(409,'作业已结清，不能手改用件')
 return r
