from abc import ABC
from dataclasses import dataclass, field


@dataclass
class Parameter(ABC):
    pass


@dataclass
class HttpParameter(Parameter):
    """Parameters for a single HTTP request to the target microservice."""

    method: str                          # e.g. "GET", "POST"
    url: str                             # full URL, e.g. "http://localhost:8080/api/users/1"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None              # JSON body for POST/PUT; None for GET