from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.entities import Job
from src.services.business import dump
def save_job(s,data,i=None):
 r=s.get(Job,i) if i else Job()
 if not r:raise HTTPException(404,'维保作业不存在')
 if r.ledger_state in ('open','settled'):
  # 用件由流水净用量决定；编码/设备决定挖掘范围，接入流水后同样冻结。
  same=r.code==data.code and r.equipment==data.equipment and (r.parts or [])==data.parts
  if not same:raise HTTPException(409,'作业已接入领退流水，用件不再手工修改；结清后补料请先在流水页重开核算')
  r.note=data.note
 else:
  for k,v in data.model_dump().items():setattr(r,k,v)
 s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'作业编号重复')
 return dump(r)
