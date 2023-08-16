from sqlite3 import Row
from typing import Optional

from fastapi import Query
from pydantic import BaseModel
from loguru import logger

class CreateTposData(BaseModel):
    wallet: Optional[str]
    name: Optional[str]
    currency: Optional[str]
    tip_options: Optional[str]
    tip_wallet: Optional[str]
    withdrawlimit: Optional[int]
    withdrawpin: Optional[int]
    withdrawamt: Optional[int]
    withdrawtime: Optional[int]

class TPoS(BaseModel):
    id: str
    wallet: str
    name: str
    currency: str
    tip_options: Optional[str]
    tip_wallet: Optional[str]
    withdrawlimit: Optional[int]
    withdrawpin: Optional[int]
    withdrawamt: Optional[int]
    withdrawtime: Optional[int]

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
    tip_options: Optional[str]
    withdrawlimit: Optional[int]
    withdrawamt: Optional[int]
    withdrawtime: Optional[int]

    @classmethod
    def from_row(cls, row: Row) -> "TPoSClean":
        return cls(**dict(row))
    
    @property
    def withdrawamtposs(self) -> int:
        return self.withdrawlimit - self.withdrawamt

class LNURLCharge(BaseModel):
    id: str
    tpos_id: str
    amount: Optional[int]
    claimed: Optional[bool]

    @classmethod
    def from_row(cls, row: Row) -> "LNURLCharge":
        return cls(**dict(row))
    
class PayLnurlWData(BaseModel):
    lnurl: str
