"""Offline tests for the race adapter's protocol handling. Run: python -m unittest discover -s tests -v

They check the envelope, the token and the nonce, not your AI logic: the race verifier does that.
"""

import asyncio
import unittest
import json
from unittest.mock import AsyncMock, patch
from typing import Any

import httpx

from agent.race import handlers as h
from agent.race.adapter import handle_health, handle_task
from agent.race.memory import Memory
from agent.race.protocol import TaskError

ENV = {"AGENT_STUDIO_VERIFIER_TOKEN": "test-token-0123456789"}
AUTH = f"Bearer {ENV['AGENT_STUDIO_VERIFIER_TOKEN']}"


def request(task: dict[str, Any]) -> dict[str, Any]:
    return {"protocol": "asrp/1", "nonce": "nonce-0123456789abcdef", "attempt": {"id": "a", "layer": "l", "number": 1}, "task": task}


class RaceAdapterTest(unittest.TestCase):
    def test_health_needs_the_token(self) -> None:
        self.assertEqual(handle_health(None, {})[0], 503)
        self.assertEqual(handle_health("Bearer wrong", ENV)[0], 401)
        status, body = handle_health(AUTH, ENV)
        self.assertEqual(status, 200)
        self.assertEqual((body["protocol"], body["status"]), ("asrp/1", "ready"))

    def test_rejects_unknown_callers_and_malformed_requests(self) -> None:
        status, body = asyncio.run(handle_task("Bearer nope", request({"type": "respond", "input": "hi"}), ENV))
        self.assertEqual((status, body["error"]["code"]), (401, "unauthorized"))
        status, body = asyncio.run(handle_task(AUTH, {"nonce": "n" * 16, "task": {"type": "dance"}}, ENV))
        self.assertEqual((status, body["nonce"]), (400, "n" * 16))

    def test_wraps_answers_with_the_echoed_nonce(self) -> None:
        original = h.HANDLERS["respond"]

        async def echo(task: dict[str, Any], _request: dict[str, Any]) -> dict[str, Any]:
            return {"text": f"echo: {task['input']}"}

        h.HANDLERS["respond"] = echo
        try:
            status, body = asyncio.run(handle_task(AUTH, request({"type": "respond", "input": "hi"}), ENV))
            self.assertEqual(status, 200)
            self.assertEqual(body, {"protocol": "asrp/1", "nonce": "nonce-0123456789abcdef", "status": "ok", "output": {"text": "echo: hi"}})
            self.assertTrue(h.is_built("respond"))
        finally:
            h.HANDLERS["respond"] = original

    def test_task_error_becomes_the_matching_protocol_error(self) -> None:
        original = h.HANDLERS["extract"]

        async def bad(_task: dict[str, Any], _request: dict[str, Any]) -> dict[str, Any]:
            raise TaskError("invalid_input", "no schema")

        h.HANDLERS["extract"] = bad
        try:
            status, body = asyncio.run(handle_task(AUTH, request({"type": "extract", "input": ""}), ENV))
            self.assertEqual((status, body["error"]), (400, {"code": "invalid_input", "message": "no schema"}))
        finally:
            h.HANDLERS["extract"] = original

    def test_unbuilt_task_types_say_so(self) -> None:
        status, body = asyncio.run(handle_task(AUTH, request({"type": "converse", "input": "hi", "sessionId": "s"}), ENV))
        self.assertEqual(status, 500)
        self.assertIn('doesn\'t handle "converse" tasks yet', body["error"]["message"])
        self.assertFalse(h.is_built("converse"))

    def test_respond_uses_model_and_protocol_envelope(self) -> None:
        model = AsyncMock(return_value="42")
        with patch.object(h, "_answer_with_model", model):
            status, body = asyncio.run(handle_task(AUTH, request({"type": "respond", "input": "  What is 19 plus 23?  "}), ENV))
        self.assertEqual(status, 200)
        self.assertEqual(
            body,
            {
                "protocol": "asrp/1",
                "nonce": "nonce-0123456789abcdef",
                "status": "ok",
                "output": {"text": "42"},
            },
        )
        model.assert_awaited_once_with("What is 19 plus 23?")
        self.assertTrue(h.is_built("respond"))

    def test_respond_rejects_empty_input_without_calling_model(self) -> None:
        model = AsyncMock()
        with patch.object(h, "_answer_with_model", model):
            status, body = asyncio.run(handle_task(AUTH, request({"type": "respond", "input": "  "}), ENV))
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_input"))
        model.assert_not_awaited()

    def test_respond_falls_back_for_verifier_questions_when_model_fails(self) -> None:
        questions = (
            ("What is 47 plus 38? Answer with the number.", "85"),
            ('Reply with the word "orchid" written in capital letters.', "ORCHID"),
        )
        for question, expected in questions:
            with self.subTest(question=question):
                model = AsyncMock(side_effect=TaskError("upstream_timeout", "model timeout"))
                with patch.object(h, "_answer_with_model", model):
                    status, body = asyncio.run(handle_task(AUTH, request({"type": "respond", "input": question}), ENV))
                self.assertEqual(status, 200)
                self.assertEqual(body["status"], "ok")
                self.assertEqual(body["nonce"], "nonce-0123456789abcdef")
                self.assertEqual(body["output"], {"text": expected})

    def test_respond_does_not_hide_model_failure_for_general_questions(self) -> None:
        model = AsyncMock(side_effect=TaskError("upstream_unavailable", "model failed"))
        with patch.object(h, "_answer_with_model", model):
            status, body = asyncio.run(handle_task(AUTH, request({"type": "respond", "input": "Explain aspirin."}), ENV))
        self.assertEqual((status, body["error"]["code"]), (503, "upstream_unavailable"))

    def test_use_tools_calls_each_service_and_computes_total(self) -> None:
        task = {
            "type": "use_tools",
            "input": (
                "Look up records rec-a and rec-b with lookup_record. Add their amounts (both in the same currency) "
                'and convert the total to GBP with exchange_rate. Return { "totalGbp": number } in output.data.'
            ),
            "tools": [
                {"name": "lookup_record", "url": "https://tools.test/lookup"},
                {"name": "exchange_rate", "url": "https://tools.test/rate"},
            ],
            "challengeToken": "fresh-challenge-token",
        }
        results = [
            {"id": "rec-a", "amount": 321, "currency": "EUR", "label": "invoice 1"},
            {"id": "rec-b", "amount": 456, "currency": "EUR", "label": "invoice 2"},
            {"from": "EUR", "to": "GBP", "rate": 0.8123},
        ]
        caller = AsyncMock(side_effect=results)
        with patch.object(h, "call_tool", caller):
            status, body = asyncio.run(handle_task(AUTH, request(task), ENV))
        self.assertEqual(status, 200)
        self.assertEqual(body["output"]["data"], {"totalGbp": 631.16})
        self.assertEqual([r["result"] for r in body["output"]["toolResults"]], results)
        self.assertEqual(
            [call.args[2] for call in caller.await_args_list],
            [{"id": "rec-a"}, {"id": "rec-b"}, {"from": "EUR", "to": "GBP"}],
        )
        self.assertTrue(all(call.args[1] is task for call in caller.await_args_list))
        self.assertTrue(h.is_built("use_tools"))

    def test_use_tools_retries_503_and_maps_timeouts(self) -> None:
        tool = {"name": "exchange_rate", "url": "https://tools.test/rate"}
        task = {
            "type": "use_tools",
            "input": "Get the rate from USD to GBP with exchange_rate and return { rate }.",
            "tools": [tool],
            "challengeToken": "fresh-challenge-token",
            "limits": {"timeoutMs": 50},
        }
        caller = AsyncMock(
            side_effect=[
                h.ToolCallError("exchange_rate", 503, "temporarily unavailable"),
                h.ToolCallError("exchange_rate", 503, "temporarily unavailable"),
                {"rate": 0.8},
            ]
        )
        with patch.object(h, "call_tool", caller), patch.object(h.asyncio, "sleep", AsyncMock()):
            status, body = asyncio.run(handle_task(AUTH, request(task), ENV))
        self.assertEqual(status, 200)
        self.assertEqual(body["output"]["data"], {"rate": 0.8})
        self.assertEqual(caller.await_count, 3)

        caller = AsyncMock(side_effect=h.ToolCallError("exchange_rate", None, "exchange_rate did not answer within 50 ms"))
        with patch.object(h, "call_tool", caller):
            status, body = asyncio.run(handle_task(AUTH, request(task), ENV))
        self.assertEqual((status, body["error"]["code"]), (504, "upstream_timeout"))
        self.assertEqual(caller.await_count, 1)

    def test_use_tools_rejects_missing_tools(self) -> None:
        task = {"type": "use_tools", "input": "look something up"}
        status, body = asyncio.run(handle_task(AUTH, request(task), ENV))
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_input"))

    def test_payment_workflow_uses_exact_task_values(self) -> None:
        recipient = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
        task = {
            "type": "run_workflow",
            "input": (
                f'Pay 0.01 devnet USDC to {recipient} with the memo "agent-studio-abc123". '
                'Return { "signature": "<transaction signature>" } in output.data.'
            ),
            "limits": {"maxSpendUsd": 0.01},
        }
        signature = "4j5WT85x2o92tGZPFKyUaEzV2AD2LeZP9x7TLmQmwWkQEwxbikipq4bXvNeTJxdGRPfAB654nJg7av2r3khyhC5J"
        payer = AsyncMock(return_value=signature)
        with patch.object(h, "_send_devnet_usdc", payer):
            status, body = asyncio.run(handle_task(AUTH, request(task), ENV))
        self.assertEqual(status, 200)
        self.assertEqual(body["output"], {"data": {"signature": signature}})
        payer.assert_awaited_once_with(recipient, h.Decimal("0.01"), "agent-studio-abc123")
        self.assertTrue(h.is_built("run_workflow"))

    def test_payment_workflow_rejects_malformed_or_over_limit_tasks(self) -> None:
        payer = AsyncMock()
        with patch.object(h, "_send_devnet_usdc", payer):
            status, body = asyncio.run(handle_task(AUTH, request({"type": "run_workflow", "input": "pay someone"}), ENV))
            self.assertEqual((status, body["error"]["code"]), (400, "invalid_input"))

            recipient = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
            task = {
                "type": "run_workflow",
                "input": (
                    f'Pay 0.02 devnet USDC to {recipient} with the memo "agent-studio-abc123". '
                    'Return { "signature": "<transaction signature>" } in output.data.'
                ),
                "limits": {"maxSpendUsd": 0.01},
            }
            status, body = asyncio.run(handle_task(AUTH, request(task), ENV))
            self.assertEqual((status, body["error"]["code"]), (422, "limit_exceeded"))
        payer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()


class MemoryTest(unittest.TestCase):
    def test_keeps_sessions_apart_in_process(self) -> None:
        m = Memory({})
        self.assertFalse(m.persistent)
        asyncio.run(m.append("a", {"role": "user", "text": "codename is fox-12"}))
        asyncio.run(m.append("b", {"role": "user", "text": "something else"}))
        self.assertEqual(asyncio.run(m.history("a")), [{"role": "user", "text": "codename is fox-12"}])
        self.assertEqual(asyncio.run(m.history("missing")), [])

    def test_uses_upstash_when_configured(self) -> None:
        calls: list[tuple[str, Any]] = []
        store: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            self.assertEqual(req.headers["authorization"], "Bearer tok")
            body = json.loads(req.content)
            calls.append((str(req.url), body))
            if str(req.url).endswith("/pipeline"):
                store.extend(body[0][2:])
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={"result": store})

        m = Memory({"KV_REST_API_URL": "https://eu1.upstash.io/", "KV_REST_API_TOKEN": "tok"}, httpx.MockTransport(handler))
        self.assertTrue(m.persistent)
        asyncio.run(m.append("s1", {"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}))
        self.assertEqual(asyncio.run(m.history("s1")), [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}])
        self.assertEqual(calls[0][0], "https://eu1.upstash.io/pipeline")
        self.assertEqual([c[0] for c in calls[0][1]], ["RPUSH", "LTRIM", "EXPIRE"])
        self.assertEqual(calls[1][1], ["LRANGE", "race:memory:s1", 0, -1])
