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
    claimed: Optional[bool] = Query(False)

    @classmethod
    def from_row(cls, row: Row) -> "LNURLCharge":
        return cls(**dict(row))

    def lnurl(self, req: Request) -> Lnurl:
        url = req.url_for(
            name="tpos.tposlnurlcharge", lnurlcharge_id=self.id, amount=self.amount
        )
        logger.debug(url)
        return lnurl_encode(str(url))

    def lnurl_response(self, req: Request) -> LnurlWithdrawResponse:
        url = req.url_for(
            name="tpos.tposlnurlcharge.callback"
        )
        return LnurlWithdrawResponse(
            callback=ClearnetUrl(str(url), scheme="https"),
            k1=self.k1,
            minWithdrawable=MilliSatoshi(self.min_withdrawable * 1000),
            maxWithdrawable=MilliSatoshi(self.max_withdrawable * 1000),
            defaultDescription=self.title,
        )

class HashCheck(BaseModel):
    hash: bool
    lnurl: bool

class PayLnurlWData(BaseModel):
    lnurl: str
