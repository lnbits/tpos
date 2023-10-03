from sqlite3 import Row
from typing import Optional

from fastapi import Query, Request
from lnurl import Lnurl, LnurlWithdrawResponse
from lnurl import encode as lnurl_encode
from lnurl.models import ClearnetUrl, MilliSatoshi
from pydantic import BaseModel
from loguru import logger


class CreateTposData(BaseModel):
    wallet: Optional[str]
    name: Optional[str]
    currency: Optional[str]
    tip_options: str = Query(None)
    tip_wallet: str = Query(None)
    withdrawlimit: int = Query(None)
    withdrawpin: int = Query(None)
    withdrawamt: int = Query(None)
    withdrawtime: int = Query(None)


class TPoS(BaseModel):
    id: str
    wallet: str
    name: str
    currency: str
    tip_options: Optional[str]
    tip_wallet: Optional[str]
    withdrawlimit: int
    withdrawpin: int
    withdrawamt: int
    withdrawtime: int

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
    withdrawtime: int

    @classmethod
    def from_row(cls, row: Row) -> "TPoSClean":
        return cls(**dict(row))

    @property
    def withdrawamtposs(self) -> int:
        return self.withdrawlimit - self.withdrawamt


class LNURLCharge(BaseModel):
    id: str
    tpos_id: str
    amount: int = Query(None)
    claimed: bool = Query(False)

    @classmethod
    def from_row(cls, row: Row) -> "LNURLCharge":
        return cls(**dict(row))

    def lnurl(self, req: Request) -> Lnurl:
        url = str(
            req.url_for(
                "tpos.tposlnurlcharge",
                lnurlcharge_id=self.id,
                amount=self.amount,
            )
        )
        return lnurl_encode(url)

    def lnurl_response(self, req: Request) -> LnurlWithdrawResponse:
        url = str(req.url_for("tpos.tposlnurlcharge.callback"))
        assert self.amount
        amount = int(self.amount)
        return LnurlWithdrawResponse(
            callback=ClearnetUrl(url, scheme="https"),
            k1=self.k1,
            minWithdrawable=MilliSatoshi(amount * 1000),
            maxWithdrawable=MilliSatoshi(amount * 1000),
            defaultDescription=self.title,
        )


class HashCheck(BaseModel):
    hash: bool
    lnurl: bool


class PayLnurlWData(BaseModel):
    lnurl: str
