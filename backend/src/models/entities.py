from sqlalchemy import Column,Integer,String,Float,JSON,Text,ForeignKey,UniqueConstraint
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True,nullable=False);password=Column(String(200));role=Column(String(20))
class Job(Base):
 __tablename__='jobs'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);equipment=Column(String(40));parts=Column(JSON);note=Column(Text,default='')
 # manual：仍按作业表单记录用件；open：已接流水，核算中，不进入新挖掘；settled：已结清，使用净用量正件
 ledger_state=Column(String(20),nullable=False,default='manual',server_default='manual')
 settled_at=Column(String(40),nullable=True)
 # 每次流水/状态变化自增；结清以此为条件原子提交，避免与并发退料交错发布半份用件
 ledger_version=Column(Integer,nullable=False,default=0,server_default='0')
class LedgerEntry(Base):
 __tablename__='ledger_entries'
 id=Column(Integer,primary_key=True)
 entry_no=Column(String(64),unique=True,nullable=False)
 job_id=Column(Integer,ForeignKey('jobs.id'),nullable=False,index=True)
 direction=Column(String(10),nullable=False)
 part=Column(String(40),nullable=False)
 qty=Column(Integer,nullable=False)
 returned_qty=Column(Integer,nullable=False,default=0,server_default='0')
 ref_entry_no=Column(String(64),nullable=True,index=True)
 status=Column(String(20),nullable=False,default='booked',server_default='booked')
 reject_reason=Column(Text,default='')
 created_by=Column(String(100),default='')
 created_at=Column(String(40),default='')
 __table_args__=(UniqueConstraint('entry_no',name='uq_ledger_entry_no'),)
class MiningRun(Base):
 __tablename__='mining_runs'
 id=Column(Integer,primary_key=True);name=Column(String(100));equipment=Column(String(40));min_support=Column(Float);min_confidence=Column(Float);min_lift=Column(Float);state=Column(String(20),default='queued');result=Column(JSON,nullable=True);error=Column(Text,default='')
class AssociationRule(Base):
 __tablename__='association_rules'
 id=Column(Integer,primary_key=True);run_id=Column(Integer,ForeignKey('mining_runs.id'));signature=Column(String(200));antecedent=Column(JSON);consequent=Column(JSON);support=Column(Float);confidence=Column(Float);lift=Column(Float);count=Column(Integer)
 __table_args__=(UniqueConstraint('run_id','signature'),)
class Kit(Base):
 __tablename__='kits'
 id=Column(Integer,primary_key=True);name=Column(String(100));rule_id=Column(Integer,ForeignKey('association_rules.id'));parts=Column(JSON);note=Column(Text);status=Column(String(20),default='draft')
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
