"""Contract tests for the Seller ADK-to-A2A execution trace transport."""

from types import SimpleNamespace

from google.adk.events import Event
from google.adk.a2a.converters.part_converter import convert_genai_part_to_a2a_part
from google.genai import types
from google.protobuf.json_format import MessageToDict

from veriproof.agent_a.a2a_trace import (
    SELLER_TOOL_TRACE_METADATA_KEY,
    seller_trace_event_converter,
)
from agents.buyer_agent.tools import TracePreservingRemoteA2aAgent, _response_trace


def test_final_seller_a2a_artifact_carries_real_tool_trace_with_redaction():
    converter = seller_trace_event_converter()
    conversion_args = (None, "task-1", "context-1", convert_genai_part_to_a2a_part)
    converter(
        Event(
            author="veriproof_seller_agent",
            invocation_id="seller-invocation",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="search_licensable_assets",
                            id="search-1",
                            args={
                                "query": "바다",
                                "execution_reason": "요청 조건의 공개 작품을 조회합니다.",
                                "api_token": "must-not-leave-seller",
                            },
                        )
                    )
                ],
            ),
        ),
        *conversion_args,
    )
    converter(
        Event(
            author="veriproof_seller_agent",
            invocation_id="seller-invocation",
            content=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            name="search_licensable_assets",
                            id="search-1",
                            response={"count": 1},
                        )
                    )
                ],
            ),
        ),
        *conversion_args,
    )
    events = converter(
        Event(
            author="veriproof_seller_agent",
            invocation_id="seller-invocation",
            turn_complete=True,
            content=types.Content(role="model", parts=[types.Part(text="찾았습니다.")]),
        ),
        *conversion_args,
    )

    artifact = events[0].artifact
    trace = MessageToDict(artifact.metadata)[SELLER_TOOL_TRACE_METADATA_KEY]
    assert [record["type"] for record in trace] == ["tool_call", "tool_result"]
    assert trace[0]["tool"] == "search_licensable_assets"
    assert trace[0]["input"] == {"query": "바다", "api_token": "[redacted]"}
    assert trace[0]["reason"] == "요청 조건의 공개 작품을 조회합니다."
    assert trace[1]["output"] == {"count": 1}


def test_seller_tool_response_carries_trace_without_waiting_for_final_text():
    converter = seller_trace_event_converter()
    conversion_args = (None, "task-2", "context-2", convert_genai_part_to_a2a_part)
    converter(
        Event(
            author="veriproof_seller_agent",
            invocation_id="seller-invocation",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="get_licensable_asset",
                            id="asset-1",
                            args={"asset_id": "asset-1"},
                        )
                    )
                ],
            ),
        ),
        *conversion_args,
    )
    events = converter(
        Event(
            author="veriproof_seller_agent",
            invocation_id="seller-invocation",
            content=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            name="get_licensable_asset",
                            id="asset-1",
                            response={"status": "found"},
                        )
                    )
                ],
            ),
        ),
        *conversion_args,
    )

    trace = MessageToDict(events[0].artifact.metadata)[SELLER_TOOL_TRACE_METADATA_KEY]
    assert [record["tool"] for record in trace] == [
        "get_licensable_asset",
        "get_licensable_asset",
    ]


def test_buyer_adapter_reads_trace_from_streamed_seller_artifact():
    trace = [{"type": "tool_call", "tool": "search_licensable_assets"}]
    task = SimpleNamespace(
        metadata={},
        artifacts=[
            SimpleNamespace(metadata={SELLER_TOOL_TRACE_METADATA_KEY: trace})
        ],
        status=SimpleNamespace(message=None),
    )

    assert _response_trace((task, None)) == trace


def test_buyer_adapter_reads_trace_from_a2a_response_wrapper():
    trace = [{"type": "tool_call", "tool": "search_licensable_assets"}]
    response = SimpleNamespace(
        task=SimpleNamespace(
            artifacts=[
                SimpleNamespace(metadata={SELLER_TOOL_TRACE_METADATA_KEY: trace})
            ]
        )
    )

    assert _response_trace(response) == trace


def test_buyer_publishes_seller_trace_when_stream_update_has_no_adk_event(monkeypatch):
    trace = [{"type": "tool_call", "tool": "search_licensable_assets"}]
    response = SimpleNamespace(
        metadata={SELLER_TOOL_TRACE_METADATA_KEY: trace}
    )
    context = SimpleNamespace(
        session=SimpleNamespace(
            state={
                "temp:veriproof.demo_stream_id": "demo-stream",
                "temp:veriproof.seller_call_id": "seller-call",
            }
        )
    )
    published = []
    monkeypatch.setattr(
        "agents.buyer_agent.tools.publish",
        lambda stream_id, payload: published.append((stream_id, payload)),
    )

    assert TracePreservingRemoteA2aAgent._with_seller_trace(
        None, response, context
    ) is None
    assert published == [
        (
            "demo-stream",
            {
                "type": "seller_execution",
                "call_id": "seller-call",
                "execution": trace,
            },
        )
    ]
