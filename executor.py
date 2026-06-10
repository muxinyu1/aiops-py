from dataclasses import dataclass

from parameter import Parameter
from trace import Trace


@dataclass
class Executor():
    def execute(self, param: Parameter) -> Trace:
        return NotImplemented
