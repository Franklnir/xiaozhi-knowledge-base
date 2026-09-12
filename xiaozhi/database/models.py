from typing import List, Optional, Union

from pydantic import BaseModel


class SearchResponse(BaseModel):
    search_keyword: str
    message: str
    results: List[dict]


class ApiImportPreviewRequest(BaseModel):
    api_url: str


class ApiImportCommitRequest(BaseModel):
    api_url: str
    categories: List[str]


class LiveApiPreviewRequest(BaseModel):
    api_url: str
    previous_hash: str = ""


class AccountSearchResponse(BaseModel):
    accounts: List[dict]


class UserThemeCommand(BaseModel):
    theme: str


class SmartHomeRelayCommand(BaseModel):
    channel: int
    state: bool


class SmartHomeAllCommand(BaseModel):
    state: bool


class RealRelayApiGenerateRequest(BaseModel):
    nama_tempat: str


class RealRelayControlCommand(BaseModel):
    room_id: int
    relay_number: int
    command: str


class RealRelayDeviceStatusCommand(BaseModel):
    token: str = ""
    relay: Optional[int] = None
    relay_number: Optional[int] = None
    status: Optional[Union[str, bool, int]] = None
    command: Optional[Union[str, bool, int]] = None
    state: Optional[Union[str, bool, int]] = None
    command_id: str = ""
