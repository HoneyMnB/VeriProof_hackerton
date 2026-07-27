"""Google ADK가 탐색할 수 있는 구매자 에이전트 패키지."""

__all__ = ["root_agent"]


def __getattr__(name: str):
    """ADK가 root_agent를 요청할 때만 전체 에이전트 그래프를 로드한다."""
    if name == "root_agent":
        from .agent import root_agent

        return root_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
