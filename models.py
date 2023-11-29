from sqlite3 import Row
from typing import Optional, Any, List

from fastapi import Request
from lnurl import Lnurl, LnurlWithdrawResponse
from lnurl import encode as lnurl_encode
from lnurl.models import ClearnetUrl, MilliSatoshi
from pydantic import BaseModel, Field


class CreateTposData(BaseModel):
    wallet: Optional[str]
    name: Optional[str]
    currency: Optional[str]
    tip_options: str = Field(None)
    tip_wallet: str = Field(None)
    withdrawlimit: int = Field(None, ge=1)
    withdrawpin: int = Field(None, ge=1)
    withdrawamt: int = Field(None, ge=0)
    withdrawtime: int = Field(0)
    withdrawbtwn: int = Field(10, ge=1)
    withdrawpremium: float = Field(None)


class TPoS(BaseModel):
    id: str
    wallet: str
    name: str
    currency: str
    tip_options: Optional[str]
    tip_wallet: Optional[str]
    withdrawlimit: Optional[int]
    withdrawpin: Optional[int]
    withdrawamt: int
    withdrawtime: int
    withdrawbtwn: int
    withdrawpremium: Optional[float]
    items: Optional[str]

    @classmethod
    def from_row(cls, row: Row) -> "TPoS":
        return cls(**dict(row))

    @property
    def withdrawamtposs(self) -> int:
        return self.withdrawlimit - self.withdrawamt if self.withdrawlimit else 0


class TPoSClean(BaseModel):
    id: str
    name: str
    currency: str
    tip_options: Optional[str]
    withdrawlimit: Optional[int]
    withdrawamt: int
    withdrawtime: int
    withdrawbtwn: int
    withdrawpremium: Optional[float]
    items: Optional[str]

    @classmethod
    def from_row(cls, row: Row) -> "TPoSClean":
        return cls(**dict(row))

    @property
    def withdrawamtposs(self) -> int:
        return self.withdrawlimit - self.withdrawamt if self.withdrawlimit else 0


class LNURLCharge(BaseModel):
    id: str
    tpos_id: str
    amount: int = Field(None)
    claimed: bool = Field(False)

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


class Item(BaseModel):
    image: Optional[str]
    price: float
    title: str
    description: Optional[str]
    tax: Optional[float] = 0.0
    disabled: bool = False


class CreateUpdateItemData(BaseModel):
    items: List[Item]
