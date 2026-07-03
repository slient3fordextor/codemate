from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    checks: dict[str, str]


class VersionResponse(BaseModel):
    name: str
    version: str
    api_version: str
