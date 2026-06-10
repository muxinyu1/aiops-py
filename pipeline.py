from dataclasses import dataclass


@dataclass
class Pipeline():
    def run(self):
        # 开始llm loop，重复 llm fuzz参数(fuzzer) -> 执行(executor) -> 偏差(differ) -> 重新生成参数(fuzzer)的过程
        pass