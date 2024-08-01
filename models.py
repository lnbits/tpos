from sqlite3 import Row
from typing import List, Optional

from fastapi import Request
from lnurl import Lnurl
from lnurl import encode as lnurl_encode
from pydantic import BaseModel, Field, validator


class CreateTposData(BaseModel):
    wallet: Optional[str]
    name: Optional[str]
    currency: Optional[str]
    tip_options: str = Field("[]")
    tip_wallet: str = Field("")
    withdrawlimit: int = Field(None, ge=1)
    withdrawpin: int = Field(None, ge=1)
    withdrawamt: int = Field(None, ge=0)
    withdrawtime: int = Field(0)
    withdrawtimeopt: Optional[str]
    withdrawbtwn: int = Field(10, ge=1)
    withdrawpremium: float = Field(None)
    withdrawpindisabled: bool = Field(False)
    tax_inclusive: bool = Field(True)
    tax_default: float = Field(None)


class TPoSClean(BaseModel):
    id: str
    name: str
    currency: str
    tip_options: Optional[str]
    withdrawlimit: Optional[int]
    withdrawamt: int
    withdrawtime: int
    withdrawtimeopt: Optional[str]
    withdrawbtwn: int
    withdrawpremium: Optional[float]
    withdrawpindisabled: Optional[bool]
    items: Optional[str]
    tax_inclusive: bool
    tax_default: Optional[float]

    @classmethod
    def from_row(cls, row: Row) -> "TPoSClean":
        return cls(**dict(row))

    @property
    def withdrawamtposs(self) -> int:
        return self.withdrawlimit - self.withdrawamt if self.withdrawlimit else 0


class TPoS(TPoSClean, BaseModel):
    wallet: str
    tip_wallet: Optional[str]
    withdrawpin: Optional[int]


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
    tax: Optional[float] = Field(0, ge=0.0)
    disabled: bool = False
    categories: Optional[List[str]] = []

    @validator("tax", pre=True, always=True)
    def set_default_tax(cls, v):
        return v or 0


class CreateUpdateItemData(BaseModel):
    items: List[Item]
