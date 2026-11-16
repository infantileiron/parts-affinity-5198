from pydantic import BaseModel,Field,field_validator,model_validator
from typing import Literal,Optional
from src.constants.enums import PARTS
class Login(BaseModel):email:str;password:str
class JobInput(BaseModel):
 code:str=Field(min_length=1,max_length=60)
 equipment:Literal['泵组','压缩机','风机']
 parts:list[str]=Field(min_length=1,max_length=30)
 note:str=Field(default='',max_length=1000)
 @field_validator('parts')
 @classmethod
 def validate_parts(cls,v):
  values=sorted(set(p.strip() for p in v))
  if any(p not in PARTS for p in values):raise ValueError('备件必须来自12项标准目录')
  return values
class RunInput(BaseModel):
 name:str=Field(min_length=2,max_length=100)
 equipment:Literal['全部','泵组','压缩机','风机']='全部'
 min_support:float=Field(default=.15,gt=0,le=1,allow_inf_nan=False)
 min_confidence:float=Field(default=.6,gt=0,le=1,allow_inf_nan=False)
 min_lift:float=Field(default=1,ge=0,le=50,allow_inf_nan=False)
class KitInput(BaseModel):
 name:str=Field(min_length=2,max_length=100)
 rule_id:int=Field(gt=0)
 note:str=Field(min_length=8,max_length=2000)
class KitEdit(BaseModel):
 name:str=Field(min_length=2,max_length=100)
 note:str=Field(min_length=8,max_length=2000)
class RuleOutput(BaseModel):
 id:int;run_id:int;antecedent:list[str];consequent:list[str];support:float;confidence:float;lift:float;count:int
class EntryInput(BaseModel):
 entry_no:str=Field(min_length=1,max_length=64)
 job_code:Optional[str]=Field(default=None,min_length=1,max_length=60)
 job_id:Optional[int]=Field(default=None,gt=0)
 direction:Literal['issue','return']
 part:str
 qty:int=Field(gt=0,le=1000000)
 ref_entry_no:Optional[str]=Field(default=None,max_length=64)
 @field_validator('entry_no','ref_entry_no','job_code')
 @classmethod
 def strip_text(cls,v):
  if isinstance(v,str):
   v=v.strip()
   if not v:raise ValueError('流水号与作业编号不能为空白')
  return v
 @field_validator('direction',mode='before')
 @classmethod
 def direction_alias(cls,v):
  aliases={'领用':'issue','退料':'return','issue':'issue','return':'return'}
  if v not in aliases:raise ValueError('direction 只能是 issue（领用）或 return（退料）')
  return aliases[v]
 @field_validator('part')
 @classmethod
 def standard_part(cls,v):
  p=v.strip()
  if p not in PARTS:raise ValueError('备件必须来自12项标准目录')
  return p
 @model_validator(mode='after')
 def check_ref(cls,data):
  if not data.job_code and not data.job_id:raise ValueError('必须指定作业（job_code 或 job_id）')
  if data.job_code and data.job_id:raise ValueError('job_code 与 job_id 只能提供一个')
  if data.direction=='return' and not data.ref_entry_no:raise ValueError('退料必须引用原领用流水号')
  if data.direction=='issue' and data.ref_entry_no:raise ValueError('领用流水不能引用其他流水')
  if data.direction=='return' and data.ref_entry_no==data.entry_no:raise ValueError('退料不能引用自身流水号')
  return data
