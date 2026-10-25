"""The browser UI for the drug evidence checker.

Code clients still receive JSON from GET /; see home() in server.py.
"""

from html import escape

from .modules import StudioModule


def _try_it(module_id: str, origin: str) -> list[tuple[str, str]]:
    """Copy-paste commands for what each module adds: (what it does, command)."""
    if module_id == "gemini":
        return [
            ("Ask Gemini from the command line", 'python -m agent ask "Explain an API in two sentences."'),
            ("Or over HTTP", f"curl -X POST {origin}/api/chat -H \"Content-Type: application/json\" -d '{{\"prompt\":\"hi\"}}'"),
        ]
    if module_id == "paidapi":
        return [
            ("402 Payment Required, with the price", f"curl -i {origin}/premium-data"),
            ("The agent pays, within its spending limits", "python -m agent buy"),
        ]
    if module_id == "payments":
        return [
            ("A devnet address to receive payments", "python -m agent keygen --pay-to"),
            ("Check devnet USDC balances", "python -m agent wallets"),
        ]
    if module_id == "spending":
        return [("The agent's budget so far", f"curl {origin}/api/buy")]
    if module_id == "paysh":
        return [("Pay it with the Pay.sh CLI (brew install pay)", "python -m agent paysh")]
    return []


def render_landing(modules: list[StudioModule], origin: str) -> str:
    ids = {m["id"] for m in modules}
    cards: list[str] = []
    for m in modules:
        commands = _try_it(m["id"], origin)
        code = (
            '<pre class="code">' + "\n".join(
                f'<span class="comment"># {escape(what)}</span>\n<span class="prompt">$</span> {escape(cmd)}'
                for what, cmd in commands
            ) + "</pre>"
            if commands
            else ""
        )
        led = "signal" if m.get("href") else "ok"
        cards.append(
            f'<article class="key"><span class="led led--{led}"></span>'
            f"<b>{escape(m['name'])}</b><p>{escape(m['summary'])}</p>{code}</article>"
        )
    steps: list[str] = []
    if ids & {"gemini", "payments"}:
        steps.append("Copy <code>.env.example</code> to <code>.env</code> and fill it in. Keys stay on this machine.")
    if "payments" in ids:
        steps.append(
            "Run <code>python -m agent keygen --pay-to</code>, then get free devnet USDC at "
            '<a href="https://faucet.circle.com">faucet.circle.com ↗</a>. No real money.'
        )
    steps.append("Read <code>README.md</code>: every module explains its commands, endpoints and files.")
    steps.append(
        "Make it yours: add commands in <code>agent/commands/</code> and endpoints in <code>agent/routes/</code>. "
        "Each file registers itself."
    )
    count = f"{len(modules)} {'module' if len(modules) == 1 else 'modules'}"
    body = (
        f'<div class="keys">{"".join(cards)}</div>'
        if modules
        else '<div class="empty">Nothing plugged in yet. Add endpoints as files in <code>agent/routes/</code>.</div>'
    )
    step_items = "".join(f"<li><span>{i + 1:02d}</span><span>{s}</span></li>" for i, s in enumerate(steps))
    footer = "SOLANA DEVNET · TEST MONEY ONLY" if "payments" in ids else "STARTER PROJECT"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Drug Evidence Checker</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..800&family=Martian+Mono:wght@400;500;600&display=swap">
<style>{_STYLES}</style>
</head>
<body>
<header class="header"><div class="inner"><span class="wordmark"><b>DRUGCHECKER</b><span>ChEMBL evidence agent</span></span><a class="docs-link" href="/docs">API docs ↗</a></div></header>
<main class="page">
  <span class="eyebrow">Evidence-first drug discovery</span>
  <h1 class="display">Check a molecule<br>against its target.</h1>
  <p class="lead">Submit a potency claim and the agent will resolve it against human, wild-type bioactivity records in ChEMBL, then summarize the supporting evidence.</p>
  <section class="checker" aria-labelledby="checker-title">
    <div class="checker-copy">
      <span class="step-label">Live API demonstration</span>
      <h2 id="checker-title">Test <code>POST /api/check</code></h2>
      <p>This can take a few seconds while the agent retrieves and filters source measurements.</p>
    </div>
    <form id="check-form" class="check-form">
      <label>Drug or molecule<input id="drug" name="drug" value="imatinib" required autocomplete="off"></label>
      <label>Protein target<input id="target" name="target" value="ABL1" required autocomplete="off"></label>
      <label>Activity type<select id="activity-type" name="activity_type"><option>IC50</option><option>Ki</option><option>Kd</option><option>EC50</option></select></label>
      <label>Claimed potency<div class="measure"><input id="value" name="value" type="number" value="100" min="0" step="any" required><select id="units" name="units"><option>nM</option><option>pM</option><option>uM</option><option>mM</option></select></div></label>
      <button id="submit-button" type="submit"><span>Run evidence check</span><span aria-hidden="true">→</span></button>
    </form>
    <div id="result" class="result" aria-live="polite" hidden></div>
  </section>
  <section class="module">
    <div class="strip"><span>Commands &amp; endpoints</span><span><span class="led led--ok"></span> {count}</span></div>
    <div class="body">{body}</div>
  </section>
  <section class="module">
    <div class="strip"><span>Next steps</span></div>
    <div class="body"><ol class="steps">{step_items}</ol></div>
  </section>
</main>
<footer class="footer"><div class="inner"><span>{footer}</span><span>JSON: curl {escape(origin)}/</span></div></footer>
<script>{_SCRIPT}</script>
</body>
</html>"""


_STYLES = """
:root{--canvas:#E9E9E4;--surface:#fff;--strip:#F5F5F1;--diagram:#FAFAF8;--ink:#161616;--ink-2:#3D3D3A;--muted:#6A6A64;--hairline:#E2E2DC;--rest:#CFCFC8;--signal:#5A3CF0;--ok:#10A36D;--paper:#EDEDE8;--sans:"Archivo",system-ui,sans-serif;--mono:"Martian Mono",ui-monospace,monospace}
*{box-sizing:border-box}html,body{margin:0}
body{min-height:100vh;display:flex;flex-direction:column;background:var(--canvas);background-image:radial-gradient(#CFCFC8 1px,transparent 1px);background-size:16px 16px;color:var(--ink);font-family:var(--sans);-webkit-font-smoothing:antialiased}
a{color:var(--signal)}code{font-family:var(--mono);font-size:.88em;background:var(--strip);border:1px solid var(--hairline);border-radius:3px;padding:1px 5px}
.header,.footer{background:var(--ink);color:var(--paper)}.inner{max-width:1080px;margin:0 auto;padding:0 clamp(12px,3vw,32px);min-height:56px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.wordmark{display:flex;align-items:baseline;gap:10px}.wordmark b{font-weight:800;font-stretch:125%;font-size:14px;letter-spacing:.02em}.wordmark span{font:400 10px var(--mono);color:#8E8E87}.docs-link{font:500 10px var(--mono);color:#d7d7d0;text-decoration:none}.docs-link:hover{text-decoration:underline}
.footer{color:#8E8E87;font:400 10.5px var(--mono)}.footer .inner{min-height:48px}
.page{width:100%;max-width:1080px;margin:0 auto;padding:clamp(28px,4vw,48px) clamp(12px,3vw,32px) 64px;display:flex;flex-direction:column;gap:16px;flex:1}
.eyebrow{font:500 10.5px var(--mono);letter-spacing:.06em;color:var(--muted);text-transform:uppercase}
.display{margin:0;font-weight:800;font-stretch:125%;font-size:clamp(34px,5vw,60px);line-height:.95;letter-spacing:-.035em}
.lead{margin:0 0 8px;max-width:600px;font-size:16px;line-height:1.5;color:var(--ink-2)}
.checker{display:grid;grid-template-columns:minmax(220px,.75fr) minmax(360px,1.25fr);gap:24px;padding:clamp(18px,3vw,30px);background:var(--ink);color:var(--paper);border:1px solid var(--ink);border-radius:4px;box-shadow:0 5px 0 #bdbdb5}.checker-copy{align-self:start}.step-label{font:500 10px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:#a8a89f}.checker h2{margin:12px 0 8px;font-size:clamp(21px,3vw,30px);line-height:1.1}.checker h2 code{color:#a897ff;background:#242424;border-color:#444}.checker-copy p{margin:0;color:#bcbcb5;font-size:13px;line-height:1.55}.check-form{display:grid;grid-template-columns:1fr 1fr;gap:14px}.check-form label{display:flex;flex-direction:column;gap:7px;font:500 10px var(--mono);letter-spacing:.03em;text-transform:uppercase;color:#bcbcb5}.check-form input,.check-form select{width:100%;min-width:0;border:1px solid #555;border-radius:3px;background:#242424;color:#fff;padding:11px 12px;font:500 14px var(--sans);outline:none}.check-form input:focus,.check-form select:focus{border-color:#8d78ff;box-shadow:0 0 0 2px rgba(90,60,240,.35)}.measure{display:grid;grid-template-columns:1fr 78px;gap:7px}.check-form button{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;border:0;border-radius:3px;background:var(--signal);color:#fff;padding:13px 15px;font:700 13px var(--sans);cursor:pointer}.check-form button:hover{background:#6b4ff4}.check-form button:disabled{cursor:wait;opacity:.65}.result{grid-column:1/-1;border-top:1px solid #40403c;padding-top:20px}.result-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:9px}.result-head strong{font-size:22px}.badge{padding:5px 8px;border-radius:2px;background:#2b654f;color:#8af0c6;font:600 9px var(--mono);letter-spacing:.06em;text-transform:uppercase}.badge--error{background:#6d3030;color:#ffc4c4}.result p{margin:0;color:#d1d1ca;font-size:14px;line-height:1.6}.result details{margin-top:15px}.result summary{cursor:pointer;color:#a8a89f;font:500 10px var(--mono);text-transform:uppercase}.result pre{max-height:300px;overflow:auto;margin:10px 0 0;padding:12px;border:1px solid #444;border-radius:3px;background:#101010;color:#cacac3;font:400 10px/1.55 var(--mono);white-space:pre-wrap}
.module{background:var(--surface);border:1px solid var(--ink);border-radius:4px;overflow:hidden}
.strip{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:9px 14px;border-bottom:1px solid var(--ink);background:var(--strip);font:500 10.5px var(--mono);letter-spacing:.04em;text-transform:uppercase}
.strip>span:last-child{color:var(--muted);display:flex;align-items:center;gap:7px}.body{padding:14px}
.led{display:inline-block;width:8px;height:8px;border-radius:1px;background:#D9D9D2}.led--signal{background:var(--signal)}.led--ok{background:var(--ok)}
.keys{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:8px}
.key{position:relative;display:flex;flex-direction:column;gap:8px;padding:14px 12px 12px;border:1px solid var(--rest);border-radius:4px;background:#fff;box-shadow:0 2px 0 var(--rest)}
.key .led{position:absolute;top:12px;right:12px;width:10px;height:10px}.key b{font-size:15px;padding-right:18px}.key p{margin:0;font-size:13px;line-height:1.45;color:var(--ink-2)}
.code{margin:4px 0 0;padding:10px 12px;background:var(--ink);color:#D4D4CE;border-radius:4px;font:400 11.5px/1.7 var(--mono);white-space:pre;overflow-x:auto}.prompt{color:#5EE0A8}.comment{color:#7A7A73}
.empty{padding:24px 16px;border:1.5px dashed #AFAFA8;border-radius:4px;text-align:center;color:var(--muted);font-size:14px}
.steps{margin:0;padding:0;list-style:none}.steps li{display:grid;grid-template-columns:28px 1fr;gap:10px;padding:11px 0;border-bottom:1px solid var(--hairline);font-size:14px;line-height:1.5;color:var(--ink-2)}.steps li:last-child{border-bottom:0}.steps li>span:first-child{font:600 10px/22px var(--mono);color:var(--muted)}
@media(max-width:720px){.checker{grid-template-columns:1fr}.check-form{grid-template-columns:1fr}.wordmark span{display:none}}
"""


_SCRIPT = r"""
const form = document.getElementById("check-form");
const result = document.getElementById("result");
const button = document.getElementById("submit-button");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  button.disabled = true;
  button.firstElementChild.textContent = "Checking ChEMBL…";
  result.hidden = false;
  result.innerHTML = '<div class="result-head"><strong>Working</strong><span class="badge">Live request</span></div><p>Resolving the molecule and target, then filtering relevant measurements.</p>';

  const payload = {
    drug: document.getElementById("drug").value.trim(),
    target: document.getElementById("target").value.trim(),
    activity_type: document.getElementById("activity-type").value,
    value: Number(document.getElementById("value").value),
    units: document.getElementById("units").value
  };

  try {
    const response = await fetch("/api/check", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || data.detail || `Request failed (${response.status})`);

    const verdict = data.result?.verdict || "Complete";
    result.replaceChildren();
    const head = document.createElement("div");
    head.className = "result-head";
    const title = document.createElement("strong");
    title.textContent = verdict;
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = "ChEMBL checked";
    head.append(title, badge);
    const explanation = document.createElement("p");
    explanation.textContent = data.explanation || "The evidence check completed successfully.";
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "View complete API response";
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(data, null, 2);
    details.append(summary, pre);
    result.append(head, explanation, details);
  } catch (error) {
    result.replaceChildren();
    const head = document.createElement("div");
    head.className = "result-head";
    const title = document.createElement("strong");
    title.textContent = "Could not complete check";
    const badge = document.createElement("span");
    badge.className = "badge badge--error";
    badge.textContent = "API error";
    head.append(title, badge);
    const message = document.createElement("p");
    message.textContent = error instanceof Error ? error.message : "Unexpected request failure";
    result.append(head, message);
  } finally {
    button.disabled = false;
    button.firstElementChild.textContent = "Run evidence check";
  }
});
"""
