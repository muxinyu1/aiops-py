from dataclasses import dataclass
from enum import Enum

class Type(str, Enum):
    RESTFUL = "RESTFUL"
    RPC = "RPC"

@dataclass
class RPCSource():
    pass

@dataclass
class RESTfulSource():
    pass

@dataclass
class Source():
    type: Type
    data: RPCSource | RESTfulSource
