import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
url=os.environ['DATABASE_URL']
extra={'connect_args':{'check_same_thread':False,'timeout':30}} if url.startswith('sqlite') else {}
engine=create_engine(url,pool_pre_ping=True,**extra)
Session=sessionmaker(bind=engine,expire_on_commit=False)
Base=declarative_base()
def db():
 with Session() as s: yield s
