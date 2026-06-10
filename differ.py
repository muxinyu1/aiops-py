from dataclasses import dataclass
from trace import Trace

from difference import Difference


@dataclass
class Differ():
    def diff(self, trace: Trace, target_trace: Trace) -> Difference:
        return NotImplemented
    pass