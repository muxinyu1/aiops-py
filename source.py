from dataclasses import dataclass
from enum import Enum

class Type(Enum, str):
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
