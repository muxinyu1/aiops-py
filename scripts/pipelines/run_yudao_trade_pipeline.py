"""Pipeline: yudao-cloud trade-server fuzz."""
import pathlib
from pipeline import run_pipeline

YUDAO_ROOT = pathlib.Path("examples/yudao-cloud")
YUDAO_TRADE_SOURCES = YUDAO_ROOT / "yudao-module-mall" / "yudao-module-trade-server" / "src"

TRADE_SINKS = "sinks/yudao-logging-sinks.json"

if __name__ == "__main__":
    run_pipeline(
        project_name="yudao-cloud-trade",
        source_root=str(YUDAO_TRADE_SOURCES),
        sink_file=TRADE_SINKS,
        compose_file="examples-yml/yudao-cloud/compose.trade.real.yaml",
        base_url="http://127.0.0.1:8080",
        container_name="trace-real-yudao-cloud-trade-server",
        service_port=8080,
    )
