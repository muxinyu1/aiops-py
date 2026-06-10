from dataclasses import dataclass

from sink import Sink
from source import Source

@dataclass
class TraceNode():
    src_file: str # path
    line_number: int
    content: str
    fucntion: str
    # ...

@dataclass
class Trace():
    source: Source
    sink: Sink
    nodes: list[TraceNode]