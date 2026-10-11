# 3-minute demo video script

Rules to respect: under 3 minutes, public on YouTube, show the project working on the device it was built for,
no third-party trademarks or copyrighted music. Use your own voice or captions. Screen-record at 1080p.

Before recording: run `r2 cloud` (Nebius), have Ollama running, clear `data/egress.jsonl` so the ledger starts empty,
and keep `r2 daemon` running so the floating shield is visible.

| Time | Show | Say (short) |
|---|---|---|
| 0:00-0:15 | The floating shield on the desktop, then the chat window | "This is R2, an always-on assistant on my real PC. Every personal AI says your data is safe. R2 lets you check." |
| 0:15-0:40 | Type: "Plan my week around three deadlines: Monday report, Wednesday demo, Friday review" | "A planning request. R2 routes it to Nemotron Ultra on Nebius Token Factory. Plain chat would go to Nano, tool tasks to Super, so I only spend the big model when it's worth it." |
| 0:40-1:10 | Open the Privacy ledger panel (web page) or run `r2 ledger` | "Here is the routing decision and the reason, the host it went to, and exactly what was sent." |
| 1:10-1:50 | Type: "My phone is 98765 43210 and my card is 4111 1111 1111 1111, remind me to call the bank" | "Now something private. The gateway masked the phone and card before sending. The model only saw placeholders; the real values come back locally. With Ollama running, a request like this stays on the PC entirely." Show the ledger line: `[1 PHONE, 1 CARD]` and "stayed on this PC". |
| 1:50-2:15 | Type: "Tidy my Downloads folder" (skills panel visible) | "R2 found its tidy-downloads skill, told me the plan first, and asked before moving anything. Skills are plain text recipes. R2 can propose new ones, but only I can approve them." Show Approve/Reject on a proposal. |
| 2:15-2:35 | `r2 tools disable run_shell`, then ask R2 to run a command | "I choose the tools. A disabled tool is removed entirely, so the model can't even try it." |
| 2:35-2:50 | `r2 memory export`, open MEMORY.md in Notepad | "Memory is a file I can open, edit and import back. My data, in my hands." |
| 2:50-3:00 | Title card: repo URL, "Nemotron on Nebius Token Factory, Tavily, OpenShell policy" | "R2: a personal AI you can audit. Open source, MIT." |

Tips: cut every wait (model thinking) with a jump cut; keep the ledger on screen long enough to read; do not show
your real key, real phone number or real card (use the dummy ones above, which are standard test values).
