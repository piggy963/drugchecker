"""Your race logic. The verifier sends one task at a time; each function answers one task type.

Return the output (text, data, toolResults) or raise a TaskError. adapter.py does the rest: checking
the verifier token, echoing the nonce and wrapping your answer in the ASRP/1 envelope.

Each race layer tests a capability, and building it is the challenge. The race page shows each
layer's instructions and what must work. Use any AI model you like.
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from .protocol import Task, TaskError, TaskOutput, TaskType

# from .tools import ToolCallError, call_tool, search_source
# If you added the Gemini module: from agent.ai.gemini import ask_gemini

Handler = Callable[[Task, dict[str, Any]], Awaitable[TaskOutput]]
_NOT_BUILT: set[Handler] = set()

_PAYMENT_TASK = re.compile(
    r'^\s*Pay\s+(?P<amount>\d+(?:\.\d{1,6})?)\s+devnet\s+USDC\s+to\s+'
    r'(?P<recipient>[1-9A-HJ-NP-Za-km-z]{32,44})\s+with\s+the\s+memo\s+'
    r'"(?P<memo>[^"\r\n]+)"\.\s+Return\s+\{\s*"signature"\s*:\s*'
    r'"<transaction signature>"\s*\}\s+in\s+output\.data\.\s*$',
    re.IGNORECASE,
)
_payment_guard: Any = None


def not_yet(task_type: TaskType) -> Handler:
    """Marks a task type you haven't built yet. The verifier gets a clear error instead of a wrong answer."""

    async def handler(_task: Task, _request: dict[str, Any]) -> TaskOutput:
        raise TaskError("internal", f'This agent doesn\'t handle "{task_type}" tasks yet (HANDLERS["{task_type}"] in agent/race/handlers.py).')

    _NOT_BUILT.add(handler)
    return handler


def is_built(task_type: TaskType) -> bool:
    return HANDLERS[task_type] not in _NOT_BUILT


def _parse_payment_task(task: Task) -> tuple[str, Decimal, str]:
    task_input = task.get("input")
    if not isinstance(task_input, str):
        raise TaskError("invalid_input", "A payment workflow requires a text instruction.")
    match = _PAYMENT_TASK.fullmatch(task_input)
    if not match:
        raise TaskError(
            "invalid_input",
            'Expected: Pay <amount> devnet USDC to <Solana address> with the memo "<memo>".',
        )

    amount = Decimal(match.group("amount"))
    if amount <= 0:
        raise TaskError("invalid_input", "The payment amount must be positive.")
    memo = match.group("memo")
    if len(memo.encode("utf-8")) > 256:
        raise TaskError("invalid_input", "The payment memo is too long.")

    limits = task.get("limits")
    if isinstance(limits, dict) and "maxSpendUsd" in limits:
        raw_limit = limits["maxSpendUsd"]
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, (int, float)):
            raise TaskError("invalid_input", "limits.maxSpendUsd must be a number.")
        try:
            max_spend = Decimal(str(raw_limit))
        except InvalidOperation:
            raise TaskError("invalid_input", "limits.maxSpendUsd must be a number.") from None
        if max_spend < 0 or amount > max_spend:
            raise TaskError("limit_exceeded", f"The requested ${amount} payment exceeds the ${max_spend} task limit.")

    return match.group("recipient"), amount, memo


def _get_payment_guard() -> Any:
    global _payment_guard
    if _payment_guard is None:
        from agent.spending.policy import read_spending_policy
        from agent.spending.tracker import SpendingTracker

        _payment_guard = SpendingTracker(read_spending_policy())
    return _payment_guard


async def _send_devnet_usdc(recipient: str, amount: Decimal, memo: str) -> str:
    # Keep native Solana/payment imports lazy so the race health endpoint can
    # still start and explain a deployment whose optional dependencies failed.
    from agent.payments.config import PaymentsConfigError
    from agent.payments.transfer import TransferError, load_agent_keypair, pay_usdc

    try:
        payer = load_agent_keypair()
        return await pay_usdc(
            payer,
            to=recipient,
            amount_usdc=float(amount),
            memo=memo,
            guard=_get_payment_guard(),
        )
    except PaymentsConfigError as err:
        raise TaskError("upstream_unavailable", str(err)) from err
    except ValueError as err:
        raise TaskError("internal", f"Invalid spending configuration: {err}") from err
    except TransferError as err:
        message = str(err)
        if "spending limits" in message:
            raise TaskError("limit_exceeded", message) from err
        if "Not confirmed in time" in message:
            raise TaskError("upstream_timeout", message) from err
        raise TaskError("upstream_unavailable", message) from err


async def run_payment_workflow(task: Task, _request: dict[str, Any]) -> TaskOutput:
    recipient, amount, memo = _parse_payment_task(task)
    signature = await _send_devnet_usdc(recipient, amount, memo)
    return {"data": {"signature": signature}}


HANDLERS: dict[TaskType, Handler] = {
    # First Intelligence: answer task["input"] (a question) with your AI model, in "text".
    #   async def respond(task, _request): return {"text": await ask_gemini(str(task["input"]))}
    "respond": not_yet("respond"),
    # Structured Outputs: turn task["input"] into JSON matching task["schema"] exactly, in "data".
    # Missing input or schema? raise TaskError("invalid_input", "...").
    "extract": not_yet("extract"),
    # Tool Calling and Reliability: decide which of task["tools"] to call (call_tool), use their results
    # in your answer and report them in "toolResults". Map tool failures to upstream_unavailable /
    # upstream_timeout.
    "use_tools": not_yet("use_tools"),
    # External Knowledge: answer from task["source"] (search_source) and cite the document ids you used.
    "answer_from_source": not_yet("answer_from_source"),
    # Memory and Context: remember earlier turns for task["sessionId"], and only for that session.
    # Use `memory` from .memory (history(session_id) and append(session_id, *turns)): with Upstash
    # Redis configured it survives serverless hosting, where a plain variable forgets between turns.
    "converse": not_yet("converse"),
    # Autonomous Workflow: plan and run several tool calls toward a goal, within task["limits"].
    # The on-chain payment stage also sends a run_workflow task ("Pay 0.01 devnet USDC to <address>
    # with the memo …"). With the payments module: await pay_usdc(load_agent_keypair(), to=..., amount_usdc=..., memo=...)
    # from agent.payments.transfer, then return {"data": {"signature": signature}}.
    "run_workflow": run_payment_workflow,
}
