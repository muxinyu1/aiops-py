from dataclasses import dataclass

from difference import Difference
from llm import LLM
from parameter import Parameter
from sink import Sink
from source import Source


@dataclass
class Fuzzer:

    # fuzzer无状态
    llm: LLM

    def fuzz(
        self,
        source: Source,
        sink: Sink,
        history: list[Parameter],
        differences: list[Difference],
    ) -> Parameter:
        # TODO 构造prompt，提示llm生成fuzz的参数，如果历史不空的话，把最后一个偏差加入prompt

        return NotImplemented
