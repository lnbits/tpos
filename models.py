from sqlite3 import Row
from typing import Optional

from fastapi import Query
from pydantic import BaseModel
from loguru import logger

class CreateTposData(BaseModel):
    wallet: Optional[str]
    name: Optional[str]
    currency: Optional[str]
    tip_options: Optional[str] = Query(None)
    tip_wallet: Optional[str] = Query(None)
    withdrawlimit: Optional[int] = Query(None)
    withdrawpin: Optional[int] = Query(None)
    withdrawamt: Optional[int] = Query(None)
    withdrawtime: Optional[int] = Query(None)

class TPoS(BaseModel):
    id: str
    wallet: str
    name: str
    currency: str
    tip_options: Optional[str] = Query(None)
    tip_wallet: Optional[str] = Query(None)
    withdrawlimit: Optional[int] = Query(None)
    withdrawpin: Optional[int] = Query(None)
    withdrawamt: Optional[int] = Query(None)
    withdrawtime: Optional[int] = Query(None)

    @classmethod
    def from_row(cls, row: Row) -> "TPoS":
        return cls(**dict(row))

    @property
    def withdrawamtposs(self) -> int:
        return self.withdrawlimit - self.withdrawamt

class TPoSClean(BaseModel):
    id: str
    name: str
    currency: str
    tip_options: Optional[str] = Query(None)
    withdrawlimit: Optional[int] = Query(None)
    withdrawamt: Optional[int] = Query(None)
    withdrawtime: Optional[int] = Query(None)

    @classmethod
    def from_row(cls, row: Row) -> "TPoSClean":
        return cls(**dict(row))
    
    @property
    def withdrawamtposs(self) -> int:
        return self.withdrawlimit - self.withdrawamt

class LNURLCharge(BaseModel):
    id: str
    tpos_id: str
    amount: Optional[int] = Query(None)
    claimed: Optional[bool] = Query(False)

    @classmethod
    def from_row(cls, row: Row) -> "LNURLCharge":
        return cls(**dict(row))
    
class PayLnurlWData(BaseModel):
    lnurl: str

# comment