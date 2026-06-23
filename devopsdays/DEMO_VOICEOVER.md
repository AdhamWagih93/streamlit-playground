# 🎙️ Demo Voice-Over Script · ~5 minutes
### Screen-capture walkthrough of the "Day of a DevOps Engineer · 2026" suite

> **Purpose:** a self-contained ~5-min recorded demo (screen capture + voice-over). Faster and
> tighter than the live talk — you're *narrating clicks*, not teaching deeply.
>
> **Setup:** `docker compose up --build` → full-screen browser at `http://localhost:8080`.
> Record at 1280×900+. Each line below is roughly what you say while doing the **[ACTION]**.
> Timecodes are cumulative. Aim for a calm, confident pace (~150 wpm).

---

### 0:00 – 0:30 · OPEN  *(on the hub)*
**[ACTION: Mission Control hub on screen, cursor still.]**

"This is a day in the life of a DevOps engineer in 2026 — built as seven small, interactive
demos. Each one is a hand-written web app in its own nginx container. **No Streamlit this year.**
Three humans on call here, eleven autonomous agents, and most of today's deploys were started
**by a machine.** Let me show you the day."

---

### 0:30 – 1:00 · 30-SECOND RECAP OF 2025  *(click "◂ Previously, in 2025")*
**[ACTION: open recap, click "▸ Replay 2025 → 2026", let the montage stream.]**

"Quick recap of last year's foundation. We built an internal **platform**. We hosted **open models
on-prem with Ollama**. We generated **UIs with Streamlit and Python**. We adopted **MCP** to give
models tools. And we wired our first **agentic workflows in n8n**. *(beat)* Every one of those grew
into something this year — the platform now runs agents, the on-prem models now guard our privacy,
and those little n8n workflows? They grew up into the agents in this next demo."

**[ACTION: click "Begin the 2026 day ▸" → hub.]**

---

### 1:00 – 1:45 · ST-01 · STANDUP WITH A MACHINE  *(open ST-01)*
**[ACTION: click "▸ Next proposal".]**

"Maya's standup includes an AI teammate, ARIA, that assigns work to itself. Her job is to decide
what it's allowed to do. First request — a safe, reversible dependency bump. **That's toil, let
the agent own it.**"
**[ACTION: click "✓ Approve".]**

**[ACTION: click "▸ Next proposal".]**
"Next — an *irreversible* database migration the agent is only 61% sure about. **That's not an
approve.**"
**[ACTION: click "↻ Send back".]**
"I send it back for a rollback plan. Trust goes up, because I taught it where the bar is. **An
agent isn't a tool you operate — it's a teammate you delegate to.**"

---

### 1:45 – 2:30 · ST-02 · THE GUARDRAIL ROOM  *(back to hub, open ST-02)*
**[ACTION: click "▸ Run governance".]**

"A payments deploy wants to ship itself. In 2026, governance is **code that runs on every deploy.**
Watch the gates — policy, tests… then the **ethics gate stops everything.** It flagged a possible
unfair pricing impact, and it's not sure — so it hands the call to a human."
**[ACTION: click "⛔ Hold & escalate".]**
"And my decision gets **signed into an immutable audit trail.** Autonomy doesn't mean no rules.
It means the rules run every time — and trust is **auditable.**"

---

### 2:30 – 3:10 · ST-03 · THE PULSE  *(hub → ST-03)*
**[ACTION: point at Sam's red card, then click "▸ Let the system rebalance".]**

"Now the best part — automation pointed at **people**, not servers. The system noticed an engineer,
Sam, heading for burnout **before Sam did.** One click: it reroutes on-call, hands toil to an agent,
and **protects tomorrow morning as recovery time** — and tells the manager with a wellbeing note,
not a performance flag. **In 2026, sustainable pace is an engineering metric.**"

---

### 3:10 – 3:50 · ST-04 · NO BLAME, JUST SIGNAL  *(hub → ST-04)*
**[ACTION: timeline on "Blame", then flip the lens to "Blameless".]**

"Checkout broke for eight minutes. Here's the incident review — with a switch. On **'blame,'** we
find the guilty human… and people start hiding mistakes. **Watch the same facts on 'blameless.'**"
**[ACTION: flip lens.]**
"Now the question becomes *which guardrail was missing* — and the fix is a schema gate and a
canary, not 'be more careful.' **Safe teams aren't soft. They're faster, because nothing stays
hidden.**"

---

### 3:50 – 4:25 · ST-05 · VIBE SHIPPING  *(hub → ST-05)*
**[ACTION: click "▸ Vibe it"; answer the clarifying question when it appears.]**

"Maya describes a feature in plain English. The pipeline plans, writes code and tests — but **it
stops to ask her a clarifying question.** The human still steers."
**[ACTION: click an answer; let it finish to the PR.]**
"Idea to pull request in about four minutes. But notice — **it does not merge.** A human always
reviews. Vibe engineering moves the bottleneck from **typing to thinking.**"

---

### 4:25 – 5:00 · ST-06 + ST-07 LIGHTNING + CLOSE
**[ACTION: hub → ST-06, drag the 2024⇄2026 slider once.]**
"Half these job titles are new — Agent Wrangler, Reliability Ethicist. The role didn't get
automated away. **It got promoted.**"

**[ACTION: hub → ST-07, click "▸ Scan through membrane".]**
"And before any data reaches a model, a **membrane** redacts the personal data, blocks the secrets,
keeps regulated data on-prem, and catches a prompt-injection attack. **Sensitive data shouldn't
trust the model — it should never reach it.**"

**[ACTION: back to hub, cursor still.]**
"Seven stops, one day. And notice — **none of the hard problems were technical.** They were about
trust, ethics, sustainability, and looking after each other. *That's* the day of a DevOps engineer
in 2026. Thanks for watching."

**[ACTION: hold on the hub for 2 seconds, then stop recording.]**

---

### Recording checklist
- [ ] `docker compose up --build` running; hub loads at `localhost:8080`.
- [ ] Browser full-screen, bookmarks/toolbars hidden, system notifications off.
- [ ] Do a dry run once — the animated sequences (governance, rebalance, vibe pipeline) have
      built-in delays; **let them finish before you click on.**
- [ ] If a take goes wrong, every station has a stable URL — jump straight back to it.
- [ ] Total target: **5:00.** If long, trim the ST-06/ST-07 lightning round first.
